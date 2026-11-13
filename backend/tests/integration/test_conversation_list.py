from datetime import timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from integration.test_auth import application, login
from sqlmodel import Session

from app.models import Conversation, Message, now


def test_history_titles_use_only_owner_input_and_keep_custom_titles(pg_engine):
    client = TestClient(application(pg_engine))
    state, headers = login(client)
    with Session(pg_engine) as db, db.begin():
        principal = state["principal"]
        empty = Conversation(**principal)
        default = Conversation(**principal, created_at=now() - timedelta(days=1))
        custom = Conversation(
            **principal, title="季度采购规划", created_at=now() - timedelta(days=2)
        )
        db.add_all([empty, default, custom])
        db.flush()
        for row in (default, custom):
            db.add(
                Message(
                    **principal,
                    conversation_id=row.id,
                    client_message_id=uuid4(),
                    role="assistant",
                    content="不可用于标题的受限 ERP 回复",
                    provenance={"source": "erp"},
                    created_at=now() - timedelta(minutes=1),
                )
            )
            db.add(
                Message(
                    **principal,
                    conversation_id=row.id,
                    client_message_id=uuid4(),
                    role="user",
                    content="  采购\n矿泉水  " + "五箱" * 40,
                    provenance={"source": "user"},
                )
            )
        ids = [str(row.id) for row in (empty, default, custom)]
    result = client.get("/assistant/api/v1/conversations", headers=headers)
    assert result.status_code == 200
    rows = result.json()
    assert [row["id"] for row in rows] == ids
    assert rows[0]["title"] == "新对话"
    assert rows[1]["title"] == ("采购 矿泉水 " + "五箱" * 40)[:48]
    assert rows[2]["title"] == "季度采购规划"
    assert all(row["created_at"] for row in rows)
    assert "受限 ERP" not in result.text
    # Listing is read-only and does not rewrite the stored title.
    with Session(pg_engine) as db:
        assert db.get(Conversation, UUID(ids[1])).title == "新对话"
    _, other_headers = login(client, "fixture-other")
    assert client.get("/assistant/api/v1/conversations", headers=other_headers).json() == []
    assert client.get("/assistant/api/v1/conversations", headers=headers).status_code == 409
