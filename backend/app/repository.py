from dataclasses import asdict, dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models import Conversation, Owned, Task


@dataclass(frozen=True)
class Principal:
    connection_id: str
    tenant_id: str
    erp_user_id: str

    def fields(self) -> dict[str, str]:
        return asdict(self)


class ResourceNotFound(Exception):
    pass


class Repository:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def query(self, model: type[Owned]):
        return select(model).where(
            *(getattr(model, field) == value for field, value in self.principal.fields().items())
        )

    def get(self, model: type[Owned], identity: UUID, *, lock: bool = False):
        statement = self.query(model).where(model.id == identity)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        result = self.session.exec(statement).first()
        if result is None:
            raise ResourceNotFound
        return result

    def conversation(self, title: str = "新对话") -> Conversation:
        result = Conversation(**self.principal.fields(), title=title)
        self.session.add(result)
        self.session.flush()
        return result

    def task(
        self, conversation_id: UUID, kind: str, context: dict, *, provenance: dict | None = None
    ) -> Task:
        self.get(Conversation, conversation_id)
        result = Task(
            **self.principal.fields(),
            conversation_id=conversation_id,
            kind=kind,
            context=context,
            provenance=provenance if provenance is not None else {"source": "user"},
        )
        self.session.add(result)
        self.session.flush()
        return result
