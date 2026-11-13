"""Guard the opt-in live acceptance runner; these tests make no real ERP calls."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from helpers import configuration
from pydantic import SecretStr

from app.erp.client import ERPClient, ERPError

spec = importlib.util.spec_from_file_location(
    "purchase_fault_harness", Path(__file__).parents[2] / "scripts/verify_purchase_faults.py"
)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def test_live_runner_requires_opt_in_before_loading_private_configuration(monkeypatch):
    monkeypatch.setattr(harness.sys, "argv", ["verify_purchase_faults.py"])
    monkeypatch.setattr(harness, "configuration", lambda: pytest.fail("must require opt-in first"))
    with pytest.raises(harness.VerificationError, match="allow-synthetic-orders"):
        harness.main()


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "production"),
        ("public_origin", "https://erp.example.invalid"),
        ("database_url", SecretStr("postgresql+psycopg://test:synthetic@remote:15432/assistant")),
        ("database_url", SecretStr("postgresql+psycopg://test:synthetic@127.0.0.1:5432/assistant")),
        (
            "database_url",
            SecretStr("postgresql+psycopg://test:synthetic@127.0.0.1:15432/production"),
        ),
        ("data", "business-data"),
        ("contract", "unverified"),
    ],
)
def test_live_runner_rejects_nonfixture_environments(tmp_path, monkeypatch, field, value):
    manifest = {"data": "synthetic-only", "contract": "jsh-38ad91e-v1"}
    settings = configuration().model_copy(
        update={
            "database_url": SecretStr(
                "postgresql+psycopg://test:synthetic@127.0.0.1:15432/assistant"
            )
        }
    )
    if field in manifest:
        manifest[field] = value
    else:
        settings = settings.model_copy(update={field: value})
    runtime = tmp_path / ".runtime/erp"
    runtime.mkdir(parents=True)
    (runtime / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    monkeypatch.setattr(harness, "load_settings", lambda: settings)
    with pytest.raises(harness.VerificationError, match="bundled local synthetic"):
        harness.configuration()


@pytest.mark.parametrize(
    "schema", ["public", "a4j_test_other", "a4j_fault_bad; DROP SCHEMA public"]
)
def test_worker_never_connects_to_normal_or_arbitrary_schema(monkeypatch, schema):
    monkeypatch.setattr(harness, "create_engine", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(harness.VerificationError, match="Invalid schema"):
        harness.database(configuration(), schema)


@pytest.mark.parametrize("lost", [False, True])
def test_transport_discards_only_write_response_and_records_no_credentials(
    tmp_path, monkeypatch, lost
):
    seen = []

    def upstream(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"code": 200, "data": {}})

    monkeypatch.setattr(
        harness.httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(upstream)
    )
    log_path = tmp_path / "attempts"
    client = ERPClient(
        configuration().model_copy(update={"purchase_writes_enabled": True}),
        SecretStr("synthetic-private-token"),
        transport=harness.RecordingTransport(log_path, lose_response=lost),
    )
    from app.erp.client import Endpoint

    assert asyncio.run(client.get(Endpoint.SESSION)) == {}
    assert not log_path.exists()
    if lost:
        with pytest.raises(ERPError, match="erp_unavailable"):
            asyncio.run(client.create_purchase({"info": "synthetic-private-body"}))
    else:
        assert (
            asyncio.run(client.create_purchase({"info": "synthetic-private-body"}))["code"] == 200
        )
    assert len(seen) == 2
    assert log_path.read_text() == "create_attempt\n"
