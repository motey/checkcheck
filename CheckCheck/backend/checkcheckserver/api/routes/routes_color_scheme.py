from typing import Annotated, Sequence, List, Type, Optional
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import (
    Depends,
    Security,
    HTTPException,
    status,
    Query,
    Body,
    Form,
    Path,
    Response,
)


from fastapi import Depends, APIRouter


from checkcheckserver.db.user import User

from checkcheckserver.api.auth.security import (
    user_is_admin,
    user_is_usermanager,
    get_current_user,
)

from checkcheckserver.api.access import (
    user_has_checklist_access,
    UserChecklistAccess,
)
from checkcheckserver.api.paginator import (
    PaginatedResponse,
    create_query_params_class,
    QueryParamsInterface,
)
from checkcheckserver.log import get_logger
from checkcheckserver.config import Config

config = Config()


from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme
from checkcheckserver.db.checklist_color_scheme import ChecklistColorSchemeCRUD

log = get_logger()


fast_api_color_scheme_router: APIRouter = APIRouter()

# ColorSchemeQueryParams: Type[QueryParamsInterface] = create_query_params_class(
#    ChecklistColorScheme, default_order_by_attr="id"
# )


@fast_api_color_scheme_router.get(
    "/color",
    response_model=List[ChecklistColorScheme],
    description=f"List all available color schemes",
)
async def list_colors(
    color_scheme_crud: ChecklistColorSchemeCRUD = Depends(
        ChecklistColorSchemeCRUD.get_crud
    ),
    current_user: User = Depends(get_current_user),
) -> List[ChecklistColorScheme]:
    colors = await color_scheme_crud.list()
    return colors
