import asyncio
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.api import Database, Identity
from app.auth import assert_access_current, client_for_access
from app.erp.reads import ReadTools, Search
from app.errors import APIError
from app.models import DraftRevision, PurchaseDraft, Task
from app.purchases.contracts import (
    ConfirmDraft,
    CreateDraft,
    DraftInput,
    EditDraft,
    ReopenDraft,
    VersionCommand,
)
from app.purchases.drafts import (
    content_hash,
    document,
    prepare,
    prepare_confirmation,
    save,
)
from app.repository import Repository

router = APIRouter(prefix="/assistant/api/v1")


def tools(request):
    access = request.state.access
    return ReadTools(client_for_access(request, access), access.permissions, access.principal)


@router.get("/lookups/{kind}")
def lookup(request: Request, kind: str, query: str, principal: Identity):
    reader = tools(request)
    if kind not in {"materials", "suppliers"}:
        raise APIError("not_found", "不支持此查询", 404)
    try:
        args = Search(query=query)
    except ValidationError:
        raise APIError("invalid_query", "请输入1至80字的关键词", 422) from None
    return asyncio.run(getattr(reader, kind)(args))


@router.get("/tasks/{task_id}/draft")
def task_draft(request: Request, task_id: UUID, principal: Identity, database: Database):
    repo = Repository(database, principal)
    repo.get(Task, task_id)
    draft = database.exec(repo.query(PurchaseDraft).where(PurchaseDraft.task_id == task_id)).first()
    return document(draft, request.state.access.permissions) if draft else None


@router.get("/drafts/{draft_id}")
def get_draft(request: Request, draft_id: UUID, principal: Identity, database: Database):
    return document(
        Repository(database, principal).get(PurchaseDraft, draft_id),
        request.state.access.permissions,
    )


@router.post("/conversations/{conversation_id}/drafts", status_code=201)
def create_draft(
    request: Request,
    conversation_id: UUID,
    body: CreateDraft,
    principal: Identity,
    database: Database,
):
    repo = Repository(database, principal)
    existing = database.exec(
        repo.query(PurchaseDraft).where(PurchaseDraft.creation_command == body.command_id)
    ).first()
    if existing:
        task = repo.get(Task, existing.task_id)
        revision = database.exec(
            repo.query(DraftRevision).where(
                DraftRevision.draft_id == existing.id, DraftRevision.version == 1
            )
        ).one()
        if (
            (body.task_id and body.task_id != existing.task_id)
            or task.conversation_id != conversation_id
            or revision.command_hash != content_hash(body.input.model_dump(mode="json"))
        ):
            raise APIError("command_conflict", "命令内容已变化", 409)
        return document(existing, request.state.access.permissions)
    resolved = asyncio.run(prepare(body.input, tools(request), request.app.state.settings))
    assert_access_current(request, database, request.state.access)
    draft = save(
        repo,
        conversation_id,
        body.command_id,
        body.input,
        resolved,
        request.state.access.permissions,
        task_id=body.task_id,
    )
    database.commit()
    return document(draft, request.state.access.permissions)


@router.patch("/drafts/{draft_id}")
def edit_draft(
    request: Request, draft_id: UUID, body: EditDraft, principal: Identity, database: Database
):
    repo = Repository(database, principal)
    draft = repo.get(PurchaseDraft, draft_id)
    task = repo.get(Task, draft.task_id)
    previous = database.exec(
        repo.query(DraftRevision).where(
            DraftRevision.draft_id == draft.id, DraftRevision.command_id == body.command_id
        )
    ).first()
    base = previous.input_data if previous else draft.input_data
    try:
        data = DraftInput.model_validate({**base, **body.patch})
    except ValidationError:
        raise APIError("invalid_fields", "草稿字段或数量/价格格式有误，最多六位小数", 422) from None
    if previous:
        if previous.command_hash != content_hash(data.model_dump(mode="json")):
            raise APIError("command_conflict", "相同命令的内容已变化", 409)
        return document(draft, request.state.access.permissions)
    if draft.version != body.expected_version:
        raise APIError("version_conflict", "草稿已更新，请重新检查", 409)
    resolved = asyncio.run(prepare(data, tools(request), request.app.state.settings))
    assert_access_current(request, database, request.state.access)
    draft = save(
        repo,
        task.conversation_id,
        body.command_id,
        data,
        resolved,
        request.state.access.permissions,
        draft_id=draft_id,
        expected_version=body.expected_version,
    )
    database.commit()
    return document(draft, request.state.access.permissions)


@router.post("/drafts/{draft_id}/confirmation")
def confirmation(
    request: Request, draft_id: UUID, body: VersionCommand, principal: Identity, database: Database
):
    assert_access_current(request, database, request.state.access)
    result = prepare_confirmation(
        Repository(database, principal),
        draft_id,
        body.expected_version,
        request.state.access.permissions,
    )
    database.commit()
    return result


@router.post("/tasks/{task_id}/cancel")
def cancel(
    request: Request, task_id: UUID, body: VersionCommand, principal: Identity, database: Database
):
    from app.purchases.execution import cancel as cancel_task

    assert_access_current(request, database, request.state.access)
    draft = cancel_task(Repository(database, principal), task_id, body.expected_version)
    database.commit()
    return document(draft, request.state.access.permissions)


@router.post("/drafts/{draft_id}/confirm", status_code=202)
def confirm_draft(
    request: Request, draft_id: UUID, body: ConfirmDraft, principal: Identity, database: Database
):
    from app.purchases.execution import confirm
    from app.purchases.execution import document as execution_document

    assert_access_current(request, database, request.state.access)
    execution = confirm(
        Repository(database, principal),
        draft_id,
        body,
        request.state.access,
        request.app.state.settings,
    )
    database.commit()
    return execution_document(execution, request.state.access.permissions)


@router.post("/drafts/{draft_id}/reopen")
def reopen_draft(
    request: Request, draft_id: UUID, body: ReopenDraft, principal: Identity, database: Database
):
    from app.purchases.execution import reopen

    assert_access_current(request, database, request.state.access)
    draft = reopen(
        Repository(database, principal),
        draft_id,
        body.expected_version,
        request.state.access.permissions,
        body.command_id,
    )
    database.commit()
    return document(draft, request.state.access.permissions)


@router.get("/drafts/{draft_id}/execution")
def draft_execution(request: Request, draft_id: UUID, principal: Identity, database: Database):
    from app.models import Execution
    from app.purchases.execution import document as execution_document

    repo = Repository(database, principal)
    draft = repo.get(PurchaseDraft, draft_id)
    execution = database.exec(
        repo.query(Execution).where(
            Execution.draft_id == draft_id, Execution.version == draft.version
        )
    ).first()
    return execution_document(execution, request.state.access.permissions) if execution else None


@router.get("/executions/{execution_id}")
def get_execution(request: Request, execution_id: UUID, principal: Identity, database: Database):
    from app.models import Execution
    from app.purchases.execution import document as execution_document

    return execution_document(
        Repository(database, principal).get(Execution, execution_id),
        request.state.access.permissions,
    )


@router.post("/executions/{execution_id}/reconcile", status_code=202)
def reconcile_execution(
    request: Request, execution_id: UUID, principal: Identity, database: Database
):
    from app.purchases.execution import document as execution_document
    from app.purchases.execution import request_reconciliation

    assert_access_current(request, database, request.state.access)
    execution = request_reconciliation(
        Repository(database, principal),
        execution_id,
        request.state.access,
        request.app.state.settings,
    )
    database.commit()
    return execution_document(execution, request.state.access.permissions)
