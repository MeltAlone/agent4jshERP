import asyncio
import json
from uuid import UUID

from fastapi import Request
from sqlalchemy import Engine
from sqlmodel import Session

from app.auth import authorize
from app.erp.permissions import Permissions
from app.errors import APIError
from app.events import ResyncRequired, event_document, read_events
from app.history import readable
from app.repository import Principal, Repository


def parse_cursor(value: str | None, conversation_id: UUID) -> int:
    if value is None:
        return 0
    try:
        scope, sequence = value.rsplit(":", 1)
        if UUID(scope) != conversation_id or not sequence.isdecimal():
            raise ValueError
        return int(sequence)
    except ValueError, AttributeError:
        raise ResyncRequired from None


def load_batch(
    engine: Engine,
    principal: Principal,
    conversation_id: UUID,
    after: int,
    permissions: Permissions | None = None,
):
    with Session(engine) as session:
        result = []
        for event in read_events(Repository(session, principal), conversation_id, after):
            item = event_document(event)
            if not readable(event.provenance, permissions):
                item = {**item, "type": "data.redacted", "payload": {}}
            result.append(item)
        return result


async def stream_events(request: Request, principal: Principal, conversation_id: UUID, after: int):
    engine = request.app.state.engine
    idle = 0
    while not await request.is_disconnected():
        try:
            permissions = None
            if getattr(request.state, "access", None) is not None:
                previous_fingerprint = request.state.access.permissions.fingerprint
                access = await authorize(request)
                if access.permissions.fingerprint != previous_fingerprint:
                    yield 'event: permissions_changed\ndata: {"code":"reload_snapshot"}\n\n'
                    return
                if access.principal != principal:
                    raise APIError("session_changed", "身份已变化", 409)
                permissions = access.permissions
            batch = await asyncio.to_thread(
                load_batch, engine, principal, conversation_id, after, permissions
            )
        except APIError as error:
            yield "event: session_invalid\ndata: " + json.dumps({"code": error.code}) + "\n\n"
            return
        except ResyncRequired:
            yield 'event: resync_required\ndata: {"code":"resync_required"}\n\n'
            return
        for event in batch:
            after = event["sequence"]
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            yield f"id: {conversation_id}:{after}\nevent: state\ndata: {data}\n\n"
        if not batch:
            idle += 1
            if idle % 15 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)
        else:
            idle = 0
