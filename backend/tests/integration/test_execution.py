import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from integration.test_auth import login
from integration.test_purchase_api import draft_app
from sqlmodel import Session, select
from test_drafts import data
from test_purchase_contract import SAMPLE

from app.erp.client import Endpoint, ERPClient
from app.models import AssistantSession, Execution, ExecutionJob, now
from app.purchases.worker import claim, handle
from app.queue import LeaseLost


class Fixture:
    def __init__(self, engine, *, lost=False):
        self.app = draft_app(engine)
        self.app.state.settings = self.app.state.settings.model_copy(
            update={"purchase_writes_enabled": True, "reconciliation_delays": (1, 1)}
        )
        self.posts, self.created, self.lost = [], False, lost
        self.read_failure = False
        self.visible = True
        self.changed_master = False
        base = self.app.state.erp_client_factory

        def factory(settings, token):
            original = base(settings, token)

            def response(request):
                path = request.url.path
                if path.endswith(Endpoint.NUMBER):
                    return httpx.Response(
                        200, json={"code": 200, "data": {"defaultNumber": SAMPLE["number"][4:]}}
                    )
                if path.endswith("/depotHead/addDepotHeadAndDetail"):
                    self.posts.append(json.loads(request.content))
                    self.created = True
                    if self.lost:
                        raise httpx.ReadTimeout("simulated response loss")
                    return httpx.Response(200, json={"code": 200})
                if any(path.endswith(e) for e in (Endpoint.ORDERS, Endpoint.ORDER, Endpoint.ITEMS)):
                    if self.read_failure:
                        return httpx.Response(503)
                    if path.endswith(Endpoint.ORDERS):
                        result = (
                            SAMPLE["candidates"]["body"]["data"]
                            if self.created and self.visible
                            else {"total": 0, "rows": []}
                        )
                    elif path.endswith(Endpoint.ORDER):
                        result = {**SAMPLE["head"]["body"]["data"], "remark": ""}
                    else:
                        result = SAMPLE["items"]["body"]["data"]
                    return httpx.Response(200, json={"code": 200, "data": copy.deepcopy(result)})
                response = original.transport.handle_request(request)
                if self.changed_master and path.endswith(Endpoint.MATERIALS):
                    changed = response.json()
                    for row in changed["rows"]:
                        row["name"] = "主数据已变更"
                    return httpx.Response(200, json=changed)
                return response

            return ERPClient(settings, token, transport=httpx.MockTransport(response))

        self.app.state.erp_client_factory = factory
        self.factory, self.engine = factory, engine
        self.client = TestClient(self.app)
        self.state, self.headers = login(self.client)
        conversation = self.client.post(
            "/assistant/api/v1/conversations", headers=self.headers
        ).json()["id"]
        response = self.client.post(
            f"/assistant/api/v1/conversations/{conversation}/drafts",
            json={"command_id": str(uuid4()), "input": data().model_dump()},
            headers=self.headers,
        )
        assert response.status_code == 201, response.text
        self.draft = response.json()
        self.path = f"/assistant/api/v1/drafts/{self.draft['id']}"
        self.review = self.client.post(
            self.path + "/confirmation", json={"expected_version": 1}, headers=self.headers
        ).json()
        self.command = {
            "request_id": str(uuid4()),
            "version": 1,
            "challenge": self.review["challenge"],
        }

    def confirm(self):
        result = self.client.post(self.path + "/confirm", json=self.command, headers=self.headers)
        assert result.status_code == 202, result.text
        return result.json()

    def work(self, hook=None):
        lease = claim(self.engine)
        assert lease is not None
        handle(
            self.engine,
            self.app.state.settings,
            lease,
            client_factory=self.factory,
            boundary_hook=hook,
        )
        return lease

    def ready(self):
        with Session(self.engine) as db, db.begin():
            job = db.exec(select(ExecutionJob)).one()
            job.available_at = now() - timedelta(seconds=1)
            job.lease_until = now() - timedelta(seconds=1)
            db.add(job)

    def execution(self):
        return self.client.get(self.path + "/execution", headers=self.headers).json()


