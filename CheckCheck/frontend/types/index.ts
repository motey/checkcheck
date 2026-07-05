import type { components } from "#open-fetch-schemas/checkapi";
import type { PropType } from "vue";
import type { CheckapiRequestBody, CheckapiResponse } from "#open-fetch";
// ofetch's own ResponseType (NOT the DOM-lib global of the same name) — the
// augmentation below must restate FetchOptions' type parameters identically, so
// the constraint has to point at this exact symbol.
import type { ResponseType as OFetchResponseType } from "ofetch";

// Per-call opt-out for the global error toast in `plugins/api.ts` (F7). Call
// sites that own their own error UX pass `skipErrorToast: true` so a handled 4xx
// doesn't stack a generic "Error <code>" toast on top of their friendly message.
// ofetch preserves unknown option keys into `context.options` at runtime; this
// augmentation just makes the typed `$checkapi` accept the key.
declare module "ofetch" {
    interface FetchOptions<R extends OFetchResponseType = OFetchResponseType, T = any> {
        skipErrorToast?: boolean
    }
}

export { }
declare global {
    // Auth
    type AuthSchemeInfo = components["schemas"]["AuthSchemeInfo"]
    type BasicLoginBody = components["schemas"]["BasicLoginBody"]

    // CheckList Types
    type CheckListType = components["schemas"]["CheckListApiWithSubObj"]
    type CheckListCreateType = components["schemas"]["CheckListApiCreate"]
    type CheckListUpdateType = components["schemas"]["CheckListUpdate"]
    type CheckListsPageType = CheckapiResponse<"list_checklists_api_checklist_get">
    type CheckListCountsType = components["schemas"]["CheckListCountsPublic"]
    // CheckListPosition
    type CheckListPositionType = components["schemas"]["CheckListPositionPublicWithoutChecklistID"]
    type CheckListPositionUpdateType = components["schemas"]["CheckListPositionUpdate"]
    // CheckListItems Types
    type CheckListItemType = components["schemas"]["CheckListItemRead"]
    type CheckListItemCreateType = components["schemas"]["CheckListItemCreateAPI"]
    type CheckListItemUpdateType = components["schemas"]["CheckListItemUpdate"]
    type CheckListItemsPreviewType = CheckapiResponse<"list_items_api_item_get">
    type CheckListItemsPageType = CheckapiResponse<"list_checklist_items_api_checklist__checklist_id__item_get">



    // CheckListItemPosition
    type CheckListItemPositionType = components["schemas"]["CheckListItemPositionPublicWithoutChecklistID"]
    type CheckListItemPositioUpdateType = components["schemas"]["CheckListItemPositionApiUpdate"]
    // type CheckListItemPositionCreateType = components["schemas"]["CheckListItemPositionApiCreate"]
    type CheckListItemPositionUpdateType = components["schemas"]["CheckListItemPositionApiUpdate"]

    // CheckListItemState
    type CheckListItemStateType = components["schemas"]["CheckListItemStateWithoutChecklistID"]
    type CheckListItemStateUpdateType = components["schemas"]["CheckListItemStateUpdate"]

    // ChecklistColorScheme
    type ChecklistColorSchemeType = components["schemas"]["ChecklistColorScheme"]

    // Sharing (backend Phases 3–10)
    type ShareReadType = components["schemas"]["ShareRead"]
    type ShareUpsertType = components["schemas"]["ShareUpsertRequest"]
    type SharePermission = components["schemas"]["SharePermission"]      // "view"|"check"|"edit"
    type ShareStatus = components["schemas"]["ShareStatus"]              // pending|accepted|declined
    type PublicLinkReadType = components["schemas"]["PublicLinkRead"]
    type PublicLinkCreateRes = components["schemas"]["PublicLinkCreateResult"]  // carries token ONCE
    type PublicLinkCreateReq = components["schemas"]["PublicLinkCreateRequest"]
    type PublicLinkUpdateReq = components["schemas"]["PublicLinkUpdateRequest"]
    type UnlockRequestType = components["schemas"]["UnlockRequest"]          // {password}
    type UnlockResultType = components["schemas"]["UnlockResult"]            // {grant, expires_in}
    type GroupShareResult = components["schemas"]["GroupShareResult"]
    type TransferOwnershipResultType = components["schemas"]["TransferOwnershipResult"]
    type InviteReadType = components["schemas"]["InviteRead"]
    type NotificationReadType = components["schemas"]["NotificationRead"]
    type NotificationType = components["schemas"]["NotificationType"]   // card_shared|card_invited|public_link_opened
    type UnreadCountResultType = components["schemas"]["UnreadCountResult"]
    type UserSearchResult = components["schemas"]["UserSearchResult"]
    type UserType = components["schemas"]["User"]

    // API keys (self-service) — UserAuthPublic is the redacted read model;
    // APIKeyCreatedResponse additionally carries the plaintext `token` ONCE.
    type ApiKeyType = components["schemas"]["UserAuthPublic"]
    type ApiKeyCreatedType = components["schemas"]["APIKeyCreatedResponse"]
    type ApiKeyCreateReq = components["schemas"]["APIKeyCreateRequest"]
    type PublicConfigType = components["schemas"]["PublicConfig"]        // P0.2 feature flags

    // Sync
    type SyncNotificationUpdateProp =
        | "item_state" | "item_text" | "item_position" | "item_created" | "item_deleted"
        | "checklist" | "checklist_position" | "checklist_created" | "checklist_deleted"
        | "checklist_label"
        | "share_added" | "share_removed" | "share_invited" | "notification"
    type SyncNotificationType = {
        timestamp: number
        cl_id: string
        cli_id: string | null
        upd_prop: SyncNotificationUpdateProp
    }

    // Labels
    type LabelType = components["schemas"]["LabelReadAPI"]
    type LabelCreateType = components["schemas"]["LabelCreate"]
    type LabelUpdateType = components["schemas"]["LabelUpdate"]

}
