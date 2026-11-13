from uuid import uuid4

from helpers import permissions
from sqlmodel import Session

from app.events import append_event
from app.history import model_history, provenance
from app.models import Message
from app.repository import Principal, Repository
from app.snapshots import snapshot
from app.streaming import load_batch


def test_revoked_price_hides_messages_summary_cards_events_and_model_context(pg_engine):
    buyer, masked = permissions("fixture-buyer"), permissions("fixture-masked")
    owner = Principal("history", uuid4().hex, "synthetic")
    tags = provenance(buyer, capabilities=("material_read", "purchase_prices"))
    with Session(pg_engine) as session, session.begin():
        repository = Repository(session, owner)
        conversation = repository.conversation()
        identity = conversation.id
        task = repository.task(
            identity, "purchase", {"price": "SYNTHETIC-PRICE-30.125"}, provenance=tags
        )
        for role, content, source in [
            ("user", "想采购可乐", {"source": "user"}),
            ("assistant", "SYNTHETIC-PRICE-30.125", tags),
            ("assistant", "SYNTHETIC-SUMMARY-30.125", tags),
        ]:
            session.add(
                Message(
                    **owner.fields(),
                    conversation_id=identity,
                    task_id=task.id,
                    client_message_id=uuid4(),
                    role=role,
                    content=content,
                    provenance=source,
                )
            )
        append_event(
            repository,
            identity,
            "card.updated",
            {"price": "SYNTHETIC-CARD-30.125"},
            task_id=task.id,
            provenance=tags,
        )
    with Session(pg_engine) as session:
        repository = Repository(session, owner)
        import json

        assert (
            snapshot(repository, identity, buyer)["tasks"][0]["context"]["price"]
            == "SYNTHETIC-PRICE-30.125"
        )
        assert "SYNTHETIC-PRICE" in json.dumps(snapshot(repository, identity, buyer))
        state = json.dumps(snapshot(repository, identity, masked))
        assert "SYNTHETIC-PRICE" not in state and "SYNTHETIC-SUMMARY" not in state
        assert model_history(repository, identity, masked) == [
            {"role": "user", "content": "想采购可乐"}
        ]
    events = load_batch(pg_engine, owner, identity, 0, masked)
    assert events[0]["type"] == "data.redacted" and events[0]["payload"] == {}
    assert "SYNTHETIC-CARD" not in str(events)


def test_model_history_only_uses_selected_task_and_general_chat_is_independent(pg_engine):
    owner = Principal("history", uuid4().hex, "synthetic")
    with Session(pg_engine) as session, session.begin():
        repo = Repository(session, owner)
        conversation = repo.conversation()
        first = repo.task(conversation.id, "context", {})
        second = repo.task(conversation.id, "context", {})
        for task_id, text in [
            (first.id, "可乐数量三箱"),
            (second.id, "矿泉水五箱"),
            (None, "你好"),
        ]:
            session.add(
                Message(
                    **owner.fields(),
                    conversation_id=conversation.id,
                    task_id=task_id,
                    client_message_id=uuid4(),
                    role="user",
                    content=text,
                    provenance={"source": "user"},
                )
            )
        session.flush()
        caps = permissions("fixture-buyer")
        assert model_history(repo, conversation.id, caps, task_id=first.id) == [
            {"role": "user", "content": "可乐数量三箱"}
        ]
        assert model_history(repo, conversation.id, caps, task_id=second.id) == [
            {"role": "user", "content": "矿泉水五箱"}
        ]
        assert model_history(repo, conversation.id, caps, task_id=None) == [
            {"role": "user", "content": "你好"}
        ]
