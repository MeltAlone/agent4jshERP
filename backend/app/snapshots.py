from uuid import UUID

from app.erp.permissions import Permissions
from app.history import message_document, readable
from app.models import Conversation, Message, Run, Task
from app.repository import Repository


def snapshot(
    repository: Repository, conversation_id: UUID, permissions: Permissions | None = None
) -> dict:
    # Keep snapshot and cursor consistent with every state/event writer.
    conversation = repository.get(Conversation, conversation_id, lock=True)
    messages = repository.session.exec(
        repository.query(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    ).all()
    tasks = repository.session.exec(
        repository.query(Task)
        .where(Task.conversation_id == conversation_id)
        .order_by(Task.created_at, Task.id)
    ).all()
    runs = repository.session.exec(
        repository.query(Run)
        .where(Run.conversation_id == conversation_id)
        .order_by(Run.created_at, Run.id)
    ).all()
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "cursor": f"{conversation.id}:{conversation.next_sequence}",
        "schema_version": 1,
        "messages": [message_document(message, permissions) for message in messages],
        "tasks": [task_document(task, permissions) for task in tasks],
        "runs": [
            {
                "id": str(run.id),
                "message_id": str(run.message_id),
                "status": run.status,
                "error_code": run.error_code,
            }
            for run in runs
        ],
    }


def task_document(task: Task, permissions: Permissions | None = None) -> dict:
    return {
        "id": str(task.id),
        "conversation_id": str(task.conversation_id),
        "kind": task.kind,
        "status": task.status,
        "resource_version": task.resource_version,
        "context": task.context if readable(task.provenance, permissions) else {},
        "context_redacted": not readable(task.provenance, permissions),
        "blocked_reason": task.blocked_reason,
    }
