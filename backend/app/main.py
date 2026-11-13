from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import Settings, load_settings
from app.context_api import router as context_router
from app.conversation_api import router as conversation_router
from app.database import check_database, create_database_engine
from app.errors import install_error_handlers
from app.purchases.api import router as purchase_router
from app.session_api import router as session_router
from app.startup import check_erp


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or load_settings()
        engine = create_database_engine(configuration)
        try:
            check_database(engine)
            await check_erp(configuration)
            application.state.settings = configuration
            application.state.engine = engine
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title="agent4jshERP",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url="/assistant/api/v1/openapi.json",
        docs_url="/assistant/api/v1/docs",
        redoc_url=None,
    )

    install_error_handlers(application)
    application.include_router(router)
    application.include_router(session_router)
    application.include_router(context_router)
    application.include_router(conversation_router)
    application.include_router(purchase_router)

    @application.get("/assistant/api/v1/health/live", tags=["health"])
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/assistant/api/v1/health/ready", tags=["health"])
    def readiness() -> dict[str, str]:
        check_database(application.state.engine)
        return {"status": "ready"}

    @application.get("/assistant/api/v1/status", tags=["health"])
    def status() -> dict[str, object]:
        return {
            "service": "agent4jshERP",
            "version": "0.1.0",
            "stage": "purchase_assistant",
            "agent_available": bool(
                getattr(application.state, "settings", None)
                and application.state.settings.model_api_key
            ),
            "purchase_writes_enabled": bool(
                getattr(
                    getattr(application.state, "settings", None), "purchase_writes_enabled", False
                )
            ),
        }

    return application


app = create_app()
