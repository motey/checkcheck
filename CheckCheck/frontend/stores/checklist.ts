import { defineStore } from "pinia";
import { findNewPlacementForItem, sortBySubset } from "~/utils/helpers";

export type CheckListState = {
  checkLists: CheckListType[];
  total_backend_count: number;
  searchResults: CheckListType[] | null;
  searchTotalCount: number;
};

export const useCheckListsStore = defineStore("checkList", {
  state: () =>
    ({
      checkLists: [],
      total_backend_count: -1,
      searchResults: null,
      searchTotalCount: 0,
    } as CheckListState),
  getters: {
    checklist_ids(state) {
      return state.checkLists.map((item) => item.id);
    },
    get: (state) => {
      return (checkListId: string) =>
        state.checkLists.find((cl) => cl.id === checkListId);
    },
    getCheckLists: (state) => {
      return ({
        archived = null,
        pinned = null,
        label_id = null,
        limit = null,
      }: {
        archived?: boolean | null;
        pinned?: boolean | null;
        label_id?: string | null;
        limit?: number | null;
      }): CheckListType[] => {
        if (archived == null && pinned == null && label_id == null && limit == null) {
          return [...state.checkLists];
        }
        const filtered = state.checkLists.filter((item) => {
          if (archived !== null && item.position.archived !== archived) return false;
          if (pinned !== null && item.position.pinned !== pinned) return false;
          if (label_id !== null && !item.labels.some((label) => label.id === label_id)) return false;
          return true;
        });
        return limit !== null && limit > 0 ? filtered.slice(0, limit) : filtered;
      };
    },
  },
  actions: {
    async reorderCheckLists(newOrder: CheckListType[], movedItem: CheckListType) {
      const placement = findNewPlacementForItem(movedItem, newOrder);
      if (placement.placement == "above") {
        await this.moveCheckListAboveOtherCheckList(movedItem, placement.target_neighbor_item as CheckListType);
      } else if (placement.placement == "below") {
        await this.moveCheckListUnderOtherCheckList(movedItem, placement.target_neighbor_item as CheckListType);
      }
      if (newOrder.length === this.checkLists.length) {
        this.checkLists = sortBySubset(this.checkLists, newOrder) as CheckListType[];
      } else {
        // Subset drag (search results) — position already updated by the move action above.
        await this._sort();
        if (this.searchResults !== null) {
          this.searchResults = [...newOrder];
        }
      }
    },
    async create(checklist: CheckListCreateType): Promise<CheckListType> {
      if (!checklist) throw new Error("Checklistid empty");
      const { $checkapi } = useNuxtApp();
      const checkListItemStore = useCheckListsItemStore();
      let resChecklist: CheckListType;
      try {
        resChecklist = await $checkapi("/api/checklist", { method: "post", body: checklist });
      } catch (error) {
        console.error("Could not store new checklist to backend 'POST /checklist'", error);
        throw error;
      }
      await checkListItemStore.refreshAllCheckListItems(resChecklist.id);
      // SSE checklist_created may have already added the item during the await above
      if (!this.checkLists.some((c) => c.id === resChecklist.id)) {
        this.checkLists.push(resChecklist);
      }
      this._sort();
      return resChecklist;
    },
    async update(checkListId: string, checklist: CheckListUpdateType): Promise<CheckListType | undefined> {
      if (!checklist) return;
      const { $checkapi } = useNuxtApp();
      let resChecklist: CheckListType;
      try {
        resChecklist = await $checkapi("/api/checklist/{checklist_id}", {
          path: { checklist_id: checkListId },
          method: "patch",
          body: checklist,
        });
      } catch (error) {
        console.error("Could not update checklist 'PATCH /checklist/" + checkListId + "'", error);
        return checklist as CheckListType;
      }
      const index = this.checkLists.findIndex((c) => c.id == resChecklist.id);
      if (index !== -1) {
        this.checkLists.splice(index, 1, resChecklist);
      } else {
        this.checkLists.push(resChecklist);
        await this._sort();
      }
      return resChecklist;
    },
    async refresh(checkListId: string): Promise<CheckListType> {
      if (!checkListId) throw new Error("Checklistid empty");
      const { $checkapi } = useNuxtApp();
      let resChecklist: CheckListType;
      try {
        resChecklist = await $checkapi("/api/checklist/{checklist_id}", {
          path: { checklist_id: checkListId },
          method: "get",
        });
      } catch (error) {
        console.error("Could not refresh checklist 'GET /checklist/" + checkListId + "'", error);
        throw error;
      }
      const index = this.checkLists.findIndex((c) => c.id == resChecklist.id);
      if (index !== -1) {
        this.checkLists.splice(index, 1, resChecklist);
      } else {
        this.checkLists.push(resChecklist);
      }
      return resChecklist;
    },
    async fetch(checkListId: string): Promise<CheckListType> {
      if (!checkListId) throw new Error("Checklistid empty");
      const index = this.checkLists.findIndex((c) => c.id == checkListId);
      if (index == -1) return await this.refresh(checkListId);
      return this.checkLists[index]!;
    },
    async archive(checkListId: string, state: boolean = true) {
      if (!checkListId) throw new Error("Checklistid empty");
      const checkList = await this.fetch(checkListId);
      checkList.position.archived = state;
      checkList.position = await this.updatePosition(checkListId, checkList.position);
    },
    async fetchNextPage() {
      const { $checkapi } = useNuxtApp();
      const checkListItemStore = useCheckListsItemStore();
      const fetched_count = this.checkLists.length;
      let resChecklistPage: CheckListsPageType;
      try {
        resChecklistPage = await $checkapi("/api/checklist", {
          method: "get",
          query: { offset: fetched_count, limit: 5 },
        });
      } catch (error) {
        console.error("Could not fetch checklist page 'GET /api/checklist'", error);
        return;
      }
      await checkListItemStore.fetchMultipleChecklistsItemsPreview(resChecklistPage.items.map((item) => item.id));
      const existingIds = new Set(this.checkLists.map((c) => c.id));
      this.checkLists = [...this.checkLists, ...resChecklistPage.items.filter((c) => !existingIds.has(c.id))];
      this.total_backend_count = resChecklistPage.total_count;
      return resChecklistPage.items;
    },
    async moveCheckListUnderOtherCheckList(
      itemToMove: CheckListType,
      otherItem: CheckListType
    ): Promise<CheckListPositionType> {
      const { $checkapi } = useNuxtApp();
      try {
        const resPos = await $checkapi("/api/checklist/{checklist_id}/move/under/{other_checklist_id}", {
          path: { checklist_id: itemToMove.id, other_checklist_id: otherItem.id },
          method: "put",
        });
        itemToMove.position = resPos;
      } catch (error) {
        console.error("Could not move checklist under another", error);
        throw error;
      }
      this._sort();
      return itemToMove.position;
    },
    async moveCheckListAboveOtherCheckList(
      itemToMove: CheckListType,
      otherItem: CheckListType
    ): Promise<CheckListPositionType> {
      const { $checkapi } = useNuxtApp();
      try {
        const resPos = await $checkapi("/api/checklist/{checklist_id}/move/above/{other_checklist_id}", {
          path: { checklist_id: itemToMove.id, other_checklist_id: otherItem.id },
          method: "put",
        });
        itemToMove.position = resPos;
      } catch (error) {
        console.error("Could not move checklist above another", error);
        throw error;
      }
      this._sort();
      return itemToMove.position;
    },
    async updatePosition(
      checkListId: string,
      checklistPosition: CheckListPositionUpdateType
    ): Promise<CheckListPositionType> {
      if (!checkListId) throw new Error("checkListId empty");
      const { $checkapi } = useNuxtApp();
      let resChecklistPosition: CheckListPositionType;
      try {
        resChecklistPosition = await $checkapi("/api/checklist/{checklist_id}/position", {
          path: { checklist_id: checkListId },
          method: "patch",
          body: checklistPosition,
        });
      } catch (error) {
        console.error("Could not update position 'PATCH /checklist/" + checkListId + "/position'", error);
        throw error;
      }
      const index = this.checkLists.findIndex((c) => c.id == checkListId);
      const checkList = index !== -1 ? this.checkLists[index]! : await this.refresh(checkListId);
      checkList.position = resChecklistPosition;
      return checkList.position;
    },
    async searchChecklists(query: string, labelId: string | null = null) {
      const { $checkapi } = useNuxtApp();
      const checkListItemStore = useCheckListsItemStore();
      let resPage: CheckListsPageType;
      try {
        resPage = await $checkapi("/api/checklist", {
          method: "get",
          query: { search: query, archived: false, limit: 100, offset: 0, ...(labelId ? { label_id: labelId } : {}) },
        });
      } catch (error) {
        console.error("Could not search checklists 'GET /api/checklist?search='", error);
        return;
      }
      await checkListItemStore.fetchMultipleChecklistsItemsPreview(resPage.items.map((i) => i.id));
      this.searchResults = resPage.items;
      this.searchTotalCount = resPage.total_count;
    },
    clearSearch() {
      this.searchResults = null;
      this.searchTotalCount = 0;
    },
    async _sort() {
      this.checkLists.sort((a, b) => b.position.index - a.position.index);
    },
  },
});
