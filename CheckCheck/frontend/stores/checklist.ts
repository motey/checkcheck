import { defineStore } from "pinia";
import { findNewPlacementForItem, sortBySubset } from "~/utils/helpers";

// Active filters for the server-side "filtered view" (text search and/or the
// shared=with_me|by_me filter, optionally narrowed by a label; or the Archive
// view, which pages archived cards the same way). Held in the store so
// fetchMoreFiltered() can request the next page with the same filters.
export type FilteredViewParams = {
  query: string | null;
  labelId: string | null;
  shared: "with_me" | "by_me" | null;
  archived: boolean;
};

export type CheckListState = {
  checkLists: CheckListType[];
  total_backend_count: number;
  searchResults: CheckListType[] | null;
  searchTotalCount: number;
  searchOffset: number;
  searchParams: FilteredViewParams | null;
  // Aggregate card counts for the sidebar badges (Home / shared / Archive /
  // per label). Fetched from GET /api/checklist/counts and refreshed on the
  // same SSE events that mutate the board (debounced in useSync). null until
  // the first fetch resolves.
  counts: CheckListCountsType | null;
};

// Page size for the paginated filtered view (search / shared filters).
const FILTERED_PAGE_SIZE = 20;

export const useCheckListsStore = defineStore("checkList", {
  state: () =>
    ({
      checkLists: [],
      total_backend_count: -1,
      searchResults: null,
      searchTotalCount: 0,
      searchOffset: 0,
      searchParams: null,
      counts: null,
    } as CheckListState),
  getters: {
    get: (state) => {
      // Fall back to the filtered view's results: cards in a server-side
      // filtered view (search / shared / Archive) may not be in the main feed —
      // archived cards in particular are never in `checkLists` — so resolve
      // them from `searchResults` so previews/editors can still render them.
      return (checkListId: string): CheckListType | undefined =>
        state.checkLists.find((cl) => cl.id === checkListId) ??
        state.searchResults?.find((cl) => cl.id === checkListId);
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
          if (pinned !== null && (item.position.pinned ?? false) !== pinned) return false;
          if (label_id !== null && !item.labels.some((label) => label.id === label_id)) return false;
          return true;
        });
        return limit !== null && limit > 0 ? filtered.slice(0, limit) : filtered;
      };
    },
  },
  actions: {
    async reorderCheckLists(newOrder: CheckListType[], movedItem: CheckListType, targetPinned?: boolean) {
      // Cross-list drag (pinned <-> normal): flip the pinned flag before the
      // index move so the subsequent pinned-aware _sort() places it correctly.
      if (targetPinned !== undefined && (movedItem.position.pinned ?? false) !== targetPinned) {
        await this.setPinned(movedItem.id, targetPinned);
      }
      const placement = findNewPlacementForItem(movedItem, newOrder);
      if (placement.placement == "above") {
        await this.moveCheckListAboveOtherCheckList(movedItem, placement.target_neighbor_item as CheckListType);
      } else if (placement.placement == "below") {
        await this.moveCheckListUnderOtherCheckList(movedItem, placement.target_neighbor_item as CheckListType);
      }
      if (newOrder.length === this.checkLists.length) {
        this.checkLists = sortBySubset(this.checkLists, newOrder) as CheckListType[];
      } else {
        // Subset drag — only one group (pinned OR normal), or a search subset.
        // The moved item's position is already updated above; re-sort the
        // full lists by the same pinned-then-index key so both groups stay
        // intact (replacing searchResults with newOrder would drop the other
        // group's results).
        await this._sort();
        if (this.searchResults !== null) {
          this.searchResults.sort(
            (a, b) =>
              Number(b.position.pinned ?? false) - Number(a.position.pinned ?? false) ||
              b.position.index - a.position.index
          );
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
    // Permanently delete a checklist (used from the Archive view only; the
    // normal trash action soft-archives via archive()). The backend broadcasts
    // `checklist_deleted`, which useSync also handles — guard against
    // double-removal by using findIndex before splicing.
    async delete(checkListId: string) {
      if (!checkListId) throw new Error("Checklistid empty");
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/checklist/{checklist_id}", {
          path: { checklist_id: checkListId },
          method: "delete",
        });
      } catch (error) {
        console.error("Could not delete checklist 'DELETE /checklist/" + checkListId + "'", error);
        throw error;
      }
      const index = this.checkLists.findIndex((c) => c.id === checkListId);
      if (index !== -1) {
        this.checkLists.splice(index, 1);
        this.total_backend_count = Math.max(0, this.total_backend_count - 1);
      }
      if (this.searchResults !== null) {
        const sIdx = this.searchResults.findIndex((c) => c.id === checkListId);
        if (sIdx !== -1) {
          this.searchResults.splice(sIdx, 1);
          this.searchTotalCount = Math.max(0, this.searchTotalCount - 1);
          this.searchOffset = Math.max(0, this.searchOffset - 1);
        }
      }
    },
    async setPinned(checkListId: string, pinned: boolean = true) {
      if (!checkListId) throw new Error("Checklistid empty");
      const checkList = await this.fetch(checkListId);
      checkList.position.pinned = pinned;
      checkList.position = await this.updatePosition(checkListId, checkList.position);
      this._sort();
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
    async resync(): Promise<void> {
      // Called after the SSE stream reconnects: events fired while we were
      // disconnected are gone (NOTIFY/SSE are fire-and-forget), so reconcile
      // the whole loaded view against the backend to recover any drift.
      const { $checkapi } = useNuxtApp();
      const checkListItemStore = useCheckListsItemStore();
      const limit = Math.max(this.checkLists.length, 5);
      let page: CheckListsPageType;
      try {
        page = await $checkapi("/api/checklist", {
          method: "get",
          query: { offset: 0, limit },
        });
      } catch (error) {
        console.error("Could not resync checklists 'GET /api/checklist'", error);
        return;
      }
      const freshIds = new Set(page.items.map((c) => c.id));
      // Drop checklists deleted while we were disconnected.
      this.checkLists = this.checkLists.filter((c) => freshIds.has(c.id));
      // Update existing / add new.
      for (const fresh of page.items) {
        const index = this.checkLists.findIndex((c) => c.id === fresh.id);
        if (index !== -1) this.checkLists.splice(index, 1, fresh);
        else this.checkLists.push(fresh);
      }
      this.total_backend_count = page.total_count;
      this._sort();
      // Reconcile items: drop caches for gone checklists, fully reload the
      // ones we had fully loaded, refresh previews (and counts) for the rest.
      const fullyLoaded: string[] = [];
      const previewOnly: string[] = [];
      for (const id of Object.keys(checkListItemStore.checkListsItems)) {
        if (!freshIds.has(id)) {
          checkListItemStore.dropChecklistItems(id);
        } else if (checkListItemStore.checklistWasFullLoadedOnce[id]) {
          fullyLoaded.push(id);
        } else {
          previewOnly.push(id);
        }
      }
      await Promise.all(
        fullyLoaded.map((id) => checkListItemStore.refreshAllCheckListItems(id))
      );
      if (previewOnly.length) {
        await checkListItemStore.fetchMultipleChecklistsItemsPreview(previewOnly, null, true);
      }
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
    // Open (or replace) the server-side filtered view. Active whenever there is
    // text search, a shared filter, or the Archive view; an optional label
    // narrows it further. Loads the first page; call fetchMoreFiltered() to page
    // through the rest.
    async searchChecklists(
      query: string | null,
      labelId: string | null = null,
      shared: "with_me" | "by_me" | null = null,
      archived: boolean = false
    ) {
      this.searchParams = { query: query || null, labelId, shared, archived };
      this.searchOffset = 0;
      this.searchResults = [];
      this.searchTotalCount = 0;
      await this._fetchFilteredPage();
    },
    // Append the next page of the active filtered view (no-op once every match
    // is loaded, or when no filtered view is active).
    async fetchMoreFiltered() {
      if (this.searchResults === null || this.searchParams === null) return;
      if (this.searchResults.length >= this.searchTotalCount) return;
      await this._fetchFilteredPage();
    },
    async _fetchFilteredPage() {
      if (this.searchParams === null) return;
      const { $checkapi } = useNuxtApp();
      const checkListItemStore = useCheckListsItemStore();
      const { query, labelId, shared, archived } = this.searchParams;
      let resPage: CheckListsPageType;
      try {
        resPage = await $checkapi("/api/checklist", {
          method: "get",
          query: {
            archived,
            limit: FILTERED_PAGE_SIZE,
            offset: this.searchOffset,
            ...(query ? { search: query } : {}),
            ...(labelId ? { label_id: labelId } : {}),
            ...(shared ? { shared } : {}),
          },
        });
      } catch (error) {
        console.error("Could not load filtered checklists 'GET /api/checklist'", error);
        return;
      }
      await checkListItemStore.fetchMultipleChecklistsItemsPreview(resPage.items.map((i) => i.id));
      const existingIds = new Set((this.searchResults ?? []).map((c) => c.id));
      this.searchResults = [
        ...(this.searchResults ?? []),
        ...resPage.items.filter((c) => !existingIds.has(c.id)),
      ];
      this.searchTotalCount = resPage.total_count;
      this.searchOffset += resPage.items.length;
    },
    // Fetch the sidebar count badges in one request (avoids an N+1 per label).
    // Called on mount and, debounced, on board-mutating SSE events (see
    // useSync). Best-effort: a failed fetch keeps the previous counts rather
    // than blanking the badges.
    async fetchCounts() {
      const { $checkapi } = useNuxtApp();
      try {
        this.counts = await $checkapi("/api/checklist/counts", { method: "get" });
      } catch (error) {
        console.error("Could not fetch checklist counts 'GET /api/checklist/counts'", error);
      }
    },
    clearSearch() {
      this.searchResults = null;
      this.searchTotalCount = 0;
      this.searchOffset = 0;
      this.searchParams = null;
    },
    async _sort() {
      // Pinned first, then by descending index within each group.
      this.checkLists.sort(
        (a, b) =>
          Number(b.position.pinned ?? false) - Number(a.position.pinned ?? false) ||
          b.position.index - a.position.index
      );
    },
  },
});