def test_concurrent_confirmation_deduplicates_execution_and_posts_once_then_verifies(pg_engine):
    fixture = Fixture(pg_engine)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: fixture.confirm(), [1, 2]))
    assert results[0]["id"] == results[1]["id"]
    with Session(pg_engine) as db:
        assert (
            len(db.exec(select(Execution)).all()) == len(db.exec(select(ExecutionJob)).all()) == 1
        )
    fixture.work()
    assert len(fixture.posts) == 1 and fixture.execution()["status"] == "reconciling"
    fixture.ready()
    fixture.work()
    assert fixture.execution()["status"] == "succeeded"
    assert fixture.execution()["receipt"]["id"] == 277
    assert fixture.confirm()["id"] == results[0]["id"]
    assert len(fixture.posts) == 1
    assert (
        fixture.client.post(
            fixture.path + "/reopen",
            json={"command_id": str(uuid4()), "expected_version": 1},
            headers=fixture.headers,
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "boundary,posts", [("before_mark", 1), ("after_mark", 0), ("after_post", 1)]
)
def test_worker_crash_before_or_after_boundary_never_repeats_possible_send(
    pg_engine, boundary, posts
):
    fixture = Fixture(pg_engine)
    fixture.confirm()

    def crash(stage, execution):
        if stage == boundary:
            raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError):
        fixture.work(crash)
    for _ in range(3):
        fixture.ready()
        if claimable := claim(pg_engine):
            handle(pg_engine, fixture.app.state.settings, claimable, client_factory=fixture.factory)
    assert len(fixture.posts) == posts
    assert fixture.execution()["status"] == ("needs_attention" if posts == 0 else "succeeded")


def test_erp_committed_response_lost_and_write_switch_disabled_still_reconciles(pg_engine):
    fixture = Fixture(pg_engine, lost=True)
    fixture.confirm()
    fixture.work()
    fixture.app.state.settings = fixture.app.state.settings.model_copy(
        update={"purchase_writes_enabled": False}
    )
    fixture.ready()
    fixture.work()
    assert fixture.execution()["status"] == "succeeded" and len(fixture.posts) == 1


def test_cancel_wins_before_dispatch_and_old_lease_cannot_send(pg_engine):
    fixture = Fixture(pg_engine)
    fixture.confirm()
    lease = claim(pg_engine)
    response = fixture.client.post(
        f"/assistant/api/v1/tasks/{fixture.draft['task_id']}/cancel",
        json={"expected_version": 1},
        headers=fixture.headers,
    )
    assert response.status_code == 200
    with pytest.raises(LeaseLost):
        handle(pg_engine, fixture.app.state.settings, lease, client_factory=fixture.factory)
    assert fixture.execution()["status"] == "cancelled_before_send" and fixture.posts == []


def test_after_send_cancel_rejected_unknown_never_reopens_and_manual_reconciliation_is_bounded(
    pg_engine,
):
    fixture = Fixture(pg_engine)
    fixture.confirm()
    fixture.work()
    fixture.read_failure = True
    for _ in range(2):
        fixture.ready()
        fixture.work()
    execution = fixture.execution()
    assert execution["status"] == "needs_attention" and len(fixture.posts) == 1
    assert (
        fixture.client.post(
            fixture.path + "/reopen",
            json={"command_id": str(uuid4()), "expected_version": 1},
            headers=fixture.headers,
        ).status_code
        == 409
    )
    assert (
        fixture.client.post(
            f"/assistant/api/v1/tasks/{fixture.draft['task_id']}/cancel",
            json={"expected_version": 1},
            headers=fixture.headers,
        ).status_code
        == 409
    )
    fixture.read_failure = False
    assert (
        fixture.client.post(
            f"/assistant/api/v1/executions/{execution['id']}/reconcile", headers=fixture.headers
        ).status_code
        == 202
    )
    fixture.work()
    assert fixture.execution()["status"] == "succeeded" and len(fixture.posts) == 1


