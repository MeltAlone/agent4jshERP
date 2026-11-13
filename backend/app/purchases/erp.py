"""固定采购订单写映射与授权回查；不接受模型生成的 URL/请求体。"""

import json
import re
from decimal import Decimal, InvalidOperation

from app.erp.client import Endpoint, ERPError
from app.erp.records import order_candidates, positive_id


def order_body(snapshot, number):
    if not re.fullmatch(r"CGDD[0-9]{8,32}", number):
        raise ERPError("unsupported_contract")
    info = {
        "defaultNumber": number,
        "number": number,
        "organId": snapshot["supplier"]["id"],
        "operTime": snapshot["order_date"] + " 00:00:00",
        "remark": snapshot["remark"],
        "discount": 0,
        "discountMoney": 0,
        "discountLastMoney": snapshot["total"],
        "changeAmount": 0,
        "type": "其它",
        "subType": "采购订单",
        "totalPrice": "-" + snapshot["total"],
        "accountIdList": "",
        "accountMoneyList": "",
        "fileName": "",
        "status": "0",
    }
    rows = [
        {
            "name": row["name"],
            "standard": row["standard"],
            "model": row["model"],
            "barCode": row["barcode"],
            "unit": row["unit"],
            "operNumber": row["quantity"],
            "unitPrice": row["unit_price"],
            "allPrice": row["amount"],
            "taxRate": 0,
            "taxMoney": 0,
            "taxLastMoney": row["amount"],
            "depotId": "",
            "remark": "",
            "linkId": "",
        }
        for row in snapshot["lines"]
    ]
    return {
        "info": json.dumps(info, ensure_ascii=False),
        "rows": json.dumps(rows, ensure_ascii=False),
    }


def decimal_equal(value, expected):
    if isinstance(value, bool) or value is None:
        return False
    try:
        return Decimal(str(value)).is_finite() and Decimal(str(value)) == Decimal(str(expected))
    except InvalidOperation, ValueError:
        return False


async def generate_number(client):
    body = await client.get(Endpoint.NUMBER)
    number = body.get("defaultNumber") if isinstance(body, dict) else None
    if not isinstance(number, str) or not re.fullmatch(r"[0-9]{8,32}", number):
        raise ERPError("unsupported_contract")
    return "CGDD" + number


def business_content(snapshot):
    # 查询时间和来源 ID 属于证据元数据，重新验证时只比较全部业务字段。
    return {key: value for key, value in snapshot.items() if key != "sources"}


async def reconcile(client, permissions, principal, execution):
    permissions.require("purchase_read")
    permissions.require("purchase_prices")
    candidates = await order_candidates(client, permissions, number=execution.erp_number)
    if not candidates:
        raise ERPError("not_yet_visible")
    if len(candidates) != 1:
        raise ERPError("order_mismatch")
    head = await client.get(Endpoint.ORDER, {"number": execution.erp_number})
    snapshot = execution.snapshot
    if not isinstance(head, dict) or any(
        head.get(key) != value
        for key, value in {
            "id": candidates[0]["id"],
            "number": execution.erp_number,
            "type": "其它",
            "subType": "采购订单",
            "status": "0",
            "organId": snapshot["supplier"]["id"],
            "organName": snapshot["supplier"]["name"],
            "remark": snapshot["remark"],
            "deleteFlag": "0",
            "purchaseStatus": "0",
        }.items()
    ):
        raise ERPError("order_mismatch")
    if (
        str(head.get("tenantId")) != principal.tenant_id
        or str(head.get("creator")) != principal.erp_user_id
        or not isinstance(head.get("operTimeStr"), str)
        or head["operTimeStr"][:10] != snapshot["order_date"]
        or not decimal_equal(head.get("totalPrice"), snapshot["total"])
        or not decimal_equal(head.get("discountLastMoney"), snapshot["total"])
        or any(
            not decimal_equal(head.get(key), 0)
            for key in ("changeAmount", "deposit", "discount", "discountMoney")
        )
        or head.get("otherMoney") not in (None, 0, "0", "0.00")
        or head.get("accountId") is not None
        or head.get("accountIdList") != ""
        or head.get("accountMoneyList") != ""
        or not decimal_equal(head.get("lastDeposit"), 0)
    ):
        raise ERPError("order_mismatch")
    details = await client.get(
        Endpoint.ITEMS,
        {"headerId": head["id"], "linkType": "basic", "isReadOnly": "1", "mpList": ""},
    )
    if not isinstance(details, dict) or not isinstance(details.get("rows"), list):
        raise ERPError("unsupported_contract")
    # 首个兼容快照总有一条无 id 的页脚合计；其他无标识行不能被静默忽略。
    rows = [row for row in details["rows"] if isinstance(row, dict) and "id" in row]
    footers = [row for row in details["rows"] if isinstance(row, dict) and "id" not in row]
    if (
        type(details.get("total")) is not int
        or details["total"] != len(rows)
        or len(rows) != len(snapshot["lines"])
        or len(footers) != 1
        or len(rows) + len(footers) != len(details["rows"])
        or not decimal_equal(footers[0].get("allPrice"), snapshot["total"])
        or not decimal_equal(footers[0].get("taxMoney"), 0)
        or not decimal_equal(footers[0].get("taxLastMoney"), snapshot["total"])
        or not decimal_equal(
            footers[0].get("operNumber"),
            sum(Decimal(line["quantity"]) for line in snapshot["lines"]),
        )
        or any(not positive_id(row["id"]) for row in rows)
        or len({row["id"] for row in rows}) != len(rows)
    ):
        raise ERPError("order_mismatch")
    by_barcode = {row.get("barCode"): row for row in rows}
    if len(by_barcode) != len(rows):
        raise ERPError("order_mismatch")
    for expected in snapshot["lines"]:
        row = by_barcode.get(expected["barcode"], {})
        if (
            row.get("materialExtendId") != expected["extend_id"]
            or any(row.get(key) != expected[key] for key in ("name", "standard", "model", "unit"))
            or row.get("depotId") != ""
            or any(
                not decimal_equal(row.get(key), expected[value])
                for key, value in {
                    "operNumber": "quantity",
                    "basicNumber": "basic_quantity",
                    "unitPrice": "unit_price",
                    "allPrice": "amount",
                    "taxLastMoney": "amount",
                }.items()
            )
            or not decimal_equal(row.get("taxRate"), 0)
            or not decimal_equal(row.get("taxMoney"), 0)
        ):
            raise ERPError("order_mismatch")
    return {"id": head["id"], "number": head["number"], "status": "0", "verified": True}
