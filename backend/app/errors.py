from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        retryable: bool = False,
        next_action: str = "none",
    ):
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.next_action = next_action


def install_error_handlers(application: FastAPI):
    from app.erp.client import ERPError
    from app.repository import ResourceNotFound

    @application.exception_handler(ResourceNotFound)
    async def missing_resource(request, error):
        return await api_error(request, APIError("not_found", "找不到该资源", 404))

    @application.exception_handler(ERPError)
    async def dependency_error(request, error):
        from app.auth import erp_error

        return await api_error(request, erp_error(error))

    @application.exception_handler(APIError)
    async def api_error(request: Request, error: APIError):
        return JSONResponse(
            status_code=error.status,
            content={
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "next_action": error.next_action,
                "correlation_id": str(uuid4()),
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "请求字段不符合接口要求",
                "retryable": False,
                "next_action": "check_fields",
                "correlation_id": str(uuid4()),
            },
        )
