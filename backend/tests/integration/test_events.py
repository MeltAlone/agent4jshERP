import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.dependencies import current_principal
from app.events import ResyncRequired, append_event
from app.main import create_app
from app.models import Conversation, Job
from app.repository import Principal, Repository
from app.runs import submit_message
from app.snapshots import snapshot
from app.streaming import load_batch, parse_cursor, stream_events


def fixture(engine):
    principal = Principal("events", uuid4().hex, "synthetic")
    with Session(engine) as session, session.begin():
        repository = Repository(session, principal)
        conversation = repository.conversation()
        task = repository.task(conversation.id, "purchase", {"synthetic": True})
        submit_message(repository, conversation.id, uuid4(), "synthetic", task_id=task.id)
        append_event(repository, conversation.id, "run.running", {})
        return principal, conversation.id, task.id


def test_snapshot_cursor_resume_and_replay_never_dispatch(pg_engine):
    principal, conversation_id, _ = fixture(pg_engine)
    with Session(pg_engine) as session:
        repository = Repository(session, principal)
        state = snapshot(repository, conversation_id)
        assert len(state["messages"]) == 1 and len(state["tasks"]) == 1
        assert parse_cursor(state["cursor"], conversation_id) == 2
    first = load_batch(pg_engine, principal, conversation_id, 0)
    resumed = load_batch(pg_engine, principal, conversation_id, first[0]["sequence"])
    assert [event["sequence"] for event in resumed] == [2]
    assert resumed == load_batch(pg_engine, principal, conversation_id, 1)
    with Session(pg_engine) as session:
        assert len(session.exec(Repository(session, principal).query(Job)).all()) == 1


def test_expired_wrong_and_future_cursors_require_snapshot(pg_engine):
    principal, conversation_id, _ = fixture(pg_engine)
    with Session(pg_engine) as session, session.begin():
        repository = Repository(session, principal)
        conversation = repository.get(Conversation, conversation_id, lock=True)
        conversation.event_floor = 1
        session.add(conversation)
    for cursor in [0, 3, -1]:
        with pytest.raises(ResyncRequired):
            load_batch(pg_engine, principal, conversation_id, cursor)
    for text in ["invalid", f"{uuid4()}:1", f"{conversation_id}:-1"]:
        with pytest.raises(ResyncRequired):
            parse_cursor(text, conversation_id)


def test_sse_disconnect_and_persisted_catchup(pg_engine):
    principal, conversation_id, _ = fixture(pg_engine)

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(engine=pg_engine))
        state = SimpleNamespace()

        async def is_disconnected(self):
            return False

    async def exercise():
        stream = stream_events(Request(), principal, conversation_id, 0)
        first = await anext(stream)
        assert f"id: {conversation_id}:1" in first
        await stream.aclose()  # Real consumer disconnect; next stream starts at the saved cursor.
        with Session(pg_engine) as session, session.begin():
            append_event(Repository(session, principal), conversation_id, "run.completed", {})
        recovered = stream_events(Request(), principal, conversation_id, 1)
        second, third = await anext(recovered), await anext(recovered)
        assert f"id: {conversation_id}:2" in second
        assert f"id: {conversation_id}:3" in third
        await recovered.aclose()

    asyncio.run(exercise())


def test_snapshot_api_is_owner_scoped_and_anonymous_denied(pg_engine):
    principal, conversation_id, task_id = fixture(pg_engine)
    app = create_app()
    app.state.engine = pg_engine
    client = TestClient(app)
    assert client.get(f"/assistant/api/v1/conversations/{conversation_id}").status_code == 401
    app.dependency_overrides[current_principal] = lambda: principal
    assert client.get(f"/assistant/api/v1/conversations/{conversation_id}").status_code == 200
    assert client.get(f"/assistant/api/v1/tasks/{task_id}").json()["context"]["synthetic"]
    response = client.get(
        f"/assistant/api/v1/conversations/{conversation_id}/events",
        headers={"Last-Event-ID": "wrong"},
    )
    assert response.status_code == 409 and response.json()["code"] == "resync_required"
    app.dependency_overrides[current_principal] = lambda: Principal("events", "other", "synthetic")
    assert client.get(f"/assistant/api/v1/conversations/{conversation_id}").status_code == 404
    assert client.get(f"/assistant/api/v1/tasks/{task_id}").status_code == 404
