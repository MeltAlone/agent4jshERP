from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import Settings


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": int(max(1, settings.dependency_timeout_seconds))},
        hide_parameters=True,
    )


def check_database(engine: Engine) -> None:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        raise RuntimeError("数据库连接失败，请检查 database_url 与 PostgreSQL 状态") from None
