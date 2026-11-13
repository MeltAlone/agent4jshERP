"""Check the bundled synthetic ERP through the same-origin entry; never create orders."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import load_settings
from app.erp.client import Endpoint, ERPClient, ERPError
from app.erp.permissions import security_context
from app.erp.reads import MaterialID, ReadTools, Search, StockQuery

BASE = "http://127.0.0.1:18081"


async def main():
    settings = load_settings().model_copy(
        update={
            "erp_base_url": BASE + "/jshERP-boot",
            "erp_contract": "jsh-38ad91e-v1",
            "connection_id": "jsh-bundled-38ad91e",
        }
    )
    credentials = json.loads((ROOT / ".runtime/erp/credentials.json").read_text())
    expected = {
        "fixture-owner": ("9001", "9001"),
        "fixture-buyer": ("9001", "9002"),
        "fixture-readonly": ("9001", "9003"),
        "fixture-masked": ("9001", "9004"),
        "fixture-other": ("9101", "9101"),
        "admin": ("0", "9999"),
    }
    async with httpx.AsyncClient(timeout=10, trust_env=False) as http:
        for path in ("/", "/assistant/", "/assistant/api/v1/health/ready"):
            response = await http.get(BASE + path)
            assert response.status_code == 200, path
        status = (await http.get(BASE + "/assistant/api/v1/status")).json()
        assert status["purchase_writes_enabled"] is False
        for name, password in credentials.items():
            response = await http.post(
                settings.erp_base_url + "/user/login",
                json={
                    "loginName": name,
                    "password": hashlib.md5(password.encode()).hexdigest(),
                },
            )
            body = response.json()["data"]
            assert body.get("msgTip") == "user can login", name
            client = ERPClient(settings, SecretStr(body["token"]))
            principal, _, permissions = await security_context(client)
            assert (principal.tenant_id, principal.erp_user_id) == expected[name], name
            if name == "admin":
                assert not permissions.supported
                continue
            assert permissions.supported
            assert permissions.purchase_create == (
                name in {"fixture-buyer", "fixture-owner", "fixture-other"}
            )
            if name == "fixture-readonly":
                assert permissions.role_type == "个人数据"
            if name in {"fixture-buyer", "fixture-masked", "fixture-other"}:
                other = name == "fixture-other"
                tools = ReadTools(client, permissions, principal)
                candidates = (await tools.materials(Search(query="虚构可乐")))["data"]
                assert len(candidates) == 4
                material = (
                    await tools.material(
                        MaterialID(extend_id=20111 if other else 20011)
                    )
                )["data"]
                assert material["material_id"] == (3101 if other else 3001)
                assert material["configured_units"]["箱"] == "12.0"
                assert ("purchase_price" in material) == (name != "fixture-masked")
                stock = (
                    await tools.stock(
                        StockQuery(
                            extend_id=20111 if other else 20011,
                            depot_id=6101 if other else 6001,
                        )
                    )
                )["data"]
                assert stock["quantity"] == ("2.0" if other else "10.0")
                try:
                    await tools.stock(StockQuery(extend_id=20011, depot_id=6002))
                except ERPError as error:
                    assert error.code == "permission_denied"
                else:
                    raise AssertionError("Unauthorized depot accepted")
                suppliers = (
                    await tools.suppliers(
                        Search(query="隔离租户供应商" if other else "虚构饮品供应商")
                    )
                )["data"]
                assert suppliers[0]["supplier_id"] == (4101 if other else 4001)
            print(name + ": identity, permissions and applicable read checks passed")
        invalid = ERPClient(settings, SecretStr("fixture-expired-token"))
        try:
            await invalid.get(Endpoint.SESSION)
        except ERPError as error:
            assert error.code == "auth_required"
        else:
            raise AssertionError("Invalid token accepted")
    print(
        "Bundled stack verified; six synthetic logins, read checks and invalid-session rejection; no order writes"
    )


if __name__ == "__main__":
    asyncio.run(main())
