import asyncio

import httpx
import pytest
from helpers import configuration
from pydantic import ValidationError

from app.config import ConfigurationError, load_settings
from app.database import check_database
from app.startup import check_erp


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_key", "synthetic-secret"),
        ("database_url", "sqlite://"),
        ("erp_base_url", "http://user:synthetic-secret@localhost/api"),
        ("erp_base_url", "http://localhost/api?token=synthetic-secret"),
        ("public_origin", "http://localhost/path"),
    ],
)
def test_reject_invalid_configuration(monkeypatch, field, value):
    monkeypatch.setenv("A4J_" + field.upper(), str(value))
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "synthetic-secret" not in str(error.value)
    assert field in str(error.value)


def test_production_requires_https():
    with pytest.raises(ValidationError):
        configuration(environment="production", public_origin="http://localhost")


def test_db_errors_never_leak_driver_messages():
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("password=synthetic-secret")

    with pytest.raises(RuntimeError) as error:
        check_database(BrokenEngine())
    assert "synthetic-secret" not in str(error.value)


def test_erp_wrong_root_and_network_failure_are_safe(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            return httpx.Response(404, text="synthetic-secret")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(RuntimeError) as error:
        asyncio.run(check_erp(configuration()))
    assert "synthetic-secret" not in str(error.value)


def test_settings_loads_generated_false_and_redacts_dependency_failures(monkeypatch):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "backend")
    cases = {
        "A4J_SESSION_KEY": "synthetic-secret",
        "A4J_DATABASE_URL": "postgresql+psycopg://wrong:synthetic-secret@127.0.0.1:1/missing",
        "A4J_ERP_BASE_URL": "http://127.0.0.1:1/jshERP-boot",
    }
    # Only run real dependency probes when the documented local environment exists.
    if not (root / ".env").exists():
        pytest.skip("local generated configuration absent")
    for field, value in cases.items():
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.main import app; from fastapi.testclient import TestClient; "
                "client=TestClient(app); client.__enter__()",
            ],
            cwd=root,
            env={**environment, field: value},
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode != 0
        assert "synthetic-secret" not in result.stdout + result.stderr
        assert {
            "A4J_SESSION_KEY": "session_key",
            "A4J_DATABASE_URL": "数据库连接失败",
            "A4J_ERP_BASE_URL": "ERP 地址不可用",
        }[field] in result.stderr
