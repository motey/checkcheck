from typing import Annotated, Sequence, List, Type
from datetime import datetime, timedelta, timezone


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

from checkcheckserver.api.auth.security import get_current_user
from fastapi import Depends, APIRouter


from checkcheckserver.db.user import User


from checkcheckserver.config import Config
from checkcheckserver.db.user import UserCRUD
from checkcheckserver.model.healthcheck import HealthCheck, HealthCheckReport
from checkcheckserver.db.healthcheck import HealthcheckRead


config = Config()

from checkcheckserver.log import get_logger

log = get_logger()


fast_api_healthcheck_router: APIRouter = APIRouter()


@fast_api_healthcheck_router.get(
    "/health",
    response_model=HealthCheck,
    description=f"Get the basic health state of the system.",
)
async def get_health_status(
    health_read: HealthcheckRead = Depends(HealthcheckRead.get_crud),
) -> HealthCheck:
    return await health_read.get()


@fast_api_healthcheck_router.get(
    "/health/report",
    response_model=HealthCheckReport,
    description=f"Get a more detailed health report of the system.",
)
async def get_health_status(
    user: UserCRUD = Security(get_current_user),
    health_read: HealthcheckRead = Depends(HealthcheckRead.get_crud),
) -> HealthCheckReport:
    return await health_read.get_report()
