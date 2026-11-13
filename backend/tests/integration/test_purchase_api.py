import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import SAMPLES, configuration, permissions
from integration.test_auth import application, login
from integration.test_conversation import replace_config
from integration.test_orchestration import Model
from sqlmodel import Session
from test_drafts import data
from test_reads import service

from app.erp.client import ERPClient
from app.errors import APIError
from app.models import Confirmation, DraftRevision, PurchaseDraft
from app.orchestration.checkpoints import setup
from app.orchestration.handler import handle
from app.purchases.drafts import check_confirmation, document, prepare, prepare_confirmation, save
from app.queue import claim, complete
from app.repository import Principal, Repository, ResourceNotFound


def fixture(engine):
    owner = Principal("jsh-local", "9001", "9002")
    caps = permissions("fixture-buyer")
    values = data()
    resolved = asyncio.run(prepare(values, service(), configuration()))
    with Session(engine) as db, db.begin():
        repo = Repository(db, owner)
        conversation = repo.conversation()
        draft = save(repo, conversation.id, uuid4(), values, resolved, caps)
        return owner, caps, conversation.id, draft.id, resolved


def test_revision_edit_conflict_replay_and_challenge_content_binding(pg_engine):
    owner, caps, conversation, draft_id, resolved = fixture(pg_engine)
    command = uuid4()
    with Session(pg_engine) as db, db.begin():
        repo = Repository(db, owner)
        shown = prepare_confirmation(repo, draft_id, 1, caps)
        row = db.exec(repo.query(Confirmation)).one()
        assert shown["challenge"] not in row.challenge_hash
        assert (
            check_confirmation(repo, repo.get(PurchaseDraft, draft_id), shown["challenge"], 1)
            == row
        )
        changed = data(remark="改了备注")
        next_snapshot = {**resolved, "remark": "改了备注"}
        draft = save(
            repo,
            conversation,
            command,
            changed,
            next_snapshot,
            caps,
            draft_id=draft_id,
            expected_version=1,
        )
        assert draft.version == 2
        assert (
            save(
                repo,
                conversation,
                command,
                changed,
                next_snapshot,
                caps,
                draft_id=draft_id,
                expected_version=1,
            ).version
            == 2
        )
        with pytest.raises(APIError) as error:
            check_confirmation(repo, draft, shown["challenge"], 1)
        assert error.value.code == "confirmation_invalid"
        with pytest.raises(APIError) as error:
            save(
                repo,
                conversation,
                uuid4(),
                data(),
                resolved,
                caps,
                draft_id=draft_id,
                expected_version=1,
            )
        assert error.value.code == "version_conflict"
        revisions = db.exec(repo.query(DraftRevision).order_by(DraftRevision.version)).all()
        assert len(revisions) == 2 and revisions[0].snapshot["remark"] == ""
        assert document(draft, permissions("fixture-masked"))["snapshot"] == {}
    with Session(pg_engine) as db:
        with pytest.raises(ResourceNotFound):
            Repository(db, Principal("jsh-local", "9101", "9101")).get(PurchaseDraft, draft_id)


def test_concurrent_edits_only_one_expected_version_succeeds(pg_engine):
    owner, caps, conversation, draft_id, resolved = fixture(pg_engine)

    def edit(remark):
        with Session(pg_engine) as db, db.begin():
            try:
                save(
                    Repository(db, owner),
                    conversation,
                    uuid4(),
                    data(remark=remark),
                    {**resolved, "remark": remark},
                    caps,
                    draft_id=draft_id,
                    expected_version=1,
                )
                return "saved"
            except APIError as error:
                return error.code

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(edit, ["a", "b"])) == ["saved", "version_conflict"]


def draft_app(engine):
    app = application(engine)
    app.state.settings = replace_config(app.state.settings)
    base = app.state.erp_client_factory

    def factory(settings, token):
        original = base(settings, token)

        def handler(request):
            rows = SAMPLES["users"][token.get_secret_value()]
            mapping = {
                "/material/findBySelect": "materials",
                "/material/findByIdWithBarCode": "material_extend",
                "/unit/list": "units",
                "/supplier/findBySelect_sup": "supplier",
            }
            key = next(
                (value for path, value in mapping.items() if request.url.path.endswith(path)), None
            )
            return (
                httpx.Response(rows[key]["http_status"], json=rows[key]["body"])
                if key
                else original.transport.handle_request(request)
            )

        return ERPClient(settings, token, transport=httpx.MockTransport(handler))

    app.state.erp_client_factory = factory
    return app


