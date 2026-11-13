"""确认与执行领域状态；所有变更和 outbox 同事务提交。"""

from app.auth import digest
from app.errors import APIError
from app.events import append_event
from app.history import readable
from app.models import (
    Audit,
    Confirmation,
    Conversation,
    DraftRevision,
    Execution,
    ExecutionJob,
    PurchaseDraft,
    Task,
    now,
)
from app.purchases.drafts import check_confirmation, invalidate

TERMINAL = {"succeeded", "rejected", "needs_attention", "cancelled_before_send", "auth_required"}


def lock_draft(repo, draft_id):
    draft = repo.get(PurchaseDraft, draft_id)
    task = repo.get(Task, draft.task_id)
    repo.get(Conversation, task.conversation_id, lock=True)
    return repo.get(PurchaseDraft, draft_id, lock=True), repo.get(Task, task.id, lock=True)


def publish(repo, execution, task):
    task.status = {
        "queued": "executing",
        "dispatching": "executing",
        "reconciling": "reconciling",
        "succeeded": "completed",
        "rejected": "failed",
        "needs_attention": "needs_attention",
        "auth_required": "auth_required",
        "cancelled_before_send": "cancelled",
    }[execution.status]
    task.resource_version += 1
    task.blocked_reason = execution.error_code
    repo.session.add(task)
    repo.session.add(execution)
    repo.session.flush()
    append_event(
        repo,
        task.conversation_id,
        "execution.updated",
        {"execution_id": str(execution.id), "draft_id": str(execution.draft_id)},
        task_id=task.id,
        resource_version=task.resource_version,
        provenance={"source": "state"},
    )
    repo.session.add(
        Audit(
            **repo.principal.fields(),
            task_id=task.id,
            action="purchase." + execution.status,
            result=execution.error_code or execution.status,
            correlation_id=execution.id,
        )
    )


def document(execution, permissions):
    allowed = readable(execution.provenance, permissions)
    return {
        "id": str(execution.id),
        "draft_id": str(execution.draft_id),
        "task_id": str(execution.task_id),
        "version": execution.version,
        "status": execution.status,
        "erp_number": execution.erp_number if allowed else None,
        "receipt": execution.receipt if allowed else {},
        "error_code": execution.error_code,
        "redacted": not allowed,
        "sent_maybe": execution.dispatch_marked_at is not None,
        "reconciliation_attempts": execution.reconciliation_attempts,
        "can_reconcile": allowed
        and execution.dispatch_marked_at is not None
        and execution.status in {"needs_attention", "auth_required"},
        "can_reopen": allowed and execution.status == "rejected",
    }


def confirm(repo, draft_id, body, access, settings):
    draft, task = lock_draft(repo, draft_id)
    existing = repo.session.exec(
        repo.query(Execution).where(
            Execution.draft_id == draft.id, Execution.version == body.version
        )
    ).first()
    if existing:
        original = repo.get(Confirmation, existing.confirmation_id)
        if original.challenge_hash != digest(body.challenge):
            raise APIError("confirmation_invalid", "确认凭据不符，请刷新执行结果", 409)
        return existing
    if not settings.purchase_writes_enabled:
        raise APIError("writes_disabled", "采购写入未开启，草稿已保留", 409)
    access.permissions.require("purchase_create")
    access.permissions.require("purchase_prices")
    access.permissions.require("purchase_read")
    if not readable(draft.provenance, access.permissions):
        raise APIError("permissions_changed", "权限已变化，请重新核实草稿", 409)
    challenge = check_confirmation(repo, draft, body.challenge, body.version)
    execution = Execution(
        **repo.principal.fields(),
        draft_id=draft.id,
        task_id=task.id,
        version=draft.version,
        content_hash=draft.content_hash,
        snapshot=draft.snapshot,
        input_data=draft.input_data,
        provenance=draft.provenance,
        confirmation_id=challenge.id,
        request_id=body.request_id,
        session_id=access.session_id,
        session_generation=access.generation,
    )
    repo.session.add(execution)
    repo.session.flush()
    repo.session.add(ExecutionJob(**repo.principal.fields(), execution_id=execution.id))
    challenge.consumed_at = now()
    draft.status = "frozen"
    repo.session.add(challenge)
    repo.session.add(draft)
    invalidate(repo, draft)
    publish(repo, execution, task)
    return execution


