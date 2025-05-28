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


from checkcheckserver.config import Config

config = Config()

from checkcheckserver.log import get_logger

log = get_logger()


fast_api_user_self_service_router: APIRouter = APIRouter()


@fast_api_user_self_service_router.get(
    "/user/me",
    response_model=User,
    description="Get account data from the current user",
)
async def get_myself(
    current_user: User = Depends(get_current_user),
) -> User:
    return current_user


@fast_api_user_self_service_router.patch(
    "/user/me",
    response_model=User,
    description="Update my user account data.",
)
async def update_myself(
    patched_user: UserUpdateByUser,
    current_user: User = Security(get_current_user),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
) -> User:
    return await user_crud.update(user_update=patched_user, user_id=current_user.id)


@fast_api_user_self_service_router.put(
    "/user/me/password",
    response_model=bool,
    description="Set my password if i am a 'local' user. If my account was provisioned via an external OpenID Connect provider this does nothing except the return value will be `false`.",
)
async def set_my_password(
    old_password: str = Form(default=None),
    new_password: str = Form(default=None),
    new_password_repeated: str = Form(
        default=None,
        description="For good measure we require the password twice to mitiage typos.",
    ),
    current_user: User = Security(get_current_user),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
) -> bool:
    if new_password != new_password_repeated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new password and repeated new password do not match",
        )

    old_user_auth: UserAuth = await user_auth_crud.get_basic_auth_source_by_user_id(
        current_user.id
    )
    if old_user_auth is None:
        return False
    old_user_auth.verify_password(
        old_password,
        raise_exception_if_wrong=HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify authorization",
        ),
    )
    updated_user_auth = UserAuthUpdate(basic_password=new_password)
    await user_auth_crud.update(updated_user_auth, id_=old_user_auth.id)
    return True
