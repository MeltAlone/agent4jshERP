import time
from uuid import uuid4

import pytest
from helpers import configuration, permissions
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context
from test_reads import FixtureClient

from app.erp.client import ERPError
from app.erp.reads import ReadTools
from app.orchestration.checkpoints import checkpoint_store, setup, thread_id
from app.orchestration.graph import RunContext, build_graph
from app.orchestration.model import Decision, ModelError, ModelReply
from app.repository import Principal


class Model:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = 0

    def decide(self, messages, **kwargs):
        self.calls += 1
        assert "private-model-key" not in str(messages)
        assert "private-erp-token" not in str(messages)
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return ModelReply(Decision(**answer), {"total_tokens": 4})


def context(model, **settings):
    principal = Principal("jsh-local", "9001", "9002")
    caps = permissions("fixture-buyer")
    return RunContext(
        principal,
        caps,
        ReadTools(FixtureClient(), caps, principal),
        model,
        configuration(**settings),
        lambda: None,
        ("private-model-key", "private-erp-token"),
    )


def initial():
    return {
        "history": [{"role": "user", "content": "你好 private-erp-token"}],
        "started_at": time.time(),
        "facts": [],
        "observations": [],
    }


def invoke(model, **settings):
    with tracing_context(enabled=False):
        state = initial()
        state["history"][0]["content"] = "你好"
        return build_graph(InMemorySaver()).invoke(
            state,
            {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 100},
            context=context(model, **settings),
        )


def test_smalltalk_only_uses_main_model_and_has_no_business_side_effect():
    model = Model([{"action": "reply", "text": "你好，可以聊聊工作安排。"}])
    state = invoke(model)
    assert state["response"]["text"].startswith("你好")
    assert state.get("tool_calls", 0) == 0 and state.get("delegations", 0) == 0
    assert state["response"]["facts"] == []


def test_simple_stock_query_direct_tool_and_real_structured_fact():
    model = Model(
        [
            {"action": "stock", "arguments": {"extend_id": 20011, "depot_id": 6001}},
            {"action": "reply", "text": "已查询，见库存卡片。"},
        ]
    )
    state = invoke(model)
    assert state["tool_calls"] == 1 and state.get("delegations", 0) == 0
    assert state["facts"][0]["kind"] == "stock_result"
    assert state["facts"][0]["data"]["quantity"] == "10.0"


def test_specialist_result_returns_to_main_and_shared_budget():
    model = Model(
        [
            {"action": "delegate", "arguments": {"goal": "准备采购可乐"}},
            {"action": "materials", "arguments": {"query": "可乐"}},
            {
                "action": "reply",
                "text": "请选择规格",
                "arguments": {
                    "status": "needs_input",
                    "proposed_patch": {},
                    "missing_fields": ["规格", "数量"],
                    "warnings": [],
                },
            },
            {"action": "reply", "text": "有多种可乐规格，请先选择。"},
        ]
    )
    state = invoke(model)
    assert state["model_calls"] == 4 and state["delegations"] == 1
    assert state["response"]["preparation"]["missing_fields"] == ["规格", "数量"]
    assert state["response"]["text"] == "有多种可乐规格，请先选择。"


def test_budget_loop_and_model_timeout_stop_without_claiming_success():
    model = Model([{"action": "materials", "arguments": {"query": "可乐"}}] * 10)
    state = invoke(model, model_max_calls=2)
    assert state["model_calls"] == 2 and state["response"]["error"] == "budget_exhausted"
    assert len(state["response"]["facts"]) == 1
    assert state["observations"][-1]["error"] == "repeated_query"
    model = Model([ModelError("model_timeout"), ModelError("model_timeout")])
    state = invoke(model)
    assert model.calls == 2 and state["response"]["error"] == "model_timeout"


def test_delegation_cycle_and_forged_sources_or_principal_rejected():
    model = Model(
        [
            {"action": "delegate", "arguments": {"goal": "采购"}},
            {"action": "delegate", "arguments": {"goal": "无限调用"}},
        ]
    )
    assert invoke(model)["response"]["error"] == "delegation_limit"
    model = Model(
        [
            {
                "action": "stock",
                "arguments": {"extend_id": 20011, "depot_id": 6001, "principal": "admin"},
            },
            {"action": "reply", "text": "无法查询"},
        ]
    )
    state = invoke(model)
    assert state["observations"][0]["error"] == "invalid_tool_arguments" and state["facts"] == []
    assert (
        invoke(Model([{"action": "reply", "text": "已完成", "source_refs": ["invented"]}]))[
            "response"
        ]["error"]
        == "invalid_source"
    )


def test_pg_checkpoint_restart_only_replays_unfinished_node_and_excludes_secrets(pg_engine):
    setup(pg_engine)
    model = Model(
        [
            {"action": "materials", "arguments": {"query": "可乐"}},
            {"action": "reply", "text": "请选择规格"},
        ]
    )
    runtime = context(model)
    key = thread_id(runtime.principal, uuid4(), runtime.permissions.fingerprint)
    config = {"configurable": {"thread_id": key}}
    state = initial()
    state["history"][0]["content"] = "你好"
    with tracing_context(enabled=False), checkpoint_store(pg_engine) as saver:
        graph = build_graph(saver)
        graph.invoke(state, config, context=runtime, interrupt_after=["tool"], durability="sync")
        assert model.calls == 1 and graph.get_state(config).next
    with tracing_context(enabled=False), checkpoint_store(pg_engine) as saver:
        graph = build_graph(saver)
        recovered = graph.invoke(None, config, context=runtime, durability="sync")
        assert model.calls == 2 and len(recovered["facts"]) == 1
        assert recovered["response"]["text"] == "请选择规格"
        for checkpoint in saver.list(config):
            body = str(checkpoint)
            assert "private-model-key" not in body and "private-erp-token" not in body
    changed = thread_id(runtime.principal, uuid4(), "new-permissions")
    assert changed != key