def test_draft_api_full_snapshot_expiry_cancel_and_old_card_cannot_edit(pg_engine):
    app = draft_app(pg_engine)
    client = TestClient(app)
    _, headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=headers).json()["id"]
    command = {"command_id": str(uuid4()), "input": data().model_dump()}
    created = client.post(
        f"/assistant/api/v1/conversations/{conversation}/drafts", json=command, headers=headers
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    path = f"/assistant/api/v1/drafts/{draft['id']}"
    assert (
        client.post(
            f"/assistant/api/v1/conversations/{conversation}/drafts", json=command, headers=headers
        ).json()["id"]
        == draft["id"]
    )
    assert draft["snapshot"]["total"] == "75.31"
    confirmed = client.post(path + "/confirmation", json={"expected_version": 1}, headers=headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["snapshot"] == draft["snapshot"]
    with Session(pg_engine) as db, db.begin():
        row = db.exec(
            Repository(db, Principal("jsh-local", "9001", "9002")).query(Confirmation)
        ).one()
        row.expires_at -= timedelta(minutes=10)
        db.add(row)
        with pytest.raises(APIError):
            check_confirmation(
                Repository(db, Principal("jsh-local", "9001", "9002")),
                db.get(PurchaseDraft, UUID(draft["id"])),
                confirmed.json()["challenge"],
                1,
            )
    assert (
        client.post(
            f"/assistant/api/v1/tasks/{draft['task_id']}/cancel",
            json={"expected_version": 1},
            headers=headers,
        ).json()["status"]
        == "cancelled"
    )
    assert (
        client.patch(
            path,
            json={"command_id": str(uuid4()), "expected_version": 1, "patch": {"remark": "旧卡片"}},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/confirmation", json={"expected_version": 1}, headers=headers
        ).status_code
        == 422
    )
    _, other = login(client, "fixture-masked")
    assert client.get(path, headers=other).status_code == 404


def test_agent_and_form_share_same_versioned_draft_service_without_erp_write(pg_engine):
    app = draft_app(pg_engine)
    client = TestClient(app)
    _, headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=headers).json()["id"]
    response = client.post(
        f"/assistant/api/v1/conversations/{conversation}/messages",
        json={"client_message_id": str(uuid4()), "content": "准备2.5箱可乐"},
        headers=headers,
    )
    assert response.status_code == 202
    setup(pg_engine)
    lease = claim(pg_engine)
    model = Model(
        [{"action": "draft", "arguments": {"expected_version": 0, "input": data().model_dump()}}]
    )
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
    assert (
        handle(
            pg_engine,
            app.state.settings,
            lease,
            model=Model([]),
            client_factory=app.state.erp_client_factory,
        )
        is None
    )
    complete(pg_engine, lease)
    snapshot = client.get(f"/assistant/api/v1/conversations/{conversation}", headers=headers).json()
    card = snapshot["messages"][-1]["cards"][-1]
    assert card["type"] == "purchase_draft"
    path = f"/assistant/api/v1/drafts/{card['draft_id']}"
    edited = client.patch(
        path,
        json={"command_id": str(uuid4()), "expected_version": 1, "patch": {"remark": "表单更改"}},
        headers=headers,
    )
    assert edited.status_code == 200 and edited.json()["version"] == 2
    assert edited.json()["input"]["remark"] == "表单更改"


def test_followup_quantity_and_verified_display_label_create_editable_draft(pg_engine):
    app = draft_app(pg_engine)
    client = TestClient(app)
    _, headers = login(client)
    conversation = client.post("/assistant/api/v1/conversations", headers=headers).json()["id"]
    messages_path = f"/assistant/api/v1/conversations/{conversation}/messages"
    setup(pg_engine)

    def send(content, model):
        accepted = client.post(
            messages_path,
            json={
                "client_message_id": str(uuid4()),
                "content": content,
            },
            headers=headers,
        )
        assert accepted.status_code == 202, accepted.text
        lease = claim(pg_engine)
        error = handle(
            pg_engine,
            app.state.settings,
            lease,
            model=model,
            client_factory=app.state.erp_client_factory,
        )
        complete(pg_engine, lease, error_code=error)
        assert error is None

    send("帮我采购一些可乐吧", Model([{"action": "reply", "text": "需要多少？"}]))

    class FollowupModel(Model):
        def decide(self, messages, **kwargs):
            assert any(
                row["role"] == "user" and row["content"] == "帮我采购一些可乐吧" for row in messages
            )
            assert any(row["role"] == "user" and row["content"] == "5箱" for row in messages)
            return super().decide(messages, **kwargs)

    send(
        "5箱",
        FollowupModel(
            [
                {"action": "materials", "arguments": {"query": "可乐"}},
                {"action": "material", "arguments": {"extend_id": 20011}},
                {
                    "action": "draft",
                    "arguments": {
                        "expected_version": 0,
                        "input": {
                            "lines": [{"extend_id": 20011, "quantity": "5", "unit": "箱[12瓶]"}]
                        },
                    },
                },
            ]
        ),
    )
    snapshot = client.get(f"/assistant/api/v1/conversations/{conversation}", headers=headers).json()
    cards = snapshot["messages"][-1]["cards"]
    assert not any(card["type"] == "task_status" for card in cards)
    card = next(card for card in cards if card["type"] == "purchase_draft")
    path = f"/assistant/api/v1/drafts/{card['draft_id']}"
    draft = client.get(path, headers=headers).json()
    line = draft["snapshot"]["lines"][0]
    assert line["unit"] == "箱" and line["quantity"] == "5"
    assert float(line["basic_quantity"]) == 60
    assert draft["status"] == "editing" and not draft["can_prepare_confirmation"]
    assert draft["snapshot"]["missing_fields"] == ["供应商", "订单日期", "明细1单价"]
    # 原始输入保留用于相同命令重放；后续编辑仍得到规范单位快照。
    command = {"command_id": str(uuid4()), "expected_version": 1, "patch": {"remark": "表单补充"}}
    for _ in range(2):
        edited = client.patch(path, json=command, headers=headers)
        assert edited.status_code == 200, edited.text
        assert edited.json()["version"] == 2
        assert edited.json()["snapshot"]["lines"][0]["unit"] == "箱"
    with Session(pg_engine) as db:
        repo = Repository(db, Principal("jsh-local", "9001", "9002"))
        assert len(db.exec(repo.query(PurchaseDraft)).all()) == 1
        assert len(db.exec(repo.query(Confirmation)).all()) == 0
