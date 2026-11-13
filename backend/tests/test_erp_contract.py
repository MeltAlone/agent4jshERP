import asyncio
import json

import httpx
import pytest
from helpers import SAMPLES, configuration, permissions
from pydantic import SecretStr

from app.erp.client import Endpoint, ERPClient, ERPError
from app.erp.permissions import material_projection, parse_identity, parse_permissions


@pytest.mark.parametrize(
    "status,body,code",
    [
        (500, "loginOut", "auth_required"),
        (401, "", "auth_required"),
        (403, "", "permission_denied"),
        (500, "other", "erp_unavailable"),
        (200, "not-json", "unsupported_contract"),
        (200, '{"code":200}', "unsupported_contract"),
        (200, '{"code":500}', "erp_rejected"),
    ],
)
def test_adapter_classifies_without_raw_credentials(status, body, code):
    async def exercise():
        client = ERPClient(
            configuration(),
            SecretStr("synthetic-secret"),
            transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body)),
        )
        with pytest.raises(ERPError) as error:
            await client.get(Endpoint.SESSION)
        assert error.value.code == code
        assert "synthetic-secret" not in str(error.value)

    asyncio.run(exercise())


def test_timeout_empty_supplier_and_uncontrolled_endpoint():
    async def exercise():
        def timeout(request):
            raise httpx.ReadTimeout("synthetic-secret")

        client = ERPClient(
            configuration(), SecretStr("synthetic-secret"), transport=httpx.MockTransport(timeout)
        )
        with pytest.raises(ERPError, match="erp_timeout"):
            await client.get(Endpoint.SESSION)
        client.transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        with pytest.raises(ERPError, match="ambiguous_result"):
            await client.suppliers("synthetic")
        with pytest.raises(ERPError, match="unsupported_endpoint"):
            await client.get("https://untrusted.invalid/")

    asyncio.run(exercise())


def test_permissions_use_exact_button_prices_and_authorized_depots():
    buyer, readonly, masked = (
        permissions(name) for name in ["fixture-buyer", "fixture-readonly", "fixture-masked"]
    )
    assert buyer.purchase_create and buyer.depot_ids == (6001,)
    assert readonly.purchase_read and not readonly.purchase_create
    assert masked.material_read and not masked.purchase_prices and not masked.purchase_create
    assert buyer.fingerprint != masked.fingerprint
    with pytest.raises(ERPError, match="permission_denied"):
        buyer.require("stock_read", depot_id=6002)
    assert "purchaseDecimal" not in material_projection({"id": 3001, "purchaseDecimal": 30}, masked)
    assert material_projection({"id": 3001, "purchaseDecimal": 30, "secretPrice": 10}, buyer) == {
        "id": 3001,
        "purchaseDecimal": 30,
    }


def test_unknown_admin_shape_or_permission_fields_never_grant_access():
    row = SAMPLES["users"]["fixture-buyer"]
    parts = [row[key]["body"]["data"] for key in ["buttons", "price", "role", "depots"]]
    assert not parse_permissions({}, *parts[1:]).supported
    for position in range(4):
        bad = list(parts)
        bad[position] = None
        assert not parse_permissions(*bad).supported
    modified = json.loads(json.dumps(parts))
    for button in modified[0]["userBtn"]:
        if button["url"] == "/bill/purchase_order":
            button["btnStr"] = "11"
    assert not parse_permissions(*modified).purchase_create


def test_principal_comes_from_erp_not_token_suffix_or_browser():
    data = SAMPLES["users"]["fixture-buyer"]["session"]["body"]["data"]
    principal, _ = parse_identity(data, "controlled")
    assert (principal.tenant_id, principal.erp_user_id) == ("9001", "9002")
    with pytest.raises(ERPError):
        parse_identity({"user": {"id": True}}, "controlled")
