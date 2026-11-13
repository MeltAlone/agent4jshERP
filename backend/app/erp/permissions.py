import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError

from app.erp.client import Endpoint, ERPClient, ERPError
from app.repository import Principal


class User(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: StrictInt
    tenantId: StrictInt
    loginName: StrictStr
    status: StrictInt


@dataclass(frozen=True)
class Permissions:
    supported: bool = False
    material_read: bool = False
    supplier_read: bool = False
    stock_read: bool = False
    purchase_read: bool = False
    purchase_create: bool = False
    purchase_prices: bool = False
    stock_prices: bool = False
    depot_ids: tuple[int, ...] = ()
    role_type: str = "unsupported"

    @property
    def fingerprint(self) -> str:
        from dataclasses import asdict

        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def document(self) -> dict:
        from dataclasses import asdict

        return {**asdict(self), "fingerprint": self.fingerprint}

    def require(self, capability: str, *, depot_id: int | None = None):
        if capability not in {
            "material_read",
            "supplier_read",
            "stock_read",
            "purchase_read",
            "purchase_create",
            "purchase_prices",
            "stock_prices",
        }:
            raise ERPError("permission_denied")
        if not self.supported or not getattr(self, capability):
            raise ERPError("permission_denied")
        if depot_id is not None and depot_id not in self.depot_ids:
            raise ERPError("permission_denied")


def parse_identity(data: object, connection_id: str):
    try:
        user = User.model_validate(data["user"])
        if user.id <= 0 or user.tenantId < 0 or user.status != 0:
            raise ValueError
    except ValidationError, TypeError, KeyError, ValueError:
        raise ERPError("unsupported_contract") from None
    return Principal(connection_id, str(user.tenantId), str(user.id)), user.loginName


def parse_permissions(buttons, prices, role, depots) -> Permissions:
    try:
        rows = buttons["userBtn"]
        if not isinstance(rows, list) or not rows:
            raise ValueError
        routes = {}
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("url"), str)
                or not isinstance(row.get("btnStr"), str)
                or row["url"] in routes
            ):
                raise ValueError
            routes[row["url"]] = set(row["btnStr"].split(",")) - {""}
        raw = prices["priceLimit"]
        if not isinstance(raw, str):
            raise ValueError
        price_codes = set(raw.split(",")) - {""}
        if price_codes - {"4", "7"}:
            raise ValueError
        role_type = role["roleType"]
        if role_type not in {"全部数据", "个人数据"} or not isinstance(depots, list):
            raise ValueError
        depot_ids = []
        for row in depots:
            if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
                raise ValueError
            depot_ids.append(row["id"])
        material = "/material/material" in routes
        purchase = "/bill/purchase_order" in routes
        prices_visible = "4" not in price_codes
        return Permissions(
            supported=True,
            material_read=material,
            supplier_read="/system/vendor" in routes,
            stock_read=material and "/report/material_stock" in routes,
            purchase_read=purchase,
            purchase_create=purchase and "1" in routes["/bill/purchase_order"] and prices_visible,
            purchase_prices=prices_visible,
            stock_prices="7" not in price_codes,
            depot_ids=tuple(sorted(set(depot_ids))),
            role_type=role_type,
        )
    except KeyError, TypeError, ValueError:
        return Permissions()


async def security_context(client: ERPClient):
    principal, name = parse_identity(
        await client.get(Endpoint.SESSION), client.settings.connection_id
    )
    buttons = await client.get(Endpoint.BUTTONS)
    prices = await client.get(Endpoint.PRICES)
    role = await client.get(Endpoint.ROLE)
    depots = await client.get(Endpoint.DEPOTS)
    return principal, name, parse_permissions(buttons, prices, role, depots)


def material_projection(raw: dict, permissions: Permissions) -> dict:
    permissions.require("material_read")
    # ERP hides prices inconsistently; only explicitly allowed fields may leave the adapter.
    fields = {"id", "barCode", "name", "standard", "model", "unit", "unitId", "unitName", "meId"}
    if permissions.purchase_prices:
        fields |= {"purchaseDecimal"}
    return {key: value for key, value in raw.items() if key in fields}
