from fastapi import APIRouter, FastAPI


def mount_fast_api_routers(fastapi_app: FastAPI):

    ### Health
    from checkcheckserver.api.routes.routes_healthcheck import (
        fast_api_healthcheck_router,
    )

    fastapi_app.include_router(
        fast_api_healthcheck_router, tags=["Health"], prefix="/api"
    )

    ### AUTH STUFF
    from checkcheckserver.api.routes.routes_auth import (
        fast_api_auth_base_router,
    )

    fastapi_app.include_router(fast_api_auth_base_router, tags=["Auth"], prefix="/api")

    ### USER MANAGEMENT
    from checkcheckserver.api.routes.routes_user_management import (
        fast_api_user_manage_router,
    )

    fastapi_app.include_router(
        fast_api_user_manage_router, tags=["User Admin"], prefix="/api"
    )

    ### USER SELF SERVICE
    from checkcheckserver.api.routes.routes_user import (
        fast_api_user_self_service_router,
    )

    fastapi_app.include_router(
        fast_api_user_self_service_router, tags=["User"], prefix="/api"
    )

    ### APP - Business logic

    from checkcheckserver.api.routes.routes_color_scheme import (
        fast_api_color_scheme_router,
    )

    fastapi_app.include_router(
        fast_api_color_scheme_router, tags=["Checklist Color Schemes"], prefix="/api"
    )

    from checkcheckserver.api.routes.routes_checklist import fast_api_checklist_router

    fastapi_app.include_router(
        fast_api_checklist_router, tags=["Checklist"], prefix="/api"
    )

    from checkcheckserver.api.routes.routes_checklist_position import (
        fast_api_checklist_position_router,
    )

    fastapi_app.include_router(
        fast_api_checklist_position_router,
        tags=["Checklist Positioning"],
        prefix="/api",
    )

    from checkcheckserver.api.routes.routes_checklist_item import (
        fast_api_checklist_item_router,
    )

    fastapi_app.include_router(
        fast_api_checklist_item_router, tags=["Checklist Items"], prefix="/api"
    )

    from checkcheckserver.api.routes.routes_checklist_item_pos import (
        fast_api_checklist_item_pos_router,
    )

    fastapi_app.include_router(
        fast_api_checklist_item_pos_router,
        tags=["Checklist Items Positioning"],
        prefix="/api",
    )

    from checkcheckserver.api.routes.routes_checklist_item_state import (
        fast_api_checklist_item_state_router,
    )

    fastapi_app.include_router(
        fast_api_checklist_item_state_router,
        tags=["Checklist Items State"],
        prefix="/api",
    )

    from checkcheckserver.api.routes.routes_sync_notification import (
        fast_api_sse_sync_router,
    )

    fastapi_app.include_router(
        fast_api_sse_sync_router, tags=["Client Sync"], prefix="/api"
    )

    from checkcheckserver.api.routes.routes_webclient import fast_api_webclient_router

    fastapi_app.include_router(fast_api_webclient_router, tags=["WebClient"])
