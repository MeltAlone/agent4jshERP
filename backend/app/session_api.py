import asyncio
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlmodel import Session

from app.auth import COOKIE, authorize, csrf_token, exchange, session_record, verify_local_request
from app.errors import APIError
from app.models import now

router = APIRouter(prefix="/assistant/api/v1")


class ExchangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/session/exchange")
async def exchange_session(
    request: Request,
    response: Response,
    body: ExchangeBody,
    token: Annotated[SecretStr, Header(alias="X-Access-Token", min_length=8, max_length=8192)],
):
    cookie, row, permissions = await exchange(request, token)
    settings = request.app.state.settings
    response.set_cookie(
        COOKIE,
        cookie,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/assistant/",
        max_age=settings.session_max_age_seconds,
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "generation": str(row.generation),
        "csrf_token": csrf_token(settings, row),
        "capabilities": permissions.document(),
    }


@router.get("/session")
def get_session(request: Request, response: Response):
    access = asyncio.run(authorize(request, bootstrap=True))
    with Session(request.app.state.engine) as database:
        row = session_record(request, database)
        if row.id != access.session_id or row.generation != access.generation:
            raise APIError("session_changed", "助手会话已变化", 409, next_action="reload_session")
        csrf = csrf_token(request.app.state.settings, row)
    response.headers["Cache-Control"] = "no-store"
    return {
        "generation": str(access.generation),
        "display_name": access.display_name,
        "principal": access.principal.fields(),
        "csrf_token": csrf,
        "capabilities": access.permissions.document(),
    }


@router.delete("/session", status_code=204)
def revoke_session(request: Request, response: Response):
    # 撤销只依赖本地请求证明，ERP 故障或登录失效不能阻止退出。
    with Session(request.app.state.engine) as database, database.begin():
        row = session_record(request, database, lock=True)
        verify_local_request(request, row)
        row.revoked_at = now()
        database.add(row)
    response.delete_cookie(
        COOKIE,
        path="/assistant/",
        secure=request.app.state.settings.environment == "production",
        httponly=True,
        samesite="lax",
    )
