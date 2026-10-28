from typing import Self

from pydantic import Field, computed_field, model_validator

from app.schemas.common import InputSchema


class PageParams(InputSchema):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class Page[T](PageParams):
    items: list[T]
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_item_count(self) -> Self:
        if len(self.items) > self.page_size:
            raise ValueError("Item count cannot exceed page_size")

        available = max(self.total - (self.page - 1) * self.page_size, 0)
        if len(self.items) > available:
            raise ValueError("Item count exceeds the records available on this page")

        return self

    # Pydantic supports this decorator combination; mypy needs this targeted ignore.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size
