"""领取后的可信身份重建、检查点恢复与事务化结果发布。"""

import asyncio
import time
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from langsmith import tracing_context
from pydantic import SecretStr
from sqlmodel import Session

from app.config import Settings
from app.erp.client import ERPClient, ERPError
from app.erp.permissions import security_context
from app.erp.reads import ReadTools
from app.errors import APIError
from app.events import append_event
from app.history import model_history, provenance, readable
from app.models import AssistantSession, Audit, Message, PurchaseDraft, Run, Task, now
from app.orchestration.checkpoints import checkpoint_store, thread_id
from app.orchestration.graph import RunContext, build_graph, scrub
from app.orchestration.model import ModelClient
from app.purchases.contracts import DraftAction
from app.purchases.drafts import (
    document as draft_document,
)
from app.purchases.drafts import (
    prepare as prepare_draft,
)
from app.purchases.drafts import (
    save as save_draft,
)
from app.queue import heartbeat, lock_current_lease
from app.repository import Repository


def authorize_run(engine, settings, lease, *, client_factory=ERPClient):
    with Session(engine) as database:
        repository = Repository(database, lease.principal)
        run = repository.get(Run, lease.run_id)
        if not run.session_id:
            raise ERPError("auth_required")
        row = repository.get(AssistantSession, run.session_id)
        if (
            row.revoked_at
            or row.generation != run.session_generation
            or row.expires_at <= now()
            or (now() - row.last_seen_at).total_seconds() > settings.session_idle_seconds
        ):
            raise ERPError("auth_required")
        try:
            token = (
                Fernet(settings.session_key.get_secret_value())
                .decrypt(row.encrypted_token.encode())
                .decode()
            )
        except InvalidToken, ValueError:
            raise ERPError("auth_required") from None
    client = client_factory(settings, SecretStr(token))
    principal, _, permissions = asyncio.run(security_context(client))
    if principal != lease.principal:
        raise ERPError("auth_required")
    return client, permissions, token


