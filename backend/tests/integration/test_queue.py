from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.events import read_events
from app.models import Job, Message, Run, now
from app.queue import LeaseLost, claim, complete
from app.repository import Principal, Repository
from app.runs import ConversationBusy, MessageConflict, submit_message


def prepared(engine):
    principal = Principal("queue", uuid4().hex, "synthetic-user")
    with Session(engine) as session, session.begin():
        conversation = Repository(session, principal).conversation()
        identity = conversation.id
    return principal, identity


def send(engine, principal, conversation_id, message_id, content="虚构队列检查"):
    with Session(engine) as session, session.begin():
        run = submit_message(
            Repository(session, principal),
            conversation_id,
            message_id,
            content,
            job_kind="test_probe",
        )
        return run.id


def test_duplicate_and_busy_are_deterministic_under_concurrency(pg_engine):
    principal, conversation_id = prepared(pg_engine)
    message_id = uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: send(pg_engine, principal, conversation_id, message_id), range(4))
        )
    assert len(set(results)) == 1
    with pytest.raises(ConversationBusy):
        send(pg_engine, principal, conversation_id, uuid4())
    with pytest.raises(MessageConflict):
        send(pg_engine, principal, conversation_id, message_id, "changed")
    with Session(pg_engine) as session:
        repository = Repository(session, principal)
        assert len(session.exec(repository.query(Job)).all()) == 1
        assert len(session.exec(repository.query(Message)).all()) == 1
        assert len(read_events(repository, conversation_id, 0)) == 1
    lease = claim(pg_engine)
    complete(pg_engine, lease)


def test_concurrent_workers_claim_once_and_outbox_commits_with_state(pg_engine):
    principal, conversation_id = prepared(pg_engine)
    send(pg_engine, principal, conversation_id, uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        leases = list(pool.map(lambda _: claim(pg_engine), range(4)))
    leases = [lease for lease in leases if lease]
    assert len(leases) == 1
    complete(pg_engine, leases[0])
    with pytest.raises(LeaseLost):
        complete(pg_engine, leases[0])
    with Session(pg_engine) as session:
        repository = Repository(session, principal)
        events = read_events(repository, conversation_id, 0)
        assert [event.kind for event in events] == ["run.queued", "run.completed"]
        assert [event.sequence for event in events] == [1, 2]
        assert repository.get(Run, leases[0].run_id).status == "completed"


def test_expired_lease_is_reclaimed_and_old_worker_cannot_publish(pg_engine):
    principal, conversation_id = prepared(pg_engine)
    send(pg_engine, principal, conversation_id, uuid4())
    old = claim(pg_engine)
    with Session(pg_engine) as session, session.begin():
        job = session.get(Job, old.job_id)
        job.lease_until = now() - timedelta(seconds=1)
        session.add(job)
    replacement = claim(pg_engine)
    assert replacement.job_id == old.job_id and replacement.token != old.token
    with pytest.raises(LeaseLost):
        complete(pg_engine, old)
    complete(pg_engine, replacement)


def test_transaction_rollback_does_not_publish_or_lose_message(pg_engine):
    principal, conversation_id = prepared(pg_engine)
    with Session(pg_engine) as session:
        repository = Repository(session, principal)
        submit_message(repository, conversation_id, uuid4(), "synthetic", job_kind="test_probe")
        session.rollback()
        assert session.exec(repository.query(Run)).all() == []
        assert read_events(repository, conversation_id, 0) == []


def test_retry_exhaustion_is_persisted_not_silently_dropped(pg_engine):
    principal, conversation_id = prepared(pg_engine)
    send(pg_engine, principal, conversation_id, uuid4())
    with Session(pg_engine) as session, session.begin():
        job = session.exec(select(Job).where(Job.tenant_id == principal.tenant_id)).one()
        job.attempts = job.max_attempts
        session.add(job)
    lease = claim(pg_engine)
    assert lease.exhausted
    complete(pg_engine, lease)
    with Session(pg_engine) as session:
        run = session.get(Run, lease.run_id)
        assert (run.status, run.error_code) == ("failed", "retry_exhausted")


def test_real_process_exit_after_claim_and_new_process_recovery(pg_engine):
    import json
    import os
    import subprocess
    import sys

    from sqlalchemy import text

    principal, conversation_id = prepared(pg_engine)
    run_id = send(pg_engine, principal, conversation_id, uuid4())
    with pg_engine.connect() as connection:
        schema = connection.execute(text("select current_schema()")).scalar_one()
    environment = {**os.environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[2])}
    inputs = json.dumps(
        {"url": pg_engine.url.render_as_string(hide_password=False), "schema": schema}
    )
    setup = (
        "import json,sys; from sqlalchemy import create_engine; "
        "x=json.load(sys.stdin); "
        "e=create_engine(x['url'],connect_args={'options':'-csearch_path='+x['schema']}); "
    )
    dead = subprocess.run(
        [
            sys.executable,
            "-c",
            setup + "from app.queue import claim; claim(e); import os; os._exit(0)",
        ],
        input=inputs,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert dead.returncode == 0
    with Session(pg_engine) as session, session.begin():
        job = session.exec(select(Job).where(Job.run_id == run_id)).one()
        assert job.status == "leased"
        assert session.get(Run, run_id).status == "queued"
        job.lease_until = now() - timedelta(seconds=1)
        session.add(job)
    recovered = subprocess.run(
        [sys.executable, "-c", setup + "from app.worker import run_once; assert run_once(e)"],
        input=inputs,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert recovered.returncode == 0
    with Session(pg_engine) as session:
        assert session.get(Run, run_id).status == "completed"
        assert len(read_events(Repository(session, principal), conversation_id, 0)) == 2
