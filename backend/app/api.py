from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.dependencies import current_principal, database_session
from app.errors import APIError
from app.events import ResyncRequired, read_events
from app.models import Conversation, Message, Task
from app.repository import Principal, Repository, ResourceNotFound
from app.snapshots import snapshot, task_document
from app.streaming import parse_cursor, stream_events

router = APIRouter(prefix="/assistant/api/v1")
Identity = Annotated[Principal, Depends(current_principal)]
Database = Annotated[Session, Depends(database_session)]


@router.get("/conversations")
def conversations(principal: Identity, database: Database):
    repository = Repository(database, principal)
    first_prompt = (
        select(Message.content)
        .where(
            Message.conversation_id == Conversation.id,
            Message.role == "user",
            *(getattr(Message, field) == value for field, value in principal.fields().items()),
        )
        .order_by(Message.created_at, Message.id)
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    rows = database.execute(
        repository.query(Conversation)
        .add_columns(first_prompt)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
    ).all()
    # Use only the owner's input; ERP answers may no longer be readable.
    return [
        {
            "id": str(row.id),
            "title": (
                " ".join(prompt.split())[:48] if row.title == "新对话" and prompt else row.title
            ),
            "created_at": row.created_at.isoformat(),
        }
        for row, prompt in rows
    ]


@router.get("/conversations/{conversation_id}")
def conversation(request: Request, conversation_id: UUID, principal: Identity, database: Database):
    try:
        return snapshot(
            Repository(database, principal),
            conversation_id,
            getattr(getattr(request.state, "access", None), "permissions", None),
        )
    except ResourceNotFound:
        raise APIError("not_found", "找不到该对话", 404) from None


@router.get("/tasks")
def tasks(request: Request, principal: Identity, database: Database):
    repository = Repository(database, principal)
    rows = database.exec(repository.query(Task).order_by(Task.created_at.desc())).all()
    return [
        task_document(row, getattr(getattr(request.state, "access", None), "permissions", None))
        for row in rows
    ]


@router.get("/tasks/{task_id}")
def task(request: Request, task_id: UUID, principal: Identity, database: Database):
    try:
        return task_document(
            Repository(database, principal).get(Task, task_id),
            getattr(getattr(request.state, "access", None), "permissions", None),
        )
    except ResourceNotFound:
        raise APIError("not_found", "找不到该任务", 404) from None


@router.get("/conversations/{conversation_id}/events")
def events(
    request: Request,
    conversation_id: UUID,
    principal: Identity,
    last_event_id: Annotated[str | None, Header()] = None,
):
    try:
        after = parse_cursor(last_event_id, conversation_id)
        with Session(request.app.state.engine) as database:
            read_events(Repository(database, principal), conversation_id, after, limit=1)
    except ResourceNotFound:
        raise APIError("not_found", "找不到该对话", 404) from None
    except ResyncRequired:
        raise APIError(
            "resync_required", "请重新载入对话", 409, next_action="reload_snapshot"
        ) from None
    return StreamingResponse(
        stream_events(request, principal, conversation_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
