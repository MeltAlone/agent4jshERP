from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(UTC)


def timestamp():
    return Field(default_factory=now, sa_type=DateTime(timezone=True), nullable=False)


def owner_unique():
    return UniqueConstraint("id", "connection_id", "tenant_id", "erp_user_id")


def owned_reference(local: str, parent: str):
    return ForeignKeyConstraint(
        [local, "connection_id", "tenant_id", "erp_user_id"],
        [f"{parent}.id", f"{parent}.connection_id", f"{parent}.tenant_id", f"{parent}.erp_user_id"],
    )


class Owned(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    connection_id: str = Field(max_length=48, nullable=False, index=True)
    tenant_id: str = Field(max_length=64, nullable=False)
    erp_user_id: str = Field(max_length=64, nullable=False)
    created_at: datetime = timestamp()


class AssistantSession(Owned, table=True):
    __tablename__ = "assistant_session"
    __table_args__ = (owner_unique(),)
    display_name: str = Field(default="", max_length=160)
    permission_fingerprint: str = Field(default="", max_length=64)
    cookie_hash: str = Field(max_length=64, unique=True, index=True)
    encrypted_token: str
    csrf_hash: str = Field(max_length=64)
    generation: UUID = Field(default_factory=uuid4)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), nullable=False)
    last_seen_at: datetime = timestamp()
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class Conversation(Owned, table=True):
    __tablename__ = "conversation"
    __table_args__ = (owner_unique(),)
    title: str = Field(default="新对话", max_length=160)
    next_sequence: int = 0
    event_floor: int = 0


class Task(Owned, table=True):
    __tablename__ = "task"
    __table_args__ = (owner_unique(), owned_reference("conversation_id", "conversation"))
    conversation_id: UUID
    kind: str = Field(max_length=32)
    status: str = Field(default="needs_input", max_length=32)
    context: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    provenance: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    resource_version: int = 1
    blocked_reason: str | None = Field(default=None, max_length=64)


class Message(Owned, table=True):
    __tablename__ = "message"
    __table_args__ = (
        owner_unique(),
        owned_reference("conversation_id", "conversation"),
        owned_reference("task_id", "task"),
        UniqueConstraint("conversation_id", "client_message_id"),
    )
    conversation_id: UUID
    task_id: UUID | None = None
    client_message_id: UUID
    role: str = Field(max_length=16)
    cards: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    content: str
    page_context: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    provenance: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))


class Run(Owned, table=True):
    __tablename__ = "run"
    __table_args__ = (
        owner_unique(),
        owned_reference("conversation_id", "conversation"),
        owned_reference("message_id", "message"),
        UniqueConstraint("message_id"),
        Index(
            "uq_active_conversation_run",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
    conversation_id: UUID
    message_id: UUID
    status: str = Field(default="queued", max_length=32)
    session_id: UUID | None = None
    session_generation: UUID | None = None
    budget: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    error_code: str | None = Field(default=None, max_length=64)
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class Job(Owned, table=True):
    __tablename__ = "job"
    __table_args__ = (
        owner_unique(),
        owned_reference("run_id", "run"),
        UniqueConstraint("run_id", "kind"),
        Index("ix_claimable_jobs", "status", "available_at"),
    )
    run_id: UUID
    kind: str = Field(max_length=32)
    status: str = Field(default="queued", max_length=32)
    available_at: datetime = timestamp()
    lease_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    lease_token: UUID | None = None
    attempts: int = 0
    max_attempts: int = 3


class Event(Owned, table=True):
    __tablename__ = "event"
    __table_args__ = (
        owner_unique(),
        owned_reference("conversation_id", "conversation"),
        owned_reference("task_id", "task"),
        owned_reference("run_id", "run"),
        UniqueConstraint("conversation_id", "sequence"),
    )
    conversation_id: UUID
    task_id: UUID | None = None
    run_id: UUID | None = None
    sequence: int
    schema_version: int = 1
    resource_version: int = 1
    kind: str = Field(max_length=48)
    provenance: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))


class Audit(Owned, table=True):
    __tablename__ = "audit"
    __table_args__ = (
        owner_unique(),
        owned_reference("task_id", "task"),
        owned_reference("run_id", "run"),
    )
    task_id: UUID | None = None
    run_id: UUID | None = None
    action: str = Field(max_length=64)
    result: str = Field(max_length=32)
    correlation_id: UUID = Field(default_factory=uuid4)
    duration_ms: int | None = None
    model_calls: int = 0
    tool_calls: int = 0


class PurchaseDraft(Owned, table=True):
    __tablename__ = "purchase_draft"
    __table_args__ = (
        owner_unique(),
        owned_reference("task_id", "task"),
        UniqueConstraint("task_id"),
        UniqueConstraint("connection_id", "tenant_id", "erp_user_id", "creation_command"),
    )
    task_id: UUID
    creation_command: UUID
    version: int = 1
    status: str = Field(default="editing", max_length=32)
    snapshot: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    input_data: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    content_hash: str = Field(max_length=64)
    provenance: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))


class DraftRevision(Owned, table=True):
    __tablename__ = "draft_revision"
    __table_args__ = (
        owner_unique(),
        owned_reference("draft_id", "purchase_draft"),
        UniqueConstraint("draft_id", "version"),
        UniqueConstraint("draft_id", "command_id"),
    )
    draft_id: UUID
    version: int
    command_id: UUID
    command_hash: str = Field(max_length=64)
    snapshot: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    input_data: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    content_hash: str = Field(max_length=64)


class Confirmation(Owned, table=True):
    __tablename__ = "confirmation"
    __table_args__ = (owner_unique(), owned_reference("draft_id", "purchase_draft"))
    draft_id: UUID
    version: int
    content_hash: str = Field(max_length=64)
    challenge_hash: str = Field(max_length=64, unique=True)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), nullable=False)
    consumed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    invalidated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class Execution(Owned, table=True):
    __tablename__ = "execution"
    __table_args__ = (
        owner_unique(),
        owned_reference("draft_id", "purchase_draft"),
        owned_reference("task_id", "task"),
        UniqueConstraint("draft_id", "version"),
        UniqueConstraint("connection_id", "tenant_id", "erp_number"),
    )
    draft_id: UUID
    task_id: UUID
    version: int
    content_hash: str = Field(max_length=64)
    snapshot: dict = Field(sa_column=Column(JSONB, nullable=False))
    input_data: dict = Field(sa_column=Column(JSONB, nullable=False))
    provenance: dict = Field(sa_column=Column(JSONB, nullable=False))
    confirmation_id: UUID
    request_id: UUID
    session_id: UUID
    session_generation: UUID
    status: str = Field(default="queued", max_length=32)
    erp_number: str | None = Field(default=None, max_length=80)
    request_body: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    dispatch_marked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    error_code: str | None = Field(default=None, max_length=64)
    receipt: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    reconciliation_attempts: int = 0
    reconciliation_round: int = 0
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ExecutionJob(Owned, table=True):
    __tablename__ = "execution_job"
    __table_args__ = (
        owner_unique(),
        owned_reference("execution_id", "execution"),
        UniqueConstraint("execution_id"),
        Index("ix_claimable_execution_jobs", "status", "available_at"),
    )
    execution_id: UUID
    status: str = Field(default="queued", max_length=32)
    available_at: datetime = timestamp()
    lease_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    lease_token: UUID | None = None
    attempts: int = 0
