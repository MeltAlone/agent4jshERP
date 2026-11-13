"""验证本地隔离 ERP → 助手会话交换；凭据仅在内存中使用。"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import load_settings


async def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "integrations/jsh-erp/verification/capture_reads.py"
    spec = importlib.util.spec_from_file_location("fixture_capture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    credentials = json.loads((source.parent / ".runtime/credentials.json").read_text())
    settings = load_settings()
    if settings.erp_base_url != "http://127.0.0.1:19999/jshERP-boot":
        raise RuntimeError("仅支持本地隔离 ERP 夹具")
    origin = settings.public_origin
    if origin != "http://127.0.0.1:18081":
        raise RuntimeError("仅支持本地助手代理")
    identities = {"fixture-buyer": ("9001", "9002"), "fixture-readonly": ("9001", "9003"),
                  "fixture-masked": ("9001", "9004"), "fixture-other": ("9101", "9101"),
                  "admin": ("0", "9999")}
    async with httpx.AsyncClient(base_url=origin, timeout=15, trust_env=False) as client:
        previous = None
        for name, (tenant, user) in identities.items():
            erp = fixture.FixtureClient(settings.erp_base_url)
            erp.login(name, credentials[name])
            response = await client.post("/assistant/api/v1/session/exchange", json={},
                headers={"Origin": origin, "X-Access-Token": erp.token})
            assert response.status_code == 200
            assert erp.token not in response.text
            cookie = response.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=lax" in cookie and "path=/assistant/" in cookie
            state = await client.get("/assistant/api/v1/session")
            assert state.status_code == 200 and erp.token not in state.text
            state = state.json()
            assert state["principal"]["tenant_id"] == tenant
            assert state["principal"]["erp_user_id"] == user
            headers = {"X-Assistant-Generation":state["generation"], "Origin":origin,
                       "X-CSRF-Token":state["csrf_token"]}
            if previous:
                rejected = await client.get("/assistant/api/v1/tasks", headers=previous)
                assert rejected.status_code == 409 and rejected.json()["code"] == "session_changed"
            valid = await client.get("/assistant/api/v1/tasks", headers=headers)
            assert valid.status_code == 200
            if name in {"fixture-readonly", "fixture-masked", "admin"}:
                assert not state["capabilities"]["purchase_create"]
            if name == "fixture-masked":
                assert not state["capabilities"]["purchase_prices"]
            if name == "admin":
                assert not state["capabilities"]["supported"]
            previous = headers
            print(name + "：身份、能力与旧代次拒绝通过")
        assert (await client.delete("/assistant/api/v1/session", headers=previous)).status_code == 204
        assert (await client.get("/assistant/api/v1/session")).status_code == 401
        expired = await client.post("/assistant/api/v1/session/exchange", json={},
            headers={"Origin":origin,"X-Access-Token":"fixture-expired-token"})
        assert expired.status_code == 401 and expired.json()["code"] == "auth_required"
    print("真实 ERP 身份交换、受限用户、切换、登出及失效路径通过；未创建业务单据。")


if __name__ == "__main__":
    asyncio.run(main())
