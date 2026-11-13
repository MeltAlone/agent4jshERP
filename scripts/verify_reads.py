"""隔离真实 ERP 的只读工具验收；不创建单据，不输出凭据/原始业务对象。"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import load_settings
from app.erp.client import ERPClient, ERPError
from app.erp.permissions import security_context
from app.erp.reads import MaterialID, ReadTools, Search, StockQuery


async def main():
    settings = load_settings()
    if settings.erp_base_url != "http://127.0.0.1:19999/jshERP-boot":
        raise RuntimeError("仅支持隔离验证环境")
    source = (
        Path(__file__).resolve().parents[1]
        / "integrations/jsh-erp/verification/capture_reads.py"
    )
    spec = importlib.util.spec_from_file_location("fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    credentials = json.loads((source.parent / ".runtime/credentials.json").read_text())
    for name in ["fixture-buyer", "fixture-masked"]:
        login = fixture.FixtureClient(settings.erp_base_url)
        login.login(name, credentials[name])
        client = ERPClient(settings, SecretStr(login.token))
        principal, _, permissions = await security_context(client)
        tools = ReadTools(client, permissions, principal)
        candidates = await tools.materials(Search(query="虚构可乐"))
        assert len(candidates["data"]) == 4
        assert not any("stock" in row for row in candidates["data"])
        material = (await tools.material(MaterialID(extend_id=20011)))["data"]
        assert material["material_id"] == 3001 and material["unit"] == "箱"
        assert material["configured_units"]["箱"] == "12.0"
        assert ("purchase_price" in material) == (name == "fixture-buyer")
        stock = (await tools.stock(StockQuery(extend_id=20011, depot_id=6001)))["data"]
        assert stock["quantity"] == "10.0" and stock["depot"]["depot_id"] == 6001
        try:
            await tools.stock(StockQuery(extend_id=20011, depot_id=6002))
        except ERPError as error:
            assert error.code == "permission_denied"
        else:
            raise AssertionError("越权仓库未拒绝")
        suppliers = (await tools.suppliers(Search(query="虚构饮品供应商")))["data"]
        assert suppliers == [{"supplier_id": 4001, "name": "虚构饮品供应商"}]
        try:
            await tools.suppliers(Search(query="不存在的虚构供应商"))
        except ERPError as error:
            assert error.code == "ambiguous_result"
        else:
            raise AssertionError("供应商空响应未标记歧义")
        print(name + "：商品扩展映射、单位、价格过滤、授权库存与供应商歧义通过")
    print("真实只读工具验证通过；未创建/审核/付款/入库。")


if __name__ == "__main__":
    asyncio.run(main())
