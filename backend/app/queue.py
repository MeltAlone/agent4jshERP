from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, or_
from sqlmodel import Session, select

from app.events import append_event
from app.models import Conversation, Job, Run
from app.repository import Principal, Repository


@dataclass(frozen=True)
class Lease:
    job_id: UUID
    run_id: UUID
    token: UUID
    kind: str
    principal: Principal
    exhausted: bool


class LeaseLost(Exception):
    pass


def database_now(session: Session):
    return session.exec(select(func.clock_timestamp())).one()


def claim(engine: Engine, lease_seconds: int = 120) -> Lease | None:
    if not 1 <= lease_seconds <= 300:
        raise ValueError("lease_seconds must be between 1 and 300")
    with Session(engine) as session, session.begin():
        instant = database_now(session)
        job = session.exec(
            select(Job)
            .where(
                Job.available_at <= instant,
                or_(
                    Job.status == "queued", (Job.status == "leased") & (Job.lease_until <= instant)
                ),
            )
            .order_by(Job.available_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if job is None:
            return None
        exhausted = job.attempts >= job.max_attempts
        job.status = "leased"
        job.attempts += 1
        job.lease_token = uuid4()
        job.lease_until = instant + timedelta(seconds=lease_seconds)
        session.add(job)
        return Lease(
            job.id,
            job.run_id,
            job.lease_token,
            job.kind,
            Principal(job.connection_id, job.tenant_id, job.erp_user_id),
            exhausted,
        )


def lock_current_lease(session: Session, lease: Lease):
    repository = Repository(session, lease.principal)
    run = repository.get(Run, lease.run_id)
    repository.get(Conversation, run.conversation_id, lock=True)
    job = repository.get(Job, lease.job_id, lock=True)
    if (
        job.status != "leased"
        or job.lease_token != lease.token
        or job.lease_until <= database_now(session)
    ):
        raise LeaseLost
    return repository, run, job


def heartbeat(engine: Engine, lease: Lease, lease_seconds: int = 120):
    if not 1 <= lease_seconds <= 300:
        raise ValueError("invalid lease duration")
    with Session(engine) as session, session.begin():
        _, _, job = lock_current_lease(session, lease)
        job.lease_until = database_now(session) + timedelta(seconds=lease_seconds)
        session.add(job)


def complete(engine: Engine, lease: Lease, *, error_code: str | None = None):
    """Commit state and outbox together; a stale process cannot publish a completion."""
    with Session(engine) as session, session.begin():
        repository, run, job = lock_current_lease(session, lease)
        if run.status not in {"queued", "running"}:
            raise LeaseLost
        if lease.exhausted:
            error_code = "retry_exhausted"
        run.status = "failed" if error_code else "completed"
        run.error_code = error_code
        run.completed_at = database_now(session)
        job.status = "failed" if error_code else "done"
        job.lease_token = None
        job.lease_until = None
        session.add(run)
        session.add(job)
        append_event(
            repository,
            run.conversation_id,
            "run." + run.status,
            {"status": run.status, "error_code": error_code},
            run_id=run.id,
        )
