from datetime import timedelta
from uuid import uuid4

import pytest
from helpers import configuration
from sqlmodel import Session

from app.events import ResyncRequired, append_event, read_events
from app.maintenance import cleanup
from app.models import Audit, Message, now
from app.repository import Principal, Repository


def test_cleanup_preserves_active_tasks_and_expires_closed_history(pg_engine):
    instant = now()
    owner = Principal("retention", uuid4().hex, "synthetic")
    with Session(pg_engine) as db, db.begin():
        repo = Repository(db, owner)
        ids = []
        for status in ["needs_input", "completed"]:
            conversation = repo.conversation()
            conversation.created_at = instant - timedelta(days=40)
            db.add(conversation)
            task = repo.task(conversation.id, "context", {"private": "synthetic"})
            task.status = status
            task.created_at = conversation.created_at
            db.add(task)
            db.add(
                Audit(
                    **owner.fields(),
                    task_id=task.id,
                    action="retention-test",
                    result="saved",
                    created_at=instant - timedelta(days=200),
                )
            )
            db.add(
                Message(
                    **owner.fields(),
                    conversation_id=conversation.id,
                    client_message_id=uuid4(),
                    role="user",
                    content="private-synthetic",
                    created_at=conversation.created_at,
                )
            )
            event = append_event(
                repo,
                conversation.id,
                "task.created",
                {},
                task_id=task.id,
                provenance={"source": "state"},
            )
            event.created_at = conversation.created_at
            db.add(event)
            ids.append(conversation.id)
    result = cleanup(pg_engine, configuration(), instant=instant)
    assert result["messages"] == 1 and result["events"] == 1
    assert result["audits"] == 1
    with Session(pg_engine) as db:
        repo = Repository(db, owner)
        assert len(read_events(repo, ids[0], 0)) == 1
        with pytest.raises(ResyncRequired):
            read_events(repo, ids[1], 0)
        messages = db.exec(repo.query(Message)).all()
        assert sum(message.content == "private-synthetic" for message in messages) == 1
    assert cleanup(pg_engine, configuration(), instant=instant)["messages"] == 0
