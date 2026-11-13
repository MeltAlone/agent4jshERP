from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class ConfigurationError(RuntimeError):
    """Only safe field names belong in startup diagnostics."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="A4J_", env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "development"
    database_url: SecretStr
    session_key: SecretStr
    erp_base_url: str
    public_origin: str = "http://127.0.0.1:18081"
    connection_id: str = Field(default="jsh-local", pattern=r"^[a-z0-9-]{1,48}$")
    erp_contract: Literal["talk2stock-e72aae4-v1", "jsh-38ad91e-v1"] = "jsh-38ad91e-v1"
    erp_timezone: str = "Asia/Shanghai"
    purchase_writes_enabled: bool = False
    reconciliation_delays: tuple[int, ...] = (2, 5, 15, 30, 60)
    reconciliation_max_rounds: int = Field(default=3, ge=1, le=5)
    dependency_timeout_seconds: float = Field(default=5, ge=0.1, le=30)
    session_max_age_seconds: int = Field(default=28800, ge=300, le=86400)
    session_idle_seconds: int = Field(default=3600, ge=60, le=86400)
    model_base_url: str | None = None
    model_name: str | None = Field(default=None, max_length=128)
    model_api_key: SecretStr | None = None
    model_timeout_seconds: float = Field(default=25, ge=1, le=60)
    model_max_calls: int = Field(default=6, ge=1, le=12)
    tool_max_calls: int = Field(default=12, ge=1, le=24)
    delegation_max_calls: int = Field(default=2, ge=0, le=2)
    run_timeout_seconds: int = Field(default=90, ge=10, le=180)
    model_max_output_tokens: int = Field(default=1800, ge=128, le=8192)
    history_retention_days: int = Field(default=30, ge=1, le=365)
    audit_retention_days: int = Field(default=180, ge=30, le=3650)
    event_retention_days: int = Field(default=7, ge=1, le=90)

    @field_validator("reconciliation_delays")
    @classmethod
    def reconciliation_budget(cls, values):
        if not 1 <= len(values) <= 8 or any(type(x) is not int or x < 1 or x > 300 for x in values):
            raise ValueError("requires 1 to 8 bounded delays")
        return values

    @field_validator("erp_timezone")
    @classmethod
    def timezone(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError("requires an IANA timezone") from None
        return value

    @field_validator("database_url")
    @classmethod
    def database(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
                raise ValueError
            if not url.username or not url.password or url.query:
                raise ValueError
        except Exception:
            raise ValueError("requires a PostgreSQL psycopg URL") from None
        return value

    @field_validator("session_key")
    @classmethod
    def encryption(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value().encode())
        except Exception:
            raise ValueError("requires a generated Fernet key") from None
        return value

    @field_validator("erp_base_url", "public_origin", "model_base_url")
    @classmethod
    def controlled_address(cls, value: str, info) -> str:
        if value is None:
            return value
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError
            _ = parsed.port  # Validate malformed or out-of-range ports.
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError
            if info.field_name == "public_origin" and parsed.path not in {"", "/"}:
                raise ValueError
        except Exception:
            raise ValueError("requires a controlled HTTP(S) address") from None
        return value.rstrip("/")

    @model_validator(mode="after")
    def production_https(self):
        model_fields = (self.model_base_url, self.model_name, self.model_api_key)
        if any(model_fields) and not all(model_fields):
            raise ValueError("model connection requires base_url, name and api_key")
        if (
            self.environment == "production"
            and self.model_base_url
            and not self.model_base_url.startswith("https://")
        ):
            raise ValueError("production requires HTTPS model_base_url")
        if self.environment == "production" and not self.public_origin.startswith("https://"):
            raise ValueError("production requires HTTPS public_origin")
        return self


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        fields = sorted(
            {str(item["loc"][0]) if item["loc"] else "environment" for item in error.errors()}
        )
        raise ConfigurationError("配置无效，请检查字段：" + ", ".join(fields)) from None
