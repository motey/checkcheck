from typing import Optional, Generic, TypeVar, List, Annotated, Literal, Callable, Type
import inspect
from pydantic import BaseModel, Field
from fastapi import Query
from sqlmodel import desc
from checkcheckserver.config import Config

from pydantic import BaseModel
from sqlmodel.sql import expression as sqlEpression

config = Config()

from checkcheckserver.log import get_logger

log = get_logger()

from checkcheckserver.model._base_model import BaseTable

GenericCheckCheckModel = TypeVar("GenericCheckCheckModel")


# https://docs.pydantic.dev/latest/concepts/models/#generic-models
class PaginatedResponse(BaseModel, Generic[GenericCheckCheckModel]):
    total_count: int = Field(
        description="Total number of items in the database.",
        examples=[300],
    )
    offset: Optional[int] = Field(
        default=0,
        description="Starting position index of the returned items in the dataset.",
        examples=[299],
    )
    count: int = Field(
        description="Number of items returned in the response", examples=[1]
    )
    items: List[GenericCheckCheckModel] = Field(
        description=f"List of items returned in the response following given criteria"
    )


class QueryParamsInterface:
    """Placeholder class for a the dynamic generated class from create_query_params_class()

    Raises:
        NotImplementedError: _description_

    Returns:
        _type_: _description_
    """

    offset: int = 0
    limit: Optional[int] = 100
    order_by: Optional[str] = None
    order_desc: bool = False
    non_sortable_attributes: List[str] = ["ai_dataversion_id"]

    def __init__(offset: Optional[int], limit: int, order_by: str, order_desc: bool):
        raise NotImplementedError(
            "This is just a placeholder class to make code completion work. check 'create_query_params_class' for the actuall class."
        )

    def order(self, items: List, reverse: bool = False) -> List:
        if self.order_by:
            return sorted(
                items, key=lambda x: getattr(x, self.order_by), reverse=reverse
            )
        else:
            return items

    def append_to_query(
        self,
        sqlmodel_query: sqlEpression.Select,
        ignore_limit: bool = False,
        ignore_order_by: bool = False,
    ):
        sqlmodel_query = sqlmodel_query.offset(self.offset)
        if self.limit and not ignore_limit:
            sqlmodel_query = sqlmodel_query.limit(self.limit)
        if (
            hasattr(self, "order_by")
            and self.order_by is not None
            and not ignore_order_by
        ):
            order_field = self.order_by
            if self.order_desc:
                order_field = desc(self.order_by)
            sqlmodel_query = sqlmodel_query.order_by(order_field)
        return sqlmodel_query


def create_query_params_class(
    base_class: BaseTable,
    default_offset: int = QueryParamsInterface.offset,
    default_limit: int = QueryParamsInterface.limit,
    default_order_by_attr: str = None,
    non_sortable_attributes: List[str] = None,
    no_ordering: bool = False,
) -> Type[QueryParamsInterface]:
    if non_sortable_attributes is None:
        non_sortable_attributes = []
    non_sortable_attributes = list(
        set(
            non_sortable_attributes
            + QueryParamsInterface.non_sortable_attributes.copy()
        )
    )
    model_order_by_attributes = tuple(
        [
            attr
            for attr in base_class.model_fields.keys()
            if attr not in non_sortable_attributes and not attr.endswith("_ref")
        ]
    )

    init_annotations = {
        "offset": Annotated[
            Optional[int],
            Query(
                description="Specify the starting point for result sets/list",
            ),
        ],
        "limit": Annotated[
            Optional[int],
            Query(
                description="Specify the max amount of result items",
            ),
        ],
        "order_by": Annotated[
            Literal[model_order_by_attributes],
            Query(description="Order the result set by this attribute"),
        ],
        "order_desc": Annotated[bool, Query(description="Flip the sorting order")],
    }

    if no_ordering or len(model_order_by_attributes) in [0, 1]:

        def __init__func_wrapper(self, offset, limit):
            self.offset = offset
            self.limit = limit

        init_func: Callable = lambda self, offset, limit: __init__func_wrapper(
            self, offset, limit
        )
        del init_annotations["order_by"]
        del init_annotations["order_desc"]
        init_default = (default_offset, default_limit)
    else:

        def __init__func_wrapper(self, offset, limit, order_by, order_desc):
            self.offset = offset
            self.limit = limit
            self.order_by = order_by
            self.order_desc = order_desc

        init_func: Callable = (
            lambda self, offset, limit, order_by, order_desc: __init__func_wrapper(
                self, offset, limit, order_by, order_desc
            )
        )
        init_default = (default_offset, default_limit, default_order_by_attr, False)
    init_func.__defaults__ = init_default
    init_func.__annotations__ = init_annotations

    class_attrs = {
        "__init__": init_func,
        "order": QueryParamsInterface.order,
        "append_to_query": QueryParamsInterface.append_to_query,
    }
    return type(f"{base_class.__name__}QueryParams", (), class_attrs)
