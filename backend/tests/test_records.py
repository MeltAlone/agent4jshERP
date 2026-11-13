import asyncio
import copy

import pytest
from helpers import permissions

from app.erp.client import ERPError
from app.erp.records import order_candidates


def test_order_pagination_does_not_accept_truncated_or_repeated_pages():
    class Client:
        async def get(self, endpoint, params):
            return copy.deepcopy(self.body)

    client = Client()
    row = {"id": 277, "number": "CGDD00000001002", "type": "其它", "subType": "采购订单"}
    for body in [
        {"total": 101, "rows": [row]},
        {"total": 2, "rows": [row, row]},
        {"total": True, "rows": []},
    ]:
        client.body = body
        with pytest.raises(ERPError):
            asyncio.run(order_candidates(client, permissions("fixture-buyer")))
