import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request
from pydantic import SecretStr
from sqlmodel import Session, select

from app.config import Settings
from app.erp.client import ERPClient, ERPError
from app.erp.permissions import Permissions, security_context
from app.errors import APIError
from app.models import AssistantSession, now
from app.repository import Principal

COOKIE = "a4j_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(settings: Settings, row: AssistantSession) -> str:
    message = f"assistant-csrf-v1:{row.cookie_hash}:{row.generation}".encode()
    return hmac.new(
        settings.session_key.get_secret_value().encode(), message, hashlib.sha256
    ).hexdigest()


def require_origin(request: Request):
    if request.headers.get("origin") != request.app.state.settings.public_origin:
        raise APIError("origin_denied", "请求来源不受支持", 403)


def erp_error(error: ERPError) -> APIError:
    messages = {
        "auth_required": ("ERP 登录已失效，请重新登录", 401, "login"),
        "record_not_accessible": ("记录不存在或当前身份无权访问", 404, "change_selection"),
        "ambiguous_result": (
            "无法完整核实 ERP 返回结果，请重新选择或人工核对",
            422,
            "change_selection",
        ),
        "permission_denied": ("当前 ERP 身份无此权限", 403, "request_access"),
        "unsupported_contract": ("ERP 响应不在已验证的兼容范围内", 503, "contact_admin"),
    }
    message, status, action = messages.get(error.code, ("ERP 服务暂时不可用", 503, "retry_read"))
    return APIError(error.code, message, status, retryable=status == 503, next_action=action)


def erp_client(request: Request, token: SecretStr):
    factory = getattr(request.app.state, "erp_client_factory", ERPClient)
    return factory(request.app.state.settings, token)


@dataclass(frozen=True)
class Access:
    principal: Principal
    permissions: Permissions
    session_id: UUID
    generation: UUID
    display_name: str


def session_record(request: Request, database: Session, *, lock: bool = False) -> AssistantSession:
    cookie = request.cookies.get(COOKIE, "")
    if not cookie or len(cookie) > 128:
        raise APIError("auth_required", "请从 ERP 登录后打开助手", 401, next_action="login")
    query = select(AssistantSession).where(AssistantSession.cookie_hash == digest(cookie))
    row = database.exec(query.with_for_update() if lock else query).first()
    if (
        row is None
        or row.revoked_at is not None
        or row.expires_at <= now()
        or row.last_seen_at
        < now() - timedelta(seconds=request.app.state.settings.session_idle_seconds)
    ):
        raise APIError("auth_required", "助手会话已失效，请重新登录", 401, next_action="login")
    return row


def verify_local_request(request: Request, row: AssistantSession, *, bootstrap: bool = False):
    if not bootstrap and request.headers.get("x-assistant-generation") != str(row.generation):
        raise APIError(
            "session_changed",
            "登录身份已变化，请重新载入助手",
            409,
            next_action="reload_session",
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(digest(supplied), row.csrf_hash):
            raise APIError("csrf_denied", "请求校验失败，请重新载入助手", 403)


async def authorize(request: Request, *, bootstrap: bool = False) -> Access:
    return await asyncio.to_thread(_authorize, request, bootstrap=bootstrap)


def _authorize(request: Request, *, bootstrap: bool = False) -> Access:
    engine = request.app.state.engine
    with Session(engine) as database:
        row = session_record(request, database)
        settings = request.app.state.settings
        verify_local_request(request, row, bootstrap=bootstrap)
        try:
            token = Fernet(settings.session_key.get_secret_value()).decrypt(
                row.encrypted_token.encode()
            )
        except InvalidToken, ValueError:
            raise APIError(
                "auth_required", "会话密钥不可用，请重新登录", 401, next_action="login"
            ) from None
        owner = Principal(row.connection_id, row.tenant_id, row.erp_user_id)
        try:
            verified, name, permissions = asyncio.run(
                security_context(erp_client(request, SecretStr(token.decode())))
            )
        except ERPError as error:
            if error.code == "auth_required":
                row.revoked_at = now()
                database.add(row)
                database.commit()
            raise erp_error(error) from None
        database.refresh(row, with_for_update=True)
        if row.revoked_at is not None or row.expires_at <= now():
            raise APIError("session_changed", "助手会话已变化", 409, next_action="reload_session")
        if verified != owner:
            row.revoked_at = now()
            database.add(row)
            database.commit()
            raise APIError(
                "session_changed", "ERP 身份已变化，请重新登录", 409, next_action="reload_session"
            )
        row.last_seen_at = now()
        row.permission_fingerprint = permissions.fingerprint
        database.add(row)
        database.commit()
        access = Access(owner, permissions, row.id, row.generation, name)
        request.state.access = access
        return access


async def exchange(request: Request, token: SecretStr):
    return await asyncio.to_thread(_exchange, request, token)


def _exchange(request: Request, token: SecretStr):
    require_origin(request)
    settings = request.app.state.settings
    try:
        principal, name, permissions = asyncio.run(security_context(erp_client(request, token)))
    except ERPError as error:
        raise erp_error(error) from None
    cookie = secrets.token_urlsafe(32)
    row = AssistantSession(
        **principal.fields(),
        display_name=name[:160],
        cookie_hash=digest(cookie),
        csrf_hash="",
        encrypted_token=Fernet(settings.session_key.get_secret_value())
        .encrypt(token.get_secret_value().encode())
        .decode(),
        expires_at=now() + timedelta(seconds=settings.session_max_age_seconds),
        permission_fingerprint=permissions.fingerprint,
    )
    row.csrf_hash = digest(csrf_token(settings, row))
    with Session(request.app.state.engine, expire_on_commit=False) as database, database.begin():
        previous_cookie = request.cookies.get(COOKIE)
        if previous_cookie and len(previous_cookie) <= 128:
            previous = database.exec(
                select(AssistantSession)
                .where(AssistantSession.cookie_hash == digest(previous_cookie))
                .with_for_update()
            ).first()
            if previous:
                previous.revoked_at = now()
                database.add(previous)
        database.add(row)
    return cookie, row, permissions


def client_for_access(request: Request, access: Access) -> ERPClient:
    with Session(request.app.state.engine) as database:
        row = assert_access_current(request, database, access)
        try:
            token = (
                Fernet(request.app.state.settings.session_key.get_secret_value())
                .decrypt(row.encrypted_token.encode())
                .decode()
            )
        except InvalidToken, ValueError:
            raise APIError("auth_required", "请重新登录 ERP", 401, next_action="login") from None
    return erp_client(request, SecretStr(token))


def assert_access_current(request: Request, database: Session, access: Access):
    row = session_record(request, database, lock=True)
    if row.id != access.session_id or row.generation != access.generation:
        raise APIError("session_changed", "助手会话已变化", 409, next_action="reload_session")
    return row
