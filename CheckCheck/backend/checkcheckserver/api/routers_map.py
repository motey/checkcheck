from fastapi import APIRouter, FastAPI


def mount_fast_api_routers(fastapi_app: FastAPI):

    ### Health
    from checkcheckserver.api.routes.routes_healthcheck import (
        fast_api_healthcheck_router,
    )

    fastapi_app.include_router(
        fast_api_healthcheck_router, tags=["Health"], prefix="/api"
    )

    ### Public client bootstrap config (unauthenticated feature flags)
    from checkcheckserver.api.routes.routes_public_config import (
        fast_api_public_config_router,
    )

    fastapi_app.include_router(
        fast_api_public_config_router, tags=["Config"], prefix="/api"
    )

    ### AUTH STUFF
    from checkcheckserver.api.routes.routes_auth import (
        fast_api_auth_base_router,
    )

    fastapi_app.include_router(fast_api_auth_base_router, tags=["Auth"], prefix="/api")

    # Self-service routes (/user/me, /user/me/api-keys, …) must be registered
    # BEFORE management routes (/user/{user_id}, …) so FastAPI matches the
    # literal "me" segment first instead of treating it as a UUID path param.
    ### USER SELF SERVICE
    from checkcheckserver.api.routes.routes_user import (
        fast_api_user_self_service_router,
    )

    fastapi_app.include_router(
        fast_api_user_self_service_router, tags=["User"], prefix="/api"
    )

    ### USER NOTIFICATIONS (self-service /user/me/notifications — register with the
    ### other "me" routes, before /user/{user_id} management routes)
    from checkcheckserver.api.routes.routes_notification import (
        fast_api_notification_router,
    )

    fastapi_app.include_router(
        fast_api_notification_router, tags=["Notifications"], prefix="/api"
    )

    ### USER MANAGEMENT
    from checkcheckserver.api.routes.routes_user_management import (
        fast_api_user_manage_router,
    )

    fastapi_app.include_router(
        fast_api_user_manage_router, tags=["User Admin"], prefix="/api"
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

    from checkcheckserver.api.routes.routes_checklist_share import (
        fast_api_checklist_share_router,
    )

    fastapi_app.include_router(
        fast_api_checklist_share_router, tags=["Checklist Sharing"], prefix="/api"
    )

    from checkcheckserver.api.routes.routes_checklist_public import (
        fast_api_checklist_public_router,
    )

    fastapi_app.include_router(
        fast_api_checklist_public_router,
        tags=["Checklist Public Share"],
        prefix="/api",
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

    from checkcheckserver.api.routes.routes_checklist_label import (
        fast_api_checklist_label_router,
    )

    fastapi_app.include_router(
        fast_api_checklist_label_router,
        tags=["Checklist Labels"],
        prefix="/api",
    )

    from checkcheckserver.api.routes.routes_sync_notification import (
        fast_api_sse_sync_router,
    )

    fastapi_app.include_router(
        fast_api_sse_sync_router, tags=["Client Sync"], prefix="/api"
    )

    from checkcheckserver.api.routes.routes_changes import (
        fast_api_changes_router,
    )

    fastapi_app.include_router(
        fast_api_changes_router, tags=["Client Sync"], prefix="/api"
    )

    from checkcheckserver.api.routes.routes_webclient import fast_api_webclient_router

    fastapi_app.include_router(fast_api_webclient_router, tags=["WebClient"])
