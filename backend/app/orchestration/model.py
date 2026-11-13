"""受控 Chat Completions 连接。响应原文、供应商异常和凭据不进入运行记录。"""

import json
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.erp.reads import PositiveID


class ModelError(Exception):
    def __init__(self, code, *, reason=None):
        self.code = code
        # 只记录固定分类，禁止将供应商内容或校验输入写入 trace。
        self.reason = (
            reason
            if reason
            in {
                "invalid_envelope",
                "empty_content",
                "truncated_output",
                "invalid_json",
                "invalid_decision",
                "response_too_large",
            }
            else None
        )
        super().__init__(code)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(
        pattern=r"^(reply|materials|material|suppliers|depots|stock|delegate|draft)$"
    )
    arguments: dict = Field(default_factory=dict)
    text: str = Field(default="", max_length=6000)
    source_refs: list[str] = Field(default_factory=list, max_length=24)


class SuggestedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extend_id: PositiveID
    quantity: str | None = Field(default=None, pattern=r"^[0-9]{1,9}(\.[0-9]{1,6})?$")
    unit: str | None = Field(default=None, max_length=40)


class SuggestedPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[SuggestedLine] = Field(default_factory=list, max_length=20)
    supplier_id: PositiveID | None = None
    remark: str | None = Field(default=None, max_length=500)


class Preparation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "needs_input", "partial", "failed"]
    proposed_patch: SuggestedPatch
    missing_fields: list[str] = Field(max_length=24)
    warnings: list[str] = Field(max_length=24)


@dataclass(frozen=True)
class ModelReply:
    decision: Decision
    usage: dict


class ModelClient:
    def __init__(self, settings: Settings, *, transport=None):
        self.settings, self.transport = settings, transport

    def decide(self, messages, *, timeout: float, specialist=False):
        settings = self.settings
        if not settings.model_api_key or not settings.model_base_url or not settings.model_name:
            raise ModelError("model_not_configured")
        try:
            with httpx.Client(
                timeout=min(settings.model_timeout_seconds, timeout),
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    settings.model_base_url + "/chat/completions",
                    headers={
                        "Authorization": "Bearer " + settings.model_api_key.get_secret_value()
                    },
                    json={
                        "model": settings.model_name,
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                        "max_tokens": settings.model_max_output_tokens,
                        "temperature": 0.2,
                    },
                )
            if response.status_code != 200:
                raise ModelError("model_unavailable")
            if len(response.content) > 128 * 1024:
                raise ModelError("model_invalid_response", reason="response_too_large")
            try:
                raw = response.json()
                choice = raw["choices"][0]
                content = choice["message"]["content"]
                truncated = choice.get("finish_reason") == "length"
                if not isinstance(raw, dict) or not isinstance(content, str):
                    raise ValueError
            except KeyError, IndexError, TypeError, ValueError, AttributeError:
                raise ModelError("model_invalid_response", reason="invalid_envelope") from None
            if truncated:
                raise ModelError("model_invalid_response", reason="truncated_output")
            if not content.strip():
                raise ModelError("model_invalid_response", reason="empty_content")
            try:
                payload = json.loads(content)
            except ValueError:
                raise ModelError("model_invalid_response", reason="invalid_json") from None
            try:
                decision = Decision.model_validate(payload)
            except ValidationError:
                raise ModelError("model_invalid_response", reason="invalid_decision") from None
            if specialist and decision.action in {"delegate", "draft"}:
                raise ModelError("delegation_denied")
            raw_usage = raw.get("usage", {})
            usage = (
                {
                    key: value
                    for key, value in raw_usage.items()
                    if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                    and type(value) is int
                    and value >= 0
                }
                if isinstance(raw_usage, dict)
                else {}
            )
            return ModelReply(decision, usage)
        except httpx.TimeoutException:
            raise ModelError("model_timeout") from None
        except httpx.HTTPError:
            raise ModelError("model_unavailable") from None
