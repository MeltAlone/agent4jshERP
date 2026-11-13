from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_status_do_not_advertise_business_capabilities():
    # Startup connectivity is verified separately against real dependencies.
    client = TestClient(create_app())
    assert client.get("/assistant/api/v1/health/live").json() == {"status": "ok"}
    assert client.get("/assistant/api/v1/status").json() == {
        "service": "agent4jshERP",
        "version": "0.1.0",
        "stage": "purchase_assistant",
        "agent_available": False,
        "purchase_writes_enabled": False,
    }
