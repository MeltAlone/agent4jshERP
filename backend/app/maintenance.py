"""显式运维命令：清理终态历史正文/检查点，保留审计关联和活跃任务。"""

from datetime import timedelta

from sqlalchemy import delete, exists, text
from sqlmodel import Session, select

from app.models import Audit, Conversation, Event, Message, Run, Task, now
from app.orchestration.checkpoints import checkpoint_store
from app.repository import Principal, Repository


def cleanup(engine, settings, *, instant=None):
    instant = instant or now()
    counts = {"events": 0, "messages": 0, "checkpoints": 0, "audits": 0}
    # 与 worker 相同的会话行锁；未关闭任务及活跃运行整组保留。
    with Session(engine) as database, database.begin():
        database.execute(text("SELECT pg_advisory_xact_lock(41284927)"))
        for conversation in database.exec(select(Conversation).with_for_update()).all():
            principal = Principal(
                conversation.connection_id, conversation.tenant_id, conversation.erp_user_id
            )
            repo = Repository(database, principal)
            tasks = database.exec(
                repo.query(Task).where(Task.conversation_id == conversation.id)
            ).all()
            runs = database.exec(
                repo.query(Run).where(Run.conversation_id == conversation.id)
            ).all()
            if any(task.status not in {"completed", "cancelled"} for task in tasks) or any(
                run.status in {"queued", "running"} for run in runs
            ):
                continue
            old_events = database.exec(
                repo.query(Event).where(
                    Event.conversation_id == conversation.id,
                    Event.created_at < instant - timedelta(days=settings.event_retention_days),
                )
            ).all()
            if old_events:
                conversation.event_floor = max(
                    conversation.event_floor, max(event.sequence for event in old_events)
                )
                database.add(conversation)
                for event in old_events:
                    database.delete(event)
                counts["events"] += len(old_events)
            latest = max(
                [
                    conversation.created_at,
                    *(task.created_at for task in tasks),
                    *(run.completed_at or run.created_at for run in runs),
                ]
            )
            if latest >= instant - timedelta(days=settings.history_retention_days):
                continue
            for run in runs:
                key = run.budget.get("checkpoint_thread")
                if key:
                    with checkpoint_store(engine) as saver:
                        saver.delete_thread(key)
                    counts["checkpoints"] += 1
                    run.budget = {k: v for k, v in run.budget.items() if k != "checkpoint_thread"}
                    database.add(run)
            messages = database.exec(
                repo.query(Message).where(Message.conversation_id == conversation.id)
            ).all()
            for message in messages:
                if message.provenance.get("source") == "expired":
                    continue
                message.content = "此消息已超过历史保留期"
                message.cards, message.page_context, message.provenance = (
                    [],
                    {},
                    {"source": "expired"},
                )
                database.add(message)
                counts["messages"] += 1
            for task in tasks:
                task.context, task.provenance = {"expired": True}, {"source": "state"}
                database.add(task)
        active_task = exists(
            select(Task.id).where(
                Task.id == Audit.task_id, Task.status.not_in(["completed", "cancelled"])
            )
        )
        active_run = exists(
            select(Run.id).where(Run.id == Audit.run_id, Run.status.in_(["queued", "running"]))
        )
        result = database.execute(
            delete(Audit).where(
                ~active_task,
                ~active_run,
                Audit.created_at < instant - timedelta(days=settings.audit_retention_days),
            )
        )
        counts["audits"] = result.rowcount
    return counts


if __name__ == "__main__":
    from app.config import load_settings
    from app.database import create_database_engine

    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        print(cleanup(engine, settings))
    finally:
        engine.dispose()
