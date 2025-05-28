from typing import Annotated, Sequence, List, Type, Optional, Tuple
from datetime import datetime, timedelta, timezone
import uuid
import json
import asyncio
from contextlib import asynccontextmanager
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
    Request,
    BackgroundTasks,
)
from fastapi.responses import StreamingResponse


from fastapi import Depends, APIRouter, FastAPI


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
from checkcheckserver.db._session import get_async_session_context


from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme
from checkcheckserver.db.checklist_color_scheme import ChecklistColorSchemeCRUD
from checkcheckserver.db.sync_notification import (
    SyncNotifiationCRUD,
    SyncNotificationPackage,
    SyncNotification,
)

config = Config()

log = get_logger()


fast_api_sse_sync_router: APIRouter = APIRouter()
clients: List[Tuple[asyncio.Queue, User, Request]] = []


#####
# this is a proof of concept.
# Later we need a more scalable solution.
# Reqs/ideas for a later improved implementation are:
# * use redis as message broker/storage instead of a sql table(optionaly?)
# * run as an extra worker process (optionaly?)
#####


@fast_api_sse_sync_router.get(
    "/sync",
    response_model=SyncNotification,
    response_class=StreamingResponse,
    description=f"""A stream endpoint that will send notifications messages on which entities(CheckList or CheckListItem IDs) to update.""",
    responses={
        200: {
            "model": SyncNotification,
            "description": "Example of a single response message. (Code will wont be actually 200 as this is a streaming response.) There can be multiple of these messages.",
        }
    },
)
async def sync_via_server_send_events(
    request: Request,
    background_tasks: BackgroundTasks,
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    current_user: User = Depends(get_current_user),
):
    queue = asyncio.Queue()
    clients.append((queue, current_user, request))
    # background_tasks.add_task(notify_clients) # needs to be started on server start

    async def notification_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                data = await queue.get()
                yield data
        finally:
            clients.remove(queue)

    return StreamingResponse(notification_stream(), media_type="text/event-stream")


async def notify_clients():
    while True:
        await asyncio.sleep(1)
        noti: SyncNotificationPackage | None = None
        async with get_async_session_context() as session:
            async with SyncNotifiationCRUD.crud_context(session) as notification_crud:
                notification_crud: SyncNotifiationCRUD = notification_crud
                noti = await notification_crud.fetch_next_notificaton()
        if noti is None:
            continue
        for queue, user, request in clients:
            if user.id in noti.target_user_ids:
                await queue.put(f"{noti.notification.model_dump_json()}\n\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the background task to process messages
    broadcast_task = asyncio.create_task(notify_clients())

    yield  # Hand over control to the application while running

    # Cleanup: notify all clients to disconnect
    for client in clients:
        await client.put(None)  # Send a "None" to signal closing
    clients.clear()
    broadcast_task.cancel()  # Cancel the background task


fast_api_sse_sync_router.lifespan_context = lifespan
