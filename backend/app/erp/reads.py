"""只读事实服务。输入无任意 URL，输出无 ERP 原始对象或未授权字段。"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.erp.client import Endpoint, ERPClient, ERPError
from app.erp.permissions import Permissions
from app.history import provenance
from app.repository import Principal

PositiveID = Annotated[StrictInt, Field(gt=0, le=9007199254740991)]


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=80)


class MaterialID(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extend_id: PositiveID


class StockQuery(MaterialID):
    depot_id: PositiveID


def decimal(value, *, nonnegative=False) -> str:
    if type(value) not in {str, int, float, Decimal}:
        raise ERPError("unsupported_contract")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or (nonnegative and result < 0):
            raise ValueError
        return format(result, "f")
    except InvalidOperation, ValueError:
        raise ERPError("unsupported_contract") from None


def text_field(raw, key, *, required=False):
    value = raw.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > 200 or (required and not value.strip()):
        raise ERPError("unsupported_contract")
    return value


def positive_id(value):
    if type(value) is not int or value <= 0 or value > 9007199254740991:
        raise ERPError("unsupported_contract")
    return value


class ReadTools:
    """principal/permissions 由可信运行环境注入，不能作为模型工具参数。"""

    def __init__(self, client: ERPClient, permissions: Permissions, principal: Principal):
        self.client, self.permissions, self.principal = client, permissions, principal

    def fact(self, kind, data, endpoint, capabilities, *, depots=(), complete=True):
        return {
            "id": str(uuid4()),
            "kind": kind,
            "data": data,
            "source": {
                "system": "jshERP",
                "endpoint": endpoint,
                "queried_at": datetime.now(UTC).isoformat(),
            },
            "complete": complete,
            "provenance": provenance(self.permissions, capabilities=capabilities, depot_ids=depots),
        }

    async def materials(self, query: Search):
        self.permissions.require("material_read")
        # findBySelect 的 id 是扩展 ID；其 stock 是未经授权范围约束的汇总，禁止返回。
        raw = await self.client.materials(query.query)
        if (
            not isinstance(raw, dict)
            or type(raw.get("total")) is not int
            or raw["total"] < 0
            or not isinstance(raw.get("rows"), list)
        ):
            raise ERPError("unsupported_contract")
        if len(raw["rows"]) > 20 or raw["total"] < len(raw["rows"]):
            raise ERPError("unsupported_contract")
        rows, seen = [], set()
        for row in raw["rows"]:
            if not isinstance(row, dict):
                raise ERPError("unsupported_contract")
            identity = positive_id(row.get("id"))
            if identity in seen:
                raise ERPError("ambiguous_result")
            seen.add(identity)
            rows.append(
                {
                    "extend_id": identity,
                    "barcode": text_field(row, "mBarCode", required=True),
                    "name": text_field(row, "name", required=True),
                    "standard": text_field(row, "standard"),
                    "model": text_field(row, "model"),
                    "unit_label": text_field(row, "unit", required=True),
                }
            )
        if not rows and raw["total"]:
            raise ERPError("ambiguous_result")
        return self.fact(
            "entity_choice",
            rows,
            Endpoint.MATERIALS.value,
            ("material_read",),
            complete=raw["total"] == len(rows),
        )

    async def suppliers(self, query: Search):
        self.permissions.require("supplier_read")
        raw = await self.client.suppliers(query.query)
        if not isinstance(raw, list) or not raw or len(raw) > 20:
            raise ERPError("ambiguous_result")
        rows, seen = [], set()
        for row in raw:
            if not isinstance(row, dict):
                raise ERPError("unsupported_contract")
            identity = positive_id(row.get("id"))
            if identity in seen:
                raise ERPError("ambiguous_result")
            seen.add(identity)
            rows.append(
                {"supplier_id": identity, "name": text_field(row, "supplier", required=True)}
            )
        return self.fact(
            "supplier_choice",
            rows,
            "/supplier/findBySelect_sup",
            ("supplier_read",),
            complete=len(rows) < 20,
        )

    async def material(self, query: MaterialID):
        self.permissions.require("material_read")
        raw = await self.client.get(Endpoint.MATERIAL, {"meId": query.extend_id, "mpList": ""})
        if (
            not isinstance(raw, dict)
            or str(raw.get("tenantId")) != self.principal.tenant_id
            or raw.get("enabled") is not True
            or raw.get("deleteFlag") != "0"
        ):
            raise ERPError("record_not_accessible")
        # meId 可为 null，因此通过请求扩展 ID + 可信条码查回相同候选核实映射。
        barcode = text_field(raw, "mBarCode", required=True)
        found = await self.materials(Search(query=barcode))
        candidates = [
            item
            for item in found["data"]
            if item["extend_id"] == query.extend_id and item["barcode"] == barcode
        ]
        if len(candidates) != 1:
            raise ERPError("record_not_accessible")
        item = {
            **candidates[0],
            "material_id": positive_id(raw.get("id")),
            "unit": text_field(raw, "commodityUnit", required=True),
        }
        unit_id = raw.get("unitId")
        units = {item["unit"]: "1"}
        if unit_id is not None:
            positive_id(unit_id)
            response = await self.client.get(
                Endpoint.UNITS,
                {"search": '{"id":' + str(unit_id) + "}", "currentPage": 1, "pageSize": 100},
            )
            if (
                not isinstance(response, dict)
                or not isinstance(response.get("rows"), list)
                or type(response.get("total")) is not int
                or response["total"] != len(response["rows"])
            ):
                raise ERPError("ambiguous_result")
            if any(not isinstance(row, dict) for row in response["rows"]):
                raise ERPError("unsupported_contract")
            matches = [
                row
                for row in response["rows"]
                if row.get("id") == unit_id
                and str(row.get("tenantId")) == self.principal.tenant_id
                and row.get("enabled") is True
                and row.get("deleteFlag") == "0"
            ]
            if len(matches) != 1:
                raise ERPError("unsupported_contract")
            row = matches[0]
            units = {text_field(row, "basicUnit", required=True): "1"}
            for name, ratio in [
                ("otherUnit", "ratio"),
                ("otherUnitTwo", "ratioTwo"),
                ("otherUnitThree", "ratioThree"),
            ]:
                if row.get(name):
                    unit = text_field(row, name, required=True)
                    factor = decimal(row.get(ratio), nonnegative=True)
                    if Decimal(factor) <= 0 or unit in units:
                        raise ERPError("unsupported_contract")
                    units[unit] = factor
        if item["unit"] not in units:
            raise ERPError("unsupported_unit")
        item["configured_units"] = units
        caps = ("material_read",)
        if self.permissions.purchase_prices:
            if raw.get("purchaseDecimal") is not None:
                item["purchase_price"] = decimal(raw["purchaseDecimal"], nonnegative=True)
            caps += ("purchase_prices",)
        return self.fact("material_detail", item, Endpoint.MATERIAL.value, caps)

    async def depots(self):
        self.permissions.require("stock_read")
        raw = await self.client.get(Endpoint.DEPOTS)
        if not isinstance(raw, list):
            raise ERPError("unsupported_contract")
        rows = []
        for row in raw:
            if not isinstance(row, dict):
                raise ERPError("unsupported_contract")
            identity = positive_id(row.get("id"))
            if identity not in self.permissions.depot_ids:
                raise ERPError("permission_denied")
            rows.append({"depot_id": identity, "name": text_field(row, "depotName", required=True)})
        return self.fact(
            "depot_choice",
            rows,
            Endpoint.DEPOTS.value,
            ("stock_read",),
            depots=tuple(row["depot_id"] for row in rows),
        )

    async def stock(self, query: StockQuery):
        self.permissions.require("stock_read", depot_id=query.depot_id)
        material = (await self.material(MaterialID(extend_id=query.extend_id)))["data"]
        depot_fact = await self.depots()
        depot = next((row for row in depot_fact["data"] if row["depot_id"] == query.depot_id), None)
        if not depot:
            raise ERPError("permission_denied")
        raw = await self.client.get(
            Endpoint.STOCK, {"barCode": material["barcode"], "depotId": query.depot_id}
        )
        if not isinstance(raw, dict):
            raise ERPError("unsupported_contract")
        # 库存事实只含数量、明确商品/单位与授权仓库，不传播材料的采购价格。
        data = {
            key: material[key]
            for key in [
                "material_id",
                "extend_id",
                "name",
                "standard",
                "model",
                "barcode",
                "unit",
                "configured_units",
            ]
        }
        data.update({"depot": depot, "quantity": decimal(raw.get("stock"))})
        return self.fact(
            "stock_result",
            data,
            Endpoint.STOCK.value,
            ("material_read", "stock_read"),
            depots=(query.depot_id,),
        )
