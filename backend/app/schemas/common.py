from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def normalise_utc(value: datetime) -> datetime:
    """Convert an already timezone-aware timestamp to UTC."""
    return value.astimezone(UTC)


UtcTimestamp = Annotated[AwareDatetime, AfterValidator(normalise_utc)]
PositiveId = Annotated[int, Field(gt=0)]


class InputSchema(BaseModel):
    # Reject unexpected fields rather than silently ignoring them.
    model_config = ConfigDict(extra="forbid")


class ReadSchema(BaseModel):
    # Allow response schemas to read SQLAlchemy object attributes.
    model_config = ConfigDict(from_attributes=True)


class PartialUpdateSchema(InputSchema):
    """Allow omission, but reject explicit null for non-nullable fields."""

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")

        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")

        return self