def test_expired_before_send_is_known_not_sent_and_reopen_requires_new_confirmation(pg_engine):
    fixture = Fixture(pg_engine)
    fixture.confirm()
    with Session(pg_engine) as db, db.begin():
        row = db.exec(select(AssistantSession)).one()
        row.revoked_at = now()
        db.add(row)
    fixture.work()
    fixture.state, fixture.headers = login(fixture.client)
    assert fixture.execution()["status"] == "rejected" and fixture.posts == []
    reopen_command = {"command_id": str(uuid4()), "expected_version": 1}
    response = fixture.client.post(
        fixture.path + "/reopen", json=reopen_command, headers=fixture.headers
    )
    assert response.status_code == 200 and response.json()["version"] == 2
    assert (
        fixture.client.post(
            fixture.path + "/reopen", json=reopen_command, headers=fixture.headers
        ).json()["version"]
        == 2
    )
    assert (
        fixture.client.post(
            fixture.path + "/reopen",
            json={**reopen_command, "expected_version": 2},
            headers=fixture.headers,
        ).status_code
        == 409
    )
    assert (
        fixture.client.post(
            fixture.path + "/confirm",
            json={**fixture.command, "version": 2},
            headers=fixture.headers,
        ).status_code
        == 409
    )


def test_cross_account_execution_hidden_and_original_account_relogin_restores_reconciliation(
    pg_engine,
):
    fixture = Fixture(pg_engine)
    execution = fixture.confirm()
    fixture.work()
    login(fixture.client, "fixture-masked")
    fixture.ready()
    fixture.work()
    _, other_headers = login(fixture.client, "fixture-masked")
    assert (
        fixture.client.get(
            f"/assistant/api/v1/executions/{execution['id']}", headers=other_headers
        ).status_code
        == 404
    )
    fixture.state, fixture.headers = login(fixture.client)
    assert fixture.execution()["status"] == "auth_required"
    assert (
        fixture.client.post(
            f"/assistant/api/v1/executions/{execution['id']}/reconcile", headers=fixture.headers
        ).status_code
        == 202
    )
    fixture.work()
    assert fixture.execution()["status"] == "succeeded" and len(fixture.posts) == 1


def test_concurrent_claim_and_expired_lease_fence_stale_worker(pg_engine):
    fixture = Fixture(pg_engine)
    fixture.confirm()
    with ThreadPoolExecutor(2) as pool:
        leases = list(pool.map(lambda _: claim(pg_engine), range(2)))
    leases = [lease for lease in leases if lease is not None]
    assert len(leases) == 1
    fixture.ready()
    recovered = claim(pg_engine)
    assert recovered.token != leases[0].token
    with pytest.raises(LeaseLost):
        handle(pg_engine, fixture.app.state.settings, leases[0], client_factory=fixture.factory)
    assert fixture.posts == []
    handle(pg_engine, fixture.app.state.settings, recovered, client_factory=fixture.factory)
    fixture.ready()
    fixture.work()
    assert fixture.execution()["status"] == "succeeded" and len(fixture.posts) == 1


def test_cancel_races_with_committed_boundary_before_network_send(pg_engine):
    from threading import Event

    fixture = Fixture(pg_engine)
    fixture.confirm()
    marked, release = Event(), Event()

    def pause(stage, execution):
        if stage == "after_mark":
            marked.set()
            assert release.wait(10), "cancel request did not finish"

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(fixture.work, pause)
        try:
            assert marked.wait(10), "worker did not reach dispatch boundary"
            response = fixture.client.post(
                f"/assistant/api/v1/tasks/{fixture.draft['task_id']}/cancel",
                json={"expected_version": 1},
                headers=fixture.headers,
            )
            assert response.status_code == 409
            assert response.json()["code"] == "already_dispatching"
            assert fixture.posts == []
        finally:
            release.set()
        future.result(timeout=10)
    fixture.ready()
    fixture.work()
    assert fixture.execution()["status"] == "succeeded" and len(fixture.posts) == 1


