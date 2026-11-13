from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.erp.reads import PositiveID

Number = Annotated[StrictStr, Field(pattern=r"^[0-9]{1,9}(\.[0-9]{1,6})?$")]


class LineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extend_id: PositiveID
    quantity: Number | None = None
    unit: str | None = Field(default=None, max_length=40)
    unit_price: Number | None = None


class SupplierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: PositiveID
    name: str = Field(min_length=1, max_length=160)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[LineInput] = Field(default_factory=list, max_length=20)
    supplier: SupplierInput | None = None
    order_date: str | None = None
    remark: str = Field(default="", max_length=500)
    unsupported_conditions: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )

    @field_validator("order_date")
    @classmethod
    def valid_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("requires ISO date")
        return value


class EditDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: UUID
    expected_version: int = Field(gt=0, strict=True)
    patch: dict


class CreateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: UUID
    task_id: UUID | None = None
    input: DraftInput


class VersionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0, strict=True)


def money(value):
    return format(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


class DraftAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_task: bool = False
    expected_version: int = Field(ge=0, strict=True)
    input: DraftInput


class ConfirmDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    version: int = Field(gt=0, strict=True)
    challenge: str = Field(min_length=32, max_length=128)


class ReopenDraft(VersionCommand):
    command_id: UUID
