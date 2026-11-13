from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models import Conversation, Task
from app.repository import Principal, Repository, ResourceNotFound

OWNER = Principal("fixture", "tenant-a", "user-a")


def test_empty_schema_migrates_all_foundation_tables(pg_engine):
    assert set(inspect(pg_engine).get_table_names()) == {
        "alembic_version",
        "purchase_draft",
        "execution",
        "execution_job",
        "draft_revision",
        "confirmation",
        "assistant_session",
        "conversation",
        "message",
        "run",
        "task",
        "job",
        "event",
        "audit",
    }


@pytest.mark.parametrize(
    "other",
    [
        Principal("fixture", "tenant-a", "user-b"),
        Principal("fixture", "tenant-b", "user-a"),
        Principal("another", "tenant-a", "user-a"),
    ],
)
def test_repository_isolates_every_owner_component(db, other):
    repository = Repository(db, OWNER)
    conversation = repository.conversation()
    task = repository.task(conversation.id, "purchase", {"synthetic": True})
    outsider = Repository(db, other)
    for model, identity in [(Conversation, conversation.id), (Task, task.id)]:
        assert db.exec(outsider.query(model).where(model.id == identity)).first() is None
        with pytest.raises(ResourceNotFound):
            outsider.get(model, identity)
    with pytest.raises(ResourceNotFound):
        outsider.task(conversation.id, "purchase", {})


def test_database_rejects_forged_parent_owner(db):
    conversation = Repository(db, OWNER).conversation()
    forged = Task(
        **Principal("fixture", "tenant-b", "user-a").fields(),
        conversation_id=conversation.id,
        kind="purchase",
    )
    db.add(forged)
    with pytest.raises(IntegrityError):
        db.flush()


def test_random_identifiers_are_not_authorization(db):
    with pytest.raises(ResourceNotFound):
        Repository(db, OWNER).get(Conversation, uuid4())
