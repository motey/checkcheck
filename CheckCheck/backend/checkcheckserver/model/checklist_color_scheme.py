from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated, Dict
import enum
from pydantic import (
    validate_email,
    field_validator,
    model_validator,
    StringConstraints,
    ValidationInfo,
)
from fastapi import Depends
from typing import Optional
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, desc
from datetime import datetime, timezone, date
import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import (
    BaseTable,
    TimestampedModel,
)


log = get_logger()
config = Config()


class ChecklistColorScheme(BaseTable, table=True):
    __tablename__ = "checklist_color_scheme"
    id: str = Field(
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
    )
    display_name: str = Field(description="Name of the color.")
    sort_order: Optional[int] = Field(
        description="Reserverd for later use. Color order not implemented yet",
        default=None,
    )
    backgroundcolor_dark_hex: str = Field()
    backgroundcolor_dark_tailwind: str = Field()
    backgroundcolor_light_hex: str = Field()
    backgroundcolor_light_tailwind: str = Field()

    dark_text_in_dark_mode: bool = Field()
    dark_text_in_light_mode: bool = Field()