def test_manual_reconciliation_exhausts_round_budget_without_releasing_identity(pg_engine):
    fixture = Fixture(pg_engine)
    original = fixture.confirm()
    fixture.work()
    fixture.read_failure = True
    for round_number in range(4):
        for _ in range(2):
            fixture.ready()
            fixture.work()
        assert fixture.execution()["status"] == "needs_attention"
        response = fixture.client.post(
            f"/assistant/api/v1/executions/{original['id']}/reconcile",
            headers=fixture.headers,
        )
        assert response.status_code == (409 if round_number == 3 else 202)
    assert response.json()["code"] == "reconciliation_exhausted"
    assert fixture.confirm()["id"] == original["id"] and len(fixture.posts) == 1
    with Session(pg_engine) as db:
        assert len(db.exec(select(Execution)).all()) == 1
        assert db.exec(select(Execution)).one().erp_number == SAMPLE["number"]


@pytest.mark.parametrize("revoked_on", [1, 2])
def test_permissions_revoked_during_preflight_prevent_send(pg_engine, monkeypatch, revoked_on):
    from dataclasses import replace

    from app.purchases import worker

    fixture = Fixture(pg_engine)
    fixture.confirm()
    original, calls = worker.authorize, 0

    def authorize(*args):
        nonlocal calls
        client, permissions = original(*args)
        calls += 1
        if calls >= revoked_on:
            permissions = replace(permissions, purchase_create=False)
        return client, permissions

    monkeypatch.setattr(worker, "authorize", authorize)
    fixture.work()
    assert fixture.execution()["status"] == "rejected"
    assert fixture.execution()["error_code"] == "permission_denied" and fixture.posts == []


@pytest.mark.parametrize("invalid", ["forged", "expired", "version", "owner"])
def test_invalid_confirmation_never_enqueues_or_writes(pg_engine, invalid):
    from app.models import Confirmation

    fixture = Fixture(pg_engine)
    body = dict(fixture.command)
    expected = 409
    if invalid == "forged":
        body["challenge"] = "x" * 64
    elif invalid == "expired":
        with Session(pg_engine) as db, db.begin():
            row = db.exec(select(Confirmation)).one()
            row.expires_at = now() - timedelta(seconds=1)
            db.add(row)
    elif invalid == "version":
        body["version"] = 2
    else:
        _, fixture.headers = login(fixture.client, "fixture-masked")
        expected = 404
    response = fixture.client.post(fixture.path + "/confirm", json=body, headers=fixture.headers)
    assert response.status_code == expected
    with Session(pg_engine) as db:
        assert db.exec(select(Execution)).all() == []
        assert db.exec(select(ExecutionJob)).all() == []
    assert fixture.posts == []


def test_order_not_yet_visible_retries_reads_only(pg_engine):
    fixture = Fixture(pg_engine)
    fixture.confirm()
    fixture.work()
    fixture.visible = False
    fixture.ready()
    fixture.work()
    assert fixture.execution()["status"] == "reconciling" and len(fixture.posts) == 1
    fixture.visible = True
    fixture.ready()
    fixture.work()
    assert fixture.execution()["status"] == "succeeded" and len(fixture.posts) == 1


def test_unknown_execution_keeps_database_number_reservation(pg_engine):
    from sqlalchemy.exc import IntegrityError

    fixture = Fixture(pg_engine)
    fixture.confirm()
    fixture.work()
    fixture.read_failure = True
    for _ in range(2):
        fixture.ready()
        fixture.work()
    with Session(pg_engine) as db:
        original = db.exec(select(Execution)).one()
        assert original.status == "needs_attention"
        values = original.model_dump()
    values.update(id=uuid4(), version=2, request_id=uuid4())
    with pytest.raises(IntegrityError) as rejected:
        with Session(pg_engine) as db, db.begin():
            db.add(Execution(**values))
    assert "erp_number" in rejected.value.orig.diag.constraint_name
    assert fixture.confirm()["id"] == str(original.id) and len(fixture.posts) == 1


