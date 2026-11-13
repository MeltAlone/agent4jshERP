from alembic import context
from sqlmodel import SQLModel

from app import models  # noqa: F401
from app.config import load_settings
from app.database import create_database_engine

config = context.config
target_metadata = SQLModel.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # 图框架自行迁移其表，Alembic 不得自动删除或重建检查点。
    return not (type_ == "table" and name.startswith("checkpoint"))


def migrate(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Migrations require an online PostgreSQL connection")
elif config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    engine = create_database_engine(load_settings())
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
