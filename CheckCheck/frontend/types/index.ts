import type { components } from "#open-fetch-schemas/checkapi";
import type { PropType } from "vue";
import type { CheckapiRequestBody, CheckapiResponse } from "#open-fetch";

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
    
    // Labels
    type LabelType = components["schemas"]["LabelReadAPI"]
    type LabelCreateType = components["schemas"]["LabelCreate"]
    type LabelUpdateType = components["schemas"]["LabelUpdate"]
}
