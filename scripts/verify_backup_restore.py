"""Local Compose recovery drill with synthetic data in a disposable schema/database."""
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlmodel import Session

from app.config import load_settings
from app.repository import Principal, Repository


def docker(*arguments, input=None):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "database", *arguments],
        input=input, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError("恢复演练命令失败；未输出数据库内容或连接信息")
    return result.stdout


def main():
    settings = load_settings()
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    suffix = uuid4().hex
    schema = "a4j_recovery_" + suffix
    restored = "a4j_restore_" + suffix
    root = Path(__file__).resolve().parents[1]
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config(str(root / "backend/alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            with Session(bind=connection) as session:
                repository = Repository(session, Principal("recovery", "synthetic", "user"))
                conversation = repository.conversation("虚构备份恢复验证")
                task = repository.task(conversation.id, "purchase", {"synthetic": True})
                identity = str(task.id)
                session.flush()
        archive = docker("pg_dump", "-U", "assistant", "-d", "assistant", "-n", schema, "-Fc")
        docker("createdb", "-U", "assistant", restored)
        docker("pg_restore", "-U", "assistant", "-d", restored, "--exit-on-error", input=archive)
        sql = f"SELECT id FROM {schema}.task WHERE connection_id='recovery'"
        sql += " AND tenant_id='synthetic' AND erp_user_id='user';"
        actual = docker("psql", "-U", "assistant", "-d", restored, "-At", "-c", sql).decode().strip()
        assert actual == identity
        print("备份恢复通过：初始迁移和虚构任务在独立恢复库中一致。")
    finally:
        docker("dropdb", "-U", "assistant", "--if-exists", restored)
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


if __name__ == "__main__":
    main()
