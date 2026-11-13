import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import Database, Identity
from app.auth import assert_access_current
from app.context_api import PageContext, resolve_context
from app.errors import APIError
from app.models import Message, Run
from app.repository import Repository, ResourceNotFound
from app.runs import ConversationBusy, MessageConflict, submit_message

router = APIRouter(prefix="/assistant/api/v1")


class SendMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_message_id: UUID
    content: Annotated[str, Field(min_length=1, max_length=8000)]
    task_id: UUID | None = None
    page_context: PageContext | None = None


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def send_message(
    request: Request,
    conversation_id: UUID,
    body: SendMessage,
    principal: Identity,
    database: Database,
):
    if not body.content.strip():
        raise APIError("empty_message", "请输入消息", 422)
    settings = request.app.state.settings
    if not settings.model_api_key:
        raise APIError("model_not_configured", "模型连接尚未配置，请联系管理员", 503)
    repository = Repository(database, principal)
    previous = database.exec(
        repository.query(Message).where(
            Message.conversation_id == conversation_id,
            Message.client_message_id == body.client_message_id,
        )
    ).first()
    if previous:
        assert_access_current(request, database, request.state.access)
        if previous.content != body.content or previous.task_id != body.task_id:
            raise APIError("message_conflict", "相同消息标识的内容已变化", 409)
        run = database.exec(repository.query(Run).where(Run.message_id == previous.id)).one()
        return {"id": str(run.id), "message_id": str(run.message_id), "status": run.status}
    page = {}
    if (
        body.page_context
        and body.page_context.status == "available"
        and body.page_context.record_ids
    ):
        page, tags = asyncio.run(resolve_context(request, body.page_context))
        page["provenance"] = tags
    access = request.state.access
    assert_access_current(request, database, access)
    try:
        run = submit_message(
            Repository(database, principal),
            conversation_id,
            body.client_message_id,
            body.content,
            task_id=body.task_id,
            page_context=page,
            session_id=access.session_id,
            session_generation=access.generation,
        )
        database.commit()
        return {"id": str(run.id), "message_id": str(run.message_id), "status": run.status}
    except ResourceNotFound:
        raise APIError("not_found", "找不到该对话或任务", 404) from None
    except ConversationBusy:
        raise APIError("conversation_busy", "上一条消息仍在处理，请稍候再发送", 409) from None
    except MessageConflict:
        raise APIError("message_conflict", "相同消息标识的内容已变化", 409) from None
