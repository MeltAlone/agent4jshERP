from enum import StrEnum

import httpx
from pydantic import SecretStr

from app.config import Settings


class Endpoint(StrEnum):
    NUMBER = "/sequence/buildNumber"
    SESSION = "/user/getUserSession"
    BUTTONS = "/user/getUserBtnByCurrentUser"
    PRICES = "/user/getCurrentPriceLimit"
    ROLE = "/user/getRoleTypeByCurrentUser"
    DEPOTS = "/depot/findDepotByCurrentUser"
    MATERIALS = "/material/findBySelect"
    MATERIAL = "/material/findByIdWithBarCode"
    MATERIAL_BASE = "/material/findById"
    UNITS = "/unit/list"
    STOCK = "/depotItem/findStockByDepotAndBarCode"
    ORDERS = "/depotHead/list"
    ORDER = "/depotHead/getDetailByNumber"
    ITEMS = "/depotItem/getDetailList"


class ERPError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ERPClient:
    def __init__(self, settings: Settings, token: SecretStr, *, transport=None):
        self.settings = settings
        self.token = token
        self.transport = transport

    async def _request(self, method: str, path: str, *, params=None, json=None):
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.dependency_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method,
                    self.settings.erp_base_url + path,
                    headers={"X-Access-Token": self.token.get_secret_value()},
                    params=params,
                    json=json,
                )
        except httpx.TimeoutException:
            raise ERPError("erp_timeout") from None
        except httpx.RequestError:
            raise ERPError("erp_unavailable") from None
        if response.text.strip() == "loginOut" or response.status_code == 401:
            raise ERPError("auth_required")
        if response.status_code == 403:
            raise ERPError("permission_denied")
        if response.status_code >= 500:
            raise ERPError("erp_unavailable")
        if response.status_code != 200:
            raise ERPError("unsupported_contract")
        try:
            return response.json()
        except ValueError:
            raise ERPError("unsupported_contract") from None

    async def get(self, endpoint: Endpoint, params: dict | None = None):
        if not isinstance(endpoint, Endpoint):
            raise ERPError("unsupported_endpoint")
        body = await self._request("GET", endpoint.value, params=params)
        if not isinstance(body, dict) or type(body.get("code")) is not int:
            raise ERPError("unsupported_contract")
        if body["code"] != 200:
            raise ERPError("erp_rejected")
        if "data" not in body:
            raise ERPError("unsupported_contract")
        return body["data"]

    async def materials(self, query: str):
        return await self._request(
            "GET", Endpoint.MATERIALS.value, params={"q": query, "page": 1, "rows": 20}
        )

    async def suppliers(self, query: str):
        body = await self._request(
            "POST", "/supplier/findBySelect_sup", json={"key": query, "limit": 20}
        )
        if not isinstance(body, list):
            raise ERPError("unsupported_contract")
        if not body:
            raise ERPError("ambiguous_result")
        return body

    async def create_purchase(self, body: dict):
        # 唯一的业务写入口；调用方先持久化发送标记，HTTP 客户端无重试。
        if not self.settings.purchase_writes_enabled:
            raise ERPError("writes_disabled")
        return await self._request("POST", "/depotHead/addDepotHeadAndDetail", json=body)
