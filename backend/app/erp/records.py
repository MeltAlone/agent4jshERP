"""从可信 ERP 重新取得页面记录；浏览器 ID 只用作查找条件。"""

import json

from app.erp.client import Endpoint, ERPClient, ERPError
from app.erp.permissions import Permissions
from app.repository import Principal


def positive_id(value):
    return type(value) is int and value > 0


async def order_candidates(
    client: ERPClient, permissions: Permissions, *, number: str | None = None
):
    permissions.require("purchase_read")
    filters = {"type": "其它", "subType": "采购订单"}
    if number:
        filters["number"] = number
    result, total = [], None
    for page in range(1, 11):
        body = await client.get(
            Endpoint.ORDERS,
            {
                "search": json.dumps(filters, ensure_ascii=False),
                "currentPage": page,
                "pageSize": 100,
            },
        )
        if (
            not isinstance(body, dict)
            or type(body.get("total")) is not int
            or body["total"] < 0
            or not isinstance(body.get("rows"), list)
        ):
            raise ERPError("unsupported_contract")
        if total is None:
            total = body["total"]
        if body["total"] != total or len(body["rows"]) != min(100, total - len(result)):
            raise ERPError("ambiguous_result")
        for row in body["rows"]:
            if (
                not isinstance(row, dict)
                or not positive_id(row.get("id"))
                or row.get("type") != "其它"
                or row.get("subType") != "采购订单"
                or not isinstance(row.get("number"), str)
            ):
                raise ERPError("unsupported_contract")
        result.extend(body["rows"])
        if len({row["id"] for row in result}) != len(result):
            raise ERPError("ambiguous_result")
        if len(result) == total:
            return [row for row in result if not number or row["number"] == number]
    raise ERPError("ambiguous_result")


async def verified_order(client, permissions, principal, record_id, number):
    candidates = await order_candidates(client, permissions, number=number)
    if not candidates:
        raise ERPError("record_not_accessible")
    if len(candidates) != 1 or candidates[0]["id"] != record_id:
        raise ERPError("ambiguous_result")
    head = await client.get(Endpoint.ORDER, {"number": number})
    if (
        not isinstance(head, dict)
        or head.get("id") != record_id
        or head.get("number") != number
        or str(head.get("tenantId")) != principal.tenant_id
        or head.get("type") != "其它"
        or head.get("subType") != "采购订单"
    ):
        raise ERPError("ambiguous_result")
    return {"id": record_id, "number": number}


async def material_record(client, permissions: Permissions, principal: Principal, record_id: int):
    permissions.require("material_read")
    rows = await client.get(Endpoint.MATERIAL_BASE, {"id": record_id})
    if not isinstance(rows, list):
        raise ERPError("unsupported_contract")
    if not rows:
        raise ERPError("record_not_accessible")
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ERPError("ambiguous_result")
    row = rows[0]
    if (
        row.get("id") != record_id
        or str(row.get("tenantId")) != principal.tenant_id
        or row.get("enabled") is not True
        or row.get("deleteFlag") != "0"
        or not isinstance(row.get("name"), str)
    ):
        raise ERPError("record_not_accessible")
    return {key: row.get(key) for key in ("id", "name", "standard", "model", "unitName")}