def cancel(repo, task_id, version):
    task = repo.get(Task, task_id)
    repo.get(Conversation, task.conversation_id, lock=True)
    draft = repo.session.exec(
        repo.query(PurchaseDraft).where(PurchaseDraft.task_id == task.id).with_for_update()
    ).first()
    if not draft:
        raise APIError("not_found", "此任务没有采购草稿", 404)
    task = repo.get(Task, task_id, lock=True)
    if draft.version != version:
        raise APIError("version_conflict", "草稿已更新，请重新检查", 409)
    if draft.status == "cancelled":
        return draft
    execution = repo.session.exec(
        repo.query(Execution)
        .where(Execution.draft_id == draft.id, Execution.version == draft.version)
        .with_for_update()
    ).first()
    if execution:
        if execution.status != "queued" or execution.dispatch_marked_at is not None:
            raise APIError("already_dispatching", "可能已发送，不能保证撤回，结果核对将继续", 409)
        execution.status = "cancelled_before_send"
        execution.completed_at = now()
        job = repo.session.exec(
            repo.query(ExecutionJob).where(ExecutionJob.execution_id == execution.id)
        ).one()
        job.status, job.lease_token, job.lease_until = "done", None, None
        repo.session.add(job)
        publish(repo, execution, task)
    elif draft.status not in {"editing", "ready"}:
        raise APIError("draft_locked", "此草稿不可取消", 409)
    invalidate(repo, draft)
    draft.status, task.status = "cancelled", "cancelled"
    repo.session.add(draft)
    repo.session.add(task)
    append_event(
        repo,
        task.conversation_id,
        "draft.updated",
        {"draft_id": str(draft.id)},
        task_id=task.id,
        resource_version=draft.version,
        provenance={"source": "state"},
    )
    return draft


def reopen(repo, draft_id, version, permissions, command_id):
    draft, task = lock_draft(repo, draft_id)
    # 重发同一 reopen 命令应返回同一新版本。
    existing = repo.session.exec(
        repo.query(DraftRevision).where(
            DraftRevision.draft_id == draft.id, DraftRevision.command_id == command_id
        )
    ).first()
    if existing:
        if existing.command_hash != "reopen:" + str(version):
            raise APIError("command_conflict", "相同命令的内容已变化", 409)
        return draft
    if draft.version != version:
        raise APIError("version_conflict", "草稿已更新，请重新检查", 409)
    execution = repo.session.exec(
        repo.query(Execution).where(Execution.draft_id == draft.id, Execution.version == version)
    ).first()
    if not execution or execution.status != "rejected":
        raise APIError("reopen_denied", "只有确定未执行的失败可重新编辑；未知结果请核对原单号", 409)
    permissions.require("purchase_create")
    if not readable(draft.provenance, permissions):
        raise APIError("permissions_changed", "权限已变化，请重新核实", 409)
    invalidate(repo, draft)
    draft.version += 1
    draft.status, task.status, task.blocked_reason = "ready", "waiting_confirmation", None
    repo.session.add(draft)
    repo.session.add(task)
    repo.session.add(
        DraftRevision(
            **repo.principal.fields(),
            draft_id=draft.id,
            version=draft.version,
            command_id=command_id,
            command_hash="reopen:" + str(version),
            snapshot=draft.snapshot,
            input_data=draft.input_data,
            content_hash=draft.content_hash,
        )
    )
    append_event(
        repo,
        task.conversation_id,
        "draft.updated",
        {"draft_id": str(draft.id)},
        task_id=task.id,
        resource_version=draft.version,
        provenance=draft.provenance,
    )
    return draft


def request_reconciliation(repo, execution_id, access, settings):
    execution = repo.get(Execution, execution_id)
    task = repo.get(Task, execution.task_id)
    repo.get(Conversation, task.conversation_id, lock=True)
    execution = repo.get(Execution, execution_id, lock=True)
    access.permissions.require("purchase_read")
    access.permissions.require("purchase_prices")
    if execution.status in {"queued", "dispatching", "reconciling"}:
        return execution
    if not execution.dispatch_marked_at or execution.status == "succeeded":
        raise APIError("reconciliation_denied", "此执行不需要结果核对", 409)
    if execution.reconciliation_round >= settings.reconciliation_max_rounds:
        raise APIError("reconciliation_exhausted", "已达到自动核对轮数，请使用原单号人工核对", 409)
    execution.reconciliation_round += 1
    execution.completed_at = None
    execution.reconciliation_attempts = 0
    execution.session_id, execution.session_generation = access.session_id, access.generation
    execution.status, execution.error_code = "reconciling", None
    job = repo.session.exec(
        repo.query(ExecutionJob).where(ExecutionJob.execution_id == execution.id)
    ).one()
    job.status, job.available_at, job.lease_token, job.lease_until = "queued", now(), None, None
    job.attempts = 0
    repo.session.add(job)
    publish(repo, execution, task)
    return execution
