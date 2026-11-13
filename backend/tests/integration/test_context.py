import copy
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from helpers import SAMPLES
from integration.test_auth import application, login
from sqlmodel import Session, select

from app.erp.client import Endpoint
from app.models import Task, now

ORDER = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "docs/integrations/samples/talk2stock-e72aae4-v1/purchase-order.json"
    ).read_text()
)


def context(record_id=3001):
    return {
        "page_type": "material_list",
        "revision": 1,
        "record_type": "material",
        "record_ids": [record_id],
        "captured_at": now().isoformat(),
        "status": "available",
    }


def app_with_records(pg_engine):
    app = application(pg_engine)
    original_factory = app.state.erp_client_factory
    calls = []

    def factory(settings, token):
        client = original_factory(settings, token)
        original_get = client.get

        async def get(endpoint, params=None):
            if endpoint not in {Endpoint.MATERIAL_BASE, Endpoint.ORDERS, Endpoint.ORDER}:
                return await original_get(endpoint, params)
            calls.append(endpoint)
            if endpoint == Endpoint.MATERIAL_BASE:
                if params["id"] == 9999:
                    return []
                rows = copy.deepcopy(
                    SAMPLES["users"]["fixture-buyer"]["material_base"]["body"]["data"]
                )
                rows[0]["id"] = params["id"]
                if params["id"] == 3002:
                    rows[0]["name"] = "虚构矿泉水"
                return rows
            if endpoint == Endpoint.ORDERS:
                return copy.deepcopy(
                    SAMPLES["users"][token.get_secret_value()]["orders"]["body"]["data"]
                )
            return copy.deepcopy(ORDER["head"]["body"]["data"])

        client.get = get
        return client

    app.state.erp_client_factory = factory
    return app, calls


def test_page_selection_does_not_replace_saved_tasks_and_explicit_adopt_is_versioned(pg_engine):
    app, _ = app_with_records(pg_engine)
    client = TestClient(app)
    _, headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=headers).json()["id"]
    url = f"/assistant/api/v1/conversations/{conversation}/context-tasks"
    command = {"command_id": str(uuid4()), "context": context()}
    first = client.post(url, json=command, headers=headers)
    assert first.status_code == 201, first.text
    first = first.json()
    assert first["context"]["records"][0]["name"] == "虚构可乐"
    assert client.post(url, json=command, headers=headers).json()["id"] == first["id"]
    second = client.post(
        url, json={"command_id": str(uuid4()), "context": context(3002)}, headers=headers
    ).json()
    assert second["id"] != first["id"]
    get_url = "/assistant/api/v1/tasks/" + first["id"]
    assert (
        client.get(get_url, headers=headers).json()["context"]["records"][0]["name"] == "虚构可乐"
    )
    command = {"command_id": str(uuid4()), "expected_version": 1, "context": context(3002)}
    updated = client.post(get_url + "/context", json=command, headers=headers).json()
    assert (
        updated["resource_version"] == 2
        and updated["context"]["records"][0]["name"] == "虚构矿泉水"
    )
    assert (
        client.post(get_url + "/context", json=command, headers=headers).json()["resource_version"]
        == 2
    )
    command["command_id"] = str(uuid4())
    assert client.post(get_url + "/context", json=command, headers=headers).status_code == 409
    with Session(pg_engine) as database:
        assert len(database.exec(select(Task)).all()) == 2


def test_switch_restores_only_owners_tasks_and_rejects_forged_record(pg_engine):
    app, _ = app_with_records(pg_engine)
    client = TestClient(app)
    _, old_headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=old_headers).json()["id"]
    url = f"/assistant/api/v1/conversations/{conversation}/context-tasks"
    task = client.post(
        url, json={"command_id": str(uuid4()), "context": context()}, headers=old_headers
    ).json()
    _, other_headers = login(client, "fixture-other")
    assert client.get("/assistant/api/v1/tasks", headers=old_headers).status_code == 409
    assert client.get("/assistant/api/v1/tasks", headers=other_headers).json() == []
    assert (
        client.get("/assistant/api/v1/tasks/" + task["id"], headers=other_headers).status_code
        == 404
    )
    other_conversation = client.post(
        "/assistant/api/v1/conversations", headers=other_headers
    ).json()["id"]
    result = client.post(
        f"/assistant/api/v1/conversations/{other_conversation}/context-tasks",
        json={"command_id": str(uuid4()), "context": context()},
        headers=other_headers,
    )
    assert result.status_code == 404
    _, restored_headers = login(client)
    assert (
        client.get("/assistant/api/v1/tasks", headers=restored_headers).json()[0]["id"]
        == task["id"]
    )


def test_missing_stale_and_inconsistent_context_is_rejected(pg_engine):
    app, _ = app_with_records(pg_engine)
    client = TestClient(app)
    _, headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=headers).json()["id"]
    url = f"/assistant/api/v1/conversations/{conversation}/context-tasks"
    bad = [
        context(9999),
        {**context(), "captured_at": (now() - timedelta(minutes=6)).isoformat()},
        {**context(), "record_type": "purchase_order"},
        {**context(), "record_ids": [True]},
        {**context(), "tenant_id": "9001"},
    ]
    for item in bad:
        assert client.post(
            url, json={"command_id": str(uuid4()), "context": item}, headers=headers
        ).status_code in {404, 422}


def test_open_order_requires_authorized_list_before_detail(pg_engine):
    app, calls = app_with_records(pg_engine)
    client = TestClient(app)
    _, headers = login(client, "fixture-readonly")
    url = "/assistant/api/v1/host/purchase-orders/277?number=CGDD00000001002"
    assert client.get(url, headers=headers).status_code == 404
    assert Endpoint.ORDER not in calls
    _, headers = login(client)
    assert client.get(url, headers=headers).json() == {"id": 277, "number": "CGDD00000001002"}
    assert client.get(url.replace("/277?", "/278?"), headers=headers).status_code == 422
    assert (
        client.get(
            url.replace("CGDD00000001002", "javascript:alert(1)"), headers=headers
        ).status_code
        == 422
    )
