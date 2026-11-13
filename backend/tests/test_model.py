import json

import httpx
import pytest
from helpers import configuration

from app.orchestration.model import ModelClient, ModelError


def settings():
    return configuration(
        model_base_url="https://model.invalid/v1",
        model_name="fixture-model",
        model_api_key="private-model-key",
    )


def test_model_request_schema_usage_and_safe_errors():
    def respond(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private-model-key"
        raw = json.loads(request.content)
        assert raw["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"action":"reply","text":"你好"}'}}],
                "usage": {"total_tokens": 12, "fake_cost": 5},
            },
        )

    result = ModelClient(settings(), transport=httpx.MockTransport(respond)).decide(
        [{"role": "user", "content": "你好"}], timeout=2
    )
    assert result.decision.text == "你好" and result.usage == {"total_tokens": 12}
    for status, payload in [
        (500, {"error": "private-model-key"}),
        (200, {"choices": [{"message": {"content": '{"action":"create_order"}'}}]}),
    ]:
        client = ModelClient(
            settings(),
            transport=httpx.MockTransport(
                lambda request, code=status, body=payload: httpx.Response(code, json=body)
            ),
        )
        with pytest.raises(ModelError) as error:
            client.decide([], timeout=2)
        assert "private-model-key" not in str(error.value)


def test_timeout_and_specialist_recursive_delegate_are_controlled():
    def timeout(request):
        raise httpx.ReadTimeout("private-model-key")

    with pytest.raises(ModelError, match="model_timeout"):
        ModelClient(settings(), transport=httpx.MockTransport(timeout)).decide([], timeout=1)
    body = {
        "choices": [{"message": {"content": '{"action":"delegate","arguments":{"goal":"循环"}}'}}]
    }
    with pytest.raises(ModelError, match="delegation_denied"):
        ModelClient(
            settings(),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        ).decide([], timeout=2, specialist=True)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (None, "invalid_envelope"),
        ({}, "invalid_envelope"),
        ({"choices": []}, "invalid_envelope"),
        ({"choices": [{"message": {"content": None}}]}, "invalid_envelope"),
        ({"choices": [{"message": {"content": []}}]}, "invalid_envelope"),
        ({"choices": [{"message": {"content": "  "}}]}, "empty_content"),
        (
            {
                "choices": [
                    {"message": {"content": '{"action":"reply"}'}, "finish_reason": "length"}
                ]
            },
            "truncated_output",
        ),
        ({"choices": [{"message": {"content": "private-model-key invalid json"}}]}, "invalid_json"),
        ({"choices": [{"message": {"content": '{"action":"create_order"}'}}]}, "invalid_decision"),
        (
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"action":"reply","private-field":"private-model-key"}'
                        }
                    }
                ]
            },
            "invalid_decision",
        ),
        ({"choices": [{"message": {"content": "[]"}}]}, "invalid_decision"),
        (
            {"choices": [{"message": {"content": '{"action":"reply","text":15}'}}]},
            "invalid_decision",
        ),
    ],
)
def test_invalid_responses_report_only_fixed_categories(body, reason):
    client = ModelClient(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )
    with pytest.raises(ModelError) as error:
        client.decide([], timeout=1)
    assert error.value.code == "model_invalid_response"
    assert error.value.reason == reason
    assert str(error.value) == "model_invalid_response"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"not json private-model-key", "invalid_envelope"),
        (b"x" * (128 * 1024 + 1), "response_too_large"),
    ],
)
def test_invalid_http_body_is_not_exposed(body, reason):
    client = ModelClient(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    with pytest.raises(ModelError) as error:
        client.decide([], timeout=1)
    assert error.value.reason == reason
    assert str(error.value) == "model_invalid_response"


def test_specialist_draft_remains_denied_and_diagnostics_reject_arbitrary_text():
    body = {"choices": [{"message": {"content": '{"action":"draft"}'}}]}
    client = ModelClient(
        settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )
    with pytest.raises(ModelError, match="delegation_denied"):
        client.decide([], timeout=1, specialist=True)
    assert ModelError("model_invalid_response", reason="private-model-key").reason is None
