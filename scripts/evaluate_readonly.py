"""真实模型 + 隔离 ERP 场景评估；仅只读调用，输出聚合指标，不保存原文。"""

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context
from pydantic import SecretStr

from app.config import load_settings
from app.erp.client import ERPClient, ERPError
from app.erp.permissions import security_context
from app.erp.reads import ReadTools
from app.orchestration.graph import RunContext, build_graph
from app.orchestration.model import ModelClient


selected_scenarios = None


def run(name, question, principal, permissions, tools, settings, *, context=None):
    if selected_scenarios and name not in selected_scenarios:
        return
    started = time.monotonic()

    def verify():
        identity, _, current = asyncio.run(security_context(tools.client))
        if identity != principal or current.fingerprint != permissions.fingerprint:
            raise ERPError("permissions_changed")

    runtime = RunContext(
        principal,
        permissions,
        tools,
        ModelClient(settings),
        settings,
        verify,
        (
            tools.client.token.get_secret_value(),
            settings.model_api_key.get_secret_value(),
        ),
    )
    with tracing_context(enabled=False):
        result = build_graph(InMemorySaver()).invoke(
            {
                "history": [{"role": "user", "content": question}],
                "context": context or {},
                "facts": [],
                "observations": [],
                "started_at": time.time(),
            },
            {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 100},
            context=runtime,
        )
    facts = result.get("response", {}).get("facts", [])
    report = {
        "scenario": name,
        "status": result["response"]["status"],
        "error": result["response"].get("error"),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "model_calls": result.get("model_calls", 0),
        "tool_calls": result.get("tool_calls", 0),
        "delegations": result.get("delegations", 0),
        "usage": result.get("usage", {}),
        "actual_cost": None,
        "fact_kinds": [f["kind"] for f in facts],
        "tool_errors": [
            o["error"] for o in result.get("observations", []) if "error" in o
        ],
    }
    if name == "smalltalk":
        assert (
            not facts and not result.get("delegations") and result["response"]["text"]
        )
    elif name == "stock":
        stocks = [f for f in facts if f["kind"] == "stock_result"]
        assert (
            stocks
            and stocks[0]["data"]["quantity"] == "10.0"
            and stocks[0]["data"]["unit"] == "箱"
        )
    elif name == "masked_price":
        assert not any("purchase_price" in json.dumps(f) for f in facts)
    elif name == "restricted_depot":
        assert not any(f["kind"] == "stock_result" for f in facts)
    elif name == "ambiguous_purchase":
        assert any(f["kind"] == "entity_choice" and len(f["data"]) >= 2 for f in facts)
    elif name == "dependency_failure":
        assert "erp_unavailable" in report["tool_errors"] and not any(
            f["kind"] == "stock_result" for f in facts
        )
    elif name == "multitask":
        assert not facts and not result.get("delegations")
    print(json.dumps(report, ensure_ascii=False), flush=True)


def main():
    global selected_scenarios
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="append")
    selected_scenarios = parser.parse_args().scenario
    settings = load_settings()
    if (
        settings.erp_base_url != "http://127.0.0.1:19999/jshERP-boot"
        or not settings.model_api_key
    ):
        raise RuntimeError("需要隔离验证 ERP 与显式模型配置")
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
        principal, _, caps = asyncio.run(security_context(client))
        tools = ReadTools(client, caps, principal)
        if name == "fixture-masked":
            run(
                "masked_price",
                "查商品扩展ID 20011的配置单位和采购价格；若无价格权限请说明。",
                principal,
                caps,
                tools,
                settings,
            )
            continue
        run(
            "smalltalk",
            "你好，今天有点累，聊聊怎么合理安排工作和休息。",
            principal,
            caps,
            tools,
            settings,
        )
        run(
            "stock",
            "查一下330ml可乐在验证仓库一的库存，以箱为单位。",
            principal,
            caps,
            tools,
            settings,
        )
        run(
            "ambiguous_purchase",
            "我想采购可乐和矿泉水，你先帮我看看规格，不要替我决定数量。",
            principal,
            caps,
            tools,
            settings,
        )
        run(
            "restricted_depot",
            "查一下330ml可乐在仓库6002的库存，商品扩展ID20011。",
            principal,
            caps,
            tools,
            settings,
        )
        run(
            "multitask",
            "提交刚才那个。",
            principal,
            caps,
            tools,
            settings,
            context={
                "selected_task_id": None,
                "available_tasks": [
                    {"id": str(uuid4()), "kind": "purchase", "status": "needs_input"},
                    {"id": str(uuid4()), "kind": "purchase", "status": "needs_input"},
                ],
            },
        )

        class Unavailable(ReadTools):
            async def stock(self, query):
                raise ERPError("erp_unavailable")

        run(
            "dependency_failure",
            "请调用stock查询商品扩展ID20011在仓库6001的库存，若服务失败请如实报告。",
            principal,
            caps,
            Unavailable(client, caps, principal),
            settings,
        )


if __name__ == "__main__":
    main()
