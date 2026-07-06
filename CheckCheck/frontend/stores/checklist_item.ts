import { defineStore } from "pinia";
import { findNewPlacementForItem, sortBySubset } from "~/utils/helpers";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { useOutbox } from "@/composables/useOutbox";
import { itemCreateOp, itemDeleteOp, itemStateOp, itemUpdateOp, nextItemIndex } from "@/utils/outboxOps";

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
    getCheckListItems: (state) => {
      return (checkListId: string, checked: boolean | null = null, limit: number | null = null) => {
        if (limit === 0) return [];
        const items = state.checkListsItems[checkListId] || [];
        const filtered = checked !== null ? items.filter((item) => item.state.checked === checked) : items;
        return limit !== null ? filtered.slice(0, limit) : filtered;
      };
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
    async create(checkListId: string, checklistitem?: CheckListItemCreateType) {
      if (!checkListId) throw new Error("Checklistid empty");
      if (isLocalFirstEnabled()) return this._localCreate(checkListId, checklistitem);
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
      if (isLocalFirstEnabled()) return this._localUpdate(checkListId, checklistidItemId, checklistitem);
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
      if (isLocalFirstEnabled()) return this._localDelete(checkListId, checklistidItemId);
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
      if (isLocalFirstEnabled()) return this._localUpdateState(checkListId, checklistidItemId, state);
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
    async _sort(checkListId: string) {
      this.checkListsItems[checkListId]!.sort((a, b) => a.position.index - b.position.index);
    },
    async _sortAll() {
      for (const checkListId of Object.keys(this.checkListsItems)) {
        this._sort(checkListId);
      }
    },
    // ── Local-first optimistic paths (WI-8) ───────────────────────────────
    //
    // Flag-on, the four item-content actions mutate the store immediately and
    // enqueue the REST call to the WI-7 outbox instead of awaiting `$checkapi`.
    // Every op carries the client-generated item id, so replay is idempotent
    // (protocol §8) and WI-10's delta pull upserts by the same id with no
    // duplicate. Reconciliation with server truth is still the legacy SSE
    // refetch until WI-10.

    /**
     * Shift the cached count maps by the given deltas. Kept consistent offline
     * so the sidebar badges are right when `checklistWasFullLoadedOnce` is false
     * (when true, `getItemCount` reads the array and these are moot but harmless).
     */
    _adjustCounts(checkListId: string, dTotal: number, dChecked: number, dUnchecked: number) {
      this.total_backend_count_per_checklist[checkListId] =
        (this.total_backend_count_per_checklist[checkListId] ?? 0) + dTotal;
      this.total_backend_count_checked_per_checklist[checkListId] =
        (this.total_backend_count_checked_per_checklist[checkListId] ?? 0) + dChecked;
      this.total_backend_count_unchecked_per_checklist[checkListId] =
        (this.total_backend_count_unchecked_per_checklist[checkListId] ?? 0) + dUnchecked;
    },
    _localCreate(checkListId: string, checklistitem?: CheckListItemCreateType): CheckListItemType {
      const list = this.checkListsItems[checkListId] ?? (this.checkListsItems[checkListId] = []);
      // Client-generated id (or a caller-supplied one) — the create op's `id`, so
      // the eventual server row and delta upsert share it with no duplicate.
      const id = checklistitem?.id ?? crypto.randomUUID();
      const now = new Date().toISOString();
      // The server assigns `position.index` online; offline we append past the
      // largest existing index (WI-8 decision — full reorder is WI-9). A caller
      // that passed an explicit index (e.g. "add item after") keeps it.
      const index = checklistitem?.position?.index ?? nextItemIndex(list);
      const indentation = checklistitem?.position?.indentation ?? 0;
      const checked = checklistitem?.state?.checked ?? false;
      const text = checklistitem?.text ?? "";
      const row: CheckListItemType = {
        id,
        checklist_id: checkListId,
        text,
        updated_at: now,
        position: { index, indentation, updated_at: now },
        state: { checked, updated_at: now },
      };
      this._insertNewAtCorrectIndex(list, row);
      this._adjustCounts(checkListId, 1, checked ? 1 : 0, checked ? 0 : 1);
      useOutbox().enqueue(
        itemCreateOp(checkListId, id, { text, position: { index, indentation }, state: { checked } })
      );
      return row;
    },
    _localUpdate(
      checkListId: string,
      checklistidItemId: string,
      checklistitem: CheckListItemUpdateType
    ): CheckListItemType | undefined {
      const list = this.checkListsItems[checkListId];
      const index = list?.findIndex((item) => item.id == checklistidItemId) ?? -1;
      let row: CheckListItemType | undefined;
      if (list && index !== -1) {
        const now = new Date().toISOString();
        row = { ...list[index]!, ...checklistitem, updated_at: now };
        list.splice(index, 1, row);
      }
      useOutbox().enqueue(itemUpdateOp(checkListId, checklistidItemId, { text: checklistitem.text }));
      return row;
    },
    _localDelete(checkListId: string, checklistidItemId: string) {
      const list = this.checkListsItems[checkListId];
      const index = list?.findIndex((item) => item.id == checklistidItemId) ?? -1;
      if (list && index !== -1) {
        const removed = list[index]!;
        list.splice(index, 1);
        this._adjustCounts(checkListId, -1, removed.state.checked ? -1 : 0, removed.state.checked ? 0 : -1);
      }
      // A delete whose create is still queued cancels out in the outbox (WI-7
      // coalesce rule 2), so an item created-then-deleted offline never hits the
      // server.
      useOutbox().enqueue(itemDeleteOp(checkListId, checklistidItemId));
    },
    _localUpdateState(
      checkListId: string,
      checklistidItemId: string,
      state: CheckListItemStateUpdateType
    ): CheckListItemStateType {
      const now = new Date().toISOString();
      const newState: CheckListItemStateType = { checked: state.checked, updated_at: now };
      const list = this.checkListsItems[checkListId];
      const index = list?.findIndex((item) => item.id == checklistidItemId) ?? -1;
      if (list && index !== -1) {
        const wasChecked = list[index]!.state.checked;
        list[index]!.state = newState;
        // Only shift counters on a real flip (an idempotent re-check must not drift).
        if (state.checked !== wasChecked) {
          if (state.checked) this._adjustCounts(checkListId, 0, 1, -1);
          else this._adjustCounts(checkListId, 0, -1, 1);
        }
      }
      useOutbox().enqueue(itemStateOp(checkListId, checklistidItemId, { checked: state.checked }));
      return newState;
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
