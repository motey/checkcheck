from typing import (
    AsyncGenerator,
    List,
    Optional,
    Literal,
    Sequence,
    Annotated,
    Tuple,
    Dict,
)
from pydantic import validate_email, validator, StringConstraints, PositiveInt
from pydantic_core import PydanticCustomError
from fastapi import Depends
import contextlib
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, func, col

import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_color_scheme import (
    ChecklistColorScheme,
)

from checkcheckserver.model.checklist_label import CheckListLabel, CheckListLabelCreate

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class ChecklistLabelCRUD(
    create_crud_base(
        table_model=CheckListLabel,
        read_model=CheckListLabel,
        create_model=CheckListLabelCreate,
        update_model=CheckListLabel,
    )
):
    pass