def handle(engine, settings: Settings, lease, *, model=None, client_factory=ERPClient):
    client, permissions, token = authorize_run(
        engine, settings, lease, client_factory=client_factory
    )
    heartbeat(engine, lease)
    with Session(engine) as database, database.begin():
        repo, run, _ = lock_current_lease(database, lease)
        reply_id = uuid5(NAMESPACE_URL, "agent4jsherp:reply:" + str(run.id))
        if database.exec(repo.query(Message).where(Message.id == reply_id)).first():
            return run.budget.get("response_error")

    def verify():
        heartbeat(engine, lease)
        _, fresh, _ = authorize_run(engine, settings, lease, client_factory=client_factory)
        if fresh.fingerprint != permissions.fingerprint:
            raise ERPError("permissions_changed")

    def reserve(kind):
        # 每次实际外部调用都先在领域库预留，节点崩溃重放仍消耗预算。
        with Session(engine) as database, database.begin():
            _, run, _ = lock_current_lease(database, lease)
            budget = dict(run.budget)
            key = kind + "_calls"
            maximum = settings.model_max_calls if kind == "model" else settings.tool_max_calls
            if budget.get(key, 0) >= maximum:
                from app.orchestration.model import ModelError

                if kind == "model":
                    raise ModelError("budget_exhausted")
                raise ERPError("budget_exhausted")
            budget[key] = budget.get(key, 0) + 1
            run.budget = budget
            database.add(run)
            return {key: budget[key]}

    secrets = (token, settings.model_api_key.get_secret_value() if settings.model_api_key else "")
    context = RunContext(
        lease.principal,
        permissions,
        ReadTools(client, permissions, lease.principal),
        model or ModelClient(settings),
        settings,
        verify,
        secrets,
        reserve,
    )
    with Session(engine) as database, database.begin():
        repository, run, _ = lock_current_lease(database, lease)
        message = repository.get(Message, run.message_id)
        history = model_history(
            repository, run.conversation_id, permissions, task_id=message.task_id
        )[-30:]
        choices = database.exec(
            repository.query(Task).where(Task.conversation_id == run.conversation_id)
        ).all()
        task_choices = [
            {"id": str(row.id), "kind": row.kind, "status": row.status}
            for row in choices
            if row.status not in {"completed", "cancelled"}
        ]
        task = repository.get(Task, message.task_id) if message.task_id else None
        current_draft = (
            database.exec(
                repository.query(PurchaseDraft).where(PurchaseDraft.task_id == task.id)
            ).first()
            if task
            else None
        )
        task_context = task.context if task and readable(task.provenance, permissions) else {}
        page = message.page_context
        page_context = page if readable(page.get("provenance", {}), permissions) else {}
        initial = {
            "history": history,
            "context": {
                "selected_task_id": str(task.id) if task else None,
                "available_tasks": task_choices,
                "task": task_context,
                "draft": draft_document(current_draft, permissions) if current_draft else None,
                "today": datetime.now(ZoneInfo(settings.erp_timezone)).date().isoformat(),
                "page": page_context,
            },
            "facts": [],
            "observations": [],
            "started_at": time.time(),
            "model_calls": 0,
            "tool_calls": 0,
            "delegations": 0,
            "usage": {},
        }
        run.status = "running"
        database.add(run)
        append_event(
            repository,
            run.conversation_id,
            "run.running",
            {"status": "running"},
            run_id=run.id,
            provenance={"source": "state"},
        )
    config = {
        "configurable": {
            "thread_id": thread_id(lease.principal, lease.run_id, permissions.fingerprint)
        },
        "recursion_limit": 100,
        "callbacks": [],
    }
    # 禁用环境可能开启的外部 LangSmith 自动采集；仅记录本地脱敏计数。
    with tracing_context(enabled=False), checkpoint_store(engine) as saver:
        graph = build_graph(saver)
        saved = graph.get_state(config)
        if saved.values and not saved.next:
            result = saved.values
        else:
            result = graph.invoke(
                None if saved.values else scrub(initial, secrets),
                config,
                context=context,
                durability="sync",
            )
    verify()
    response = result.get(
        "response",
        {
            "text": "本轮未取得有效回复，请补充信息后继续。",
            "status": "failed",
            "error": "response_unavailable",
            "facts": [],
        },
    )
    prepared = None
    draft_request = None
    if response.get("draft_request"):
        draft_request = DraftAction.model_validate(response["draft_request"])
        try:

            class BoundedDraftTools:
                permissions = context.permissions

                async def suppliers(self, query):
                    reserve("tool")
                    return await context.tools.suppliers(query)

                async def material(self, query):
                    reserve("tool")
                    return await context.tools.material(query)

            async def bounded_prepare():
                remaining = settings.run_timeout_seconds - (time.time() - result["started_at"])
                if remaining <= 0:
                    raise ERPError("budget_exhausted")
                try:
                    return await asyncio.wait_for(
                        prepare_draft(draft_request.input, BoundedDraftTools(), settings),
                        timeout=remaining,
                    )
                except TimeoutError:
                    raise ERPError("budget_exhausted") from None

            prepared = asyncio.run(bounded_prepare())
        except (ERPError, APIError) as failure:
            response = {
                "text": failure.message
                if isinstance(failure, APIError)
                else "草稿主数据或权限暂时无法核实，请补充信息后继续。",
                "status": "failed",
                "error": failure.code,
                "facts": response.get("facts", []),
            }
    verify()
    with Session(engine) as database, database.begin():
        repository, run, _ = lock_current_lease(database, lease)
        source = repository.get(Message, run.message_id)
        identity = uuid5(NAMESPACE_URL, "agent4jsherp:reply:" + str(run.id))
        existing = database.exec(repository.query(Message).where(Message.id == identity)).first()
        if not existing:
            cards = [{"type": fact["kind"], "fact": fact} for fact in response.get("facts", [])]
            if prepared is not None and draft_request is not None:
                try:
                    selected_draft = (
                        database.exec(
                            repository.query(PurchaseDraft).where(
                                PurchaseDraft.task_id == source.task_id
                            )
                        ).first()
                        if source.task_id
                        else None
                    )
                    if selected_draft and not set(
                        selected_draft.input_data.get("unsupported_conditions", [])
                    ).issubset(draft_request.input.unsupported_conditions):
                        raise APIError(
                            "unsupported_conditions_changed",
                            "已保留原先不支持的条件；如已取消这些要求，请在卡片中明确移除。",
                            409,
                        )
                    if draft_request.new_task:
                        if selected_draft and selected_draft.status not in {
                            "completed",
                            "cancelled",
                        }:
                            raise APIError(
                                "active_task", "请先核对或取消当前采购，再明确提出新需求", 409
                            )
                        selected_draft = None
                    if (selected_draft is None) != (draft_request.expected_version == 0):
                        raise APIError("version_conflict", "草稿版本已变化，请重新检查", 409)
                    created = save_draft(
                        repository,
                        run.conversation_id,
                        uuid5(NAMESPACE_URL, "agent4jsherp:draft:" + str(run.id)),
                        draft_request.input,
                        prepared,
                        permissions,
                        draft_id=selected_draft.id if selected_draft else None,
                        expected_version=draft_request.expected_version,
                        task_id=None if draft_request.new_task else source.task_id,
                    )
                    cards.append(
                        {
                            "type": "purchase_draft",
                            "draft_id": str(created.id),
                            "task_id": str(created.task_id),
                            "version": created.version,
                        }
                    )
                except APIError as failure:
                    response = {"text": failure.message, "status": "failed", "error": failure.code}

            if response.get("preparation"):
                cards.append({"type": "preparation", "data": response["preparation"]})
            if response.get("error"):
                cards.append(
                    {
                        "type": "task_status",
                        "data": {"status": response["status"], "error": response["error"]},
                    }
                )
            tags = provenance(permissions, capabilities=())
            reply = Message(
                id=identity,
                **lease.principal.fields(),
                conversation_id=run.conversation_id,
                client_message_id=identity,
                task_id=source.task_id,
                role="assistant",
                content=response["text"],
                cards=cards,
                provenance=tags,
            )
            database.add(reply)
            database.flush()
            append_event(
                repository,
                run.conversation_id,
                "message.created",
                {"message_id": str(reply.id)},
                run_id=run.id,
                task_id=source.task_id,
                provenance={"source": "state"},
            )
            database.add(
                Audit(
                    **lease.principal.fields(),
                    run_id=run.id,
                    task_id=source.task_id,
                    action="conversation",
                    result=response["status"],
                    duration_ms=int((time.time() - result["started_at"]) * 1000),
                    model_calls=result.get("model_calls", 0),
                    tool_calls=result.get("tool_calls", 0),
                )
            )
        run.budget = {
            **run.budget,
            "model_calls": max(run.budget.get("model_calls", 0), result.get("model_calls", 0)),
            "tool_calls": max(run.budget.get("tool_calls", 0), result.get("tool_calls", 0)),
            "delegations": result.get("delegations", 0),
            "usage": result.get("usage", {}),
            "actual_cost": None,
            "response_error": response.get("error"),
            "checkpoint_thread": config["configurable"]["thread_id"],
            "trace": result.get("trace", []),
            "duration_ms": int((time.time() - result["started_at"]) * 1000),
        }
        database.add(run)
    return response.get("error")
