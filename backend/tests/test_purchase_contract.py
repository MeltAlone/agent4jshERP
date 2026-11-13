import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import configuration, permissions
from test_drafts import data
from test_reads import service

from app.erp.client import Endpoint, ERPError
from app.purchases.drafts import prepare
from app.purchases.erp import order_body, reconcile
from app.repository import Principal

SAMPLE = json.loads(
    (
        Path(__file__).parents[2]
        / "docs/integrations/samples/talk2stock-e72aae4-v1/purchase-order.json"
    ).read_text()
)


def snapshot():
    return asyncio.run(prepare(data(), service(), configuration()))


def test_fixed_write_mapping_matches_verified_erp_body_without_uncontrolled_fields():
    draft = snapshot()
    body = order_body(draft, SAMPLE["number"])
    info, rows = json.loads(body["info"]), json.loads(body["rows"])
    expected = json.loads(SAMPLE["request"]["info"])
    for field in [
        "type",
        "subType",
        "status",
        "organId",
        "changeAmount",
        "discount",
        "discountMoney",
    ]:
        assert info[field] == expected[field]
    assert info["totalPrice"] == "-75.31" and info["discountLastMoney"] == "75.31"
    assert info["operTime"] == "2026-11-12 00:00:00"
    row = rows[0]
    assert row["depotId"] == "" and row["barCode"] == "FIX900111"
    assert row["operNumber"] == "2.5" and row["unitPrice"] == "30.125"
    assert row["allPrice"] == row["taxLastMoney"] == "75.31"
    assert set(body) == {"info", "rows"} and "accountId" not in info
    with pytest.raises(ERPError):
        order_body(draft, "arbitrary-number")


def reconciliation_fixture():
    values = {
        Endpoint.ORDERS: copy.deepcopy(SAMPLE["candidates"]["body"]["data"]),
        Endpoint.ORDER: copy.deepcopy(SAMPLE["head"]["body"]["data"]),
        Endpoint.ITEMS: copy.deepcopy(SAMPLE["items"]["body"]["data"]),
    }
    values[Endpoint.ORDER]["remark"] = ""

    class Client:
        async def get(self, endpoint, params):
            return values[endpoint]

    execution = SimpleNamespace(snapshot=snapshot(), erp_number=SAMPLE["number"])
    return values, Client(), execution


def test_authorized_reconciliation_checks_every_business_field_and_summary_footer():
    _, client, execution = reconciliation_fixture()
    receipt = asyncio.run(
        reconcile(
            client, permissions("fixture-buyer"), Principal("jsh-local", "9001", "9002"), execution
        )
    )
    assert receipt == {"id": 277, "number": SAMPLE["number"], "status": "0", "verified": True}


@pytest.mark.parametrize(
    "part,field,value",
    [
        ("head", "id", 999),
        ("head", "type", "入库"),
        ("head", "subType", "采购入库"),
        ("head", "creator", 9001),
        ("head", "tenantId", 9101),
        ("head", "status", "1"),
        ("head", "organId", 4101),
        ("head", "organName", "已更名供应商"),
        ("head", "deleteFlag", "1"),
        ("head", "purchaseStatus", "1"),
        ("head", "accountId", 100),
        ("head", "accountIdList", "100"),
        ("head", "accountMoneyList", "75.31"),
        ("head", "lastDeposit", 5),
        ("head", "remark", "变更"),
        ("head", "operTimeStr", "2026-11-13 00:00:00"),
        ("head", "totalPrice", 0),
        ("head", "deposit", 5),
        ("head", "changeAmount", 1),
        ("line", "materialExtendId", 20010),
        ("line", "unit", "瓶"),
        ("line", "name", "其他商品"),
        ("line", "standard", "500ml"),
        ("line", "model", "other-model"),
        ("line", "operNumber", 3),
        ("line", "basicNumber", 2.5),
        ("line", "unitPrice", 30),
        ("line", "allPrice", 75.32),
        ("line", "taxMoney", 1),
        ("line", "depotId", 6001),
        ("line", "barCode", "OTHER"),
        ("line", "unitPrice", None),
        ("footer", "allPrice", 0),
        ("footer", "operNumber", 3),
    ],
)
def test_mismatches_never_report_business_success(part, field, value):
    values, client, execution = reconciliation_fixture()
    target = (
        values[Endpoint.ORDER]
        if part == "head"
        else values[Endpoint.ITEMS]["rows"][0 if part == "line" else -1]
    )
    target[field] = value
    with pytest.raises(ERPError, match="order_mismatch"):
        asyncio.run(
            reconcile(
                client,
                permissions("fixture-buyer"),
                Principal("jsh-local", "9001", "9002"),
                execution,
            )
        )


def test_duplicate_candidate_truncation_and_masked_price_cannot_become_success():
    values, client, execution = reconciliation_fixture()
    caps, owner = permissions("fixture-buyer"), Principal("jsh-local", "9001", "9002")
    values[Endpoint.ORDERS]["total"] = 2
    with pytest.raises(ERPError, match="ambiguous_result"):
        asyncio.run(reconcile(client, caps, owner, execution))
    values[Endpoint.ORDERS]["rows"].append({**values[Endpoint.ORDERS]["rows"][0], "id": 278})
    with pytest.raises(ERPError, match="order_mismatch"):
        asyncio.run(reconcile(client, caps, owner, execution))
    with pytest.raises(ERPError, match="permission_denied"):
        asyncio.run(reconcile(client, permissions("fixture-masked"), owner, execution))
