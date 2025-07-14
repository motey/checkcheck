from typing import Annotated, Sequence, List, Type
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import Depends, Security, FastAPI, HTTPException, status, Query, Body, Form
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from typing import Annotated

from fastapi import Depends, APIRouter
from checkcheckserver.api.paginator import (
    PaginatedResponse,
    create_query_params_class,
    QueryParamsInterface,
)

from checkcheckserver.db.user import (
    User,
    UserCRUD,
    UserCreate,
    UserUpdate,
    UserUpdateByUser,
    UserUpdateByAdmin,
)
from checkcheckserver.db.user_auth import (
    UserAuth,
    UserAuthCreate,
    UserAuthUpdate,
    UserAuthCRUD,
    AllowedAuthSchemeType,
)
from checkcheckserver.api.auth.security import (
    user_is_admin,
    user_is_usermanager,
    get_current_user,
)
from checkcheckserver.api.auth.security import (
    NEEDS_ADMIN_API_INFO,
    NEEDS_USERMAN_API_INFO,
)


from checkcheckserver.config import Config

config = Config()

from checkcheckserver.log import get_logger

log = get_logger()


fast_api_user_manage_router: APIRouter = APIRouter()


@fast_api_user_manage_router.post(
    "/user",
    response_model=User,
    name="Create local user",
    description=f"Creates a new user in the local user database. {NEEDS_USERMAN_API_INFO}",
)
async def create_user(
    user_create: Annotated[
        UserCreate, Body(description="A json body with the user details")
    ],
    user_password: Annotated[
        str,
        Query(
            description="The password for the created user. If non is defined the user will be created but not able to login until an admin user defines a password.",
        ),
    ] = None,
    current_user_is_usermanager: bool = Security(user_is_usermanager),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
) -> User:
    wrong_login_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect user_name or password",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not current_user_is_usermanager:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing role",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_create: User = await user_crud.create(
        user_create,
        raise_custom_exception_if_exists=HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User allready exists",
            headers={"WWW-Authenticate": "Bearer"},
        ),
    )
    if user_password:
        user_auth: UserAuth = await user_auth_crud.create(
            UserAuthCreate(
                user_id=user_create.id,
                auth_source_type=AllowedAuthSchemeType.basic,
                basic_password=user_password,
            )
        )
    return user_create


UserQueryParams: Type[QueryParamsInterface] = create_query_params_class(User)


@fast_api_user_manage_router.get(
    "/user",
    response_model=PaginatedResponse[User],
    description=f"Get account data from a user by its id.  {NEEDS_USERMAN_API_INFO}",
)
async def list_users(
    incl_deactivated: bool = Query(
        default=False, description="Also list deactivated users."
    ),
    is_user_manager: bool = Security(user_is_usermanager),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    pagination: QueryParamsInterface = Depends(UserQueryParams),
) -> PaginatedResponse[User]:
    users = await user_crud.list(show_deactivated=incl_deactivated)
    return PaginatedResponse(
        total_count=await user_crud.count(
            show_deactivated=incl_deactivated,
        ),
        offset=pagination.offset,
        count=len(users),
        items=users,
    )


@fast_api_user_manage_router.get(
    "/user/{user_id}",
    response_model=User,
    description=f"Get account data from a user by its id. {NEEDS_USERMAN_API_INFO}",
)
async def get_user(
    user_id: uuid.UUID,
    current_user: bool = Security(user_is_usermanager),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
) -> User:
    return await user_crud.get(user_id)


@fast_api_user_manage_router.patch(
    "/user/{user_id}",
    response_model=User,
    description=f"Get account data from a user by its id. {NEEDS_USERMAN_API_INFO}",
)
async def update_user(
    user_id: uuid.UUID,
    patched_user: Annotated[
        UserUpdateByAdmin, Body(description="The user object with changed data")
    ],
    current_user_is_user_manager: bool = Security(user_is_usermanager),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
) -> User:
    return await user_crud.update(user_update=patched_user, user_id=user_id)


@fast_api_user_manage_router.put(
    "/user/{user_id}/password",
    response_model=User,
    description=f"Set a local users password. If the user is provisioned via an external OpenID Connect provider the user will now be able to also login with basic login with this password.  {NEEDS_USERMAN_API_INFO}",
)
async def set_user_password(
    user_id: uuid.UUID,
    new_password: str = Form(),
    new_password_repeated: str = Form(
        description="For good measure we require the password twice to mitiage typos.",
    ),
    current_user_is_user_manager: bool = Security(user_is_usermanager),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
) -> bool:
    if new_password != new_password_repeated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new password and repeated new password do not match",
        )
    user = await user_crud.get(
        user_id=user_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ),
    )
    user_auth_pw = await user_auth_crud.get_basic_auth_source_by_user_id(user_id)

    if user_auth_pw is None:
        log.debug(f"First time set pw for user '{user.user_name}' {new_password}")
        # lets create a userAuth with the new password
        user_auth_create = UserAuthCreate(
            user_id=user_id,
            auth_source_type=AllowedAuthSchemeType.basic,
            basic_password=new_password,
        )
        user_auth_pw: UserAuth = await user_auth_crud.create(user_auth_create)
    else:
        user_auth_update = UserAuthUpdate(basic_password=new_password)
        user_auth_pw = await user_auth_crud.update(
            user_auth_update=user_auth_update, id_=user_auth_pw.id
        )
    return user
