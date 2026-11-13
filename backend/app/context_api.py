import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.api import Database, Identity
from app.auth import assert_access_current, client_for_access, erp_error
from app.erp.client import ERPError
from app.erp.records import material_record, order_candidates, verified_order
from app.errors import APIError
from app.events import append_event
from app.history import provenance
from app.models import Conversation, Task
from app.repository import Repository, ResourceNotFound
from app.snapshots import task_document

router = APIRouter(prefix="/assistant/api/v1")
RecordID = Annotated[StrictInt, Field(gt=0, le=9007199254740991)]


class PageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_type: Literal["material_list", "purchase_order_list", "unknown"]
    revision: Annotated[StrictInt, Field(ge=0)]
    record_type: Literal["material", "purchase_order"] | None
    record_ids: list[RecordID] = Field(max_length=100)
    captured_at: datetime
    status: Literal["available", "context_unavailable"]

    @model_validator(mode="after")
    def matching_page(self):
        expected = {
            "material_list": "material",
            "purchase_order_list": "purchase_order",
            "unknown": None,
        }
        if self.record_type != expected[self.page_type] or len(set(self.record_ids)) != len(
            self.record_ids
        ):
            raise ValueError("页面记录类型不符")
        if self.captured_at.tzinfo is None:
            raise ValueError("页面时间必须包含时区")
        if self.page_type == "unknown" and (
            self.record_ids or self.status != "context_unavailable"
        ):
            raise ValueError("未知页面不能声明记录")
        return self


class ContextCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: UUID
    context: PageContext


class AdoptContext(ContextCommand):
    expected_version: Annotated[StrictInt, Field(gt=0)]


async def resolve_context(request, context: PageContext):
    moment = datetime.now(UTC)
    if (
        context.status != "available"
        or not context.record_ids
        or context.captured_at < moment - timedelta(minutes=5)
        or context.captured_at > moment + timedelta(seconds=30)
    ):
        raise APIError("context_unavailable", "当前选择不可用或已过期，请在 ERP 重新选择", 422)
    access = request.state.access
    client = client_for_access(request, access)
    try:
        if context.record_type == "material":
            records = [
                await material_record(client, access.permissions, access.principal, identity)
                for identity in context.record_ids
            ]
            capabilities = ("material_read",)
        else:
            candidates = await order_candidates(client, access.permissions)
            by_id = {row["id"]: row for row in candidates}
            if any(identity not in by_id for identity in context.record_ids):
                raise ERPError("record_not_accessible")
            records = [
                {"id": identity, "number": by_id[identity]["number"]}
                for identity in context.record_ids
            ]
            capabilities = ("purchase_read",)
    except ERPError as error:
        raise erp_error(error) from None
    return {"page": context.model_dump(mode="json"), "records": records}, provenance(
        access.permissions, capabilities=capabilities
    )


@router.get("/host/purchase-orders/{record_id}")
def host_order(
    request: Request,
    record_id: Annotated[int, PathParam(gt=0, le=9007199254740991)],
    number: Annotated[str, Field(pattern=r"^CGDD[0-9]{11}$")],
    principal: Identity,
    database: Database,
):
    access = request.state.access
    try:
        result = asyncio.run(
            verified_order(
                client_for_access(request, access), access.permissions, principal, record_id, number
            )
        )
    except ERPError as error:
        raise erp_error(error) from None
    assert_access_current(request, database, access)
    return result


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, principal: Identity, database: Database):
    assert_access_current(request, database, request.state.access)
    row = Repository(database, principal).conversation()
    database.commit()
    return {"id": str(row.id), "title": row.title}


@router.post("/conversations/{conversation_id}/context-tasks", status_code=201)
def create_context_task(
    request: Request,
    conversation_id: UUID,
    body: ContextCommand,
    principal: Identity,
    database: Database,
):
    repository = Repository(database, principal)
    try:
        repository.get(Conversation, conversation_id)
        # 用户命令按 owner/conversation 去重；重放不重新采集实时页面。
        previous = database.exec(
            repository.query(Task).where(Task.conversation_id == conversation_id)
        ).all()
        for row in previous:
            if row.context.get("creation_command") == str(body.command_id):
                return task_document(row, request.state.access.permissions)
        context, tags = asyncio.run(resolve_context(request, body.context))
        assert_access_current(request, database, request.state.access)
        repository.get(Conversation, conversation_id, lock=True)
        for row in database.exec(
            repository.query(Task).where(Task.conversation_id == conversation_id)
        ).all():
            if row.context.get("creation_command") == str(body.command_id):
                return task_document(row, request.state.access.permissions)
        context["creation_command"] = str(body.command_id)
        context["applied_commands"] = []
        row = repository.task(conversation_id, "context", context, provenance=tags)
        append_event(
            repository,
            conversation_id,
            "task.created",
            {"task_id": str(row.id)},
            task_id=row.id,
            provenance={"source": "state"},
        )
        database.commit()
        return task_document(row, request.state.access.permissions)
    except ResourceNotFound:
        raise APIError("not_found", "找不到该对话", 404) from None


@router.post("/tasks/{task_id}/context")
def adopt_context(
    request: Request, task_id: UUID, body: AdoptContext, principal: Identity, database: Database
):
    repository = Repository(database, principal)
    try:
        row = repository.get(Task, task_id)
        if str(body.command_id) in row.context.get("applied_commands", []):
            return task_document(row, request.state.access.permissions)
        if row.kind != "context" or row.resource_version != body.expected_version:
            raise APIError("version_conflict", "任务已变化，请载入最新版本", 409)
        context, tags = asyncio.run(resolve_context(request, body.context))
        assert_access_current(request, database, request.state.access)
        row = repository.get(Task, task_id, lock=True)
        if str(body.command_id) in row.context.get("applied_commands", []):
            return task_document(row, request.state.access.permissions)
        if row.resource_version != body.expected_version:
            raise APIError("version_conflict", "任务已变化，请载入最新版本", 409)
        commands = row.context.get("applied_commands", [])
        if len(commands) >= 100:
            raise APIError("context_limit", "此任务的选择更新次数已达上限，请新建任务", 409)
        row.context = {
            **context,
            "creation_command": row.context["creation_command"],
            "applied_commands": [*commands, str(body.command_id)],
        }
        row.provenance = tags
        row.resource_version += 1
        database.add(row)
        append_event(
            repository,
            row.conversation_id,
            "task.updated",
            {"task_id": str(row.id)},
            task_id=row.id,
            resource_version=row.resource_version,
            provenance={"source": "state"},
        )
        database.commit()
        return task_document(row, request.state.access.permissions)
    except ResourceNotFound:
        raise APIError("not_found", "找不到该任务", 404) from None
