from uuid import UUID

from app.models import Conversation, Event
from app.repository import Repository


class ResyncRequired(Exception):
    pass


def append_event(
    repository: Repository,
    conversation_id: UUID,
    kind: str,
    payload: dict,
    *,
    run_id: UUID | None = None,
    task_id: UUID | None = None,
    resource_version: int = 1,
    provenance: dict | None = None,
) -> Event:
    if provenance is None:
        safe_kinds = {"run.queued", "run.running", "run.completed", "run.failed"}
        if kind not in safe_kinds or set(payload) - {"message_id", "status", "error_code"}:
            raise ValueError("Business events require explicit permission provenance")
    conversation = repository.get(Conversation, conversation_id, lock=True)
    conversation.next_sequence += 1
    repository.session.add(conversation)
    result = Event(
        **repository.principal.fields(),
        conversation_id=conversation_id,
        sequence=conversation.next_sequence,
        kind=kind,
        payload=payload,
        provenance=provenance or {"source": "state"},
        run_id=run_id,
        task_id=task_id,
        resource_version=resource_version,
    )
    repository.session.add(result)
    repository.session.flush()
    return result


def read_events(repository: Repository, conversation_id: UUID, after: int, limit: int = 100):
    conversation = repository.get(Conversation, conversation_id, lock=True)
    if after < conversation.event_floor or after > conversation.next_sequence or after < 0:
        raise ResyncRequired
    return repository.session.exec(
        repository.query(Event)
        .where(Event.conversation_id == conversation_id, Event.sequence > after)
        .order_by(Event.sequence)
        .limit(min(max(limit, 1), 500))
    ).all()


def event_document(event: Event) -> dict:
    return {
        "event_id": str(event.id),
        "sequence": event.sequence,
        "schema_version": event.schema_version,
        "resource_version": event.resource_version,
        "conversation_id": str(event.conversation_id),
        "run_id": str(event.run_id) if event.run_id else None,
        "task_id": str(event.task_id) if event.task_id else None,
        "type": event.kind,
        "payload": event.payload,
    }
