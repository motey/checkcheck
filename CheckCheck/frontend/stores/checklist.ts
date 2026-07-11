import { defineStore } from "pinia";
import { findNewPlacementForItem, sortBySubset } from "~/utils/helpers";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { useOutbox } from "@/composables/useOutbox";
import { useUserStore } from "@/stores/user";
import {
  checklistCreateOp,
  checklistDeleteOp,
  checklistPositionOp,
  checklistUpdateOp,
  fractionalIndexBetween,
} from "@/utils/outboxOps";

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
      if (isLocalFirstEnabled()) return this._localCreate(checklist);
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
      if (isLocalFirstEnabled()) return this._localUpdate(checkListId, checklist);
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
      const wasArchived = checkList.position.archived;
      checkList.position.archived = state;
      // Local-first: adjust the sidebar count badges immediately. The delta pull
      // that later confirms this archive is BLIND to it — our optimistic update
      // already set `position.archived`, so mergeDelta sees no change and never
      // triggers a counts refresh. So the actor's own archive must move the counts
      // here (this also keeps the badges right while offline). Any later absolute
      // `fetchCounts` (another user's edit, a reload) reconciles precisely. Flag-off
      // keeps refreshing counts off the SSE `checklist_position` event instead.
      if (isLocalFirstEnabled() && wasArchived !== state) {
        this._adjustCountsForArchive(checkList, state);
      }
      checkList.position = await this.updatePosition(checkListId, checkList.position);
    },
    /** Move the sidebar count badges when a card is archived/unarchived locally.
     *  `home`/`archived`/`labels` are exact from the card; `shared_with_me` keys
     *  off ownership. `shared_by_me` needs collaborator info the card DTO doesn't
     *  carry, so it is left for the next absolute `fetchCounts` to reconcile (see
     *  docs/ISSUES.md). */
    _adjustCountsForArchive(checkList: CheckListType, archived: boolean) {
      const counts = this.counts;
      if (!counts) return; // not loaded yet — the first fetch will be correct
      const d = archived ? -1 : 1; // leaving/returning to the non-archived buckets
      counts.home = Math.max(0, counts.home + d);
      counts.archived = Math.max(0, counts.archived - d);
      const myId = useUserStore().myId;
      if (myId && checkList.owner_id !== myId) {
        counts.shared_with_me = Math.max(0, counts.shared_with_me + d);
      }
      for (const label of checkList.labels ?? []) {
        const cur = counts.labels[label.id];
        if (cur != null) counts.labels[label.id] = Math.max(0, cur + d);
      }
    },
    // Permanently delete a checklist (used from the Archive view only; the
    // normal trash action soft-archives via archive()). The backend broadcasts
    // `checklist_deleted`, which useSync also handles — guard against
    // double-removal by using findIndex before splicing.
    async delete(checkListId: string) {
      if (!checkListId) throw new Error("Checklistid empty");
      if (isLocalFirstEnabled()) return this._localDelete(checkListId);
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
      if (isLocalFirstEnabled()) return this._localMoveChecklist(itemToMove, otherItem, "under");
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
      if (isLocalFirstEnabled()) return this._localMoveChecklist(itemToMove, otherItem, "above");
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
      if (isLocalFirstEnabled()) return this._localUpdatePosition(checkListId, checklistPosition);
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
    // ── Local-first optimistic paths (WI-9) ───────────────────────────────
    //
    // Flag-on, the checklist store mirrors the WI-8 item pattern: create /
    // update / delete / position mutate the store immediately and enqueue the
    // REST call to the WI-7 outbox. `create` carries a client-generated id so a
    // replay is idempotent (protocol §8) and WI-10's delta pull upserts by the
    // same id. Reconciliation with server truth is still the legacy SSE refetch
    // until WI-10.

    /**
     * The index for a newly-created card: one end-gap past the highest existing
     * index (0 on an empty board). Mirrors the server's create placement (highest
     * + 0.4), and since `_sort` orders by descending index the new card lands at
     * the top — matching the online behaviour.
     */
    _nextCheckListIndex(): number {
      let max: number | null = null;
      for (const c of this.checkLists) {
        const idx = c.position?.index;
        if (typeof idx === "number" && (max === null || idx > max)) max = idx;
      }
      return fractionalIndexBetween(max, null);
    },
    _localCreate(checklist: CheckListCreateType): CheckListType {
      const id = checklist.id ?? crypto.randomUUID();
      const now = new Date().toISOString();
      const index = checklist.position?.index ?? this._nextCheckListIndex();
      const colorStore = useCheckListsColorSchemeStore();
      const row = {
        id,
        name: checklist.name ?? "",
        text: checklist.text ?? "",
        color_id: checklist.color_id ?? null,
        color: checklist.color_id ? colorStore.getColor(checklist.color_id) ?? null : null,
        checked_items_seperated: checklist.checked_items_seperated ?? true,
        checked_items_collapsed: checklist.checked_items_collapsed ?? true,
        owner_id: useUserStore().myId ?? "",
        my_permission: "owner",
        updated_at: now,
        position: {
          index,
          pinned: checklist.position?.pinned ?? false,
          archived: checklist.position?.archived ?? false,
          checked_items_collapsed: checklist.checked_items_collapsed ?? true,
          updated_at: now,
        },
        labels: [],
      } as CheckListType;
      // A brand-new card starts with an empty, fully-loaded item list so the
      // board renders it without the (offline-failing) refetch the online path
      // does via refreshAllCheckListItems.
      const checkListItemStore = useCheckListsItemStore();
      checkListItemStore.checkListsItems[id] = [];
      checkListItemStore.checklistWasFullLoadedOnce[id] = true;
      if (!this.checkLists.some((c) => c.id === id)) this.checkLists.push(row);
      this._sort();
      useOutbox().enqueue(
        checklistCreateOp(id, {
          name: row.name,
          text: row.text,
          color_id: row.color_id,
          // Send the explicit index so the server stores the same slot we show
          // (otherwise it recomputes highest+0.4 at replay time — a divergence).
          position: { index },
        })
      );
      return row;
    },
    _localUpdate(checkListId: string, checklist: CheckListUpdateType): CheckListType | undefined {
      const now = new Date().toISOString();
      const index = this.checkLists.findIndex((c) => c.id == checkListId);
      let row: CheckListType | undefined;
      if (index !== -1) {
        row = { ...this.checkLists[index]!, ...checklist, updated_at: now };
        // The board renders the nested `color` object, not `color_id` — resolve it
        // from the colour store so a colour change shows immediately.
        if ("color_id" in checklist) {
          const colorStore = useCheckListsColorSchemeStore();
          row.color = checklist.color_id ? colorStore.getColor(checklist.color_id) ?? null : null;
        }
        this.checkLists.splice(index, 1, row);
      }
      useOutbox().enqueue(
        checklistUpdateOp(checkListId, {
          ...("name" in checklist ? { name: checklist.name } : {}),
          ...("text" in checklist ? { text: checklist.text } : {}),
          ...("color_id" in checklist ? { color_id: checklist.color_id } : {}),
        })
      );
      return row;
    },
    _localDelete(checkListId: string) {
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
      useCheckListsItemStore().dropChecklistItems(checkListId);
      // A delete cancels a still-queued create in the outbox (WI-7 coalesce rule
      // 2), so a card created-then-deleted offline never reaches the server.
      useOutbox().enqueue(checklistDeleteOp(checkListId));
    },
    _localUpdatePosition(
      checkListId: string,
      position: CheckListPositionUpdateType
    ): CheckListPositionType {
      const now = new Date().toISOString();
      const index = this.checkLists.findIndex((c) => c.id == checkListId);
      const checkList = index !== -1 ? this.checkLists[index]! : undefined;
      let resultPos: CheckListPositionType;
      if (checkList) {
        resultPos = {
          ...checkList.position,
          ...(position.index != null ? { index: position.index } : {}),
          ...(position.pinned != null ? { pinned: position.pinned } : {}),
          ...(position.archived != null ? { archived: position.archived } : {}),
          updated_at: now,
        };
        checkList.position = resultPos;
        this._sort();
      } else {
        resultPos = {
          index: position.index ?? 0,
          pinned: position.pinned ?? false,
          archived: position.archived ?? false,
          updated_at: now,
        } as CheckListPositionType;
      }
      const body: { index?: number; pinned?: boolean; archived?: boolean } = {};
      if (position.index != null) body.index = position.index;
      if (position.pinned != null) body.pinned = position.pinned;
      if (position.archived != null) body.archived = position.archived;
      useOutbox().enqueue(checklistPositionOp(checkListId, body));
      return resultPos;
    },
    /**
     * Offline card reorder: compute the moved card's new fractional index
     * client-side and route it through `_localUpdatePosition`. Cards sort by
     * DESCENDING index (see `_sort`), so "above" means a higher index and "under"
     * a lower one — the inverse of items. Neighbours are read from the current
     * cached order (ascending by index, id tiebreak), mirroring the server's
     * get_next / get_prev over current DB rows.
     */
    _localMoveChecklist(
      itemToMove: CheckListType,
      otherItem: CheckListType,
      direction: "above" | "under"
    ): CheckListPositionType {
      const sorted = this.checkLists
        .slice()
        .sort(
          (a, b) =>
            a.position.index - b.position.index || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
        );
      const otherIdx = sorted.findIndex((c) => c.id === otherItem.id);
      const otherIndex = otherItem.position.index;
      let newIndex: number;
      if (direction === "above") {
        // Higher index, between the other card and its next-higher neighbour.
        const higher = otherIdx !== -1 ? sorted[otherIdx + 1] : undefined;
        newIndex = fractionalIndexBetween(otherIndex, higher?.position.index ?? null);
      } else {
        // Lower index, between the other card's next-lower neighbour and it.
        const lower = otherIdx !== -1 ? sorted[otherIdx - 1] : undefined;
        newIndex = fractionalIndexBetween(lower?.position.index ?? null, otherIndex);
      }
      return this._localUpdatePosition(itemToMove.id, {
        index: newIndex,
      } as CheckListPositionUpdateType);
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