def test_runtime_permission_change_stops_before_model():
    runtime = context(Model([{"action": "reply", "text": "不应调用"}]))

    def denied():
        raise ERPError("permissions_changed")

    from dataclasses import replace

    runtime = replace(runtime, verify=denied)
    with pytest.raises(ERPError, match="permissions_changed"), tracing_context(enabled=False):
        build_graph(InMemorySaver()).invoke(
            initial(), {"configurable": {"thread_id": str(uuid4())}}, context=runtime
        )
    assert runtime.model.calls == 0


def test_specialist_cannot_inject_identity_or_unverified_entity_ids():
    for patch in [{"principal": "admin"}, {"lines": [{"extend_id": 999999}]}]:
        state = invoke(
            Model(
                [
                    {"action": "delegate", "arguments": {"goal": "准备采购"}},
                    {
                        "action": "reply",
                        "arguments": {
                            "status": "completed",
                            "proposed_patch": patch,
                            "missing_fields": [],
                            "warnings": [],
                        },
                    },
                ]
            )
        )
        assert state["response"]["error"] == "invalid_specialist_result"


def test_model_success_claim_has_no_execution_card_or_write_action():
    state = invoke(Model([{"action": "reply", "text": "采购成功 <script>alert(1)</script>"}]))
    assert state["response"]["status"] == "needs_input"
    assert state["response"]["facts"] == []
    assert "execution_result" not in state["response"]


def test_invalid_format_recovers_once_with_fixed_instruction_and_shared_reservations():
    from dataclasses import replace

    from app.orchestration.graph import FORMAT_CORRECTION

    class RecordingModel(Model):
        def __init__(self, answers):
            super().__init__(answers)
            self.messages = []

        def decide(self, messages, **kwargs):
            self.messages.append(messages)
            return super().decide(messages, **kwargs)

    model = RecordingModel(
        [
            ModelError("model_invalid_response", reason="invalid_json"),
            {"action": "reply", "text": "请补充采购数量。"},
        ]
    )
    reservations = []

    def reserve(kind):
        reservations.append(kind)
        return {"model_calls": len(reservations)}

    runtime = replace(context(model), reserve=reserve)
    with tracing_context(enabled=False):
        state = build_graph(InMemorySaver()).invoke(
            initial(),
            {"configurable": {"thread_id": str(uuid4())}},
            context=runtime,
        )
    assert model.calls == state["model_calls"] == 2
    assert reservations == ["model", "model"]
    assert state["response"]["text"] == "请补充采购数量。"
    assert not state.get("error") and not state["format_retry"]
    assert FORMAT_CORRECTION not in model.messages[0][0]["content"]
    assert FORMAT_CORRECTION in model.messages[1][0]["content"]
    assert model.messages[0][1:-1] == model.messages[1][1:-1]
    # 纠正时只增加固定提醒并扣除调用预算，不追加无效响应。
    assert len(model.messages[0]) == len(model.messages[1]) == 3
    assert state["history"] == initial()["history"]
    assert state["trace"][0]["reason"] == "invalid_json"
    assert state.get("tool_calls", 0) == state.get("delegations", 0) == 0
    assert "draft_request" not in state["response"]


def test_repeated_invalid_format_stops_and_does_not_claim_query_results():
    model = Model([ModelError("model_invalid_response", reason="empty_content")] * 3)
    state = invoke(model)
    assert model.calls == state["model_calls"] == 2
    response = state["response"]
    assert response["error"] == "model_invalid_response" and response["status"] == "failed"
    assert "格式校验" in response["text"]
    assert "查询结果" not in response["text"] and "补充信息" not in response["text"]
    assert response["facts"] == [] and "draft_request" not in response
    assert state.get("tool_calls", 0) == state.get("delegations", 0) == 0


def test_format_retry_preserves_actual_facts_on_final_failure():
    model = Model(
        [
            {"action": "materials", "arguments": {"query": "可乐"}},
            ModelError("model_invalid_response"),
            ModelError("model_invalid_response"),
        ]
    )
    state = invoke(model)
    assert model.calls == 3 and state["tool_calls"] == 1
    assert state["response"]["status"] == "partial"
    assert len(state["response"]["facts"]) == 1
    assert "已取得的查询结果保留" in state["response"]["text"]


def test_format_retry_cannot_exceed_model_call_budget():
    model = Model([ModelError("model_invalid_response")])
    state = invoke(model, model_max_calls=1)
    assert model.calls == state["model_calls"] == 1
    assert state["response"]["error"] == "budget_exhausted"
    assert state["trace"][0]["status"] == "model_invalid_response"
    assert state.get("tool_calls", 0) == 0


def test_format_retry_cannot_exceed_time_budget(monkeypatch):
    now = time.time()

    class ExpiringModel(Model):
        def decide(self, messages, **kwargs):
            try:
                return super().decide(messages, **kwargs)
            finally:
                monkeypatch.setattr(time, "time", lambda: now + 200)

    model = ExpiringModel([ModelError("model_invalid_response")])
    state = invoke(model)
    assert model.calls == 1 and state["response"]["error"] == "budget_exhausted"
    assert state.get("tool_calls", 0) == 0


def test_permission_errors_are_not_format_retried():
    model = Model([ModelError("delegation_denied")])
    state = invoke(model)
    assert model.calls == 1 and state["response"]["error"] == "delegation_denied"
