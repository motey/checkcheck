import { defineStore } from "pinia";
import Decimal from "decimal.js";
import { useCheckListsStore } from "@/stores/checklist";
import { findNewPlacementForItem, sortBySubset } from "~/utils/helpers";

export type CheckListItemState = {
  checkListsItems: { [key: string]: CheckListItemType[] };
  checklistWasFullLoadedOnce: { [key: string]: boolean };
  total_backend_count_per_checklist: { [key: string]: number };
  total_backend_count_unchecked_per_checklist: { [key: string]: number };
  total_backend_count_checked_per_checklist: { [key: string]: number };
};

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
      return Object.keys(state.checkListsItems);
    },
    getCheckListItems: (state) => {
      return (checkListId: string, checked: boolean | null = null, limit: number | null = null) => {
        if (limit === 0) return [];
        const items = state.checkListsItems[checkListId] || [];
        const filtered = checked !== null ? items.filter((item) => item.state.checked === checked) : items;
        return limit !== null ? filtered.slice(0, limit) : filtered;
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
        if (checked === true) return state.total_backend_count_checked_per_checklist[checkListId] ?? 0;
        if (checked === false) return state.total_backend_count_unchecked_per_checklist[checkListId] ?? 0;
        return state.total_backend_count_per_checklist[checkListId] ?? 0;
      },
  },

  actions: {
    async reorderChecklistItems(checkListId: string, newOrder: CheckListItemType[], movedItem: CheckListItemType) {
      const placement = findNewPlacementForItem(movedItem, newOrder);
      if (placement.placement == "above") {
        await this.moveCheckListItemAboveOtherItem(checkListId, movedItem, placement.target_neighbor_item as CheckListItemType);
      } else if (placement.placement == "below") {
        await this.moveCheckListItemUnderOtherItem(checkListId, movedItem, placement.target_neighbor_item as CheckListItemType);
      }
      this.checkListsItems[checkListId] = sortBySubset(
        this.checkListsItems[checkListId]!,
        newOrder
      ) as CheckListItemType[];
    },
    accessListItems(
      checkListId: string,
      checked: boolean | null = null,
      limit: number | null | undefined = null
    ): ComputedRef<CheckListItemType[]> {
      const store = this;
      return computed({
        get() {
          const result: CheckListItemType[] = [];
          if (limit === 0) return result;
          for (const item of store.checkListsItems[checkListId]!) {
            if (checked !== null && checked !== undefined) {
              if (item.state.checked === checked) result.push(item);
            } else {
              result.push(item);
            }
            if (limit && result.length >= limit) break;
          }
          return result;
        },
        set(newOrder: CheckListItemType[]) {
          sortBySubset(store.checkListsItems[checkListId]!, newOrder);
        },
      });
    },
    accessListItemsCount(
      checkListId: string,
      checked: boolean | null = null,
      limit: number | null | undefined = null
    ): ComputedRef<CheckListItemType[]> {
      const store = this;
      return computed({
        get() {
          const result: CheckListItemType[] = [];
          if (limit === 0) return result;
          for (const item of store.checkListsItems[checkListId]!) {
            if (checked !== null && checked !== undefined) {
              if (item.state.checked === checked) result.push(item);
            } else {
              result.push(item);
            }
            if (limit && result.length >= limit) break;
          }
          return result;
        },
        set(newOrder: CheckListItemType[]) {
          sortBySubset(store.checkListsItems[checkListId]!, newOrder);
        },
      });
    },
    async create(checkListId: string, checklistitem?: CheckListItemCreateType) {
      if (!checkListId) throw new Error("Checklistid empty");
      const { $checkapi } = useNuxtApp();
      let resChecklistItem: CheckListItemType;
      try {
        resChecklistItem = await $checkapi("/api/checklist/{checklist_id}/item", {
          method: "post",
          path: { checklist_id: checkListId },
          body: checklistitem ?? {},
        });
      } catch (error) {
        console.error("Could not create checklist item 'POST /checklist/" + checkListId + "/item'", error);
        throw error;
      }
      this._insertNewAtCorrectIndex(this.checkListsItems[checkListId]!, resChecklistItem);
      return resChecklistItem;
    },
    async update(checkListId: string, checklistidItemId: string, checklistitem: CheckListItemUpdateType) {
      if (!checklistitem || !checklistidItemId || !checkListId) return;
      const { $checkapi } = useNuxtApp();
      let resChecklistItem: CheckListItemType;
      try {
        resChecklistItem = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "patch",
          body: checklistitem,
        });
      } catch (error) {
        console.error("Could not update checklist item 'PATCH /checklist/" + checkListId + "/item/" + checklistidItemId + "'", error);
        throw error;
      }
      const index = this.checkListsItems[checkListId]!.findIndex((item) => item.id == resChecklistItem.id);
      if (index !== -1) {
        this.checkListsItems[checkListId]!.splice(index, 1, resChecklistItem);
      } else {
        this._insertNewAtCorrectIndex(this.checkListsItems[checkListId]!, resChecklistItem);
      }
      return resChecklistItem;
    },
    async refresh(checkListId: string, checklistidItemId: string) {
      if (!checkListId) return;
      const { $checkapi } = useNuxtApp();
      let resChecklistItem: CheckListItemType;
      try {
        resChecklistItem = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "get",
        });
      } catch (error) {
        console.error("Could not refresh checklist item 'GET /checklist/" + checkListId + "/item/" + checklistidItemId + "'", error);
        throw error;
      }
      const index = this.checkListsItems[checkListId]!.findIndex((item) => item.id == resChecklistItem.id);
      if (index !== -1) {
        this.checkListsItems[checkListId]!.splice(index, 1, resChecklistItem);
      } else {
        this._insertNewAtCorrectIndex(this.checkListsItems[checkListId]!, resChecklistItem);
      }
      return resChecklistItem;
    },
    async delete(checkListId: string, checklistidItemId: string) {
      if (!checkListId) throw new Error("checkListId empty");
      const { $checkapi } = useNuxtApp();
      const index = this.checkListsItems[checkListId]!.findIndex((item) => item.id == checklistidItemId);
      try {
        await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "delete",
        });
      } catch (error) {
        console.error("Could not delete checklist item 'DELETE /checklist/" + checkListId + "/item/" + checklistidItemId + "'", error);
        throw error;
      }
      if (index !== -1) this.checkListsItems[checkListId]!.splice(index, 1);
    },
    async refreshAllCheckListItems(checkListId: string) {
      if (!checkListId) throw new Error("Checklistid empty");
      const { $checkapi } = useNuxtApp();
      let checkListItems: CheckListItemsPageType;
      try {
        checkListItems = await $checkapi("/api/checklist/{checklist_id}/item", {
          path: { checklist_id: checkListId },
          method: "get",
          query: { limit: 999999 },
        });
      } catch (error) {
        console.error("Could not get checklist items 'GET /checklist/" + checkListId + "/item'", error);
        throw error;
      }
      // Replace with the authoritative server list. Items removed server-side
      // are gone; new items are added; existing items have fresh data.
      this.checkListsItems[checkListId] = checkListItems.items;
      this._sort(checkListId);
      this.checklistWasFullLoadedOnce[checkListId] = true;
      return checkListItems.items;
    },
    dropChecklistItems(checkListId: string) {
      // Forget everything cached for a checklist (e.g. it was deleted server-side).
      delete this.checkListsItems[checkListId];
      delete this.checklistWasFullLoadedOnce[checkListId];
      delete this.total_backend_count_per_checklist[checkListId];
      delete this.total_backend_count_unchecked_per_checklist[checkListId];
      delete this.total_backend_count_checked_per_checklist[checkListId];
    },
    async fetchMultipleChecklistsItemsPreview(
      checklist_ids: string[] = [],
      checked: boolean | null = null,
      force: boolean = false
    ) {
      const { $checkapi } = useNuxtApp();
      const appConfig = useAppConfig();
      let resChecklistPage: CheckListItemsPreviewType;
      try {
        resChecklistPage = await $checkapi("/api/item", {
          method: "get",
          query: { checklist_ids: checklist_ids, limit_per_checklist: appConfig.previewItemCount * 2 },
        });
      } catch (error) {
        console.error("Could not fetch checklist item previews 'GET /item'", error);
        throw error;
      }
      for (const checkListId of Object.keys(resChecklistPage)) {
        if (force || !(checkListId in this.checkListsItems) || this.checkListsItems[checkListId]!.length == 0) {
          this.checkListsItems[checkListId] = resChecklistPage[checkListId]!.items as CheckListItemType[];
        }
        this.total_backend_count_per_checklist[checkListId] = resChecklistPage[checkListId]!.item_count;
        this.total_backend_count_unchecked_per_checklist[checkListId] = resChecklistPage[checkListId]!.item_unchecked_count;
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
      const { $checkapi } = useNuxtApp();
      try {
        const resPos = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/position", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "patch",
          body: pos,
        });
        const index = this.checkListsItems[checkListId]!.findIndex((item) => item.id == checklistidItemId);
        if (index !== -1) this.checkListsItems[checkListId]![index]!.position = resPos;
        await this._sort(checkListId);
        return resPos;
      } catch (error) {
        console.error("Could not update item position 'PATCH /checklist/" + checkListId + "/item/" + checklistidItemId + "/position'", error);
        throw error;
      }
    },
    async updateState(checkListId: string, checklistidItemId: string, state: CheckListItemStateUpdateType) {
      const { $checkapi } = useNuxtApp();
      try {
        const resState = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/state", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "patch",
          body: state,
        });
        const index = this.checkListsItems[checkListId]!.findIndex((item) => item.id == checklistidItemId);
        if (index !== -1) this.checkListsItems[checkListId]![index]!.state = resState;
        if (resState.checked) {
          this.total_backend_count_checked_per_checklist[checkListId]!++;
          this.total_backend_count_unchecked_per_checklist[checkListId]!--;
        } else {
          this.total_backend_count_checked_per_checklist[checkListId]!--;
          this.total_backend_count_unchecked_per_checklist[checkListId]!++;
        }
        return resState;
      } catch (error) {
        console.error("Could not update item state 'PATCH /checklist/" + checkListId + "/item/" + checklistidItemId + "/state'", error);
        throw error;
      }
    },
    async refreshPosition(checkListId: string, checklistidItemId: string) {
      const { $checkapi } = useNuxtApp();
      try {
        const resPos = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/position", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "get",
        });
        const index = this.checkListsItems[checkListId]!.findIndex((item) => item.id == checklistidItemId);
        if (index !== -1) this.checkListsItems[checkListId]![index]!.position = resPos;
        if (this.checkListsItems[checkListId]!.length > 1) this._sort(checkListId);
        return resPos;
      } catch (error) {
        console.error("Could not refresh item position 'GET /checklist/" + checkListId + "/item/" + checklistidItemId + "/position'", error);
        throw error;
      }
    },
    async refreshState(checkListId: string, checklistidItemId: string) {
      const { $checkapi } = useNuxtApp();
      try {
        const resState = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/state", {
          path: { checklist_id: checkListId, checklist_item_id: checklistidItemId },
          method: "get",
        });
        const index = this.checkListsItems[checkListId]!.findIndex((item) => item.id == checklistidItemId);
        if (index !== -1) this.checkListsItems[checkListId]![index]!.state = resState;
        return resState;
      } catch (error) {
        console.error("Could not refresh item state 'GET /checklist/" + checkListId + "/item/" + checklistidItemId + "/state'", error);
        throw error;
      }
    },
    async moveCheckListItemUnderOtherItem(
      checkListId: string,
      itemToMove: CheckListItemType,
      otherItem: CheckListItemType
    ): Promise<CheckListItemPositionType> {
      const { $checkapi } = useNuxtApp();
      let resPos: CheckListItemPositionType;
      try {
        resPos = await $checkapi(
          "/api/checklist/{checklist_id}/item/{checklist_item_id}/move/under/{other_checklist_item_id}",
          {
            path: { checklist_id: checkListId, checklist_item_id: itemToMove.id, other_checklist_item_id: otherItem.id },
            method: "put",
          }
        );
      } catch (error) {
        console.error("Could not move item under another", error);
        throw error;
      }
      const idx = this.checkListsItems[checkListId]!.findIndex((i) => i.id === itemToMove.id);
      if (idx !== -1) this.checkListsItems[checkListId]![idx]!.position = resPos;
      this._sort(checkListId);
      return resPos;
    },
    async moveCheckListItemAboveOtherItem(
      checkListId: string,
      itemToMove: CheckListItemType,
      otherItem: CheckListItemType
    ): Promise<CheckListItemPositionType> {
      const { $checkapi } = useNuxtApp();
      let resPos: CheckListItemPositionType;
      try {
        resPos = await $checkapi(
          "/api/checklist/{checklist_id}/item/{checklist_item_id}/move/above/{other_checklist_item_id}",
          {
            path: { checklist_id: checkListId, checklist_item_id: itemToMove.id, other_checklist_item_id: otherItem.id },
            method: "put",
          }
        );
      } catch (error) {
        console.error("Could not move item above another", error);
        throw error;
      }
      const idx = this.checkListsItems[checkListId]!.findIndex((i) => i.id === itemToMove.id);
      if (idx !== -1) this.checkListsItems[checkListId]![idx]!.position = resPos;
      this._sort(checkListId);
      return resPos;
    },
    async moveCheckListItemToBottom(checkListId: string, itemToMove: CheckListItemType): Promise<CheckListItemPositionType> {
      const { $checkapi } = useNuxtApp();
      let resPos: CheckListItemPositionType;
      try {
        resPos = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/move/bottom", {
          path: { checklist_id: checkListId, checklist_item_id: itemToMove.id },
          method: "put",
        });
      } catch (error) {
        console.error("Could not move item to bottom", error);
        throw error;
      }
      const idx = this.checkListsItems[checkListId]!.findIndex((i) => i.id === itemToMove.id);
      if (idx !== -1) this.checkListsItems[checkListId]![idx]!.position = resPos;
      this._sort(checkListId);
      return resPos;
    },
    async moveCheckListItemToTop(checkListId: string, itemToMove: CheckListItemType): Promise<CheckListItemPositionType> {
      const { $checkapi } = useNuxtApp();
      let resPos: CheckListItemPositionType;
      try {
        resPos = await $checkapi("/api/checklist/{checklist_id}/item/{checklist_item_id}/move/top", {
          path: { checklist_id: checkListId, checklist_item_id: itemToMove.id },
          method: "put",
        });
      } catch (error) {
        console.error("Could not move item to top", error);
        throw error;
      }
      const idx = this.checkListsItems[checkListId]!.findIndex((i) => i.id === itemToMove.id);
      if (idx !== -1) this.checkListsItems[checkListId]![idx]!.position = resPos;
      this._sort(checkListId);
      return resPos;
    },
    async moveCheckListItem(
      checkListId: string,
      itemToMove: CheckListItemType,
      insertAfterItem: CheckListItemType,
      commitToBackend: boolean = true
    ) {
      const currentIndex = this.checkListsItems[checkListId]!.findIndex((item) => item.id == itemToMove.id);
      if (currentIndex === -1) throw new Error("itemToMove not found in the list");
      this.checkListsItems[checkListId]!.splice(currentIndex, 1);

      const insertAfterIndex = this.checkListsItems[checkListId]!.findIndex((item) => item.id == insertAfterItem.id);
      if (insertAfterIndex === -1) throw new Error("insertAfterItem not found in the list");
      this.checkListsItems[checkListId]!.splice(insertAfterIndex + 1, 0, itemToMove);

      const prevPositionIndex = insertAfterItem.position.index;
      const nextPositionIndex =
        insertAfterIndex + 2 < this.checkListsItems[checkListId]!.length
          ? this.checkListsItems[checkListId]![insertAfterIndex + 2]!.position.index
          : prevPositionIndex - 1;
      itemToMove.position.index = (nextPositionIndex - prevPositionIndex) / 2 + prevPositionIndex;
      if (commitToBackend) this.updatePosition(checkListId, itemToMove.id, itemToMove.position);
    },
    async _sort(checkListId: string) {
      this.checkListsItems[checkListId]!.sort((a, b) => a.position.index - b.position.index);
    },
    async _sortAll() {
      for (const checkListId of Object.keys(this.checkListsItems)) {
        this._sort(checkListId);
      }
    },
    async _insertNewAtCorrectIndex(checkListItemList: CheckListItemType[], checklistitem: CheckListItemType) {
      if (checkListItemList.findIndex((item) => item.id == checklistitem.id) !== -1) {
        throw new Error("Item is already in list. Cannot insert as new.");
      }
      let low = 0,
        high = checkListItemList.length;
      while (low < high) {
        const mid = Math.floor((low + high) / 2);
        if (checkListItemList[mid]!.position.index < checklistitem.position.index) {
          low = mid + 1;
        } else {
          high = mid;
        }
      }
      checkListItemList.splice(low, 0, checklistitem);
    },
  },
});
