import httpx
from fastapi.testclient import TestClient
from helpers import SAMPLES, configuration
from sqlmodel import Session, select

from app.auth import COOKIE
from app.erp.client import ERPClient
from app.main import create_app
from app.models import AssistantSession


def application(pg_engine):
    config = configuration(public_origin="http://testserver")
    app = create_app(config)
    app.state.engine = pg_engine
    app.state.settings = config

    def factory(settings, token):
        name = token.get_secret_value()

        def handler(request):
            if name == "expired-token":
                return httpx.Response(500, text="loginOut")
            record = SAMPLES["users"][name]
            keys = {
                "/user/getUserSession": "session",
                "/user/getUserBtnByCurrentUser": "buttons",
                "/user/getCurrentPriceLimit": "price",
                "/user/getRoleTypeByCurrentUser": "role",
                "/depot/findDepotByCurrentUser": "depots",
            }
            key = next(value for path, value in keys.items() if request.url.path.endswith(path))
            data = record[key]
            return httpx.Response(data["http_status"], json=data["body"])

        return ERPClient(settings, token, transport=httpx.MockTransport(handler))

    app.state.erp_client_factory = factory
    return app


def login(client, user="fixture-buyer"):
    response = client.post(
        "/assistant/api/v1/session/exchange",
        json={},
        headers={"Origin": "http://testserver", "X-Access-Token": user},
    )
    assert response.status_code == 200
    state = client.get("/assistant/api/v1/session").json()
    return state, {
        "Origin": "http://testserver",
        "X-Assistant-Generation": state["generation"],
        "X-CSRF-Token": state["csrf_token"],
    }


def test_exchange_uses_trusted_identity_encryption_and_scoped_cookie(pg_engine):
    app = application(pg_engine)
    client = TestClient(app)
    state, headers = login(client)
    assert state["principal"]["erp_user_id"] == "9002"
    assert state["capabilities"]["purchase_create"]
    with Session(pg_engine) as database:
        row = database.exec(select(AssistantSession)).one()
        assert "fixture-buyer" not in row.encrypted_token
        assert row.cookie_hash != client.cookies.get(COOKIE)
    assert client.get("/assistant/api/v1/conversations", headers=headers).status_code == 200
    assert client.get("/assistant/api/v1/conversations").status_code == 409
    assert client.delete("/assistant/api/v1/session", headers=headers).status_code == 204
    assert client.get("/assistant/api/v1/session").status_code == 401


def test_cross_origin_csrf_forged_identity_and_arbitrary_erp_are_rejected(pg_engine):
    client = TestClient(application(pg_engine))
    state, headers = login(client)
    assert (
        client.delete(
            "/assistant/api/v1/session", headers={**headers, "Origin": "https://attacker.invalid"}
        ).status_code
        == 403
    )
    bad = {**headers, "X-CSRF-Token": "forged"}
    assert client.delete("/assistant/api/v1/session", headers=bad).status_code == 403
    response = client.post(
        "/assistant/api/v1/session/exchange",
        json={"tenant_id": "forged", "erp_base_url": "http://attacker.invalid"},
        headers={**headers, "X-Access-Token": "fixture-buyer"},
    )
    assert response.status_code == 422
    response = client.post(
        "/assistant/api/v1/session/exchange",
        json={},
        headers={"Origin": "http://testserver", "X-Access-Token": "expired-token"},
    )
    assert response.status_code == 401
    assert "expired-token" not in response.text


def test_switch_revokes_old_cookie_and_rejects_old_generation(pg_engine):
    app = application(pg_engine)
    client = TestClient(app)
    old, headers = login(client)
    cookie = client.cookies.get(COOKIE)
    new, new_headers = login(client, "fixture-masked")
    assert old["generation"] != new["generation"]
    assert not new["capabilities"]["purchase_create"]
    assert client.get("/assistant/api/v1/conversations", headers=headers).status_code == 409
    assert client.get("/assistant/api/v1/conversations", headers=new_headers).status_code == 200
    stale = TestClient(app)
    stale.cookies.set(COOKIE, cookie, path="/assistant/")
    assert stale.get("/assistant/api/v1/session").status_code == 401


def test_expired_server_session_is_not_extended(pg_engine):
    from datetime import timedelta

    from app.models import now

    client = TestClient(application(pg_engine))
    login(client)
    with Session(pg_engine) as database, database.begin():
        row = database.exec(select(AssistantSession)).one()
        row.expires_at = now() - timedelta(seconds=1)
        database.add(row)
    assert client.get("/assistant/api/v1/session").status_code == 401


def test_production_cookie_and_idle_timeout(pg_engine):
    from datetime import timedelta

    from app.models import now

    app = application(pg_engine)
    app.state.settings = configuration(environment="production", public_origin="https://testserver")
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/assistant/api/v1/session/exchange",
        json={},
        headers={"Origin": "https://testserver", "X-Access-Token": "fixture-buyer"},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert all(
        value in cookie for value in ["secure", "httponly", "samesite=lax", "path=/assistant/"]
    )
    with Session(pg_engine) as database, database.begin():
        row = database.exec(select(AssistantSession)).one()
        row.last_seen_at = now() - timedelta(seconds=3601)
        database.add(row)
    assert client.get("/assistant/api/v1/session").status_code == 401


def test_logout_does_not_depend_on_erp_availability(pg_engine):
    app = application(pg_engine)
    client = TestClient(app)
    _, headers = login(client)

    def unavailable(*args):
        raise AssertionError("退出不得访问 ERP")

    app.state.erp_client_factory = unavailable
    assert client.delete("/assistant/api/v1/session", headers=headers).status_code == 204
    with Session(pg_engine) as database:
        assert database.exec(select(AssistantSession)).one().revoked_at is not None
    assert client.get("/assistant/api/v1/session").status_code == 401
