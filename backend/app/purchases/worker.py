"""采购执行 worker：发送边界只持久化一次；租约恢复只回查。"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import or_
from sqlmodel import Session, select

from app.erp.client import ERPClient, ERPError
from app.erp.permissions import security_context
from app.erp.reads import ReadTools
from app.errors import APIError
from app.history import readable
from app.models import (
    AssistantSession,
    Conversation,
    Execution,
    ExecutionJob,
    PurchaseDraft,
    Task,
    now,
)
from app.purchases.contracts import DraftInput
from app.purchases.drafts import prepare
from app.purchases.erp import business_content, generate_number, order_body, reconcile
from app.purchases.execution import publish
from app.queue import LeaseLost, database_now
from app.repository import Principal, Repository


@dataclass(frozen=True)
class ExecutionLease:
    job_id: UUID
    execution_id: UUID
    token: UUID
    principal: Principal


def claim(engine):
    with Session(engine, expire_on_commit=False) as db, db.begin():
        instant = database_now(db)
        job = db.exec(
            select(ExecutionJob)
            .where(
                ExecutionJob.available_at <= instant,
                or_(
                    ExecutionJob.status == "queued",
                    (ExecutionJob.status == "leased") & (ExecutionJob.lease_until <= instant),
                ),
            )
            .order_by(ExecutionJob.available_at, ExecutionJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if not job:
            return None
        job.status, job.lease_token = "leased", uuid4()
        job.lease_until = instant + timedelta(seconds=120)
        job.attempts += 1
        db.add(job)
        return ExecutionLease(
            job.id,
            job.execution_id,
            job.lease_token,
            Principal(job.connection_id, job.tenant_id, job.erp_user_id),
        )


def lock(db, lease):
    repo = Repository(db, lease.principal)
    execution = repo.get(Execution, lease.execution_id)
    task = repo.get(Task, execution.task_id)
    repo.get(Conversation, task.conversation_id, lock=True)
    job = repo.get(ExecutionJob, lease.job_id, lock=True)
    if (
        job.status != "leased"
        or job.lease_token != lease.token
        or job.lease_until <= database_now(db)
    ):
        raise LeaseLost
    execution = repo.get(Execution, lease.execution_id, lock=True)
    return repo, execution, job, repo.get(Task, execution.task_id, lock=True)


def authorize(engine, settings, lease, client_factory):
    with Session(engine, expire_on_commit=False) as db:
        repo = Repository(db, lease.principal)
        execution = repo.get(Execution, lease.execution_id)
        row = repo.get(AssistantSession, execution.session_id)
        if (
            row.revoked_at
            or row.generation != execution.session_generation
            or row.expires_at <= now()
            or (now() - row.last_seen_at).total_seconds() > settings.session_idle_seconds
        ):
            raise ERPError("auth_required")
        try:
            token = Fernet(settings.session_key.get_secret_value()).decrypt(
                row.encrypted_token.encode()
            )
        except InvalidToken, ValueError:
            raise ERPError("auth_required") from None
    client = client_factory(settings, SecretStr(token.decode()))
    principal, _, permissions = asyncio.run(security_context(client))
    if principal != lease.principal:
        raise ERPError("auth_required")
    return client, permissions


def finish(repo, execution, job, task, status, error=None):
    execution.status, execution.error_code, execution.completed_at = status, error, now()
    job.status, job.lease_token, job.lease_until = "done", None, None
    repo.session.add(job)
    if status == "succeeded":
        draft = repo.get(PurchaseDraft, execution.draft_id, lock=True)
        draft.status = "completed"
        repo.session.add(draft)
    publish(repo, execution, task)


def reschedule(repo, execution, job, task, settings, error=None):
    count = execution.reconciliation_attempts
    if count >= len(settings.reconciliation_delays):
        finish(repo, execution, job, task, "needs_attention", error or "not_yet_visible")
        return
    execution.status, execution.error_code = "reconciling", error
    job.status, job.lease_token, job.lease_until = "queued", None, None
    job.available_at = now() + timedelta(seconds=settings.reconciliation_delays[count])
    repo.session.add(job)
    publish(repo, execution, task)


def handle(engine, settings, lease, *, client_factory=ERPClient, boundary_hook=None):
    with Session(engine, expire_on_commit=False) as db, db.begin():
        repo, execution, job, task = lock(db, lease)
        if execution.status not in {"queued", "dispatching", "reconciling"}:
            finish(repo, execution, job, task, execution.status, execution.error_code)
            return
        before_send = execution.dispatch_marked_at is None
        if job.attempts > 12:
            finish(
                repo,
                execution,
                job,
                task,
                "rejected" if before_send else "needs_attention",
                "worker_recovery_exhausted",
            )
            return
        snapshot, input_data = execution.snapshot, execution.input_data
    try:
        client, permissions = authorize(engine, settings, lease, client_factory)
        if before_send:
            if not settings.purchase_writes_enabled:
                raise ERPError("writes_disabled")
            permissions.require("purchase_create")
            permissions.require("purchase_prices")
            permissions.require("purchase_read")

            async def preflight():
                return await asyncio.wait_for(
                    prepare(
                        DraftInput.model_validate(input_data),
                        ReadTools(client, permissions, lease.principal),
                        settings,
                    ),
                    timeout=60,
                )

            verified = asyncio.run(preflight())
            if business_content(snapshot) != business_content(verified):
                raise ERPError("master_data_changed")
            with Session(engine, expire_on_commit=False) as db:
                existing_number = (
                    Repository(db, lease.principal).get(Execution, lease.execution_id).erp_number
                )
            number = existing_number or asyncio.run(generate_number(client))
            with Session(engine, expire_on_commit=False) as db, db.begin():
                repo, execution, job, task = lock(db, lease)
                if execution.status != "queued" or execution.dispatch_marked_at is not None:
                    raise LeaseLost
                execution.erp_number = number
                execution.request_body = order_body(snapshot, number)
                db.add(execution)
            # 长主数据核验后再验证会话/权限，且在发送边界前复查本地代次。
            client, permissions = authorize(engine, settings, lease, client_factory)
            permissions.require("purchase_create")
            permissions.require("purchase_prices")
            if not readable(execution.provenance, permissions):
                raise ERPError("permissions_changed")
            with Session(engine, expire_on_commit=False) as db, db.begin():
                repo, execution, job, task = lock(db, lease)
                session = repo.get(AssistantSession, execution.session_id)
                if (
                    session.revoked_at
                    or session.generation != execution.session_generation
                    or session.expires_at <= now()
                ):
                    raise ERPError("auth_required")
                if execution.status != "queued" or execution.dispatch_marked_at is not None:
                    raise LeaseLost
                if boundary_hook:
                    boundary_hook("before_mark", execution)
                execution.status, execution.dispatch_marked_at = "dispatching", now()
                request_body = execution.request_body
                publish(repo, execution, task)
            if boundary_hook:
                boundary_hook("after_mark", execution)
            # 只在本次成功写入边界标记的控制流调用；恢复永远不会回到这里。
            try:
                asyncio.run(client.create_purchase(request_body))
            except ERPError:
                pass  # 所有未核实的 ERP 响应均只进入回查，绝不自动重发。
            if boundary_hook:
                boundary_hook("after_post", execution)
            with Session(engine, expire_on_commit=False) as db, db.begin():
                repo, execution, job, task = lock(db, lease)
                reschedule(repo, execution, job, task, settings)
            return
    except (ERPError, APIError, TimeoutError) as failure:
        error = failure.code if hasattr(failure, "code") else "preflight_timeout"
        with Session(engine, expire_on_commit=False) as db, db.begin():
            repo, execution, job, task = lock(db, lease)
            if execution.dispatch_marked_at is None:
                finish(repo, execution, job, task, "rejected", error)
            elif error in {"auth_required", "permission_denied"}:
                finish(repo, execution, job, task, "auth_required", error)
            else:
                execution.reconciliation_attempts += 1
                reschedule(repo, execution, job, task, settings, error)
        return
    # 已标记发送的恢复路径，包括持久化标记后尚未来得及 POST 的保守情况。
    try:
        with Session(engine, expire_on_commit=False) as db, db.begin():
            repo, execution, job, task = lock(db, lease)
            if execution.reconciliation_attempts >= len(settings.reconciliation_delays):
                finish(repo, execution, job, task, "needs_attention", "reconciliation_exhausted")
                return
            execution.reconciliation_attempts += 1
            db.add(execution)
        receipt = asyncio.run(reconcile(client, permissions, lease.principal, execution))
        with Session(engine, expire_on_commit=False) as db, db.begin():
            repo, execution, job, task = lock(db, lease)
            execution.receipt = receipt
            finish(repo, execution, job, task, "succeeded")
    except ERPError as failure:
        with Session(engine, expire_on_commit=False) as db, db.begin():
            repo, execution, job, task = lock(db, lease)
            if failure.code in {"auth_required", "permission_denied"}:
                finish(repo, execution, job, task, "auth_required", failure.code)
            elif failure.code in {"order_mismatch", "ambiguous_result", "unsupported_contract"}:
                finish(repo, execution, job, task, "needs_attention", failure.code)
            else:
                reschedule(repo, execution, job, task, settings, failure.code)


def run_once(engine, settings):
    lease = claim(engine)
    if not lease:
        return False
    try:
        handle(engine, settings, lease)
    except LeaseLost:
        pass
    except Exception:
        # 无正文日志；保留租约并有限恢复。已标记发送的执行不再创建。
        import logging

        logging.getLogger("assistant.worker").error("execution_interrupted job_id=%s", lease.job_id)
    return True