def test_frozen_snapshot_cannot_be_edited_or_injected_with_business_type(pg_engine):
    fixture = Fixture(pg_engine)
    fixture.confirm()
    response = fixture.client.patch(
        fixture.path,
        headers=fixture.headers,
        json={
            "command_id": str(uuid4()),
            "expected_version": 1,
            "patch": {"remark": "change after confirmation"},
        },
    )
    assert response.status_code == 409
    response = fixture.client.post(
        fixture.path + "/confirm",
        headers=fixture.headers,
        json={**fixture.command, "status": "1", "subType": "采购入库"},
    )
    assert response.status_code == 422
    with Session(pg_engine) as db:
        frozen = db.exec(select(Execution)).one()
        assert frozen.snapshot == fixture.review["snapshot"]
    fixture.work()
    info = json.loads(fixture.posts[0]["info"])
    assert info["status"] == "0" and info["subType"] == "采购订单" and info["remark"] == ""


def test_master_data_change_rejects_frozen_content_without_silently_replacing_it(pg_engine):
    fixture = Fixture(pg_engine)
    fixture.confirm()
    fixture.changed_master = True
    fixture.work()
    assert fixture.execution()["status"] == "rejected"
    assert fixture.execution()["error_code"] == "master_data_changed" and fixture.posts == []
    with Session(pg_engine) as db:
        assert db.exec(select(Execution)).one().snapshot == fixture.review["snapshot"]


def test_known_not_sent_retry_needs_new_review_and_keeps_prior_execution(pg_engine):
    fixture = Fixture(pg_engine)
    original = fixture.confirm()
    fixture.app.state.settings = fixture.app.state.settings.model_copy(
        update={"purchase_writes_enabled": False}
    )
    fixture.work()
    assert fixture.execution()["status"] == "rejected" and fixture.posts == []
    fixture.app.state.settings = fixture.app.state.settings.model_copy(
        update={"purchase_writes_enabled": True}
    )
    reopened = fixture.client.post(
        fixture.path + "/reopen",
        json={"expected_version": 1, "command_id": str(uuid4())},
        headers=fixture.headers,
    )
    assert reopened.status_code == 200 and reopened.json()["version"] == 2
    denied = fixture.client.post(
        fixture.path + "/confirm",
        json={**fixture.command, "version": 2},
        headers=fixture.headers,
    )
    assert denied.status_code == 409
    review = fixture.client.post(
        fixture.path + "/confirmation",
        json={"expected_version": 2},
        headers=fixture.headers,
    )
    assert review.status_code == 200 and review.json()["challenge"] != fixture.command["challenge"]
    accepted = fixture.client.post(
        fixture.path + "/confirm",
        json={"request_id": str(uuid4()), "version": 2, "challenge": review.json()["challenge"]},
        headers=fixture.headers,
    )
    assert accepted.status_code == 202 and accepted.json()["id"] != original["id"]
    fixture.work()
    assert len(fixture.posts) == 1 and fixture.execution()["version"] == 2
    assert fixture.confirm()["id"] == original["id"]
    with Session(pg_engine) as db:
        assert len(db.exec(select(Execution)).all()) == 2


def test_saying_ok_is_not_confirmation_even_when_complete_review_exists(pg_engine):
    from integration.test_orchestration import Model

    from app.models import Confirmation, Task
    from app.orchestration.checkpoints import setup
    from app.orchestration.handler import handle as handle_conversation
    from app.queue import claim as claim_conversation
    from app.queue import complete

    fixture = Fixture(pg_engine)
    with Session(pg_engine) as db:
        task = db.get(Task, UUID(fixture.draft["task_id"]))
        conversation_id = task.conversation_id
    sent = fixture.client.post(
        f"/assistant/api/v1/conversations/{conversation_id}/messages",
        headers=fixture.headers,
        json={
            "client_message_id": str(uuid4()),
            "task_id": fixture.draft["task_id"],
            "content": "好的",
        },
    )
    assert sent.status_code == 202
    setup(pg_engine)
    lease = claim_conversation(pg_engine)
    error = handle_conversation(
        pg_engine,
        fixture.app.state.settings,
        lease,
        model=Model([{"action": "reply", "text": "请审阅并点击确认卡片。"}]),
        client_factory=fixture.factory,
    )
    assert error is None
    complete(pg_engine, lease)
    with Session(pg_engine) as db:
        assert db.exec(select(Execution)).all() == []
        assert db.exec(select(ExecutionJob)).all() == []
        assert db.exec(select(Confirmation)).one().consumed_at is None
    assert fixture.posts == []
