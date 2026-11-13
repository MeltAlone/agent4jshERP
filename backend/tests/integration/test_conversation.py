from uuid import uuid4

from fastapi.testclient import TestClient
from integration.test_auth import application, login
from integration.test_orchestration import Model
from sqlmodel import Session, select

from app.models import Message, Run
from app.orchestration.checkpoints import setup
from app.orchestration.handler import handle
from app.queue import claim, complete


def test_message_api_worker_checkpoint_publication_and_duplicate_recovery(pg_engine):
    app = application(pg_engine)
    app.state.settings = replace_config(app.state.settings)
    client = TestClient(app)
    _, headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=headers).json()["id"]
    path = f"/assistant/api/v1/conversations/{conversation}/messages"
    command = {"client_message_id": str(uuid4()), "content": "你好"}
    accepted = client.post(path, json=command, headers=headers)
    assert accepted.status_code == 202, accepted.text
    assert client.post(path, json=command, headers=headers).json()["id"] == accepted.json()["id"]
    assert (
        client.post(
            path, json={**command, "client_message_id": str(uuid4())}, headers=headers
        ).status_code
        == 409
    )
    setup(pg_engine)
    lease = claim(pg_engine)
    model = Model([{"action": "reply", "text": "你好，需要了解什么业务？"}])
    assert (
        handle(
            pg_engine,
            app.state.settings,
            lease,
            model=model,
            client_factory=app.state.erp_client_factory,
        )
        is None
    )
    # 模拟已提交回复后，完成 job 前重进 handler；不得再次请求模型或复制消息。
    assert (
        handle(
            pg_engine,
            app.state.settings,
            lease,
            model=model,
            client_factory=app.state.erp_client_factory,
        )
        is None
    )
    complete(pg_engine, lease)
    assert model.calls == 1
    snapshot = client.get(path.rsplit("/", 1)[0], headers=headers).json()
    assert [m["role"] for m in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][-1]["content"].startswith("你好")
    with Session(pg_engine) as database:
        run = database.exec(select(Run)).one()
        assert run.budget["actual_cost"] is None and run.budget["usage"]["total_tokens"] == 4
        assert len(database.exec(select(Message)).all()) == 2
    assert client.post(path, json=command, headers=headers).json()["id"] == accepted.json()["id"]


def replace_config(config):
    return config.model_copy(
        update={
            "model_base_url": "https://model.invalid/v1",
            "model_name": "fixture",
            "model_api_key": __import__("pydantic").SecretStr("fixture-key"),
        }
    )


def test_switch_before_worker_run_does_not_use_new_identity(pg_engine):
    import pytest

    from app.erp.client import ERPError

    app = application(pg_engine)
    app.state.settings = replace_config(app.state.settings)
    client = TestClient(app)
    _, headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=headers).json()["id"]
    assert (
        client.post(
            f"/assistant/api/v1/conversations/{conversation}/messages",
            json={"client_message_id": str(uuid4()), "content": "你好"},
            headers=headers,
        ).status_code
        == 202
    )
    login(client, "fixture-masked")
    lease = claim(pg_engine)
    with pytest.raises(ERPError, match="auth_required"):
        handle(
            pg_engine,
            app.state.settings,
            lease,
            model=Model([]),
            client_factory=app.state.erp_client_factory,
        )


def test_external_model_call_reservation_survives_node_crash(pg_engine):
    import pytest

    app = application(pg_engine)
    app.state.settings = replace_config(app.state.settings).model_copy(
        update={"model_max_calls": 1}
    )
    client = TestClient(app)
    _, headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=headers).json()["id"]
    response = client.post(
        f"/assistant/api/v1/conversations/{conversation}/messages",
        json={"client_message_id": str(uuid4()), "content": "你好"},
        headers=headers,
    )
    assert response.status_code == 202
    setup(pg_engine)
    lease = claim(pg_engine)

    class Interrupted:
        calls = 0

        def decide(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("simulated process loss after provider call")

    model = Interrupted()
    with pytest.raises(RuntimeError):
        handle(
            pg_engine,
            app.state.settings,
            lease,
            model=model,
            client_factory=app.state.erp_client_factory,
        )
    assert (
        handle(
            pg_engine,
            app.state.settings,
            lease,
            model=model,
            client_factory=app.state.erp_client_factory,
        )
        == "budget_exhausted"
    )
    assert model.calls == 1
    complete(pg_engine, lease, error_code="budget_exhausted")
