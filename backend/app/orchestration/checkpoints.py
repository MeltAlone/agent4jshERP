"""检查点只由已授权 worker 访问，无面向浏览器的 thread_id 接口。"""

import re
from contextlib import contextmanager
from hashlib import sha256

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row
from sqlalchemy import text


def thread_id(principal, run_id, fingerprint):
    # 权限变化使用新线程，以过滤后的领域历史重建，禁止恢复旧价格状态。
    identity = ":".join([*principal.fields().values(), str(run_id), fingerprint])
    return sha256(identity.encode()).hexdigest()


@contextmanager
def checkpoint_store(engine):
    with engine.connect() as database:
        schema = database.execute(text("SELECT current_schema()")).scalar_one()
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", schema):
        raise RuntimeError("invalid checkpoint schema")
    address = engine.url
    with Connection.connect(
        host=address.host,
        port=address.port or 5432,
        user=address.username,
        password=address.password,
        dbname=address.database,
        connect_timeout=5,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options=f"-csearch_path={schema}",
    ) as connection:
        yield PostgresSaver(connection)


def setup(engine):
    with checkpoint_store(engine) as saver:
        saver.setup()


if __name__ == "__main__":
    from app.config import load_settings
    from app.database import create_database_engine

    engine = create_database_engine(load_settings())
    try:
        setup(engine)
        print("LangGraph PostgreSQL 检查点已初始化。")
    finally:
        engine.dispose()
