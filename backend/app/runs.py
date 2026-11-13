from uuid import UUID

from app.events import append_event
from app.models import Conversation, Job, Message, Run, Task
from app.repository import Repository, ResourceNotFound


class ConversationBusy(Exception):
    pass


class MessageConflict(Exception):
    pass


def submit_message(
    repository: Repository,
    conversation_id: UUID,
    client_message_id: UUID,
    content: str,
    *,
    task_id: UUID | None = None,
    page_context: dict | None = None,
    job_kind: str = "conversation",
    session_id: UUID | None = None,
    session_generation: UUID | None = None,
) -> Run:
    repository.get(Conversation, conversation_id, lock=True)
    session = repository.session
    existing = session.exec(
        repository.query(Message).where(
            Message.conversation_id == conversation_id,
            Message.client_message_id == client_message_id,
        )
    ).first()
    if existing:
        if existing.content != content or existing.task_id != task_id:
            raise MessageConflict
        return session.exec(repository.query(Run).where(Run.message_id == existing.id)).one()
    if session.exec(
        repository.query(Run).where(
            Run.conversation_id == conversation_id,
            Run.status.in_(["queued", "running"]),
        )
    ).first():
        raise ConversationBusy
    if task_id:
        task = repository.get(Task, task_id)
        if task.conversation_id != conversation_id:
            raise ResourceNotFound
    message = Message(
        **repository.principal.fields(),
        conversation_id=conversation_id,
        client_message_id=client_message_id,
        content=content,
        role="user",
        task_id=task_id,
        page_context=page_context or {},
        provenance={"source": "user"},
    )
    session.add(message)
    session.flush()
    run = Run(
        **repository.principal.fields(),
        conversation_id=conversation_id,
        message_id=message.id,
        session_id=session_id,
        session_generation=session_generation,
    )
    session.add(run)
    session.flush()
    session.add(Job(**repository.principal.fields(), run_id=run.id, kind=job_kind))
    append_event(
        repository,
        conversation_id,
        "run.queued",
        {"message_id": str(message.id)},
        run_id=run.id,
        task_id=task_id,
    )
    session.flush()
    return run
