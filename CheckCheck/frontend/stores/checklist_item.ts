import { defineStore } from "pinia";
import Decimal from "decimal.js";
import type { components, operations } from "#open-fetch-schemas/checkapi";
import { useCheckListsStore } from "@/stores/checklist";
export type CheckListItemState = {
  checkListsItems: { [key: string]: CheckListItemType[] };
  checklistWasFullLoadedOnce: { [key: string]: boolean };
  total_backend_count_per_checklist: { [key: string]: number };
  total_backend_count_unchecked_per_checklist: { [key: string]: number };
  total_backend_count_checked_per_checklist: { [key: string]: number };
};
/*
checkList: {
    type: Object as PropType<components["schemas"]["CheckListApiWithSubObj"]>,
        required: true,
    },
items: {
    type: Array as PropType<components["schemas"]["CheckListItemRead"][]>,
        required: false,
    },
        total_backend_count: number;
*/
export const useCheckListsItemStore = defineStore("checkListitem", {
  state: () =>
    ({
      checkListsItems: {},
      checklistWasFullLoadedOnce: {},
      total_backend_count_per_checklist: {},
      total_backend_count_unchecked_per_checklist: {},
      total_backend_count_checked_per_checklist: {},
    } as CheckListItemState),
  getters: {
    checklist_ids(state) {
      if (state.checkListsItems != undefined) {
        return Object.keys(state.checkListsItems);
      }
      return [];
    },
    getCheckListItems: (state) => {
      return (checkListId: string, checked: boolean | null = null, limit: number | null = null) => {
        if (limit === 0) {
          return [];
        }
        const items = state.checkListsItems[checkListId] || [];
        let filtered = items;

        if (checked !== null) {
          filtered = filtered.filter((item) => item.state.checked === checked);
        }

        if (limit !== null) {
          filtered = filtered.slice(0, limit);
        }

        return filtered;
      };
    },
    getCheckListItemCount:
      (state) =>
      (checkListId: string, checked: boolean | null = null) => {
        const items = state.checkListsItems[checkListId] ?? [];

        return checked === null ? items.length : items.filter((item) => item.state.checked === checked).length;
      },
    getItemCount:
      (state) =>
      (checkListId: string, checked: boolean | null = null) => {
        if (checkListId in state.checklistWasFullLoadedOnce && state.checklistWasFullLoadedOnce[checkListId]) {
          return (
            state.checkListsItems[checkListId]?.filter((item) => {
              if (checked === null) return true;
              return item.state.checked === checked;
            }).length ?? 0
          );
        }

        if (checked === true) {
          return state.total_backend_count_checked_per_checklist[checkListId] ?? 0;
        } else if (checked === false) {
          return state.total_backend_count_unchecked_per_checklist[checkListId] ?? 0;
        }
        return state.total_backend_count_per_checklist[checkListId] ?? 0;
      },
  },

  actions: {
    async reorderChecklistItems(checkListId: string, newOrder: CheckListItemType[], movedItem: CheckListItemType) {
      const { $sortBySubset, $findNewPlacementForItem } = useNuxtApp(); // external helper, e.g., sorts items by subset order

      const placement = $findNewPlacementForItem(movedItem, newOrder);
      console.log(placement);
      if (placement.placement == "above") {
        const neighbor = placement.target_neighbor_item as CheckListItemType;
        await this.moveCheckListItemAboveOtherItem(checkListId, movedItem, neighbor);
      } else if (placement.placement == "below") {
        const neighbor = placement.target_neighbor_item as CheckListItemType;
        await this.moveCheckListItemUnderOtherItem(checkListId, movedItem, neighbor);
      }
      const new_order_with_new_index_from_server = $sortBySubset(
        this.checkListsItems[checkListId],
        newOrder
      ) as CheckListItemType[];
      this.checkListsItems[checkListId] = new_order_with_new_index_from_server;
    },
    accessListItems(
      checkListId: string,
      checked: boolean | null = null,
      limit: number | null | undefined = null
    ): ComputedRef<CheckListItemType[]> {
      const store = this;
      //return computed(() => this.checkListsItems[checkListId].filter((item) => item.state.checked == false));
      return computed({
        get() {
          const result: CheckListItemType[] = [];
          if (limit === 0) {
            return result;
          }
          for (const item of store.checkListsItems[checkListId]) {
            if (checked !== null && checked !== undefined) {
              if (item.state.checked === checked) result.push(item);
            } else {
              result.push(item);
            }
            if (limit && result.length >= limit) break;
          }
          return result;
          //return store.checkListsItems[checkListId].filter(item => !item.state.checked);
        },
        // setter
        set(newOrder: CheckListItemType[]) {
          const { $sortBySubset } = useNuxtApp();
          $sortBySubset(store.checkListsItems[checkListId], newOrder);
        },
      });
    },
    accessListItemsCount(
      checkListId: string,
      checked: boolean | null = null,
      limit: number | null | undefined = null
    ): ComputedRef<CheckListItemType[]> {
      const store = this;
      //return computed(() => this.checkListsItems[checkListId].filter((item) => item.state.checked == false));
      return computed({
        get() {
          const result: CheckListItemType[] = [];
          if (limit === 0) {
            return result;
          }
          for (const item of store.checkListsItems[checkListId]) {
            if (checked !== null && checked !== undefined) {
              if (item.state.checked === checked) result.push(item);
            } else {
              result.push(item);
            }
            if (limit && result.length >= limit) break;
          }
          return result;
          //return store.checkListsItems[checkListId].filter(item => !item.state.checked);
        },
        // setter
        set(newOrder: CheckListItemType[]) {
          const { $sortBySubset } = useNuxtApp();
          $sortBySubset(store.checkListsItems[checkListId], newOrder);
        },
      });
    },
    async calculateItemIndex(targetItem: CheckListItemType, itemList: CheckListItemType[]): Promise<number> {
      // This function may be of use for a later offline version of the webapp. We need to recalculate index locally when the server is not available.
      if (itemList.length == 1) {
        // only one item in list. no need to adapt any indexing
        return itemList[0].position.index;
      }
      const itemIndex = itemList.findIndex((checklistItem) => checklistItem.id == targetItem.id);

      if (itemIndex === -1) throw new Error("itemToMove not found in the list");
      if (itemIndex === 0) {
        // New item position is at first index
        const index = new Decimal(itemList[1].position.index);
        return index.plus(0.4).toNumber();
      }
      if (itemIndex + 1 == itemList.length) {
        // New item position is at last index
        const index = new Decimal(itemList.at(-2)!.position.index);
        return index.minus(0.4).toNumber();
      }
      const prevPositionIndex = new Decimal(itemList[itemIndex - 1].position.index);
      const nextPositionIndex = new Decimal(itemList[itemIndex + 1].position.index);
      const newPositionIndex = nextPositionIndex.minus(prevPositionIndex).div(2).plus(prevPositionIndex).toNumber();
      return newPositionIndex;
    },
    async create(checkListId: string, checklistitem?: CheckListItemCreateType) {
      if (!checkListId) throw new Error("Checklistid empty");
      if (!checklistitem) {
        checklistitem = {};
      }
      const { $checkapi } = useNuxtApp();
      var resChecklistItem: CheckListItemType;
      try {
        console.log(checklistitem);
        resChecklistItem = await $checkapi("/api/checklist/{checklist_id}/item", {
          method: "post",
          path: { checklist_id: checkListId },
          body: checklistitem,
        });
      } catch (error) {
        console.error("Error with ", checklistitem, error);
        throw new Error(
          "Could not store new checklist item item to backend 'POST /checklist/" + checkListId + "/item/'"
        );
      }
      //this.checkListsItems[checkListId].unshift(resChecklistItem);
      this._insertNewAtCorrectIndex(this.checkListsItems[checkListId], resChecklistItem);

      return resChecklistItem;
    },
    async update(checkListId: string, checklistidItemId: string, checklistitem: CheckListItemUpdateType) {
      if (!checklistitem || !checklistidItemId || !checkListId) return;
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resChecklistItem: CheckListItemType;
      try {
        resChecklistItem = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "patch",
          body: checklistitem,
        });
      } catch (error) {
        console.error("Error with ", checklistitem, error);
        throw new Error(
          "Could not store updated checklist item to backend 'PATCH /checklist/" +
            checkListId +
            "/item/" +
            checklistidItemId +
            "'"
        );
      }
      // get index of existing checkList in store
      var index = this.checkListsItems[checkListId].findIndex(
        (checklistItem) => checklistItem.id == resChecklistItem.id
      );

      // replace the updated checklist attributvalues in state
      if (index !== -1) {
        $transferAttrs(resChecklistItem, this.checkListsItems[checkListId][index]);
      } else {
        // Insert if not existent in list.
        // This should not happen, but maybe later usefull for a feature like items moving from one checklist to another
        this._insertNewAtCorrectIndex(this.checkListsItems[checkListId], resChecklistItem);
        //this.checkListsItems[checkListId].unshift(resChecklistItem);
      }
      return this.checkListsItems[checkListId][index];
    },
    async refresh(checkListId: string, checklistidItemId: string) {
      if (!checkListId) return;
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resChecklistItem: CheckListItemType;
      try {
        resChecklistItem = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "get",
        });
      } catch (error) {
        console.error("Error with ", checkListId, error);
        throw new Error(
          "Could not refresh checklist item from backend 'GET /api/checklist/" +
            checkListId +
            "/item/" +
            checklistidItemId +
            "'"
        );
      }
      // get index of existing checkList in store
      var index = this.checkListsItems[checkListId].findIndex(
        (checklistItem) => checklistItem.id == resChecklistItem.id
      );

      // replace-update the new checklist in state
      if (index !== -1) {
        $transferAttrs(resChecklistItem, this.checkListsItems[checkListId][index]);
        //this.checkListsItems[checkListId].splice(index, 1, resChecklistItem)  // Replace the item in the store
      } else {
        // This case can happen if an item was created by another client
        this._insertNewAtCorrectIndex(this.checkListsItems[checkListId], resChecklistItem);
        //this.checkListsItems[checkListId].unshift(resChecklistItem);
      }
      return this.checkListsItems[checkListId][index];
    },

    async delete(checkListId: string, checklistidItemId: string) {
      if (!checkListId) {
        throw new Error("checkListId empty");
      }
      const { $checkapi } = useNuxtApp();
      var index = this.checkListsItems[checkListId].findIndex((checklistItem) => checklistItem.id == checklistidItemId);
      try {
        await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "delete",
        });
      } catch (error) {
        console.error("Error with ", checkListId, error);
        throw new Error(
          "Could not delete checklist from backend 'DELETE /checklist/" +
            checkListId +
            "/item/" +
            checklistidItemId +
            "'"
        );
      }
      if (index !== -1) {
        this.checkListsItems[checkListId].splice(index, 1);
      }
    },
    async refreshAllCheckListItems(checkListId: string, checkedState: boolean | null = null) {
      if (!checkListId) {
        throw new Error("checkListId empty");
      }
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var checkListItems: CheckListItemsPageType;
      try {
        if (checkedState !== null) {
          checkListItems = await $checkapi("/api/checklist/{checklist_id}/item", {
            path: { checklist_id: checkListId },
            method: "get",
            query: { limit: 999999, checked: checkedState },
          });
        } else {
          checkListItems = await $checkapi("/api/checklist/{checklist_id}/item", {
            path: { checklist_id: checkListId },
            method: "get",
            query: { limit: 999999 },
          });
        }
      } catch (error) {
        console.error(error);
        throw new Error("Could not get checklist items from backend 'GET /checklist/" + checkListId + "/item/'");
      }
      if (!(checkListId in this.checkListsItems)) {
        this.checkListsItems[checkListId] = [];
      }
      for (const responseItem of checkListItems.items) {
        var index = this.checkListsItems[checkListId].findIndex((checklistItem) => checklistItem.id == responseItem.id);
        // replace-update the new checklist in state
        if (index !== -1) {
          $transferAttrs(responseItem, this.checkListsItems[checkListId][index]); // Replace the props of the existing item in store. No refs lost :)
          //this.checkListsItems[checkListId].splice(index, 1, resItem)  // Replace the item in the store
        } else {
          this.checkListsItems[checkListId].push(responseItem);
        }
      }
      this._sort(checkListId);
      //this.checkListsItems[checkListId] = this.checkListsItems[checkListId] checkListItems.items
      this.checklistWasFullLoadedOnce[checkListId] = true;
      return checkListItems.items;
    },
    async fetchMultipleChecklistsItemsPreview(checklist_ids: string[] = [], checked: boolean | null = null) {
      const { $checkapi } = useNuxtApp();
      const appConfig = useAppConfig();
      var resChecklistPage: CheckListItemsPreviewType;
      try {
        resChecklistPage = await $checkapi("/api/item", {
          method: "get",
          query: { checklist_ids: checklist_ids, limit_per_checklist: appConfig.previewItemCount * 2},
        });
      } catch (error) {
        console.error("Error with ", checklist_ids, error);
        throw new Error("Could not fetch checklist page from backend 'GET /item'");
      }
      for (const checkListId of Object.keys(resChecklistPage)) {
        if (!(checkListId in this.checkListsItems) || this.checkListsItems[checkListId].length == 0) {
          this.checkListsItems[checkListId] = resChecklistPage[checkListId]!.items as CheckListItemType[];
        }
        this.total_backend_count_per_checklist[checkListId] = resChecklistPage[checkListId]!.item_count;
        this.total_backend_count_unchecked_per_checklist[checkListId] =
          resChecklistPage[checkListId]!.item_unchecked_count;
        this.total_backend_count_checked_per_checklist[checkListId] = resChecklistPage[checkListId]!.item_checked_count;

        if (!(checkListId in this.checklistWasFullLoadedOnce)) {
          this.checklistWasFullLoadedOnce[checkListId] = false;
        }
      }
      this._sortAll();
    },
    async updatePosition(
      checkListId: string,
      checklistidItemId: string,
      pos: CheckListItemPositionUpdateType
    ): Promise<CheckListItemPositionType> {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resPos: CheckListItemPositionType;
      try {
        resPos = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/position", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "patch",
          body: pos,
        });
      } catch (error) {
        console.error("Error with ", checkListId, error);
        throw new Error(
          "Could not update posititio to backend 'PATCH /checklist/" +
            checkListId +
            "/item/" +
            checklistidItemId +
            "/position'"
        );
      }
      var index = this.checkListsItems[checkListId].findIndex((checklistItem) => checklistItem.id == checklistidItemId);
      // this.checkListsItems[checkListId][index].position = resPos
      $transferAttrs(resPos, this.checkListsItems[checkListId][index].position);
      await this._sort(checkListId);
      return this.checkListsItems[checkListId][index].position;
    },
    async updateState(checkListId: string, checklistidItemId: string, state: CheckListItemStateUpdateType) {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resState: CheckListItemStateType;

      try {
        resState = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/state", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "patch",
          body: state,
        });
      } catch (error) {
        console.error("Error with ", checkListId, error);
        throw new Error(
          "Could update posititio to backend 'PATCH /checklist/" +
            checkListId +
            "/item/" +
            checklistidItemId +
            "/state'"
        );
      }
      var index = this.checkListsItems[checkListId].findIndex((checklistItem) => checklistItem.id == checklistidItemId);
      $transferAttrs(resState, this.checkListsItems[checkListId][index].state);
      if (!(checkListId in this.checklistWasFullLoadedOnce) || this.checklistWasFullLoadedOnce[checkListId] === false) {
        //await this.refreshAllCheckListItems(checkListId)
      }
      if (resState.checked) {
        this.total_backend_count_checked_per_checklist[checkListId]++;
        this.total_backend_count_unchecked_per_checklist[checkListId]--;
      } else {
        this.total_backend_count_checked_per_checklist[checkListId]--;
        this.total_backend_count_unchecked_per_checklist[checkListId]++;
      }
      //this.checkListsItems[checkListId][index].state = resState
      return this.checkListsItems[checkListId][index].state;
    },
    async refreshPosition(checkListId: string, checklistidItemId: string) {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resPos: CheckListItemPositionType;

      try {
        resPos = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/position", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "get",
        });
      } catch (error) {
        console.error("Error with ", checkListId, error);
        throw new Error(
          "Could update position to backend 'PATCH /checklist/" +
            checkListId +
            "/item/" +
            checklistidItemId +
            "/position'"
        );
      }
      var index = this.checkListsItems[checkListId].findIndex((checklistItem) => checklistItem.id == checklistidItemId);
      //this.checkListsItems[checkListId][index].position = resPos
      $transferAttrs(resPos, this.checkListsItems[checkListId][index].position);

      if (this.checkListsItems[checkListId].length > 1)
        this.checkListsItems[checkListId].sort((a, b) => a.position.index - b.position.index);

      return this.checkListsItems[checkListId][index].position;
    },
    async refreshState(checkListId: string, checklistidItemId: string) {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resState: CheckListItemStateType;

      try {
        resState = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/state", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "get",
        });
      } catch (error) {
        console.error("Error with ", checkListId, error);
        throw new Error(
          "Could update position to backend 'PATCH /checklist/" + checkListId + "/item/" + checklistidItemId + "/state'"
        );
      }
      var index = this.checkListsItems[checkListId].findIndex((checklistItem) => checklistItem.id == checklistidItemId);
      //this.checkListsItems[checkListId][index].state = resState
      $transferAttrs(resState, this.checkListsItems[checkListId][index].state);

      return this.checkListsItems[checkListId][index].state;
    },
    async moveCheckListItemUnderOtherItem(
      checkListId: string,
      itemToMove: CheckListItemType,
      otherItem: CheckListItemType
    ): Promise<CheckListItemPositionType> {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resPos: CheckListItemPositionType;
      try {
        resPos = await $checkapi(
          "/api/checklist/{checklist_id}/item/{checklist_item_id}/move/under/{other_checklist_item_id}",
          {
            path: {
              checklist_id: checkListId,
              checklist_item_id: itemToMove.id,
              other_checklist_item_id: otherItem.id,
            },
            method: "patch",
          }
        );
      } catch (error) {
        console.error(
          "Could update posititio to backend 'PATCH /checklist/" +
            checkListId +
            "/item/" +
            itemToMove.id +
            "/move/under/" +
            otherItem.id +
            "'",
          error
        );
        throw new Error("Could not update position to backend");
      }
      $transferAttrs(resPos, itemToMove.position);
      this._sort(checkListId);
      return itemToMove.position;
    },
    async moveCheckListItemAboveOtherItem(
      checkListId: string,
      itemToMove: CheckListItemType,
      otherItem: CheckListItemType
    ): Promise<CheckListItemPositionType> {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resPos: CheckListItemPositionType;

      try {
        resPos = await $checkapi(
          "/api/checklist/{checklist_id}/item/{checklist_item_id}/move/above/{other_checklist_item_id}",
          {
            path: {
              checklist_id: checkListId,
              checklist_item_id: itemToMove.id,
              other_checklist_item_id: otherItem.id,
            },
            method: "patch",
          }
        );
      } catch (error) {
        console.error(
          "Could update position to backend 'PATCH /checklist/" +
            checkListId +
            "/item/" +
            itemToMove.id +
            "/move/above/" +
            otherItem.id +
            "'",
          error
        );
        throw new Error("Could update position to backend");
      }
      $transferAttrs(resPos, itemToMove.position);
      this._sort(checkListId);
      return itemToMove.position;
    },
    async moveCheckListItemToBottom(
      checkListId: string,
      itemToMove: CheckListItemType
    ): Promise<CheckListItemPositionType> {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resPos: CheckListItemPositionType;

      try {
        resPos = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/move/bottom", {
          path: { checklist_id: checkListId, checklist_item_id: itemToMove.id },
          method: "patch",
        });
      } catch (error) {
        console.error(
          "Could update position to backend 'PATCH /checklist/" +
            checkListId +
            "/item/" +
            itemToMove.id +
            "/move/bottom'",
          error
        );
        throw new Error("Could update position to backend");
      }
      $transferAttrs(resPos, itemToMove.position);
      this._sort(checkListId);
      return itemToMove.position;
    },
    async moveCheckListItemToTop(
      checkListId: string,
      itemToMove: CheckListItemType
    ): Promise<CheckListItemPositionType> {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resPos: CheckListItemPositionType;

      try {
        resPos = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/move/top", {
          path: { checklist_id: checkListId, checklist_item_id: itemToMove.id },
          method: "patch",
        });
      } catch (error) {
        console.error(
          "Could update position to backend 'PATCH /checklist/" + checkListId + "/item/" + itemToMove.id + "/move/top'",
          error
        );
        throw new Error("Could update position to backend");
      }
      $transferAttrs(resPos, itemToMove.position);
      this._sort(checkListId);
      return itemToMove.position;
    },
    async moveCheckListItem(
      checkListId: string,
      itemToMove: CheckListItemType,
      insertAfterItem: CheckListItemType,
      commitToBackend: boolean = true
    ) {
      // Remove itemToMove from its current position

      const currentIndex = this.checkListsItems[checkListId].findIndex(
        (checklistItem) => checklistItem.id == itemToMove.id
      );
      if (currentIndex === -1) throw new Error("itemToMove not found in the list");
      this.checkListsItems[checkListId].splice(currentIndex, 1); // Remove itemToMove

      // Find the index of insertAfterItem
      //const insertAfterIndex = this.checkListsItems[checkListId].indexOf(insertAfterItem);
      const insertAfterIndex = this.checkListsItems[checkListId].findIndex(
        (checklistItem) => checklistItem.id == insertAfterItem.id
      );
      if (insertAfterIndex === -1) throw new Error("insertAfterItem not found in the list");

      // Insert itemToMove after insertAfterItem
      this.checkListsItems[checkListId].splice(insertAfterIndex + 1, 0, itemToMove);

      // Calculate new position
      const prevPositionIndex = insertAfterItem.position.index;
      const nextPositionIndex =
        insertAfterIndex + 2 < this.checkListsItems[checkListId].length
          ? this.checkListsItems[checkListId][insertAfterIndex + 2].position.index
          : prevPositionIndex - 1;

      // Set the new position as the midpoint of the neighbors
      //itemToMove.position.index = (prevPosition + nextPosition) / 2;
      itemToMove.position.index = (nextPositionIndex - prevPositionIndex) / 2 + prevPositionIndex;
      if (commitToBackend) {
        this.updatePosition(checkListId, itemToMove.id, itemToMove.position);
      }
    },
    async _sort(checkListId: string) {
      this.checkListsItems[checkListId].sort((a, b) => a.position.index - b.position.index);
    },
    async _sortAll() {
      for (let checkListId of Object.keys(this.checkListsItems)) {
        this._sort(checkListId);
      }
      //this.checkListsItems[checkListId].sort((a, b) => b.position.index - a.position.index);
    },
    async _insertNewAtCorrectIndex(checkListItemList: CheckListItemType[], checklistitem: CheckListItemType) {
      // This function inserts new item at the corret index. Calling this every time is more complex but computional cheaper compared to calling _sort() everytime we have new items
      var index = checkListItemList.findIndex((checklistItem) => checklistItem.id == checklistitem.id);
      if (index !== -1) {
        // Sanity Check. Can be removed when function is considered stable (including checkListItemList.findIndex)
        throw new Error("Item is allready in list. Can not insert as new.");
      }
      let low = 0,
        high = checkListItemList.length;

      while (low < high) {
        const mid = Math.floor((low + high) / 2);
        if (checkListItemList[mid].position.index < checklistitem.position.index) {
          low = mid + 1;
        } else {
          high = mid;
        }
      }

      // Insert the new item at the found index
      checkListItemList.splice(low, 0, checklistitem);
    },
  },
});
