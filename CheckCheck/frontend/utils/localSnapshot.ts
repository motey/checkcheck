import type { Pinia } from "pinia";
import { watchDebounced } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useCheckListsLabelStore } from "@/stores/label";
import { useUserStore } from "@/stores/user";
import { usePublicConfigStore } from "@/stores/publicConfig";
import { readSnapshot, writeSnapshots, readCursor, writeCursor, dropSnapshot } from "@/utils/snapshotDb";
import { mergeDelta, type DeltaTarget, type ItemCountMaps } from "@/utils/deltaApply";
import { combineGuards, defaultEditGuard } from "@/utils/editGuard";
import { emitSyncNotice } from "@/utils/syncNotices";
import { beginSync, endSync } from "@/utils/syncStatus";
import { useOutbox } from "@/composables/useOutbox";

// ── Store snapshot registry (WI-6) ───────────────────────────────────────────
//
// The five stores WI-6 snapshots. `share` / `invite` / `notification` / `color`
// are deliberately NOT here — they stay online-only (WI-12). Each spec `pick`s
// the slice of $state worth persisting: enough to render the board offline, but
// not transient view state (search results/params are re-derived on demand).
//
// `pick` output is round-tripped through JSON before it hits IndexedDB (see
// `serialize` below): the values are plain API DTOs, so this strips Vue reactive
// proxies (which don't survive IndexedDB's structured clone) and gives a clean
// snapshot.

type PersistSpec = {
  id: string;
  use: (pinia?: Pinia) => { $patch: (partial: any) => void; $state: any };
  pick: (state: any) => Record<string, unknown>;
};

const SPECS: PersistSpec[] = [
  {
    id: "checkList",
    use: useCheckListsStore as any,
    // Board render state only — NOT the server-side filtered view (search /
    // shared / archive), which is re-fetched when a filter is active.
    pick: (s) => ({
      checkLists: s.checkLists,
      total_backend_count: s.total_backend_count,
      counts: s.counts,
    }),
  },
  {
    id: "checkListitem",
    use: useCheckListsItemStore as any,
    pick: (s) => ({
      checkListsItems: s.checkListsItems,
      checklistWasFullLoadedOnce: s.checklistWasFullLoadedOnce,
      total_backend_count_per_checklist: s.total_backend_count_per_checklist,
      total_backend_count_unchecked_per_checklist: s.total_backend_count_unchecked_per_checklist,
      total_backend_count_checked_per_checklist: s.total_backend_count_checked_per_checklist,
    }),
  },
  {
    id: "checkListLabelStore",
    use: useCheckListsLabelStore as any,
    pick: (s) => ({ labels: s.labels }),
  },
  {
    id: "user",
    use: useUserStore as any,
    // Only the current user; API keys are sensitive and re-fetched on demand.
    pick: (s) => ({ me: s.me }),
  },
  {
    id: "publicConfig",
    use: usePublicConfigStore as any,
    pick: (s) => ({ config: s.config }),
  },
];

const SPEC_BY_ID = new Map(SPECS.map((spec) => [spec.id, spec]));

/** Deep-clone to plain JSON — strips reactivity and anything IndexedDB can't clone. */
function serialize<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

/**
 * Hydrate the snapshotted stores from IndexedDB. Best-effort and disposable: a
 * read failure for one store leaves it empty (the background sync / legacy fetch
 * repopulates it) rather than aborting the whole boot.
 *
 * Must run before the board mounts so the first paint is from cache. The
 * local-first plugin awaits this.
 */
export async function hydrateStores(pinia: Pinia): Promise<void> {
  await Promise.all(
    SPECS.map(async (spec) => {
      try {
        const data = await readSnapshot<Record<string, unknown>>(spec.id);
        if (data) spec.use(pinia).$patch(data);
      } catch (err) {
        console.warn(`[localFirst] failed to hydrate store "${spec.id}"`, err);
      }
    })
  );
}

/**
 * Wire debounced persistence: a Pinia plugin that, for each snapshotted store,
 * watches its persisted slice and writes it to IndexedDB 500 ms after the last
 * change (capped at 2 s so a long stream of edits still lands).
 *
 * Pinia applies a plugin registered via `pinia.use()` to already-created stores
 * too, so this is safe to call before or after the stores are instantiated.
 */
export function registerSnapshotPersistence(pinia: Pinia): void {
  pinia.use(({ store }) => {
    const spec = SPEC_BY_ID.get(store.$id);
    if (!spec) return;
    watchDebounced(
      () => spec.pick(store.$state),
      (slice) => {
        void writeSnapshots({ [store.$id]: serialize(slice) });
      },
      { deep: true, debounce: 500, maxWait: 2000 }
    );
  });
}

// ── Delta application (WI-10) ────────────────────────────────────────────────
//
// The single read path for server truth once the localFirst flag is on: pull
// GET /api/changes from the persisted cursor and fold the result into the live
// stores (upsert rows, drop tombstones + revoked cards, persist the new cursor).
// Driven on boot (runBackgroundSync), on the SSE `changes_available` poke, and
// on SSE reconnect (composables/useSync.ts) — replacing the legacy per-entity
// refetch. Application is idempotent (utils/deltaApply), so overlapping triggers
// are harmless; a module-level chain still serialises pulls so concurrent pokes
// don't race the cursor.

/** Serialises overlapping pulls (bursty pokes) — idempotent, but avoids churn. */
let syncChain: Promise<void> = Promise.resolve();

/** A delta with nothing in it — the signal to stop walking the cursor (§3). */
function isEmptyDelta(res: ChangesResponseType): boolean {
  return (
    !res.checklists.length &&
    !res.items.length &&
    !res.labels.length &&
    !res.checklist_tombstones.length &&
    !res.item_tombstones.length &&
    !res.label_tombstones.length &&
    !res.removed_checklist_ids.length
  );
}

/** Live store slices as the deltaApply target (mutating these is reactive). */
function deltaTarget(pinia: Pinia): DeltaTarget {
  const itemStore = useCheckListsItemStore(pinia);
  const counts: ItemCountMaps = {
    total: itemStore.total_backend_count_per_checklist,
    checked: itemStore.total_backend_count_checked_per_checklist,
    unchecked: itemStore.total_backend_count_unchecked_per_checklist,
    fullyLoaded: itemStore.checklistWasFullLoadedOnce,
  };
  return {
    checkLists: useCheckListsStore(pinia).checkLists,
    items: itemStore.checkListsItems,
    labels: useCheckListsLabelStore(pinia).labels,
    itemCounts: counts,
  };
}

// Debounced sidebar-count refresh — the same behaviour as the legacy
// `scheduleCountsRefresh` in useSync, but driven only when a delta actually
// changed something card-level (create/delete/archive/pin/label/revoke), not on
// every poke.
let countsTimer: ReturnType<typeof setTimeout> | null = null;
function scheduleCountsRefresh(pinia: Pinia): void {
  if (countsTimer) clearTimeout(countsTimer);
  countsTimer = setTimeout(() => {
    countsTimer = null;
    void useCheckListsStore(pinia).fetchCounts();
  }, 500);
}

// Debounced per-card preview-count refresh. For a card that is NOT fully loaded
// locally, the cached item list is only a preview window: when a delta ships an
// item we don't hold, we cannot tell "brand new" (count +1) from "existed all
// along outside the window" (count unchanged) — and an item tombstone outside
// the window decrements nothing. mergeDelta's incremental adjustment is an
// immediate best-effort estimate; this refetch replaces it with the server's
// authoritative per-card counts (and backfills the preview list if it was empty).
let previewCountsTimer: ReturnType<typeof setTimeout> | null = null;
const pendingPreviewCountIds = new Set<string>();
function schedulePreviewCountsRefresh(pinia: Pinia, ids: Iterable<string>): void {
  for (const id of ids) pendingPreviewCountIds.add(id);
  if (pendingPreviewCountIds.size === 0) return;
  if (previewCountsTimer) clearTimeout(previewCountsTimer);
  previewCountsTimer = setTimeout(() => {
    previewCountsTimer = null;
    const batch = [...pendingPreviewCountIds];
    pendingPreviewCountIds.clear();
    void useCheckListsItemStore(pinia)
      .fetchMultipleChecklistsItemsPreview(batch)
      .catch(() => {});
  }, 500);
}

/** Drop cards the delta removed (tombstone / revoked access) from the open filtered view too. */
function pruneSearchResults(pinia: Pinia, removed: Set<string>): void {
  const store = useCheckListsStore(pinia);
  if (!store.searchResults || removed.size === 0) return;
  const kept = store.searchResults.filter((c) => !removed.has(c.id));
  if (kept.length !== store.searchResults.length) {
    store.searchTotalCount = Math.max(0, store.searchTotalCount - (store.searchResults.length - kept.length));
    store.searchOffset = Math.max(0, store.searchOffset - (store.searchResults.length - kept.length));
    store.searchResults = kept;
  }
}

/**
 * A `full_resync` response is computed as `since=0` (the caller's entire
 * accessible state, §5/§6): drop the disposable cache and rebuild the stores
 * wholesale from it, then persist the fresh cursor.
 */
async function rebuildFromFull(pinia: Pinia, res: ChangesResponseType): Promise<void> {
  // Reconcile the outbox against the reset server BEFORE rebuilding: queued
  // edits/deletes of rows the reset DB never knew would drain to a silent 404
  // (finding #5). Drop them here and surface one aggregate notice; surviving
  // creates + edits of still-known rows keep draining. `knownIds` is every id the
  // resync carries (cards + items + labels) — the rows the reset server accepts.
  const knownIds = new Set<string>();
  for (const cl of res.checklists) knownIds.add(cl.id);
  for (const item of res.items) knownIds.add(item.id);
  for (const label of res.labels) knownIds.add(label.id);
  try {
    const dropped = await useOutbox().reconcileResync(knownIds);
    if (dropped.length > 0) emitSyncNotice({ type: "resync-dropped", count: dropped.length });
  } catch (err) {
    console.warn("[localFirst] outbox resync reconcile failed", err);
  }

  await dropSnapshot();
  const checkListStore = useCheckListsStore(pinia);
  const itemStore = useCheckListsItemStore(pinia);
  const labelStore = useCheckListsLabelStore(pinia);

  // Group the flat item list by checklist. A since=0 pull ships every live item,
  // so every card is fully loaded.
  const itemsByChecklist: Record<string, CheckListItemType[]> = {};
  for (const cl of res.checklists) itemsByChecklist[cl.id] = [];
  for (const item of res.items) {
    (itemsByChecklist[item.checklist_id] ??= []).push(item);
  }
  const total: Record<string, number> = {};
  const checked: Record<string, number> = {};
  const unchecked: Record<string, number> = {};
  const fullyLoaded: Record<string, boolean> = {};
  for (const [clId, list] of Object.entries(itemsByChecklist)) {
    list.sort((a, b) => a.position.index - b.position.index || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    const c = list.filter((i) => i.state.checked).length;
    total[clId] = list.length;
    checked[clId] = c;
    unchecked[clId] = list.length - c;
    fullyLoaded[clId] = true;
  }

  checkListStore.checkLists = [...res.checklists].sort(
    (a, b) =>
      Number(b.position?.pinned ?? false) - Number(a.position?.pinned ?? false) ||
      (b.position?.index ?? 0) - (a.position?.index ?? 0)
  );
  checkListStore.total_backend_count = res.checklists.length;
  checkListStore.clearSearch();
  itemStore.checkListsItems = itemsByChecklist;
  itemStore.checklistWasFullLoadedOnce = fullyLoaded;
  itemStore.total_backend_count_per_checklist = total;
  itemStore.total_backend_count_checked_per_checklist = checked;
  itemStore.total_backend_count_unchecked_per_checklist = unchecked;
  labelStore.labels = [...res.labels].sort((a, b) => (b.sort_order ?? 0) - (a.sort_order ?? 0));

  await writeCursor(res.next_cursor);
  void checkListStore.fetchCounts();
}

/**
 * One cursor-walk: pull from the persisted cursor and apply until the delta is
 * empty. Returns whether the pull reached the server and converged — `false`
 * means an offline / error attempt (the caller's last-synced clock must not
 * advance). "Already caught up" counts as a successful sync.
 */
async function pullAndApply(pinia: Pinia, opts?: { sinceSeq?: number }): Promise<boolean> {
  const { $checkapi } = useNuxtApp();
  const checkListStore = useCheckListsStore(pinia);
  let since = await readCursor();

  // Poke skip (§9b): the poke carries the server's high-water mark; if it is not
  // ahead of our cursor we are already caught up — no request needed.
  if (opts?.sinceSeq != null && opts.sinceSeq <= since) return true;

  // No server-side pagination today (§3), but walk the cursor to empty so a
  // mid-pull commit (delivered again next pull) still converges. Bounded so a
  // write storm can't spin forever.
  for (let page = 0; page < 20; page++) {
    // Report cached checklist ids so the server can compute revocations (§7) —
    // but never a card whose `create` op is still queued in the outbox: the
    // server doesn't know it yet, so it would come back in
    // `removed_checklist_ids` and we would delete our own optimistic card.
    const pendingCreates = useOutbox().queuedCreateIds("checklist");
    const known = checkListStore.checkLists
      .map((c) => c.id)
      .filter((id) => !pendingCreates.has(id))
      .join(",");
    let res: ChangesResponseType;
    try {
      res = (await $checkapi("/api/changes", {
        method: "get",
        query: { since, ...(known ? { known } : {}) },
        // We own the outcome (best-effort background pull) — no generic toast.
        skipErrorToast: true,
      })) as ChangesResponseType;
    } catch (err) {
      // Offline / server error — the stored cursor stands; retried on the next
      // boot, poke, or reconnect.
      console.warn("[localFirst] delta pull failed", err);
      return false;
    }

    if (res.full_resync) {
      await rebuildFromFull(pinia, res);
      return true;
    }

    // Guard = focused edits (§4) + undrained outbox ops (WI-11 finding #2), so a
    // delta for another field of a row with a pending local edit doesn't revert it.
    const guard = combineGuards(defaultEditGuard, useOutbox().fieldGuard());
    const summary = mergeDelta(deltaTarget(pinia), res, guard);
    // Surface concurrent edits (the local value was kept; LWW converges on drain).
    for (const c of summary.conflicts) {
      emitSyncNotice({ type: "conflict", entity: c.kind, id: c.id, field: c.field });
    }
    if (checkListStore.total_backend_count >= 0 && summary.cardCountDelta !== 0) {
      checkListStore.total_backend_count = Math.max(0, checkListStore.total_backend_count + summary.cardCountDelta);
    }
    pruneSearchResults(pinia, summary.removedCheckListIds);
    if (summary.cardLevelChanged) scheduleCountsRefresh(pinia);
    // Preview-only (not fully loaded) cards can't derive exact item counts from
    // a delta — re-read their counts from the server (see the helper above).
    const itemStore = useCheckListsItemStore(pinia);
    const previewTouched = [...summary.touchedItemChecklistIds].filter(
      (clId) => !itemStore.checklistWasFullLoadedOnce[clId]
    );
    if (previewTouched.length) schedulePreviewCountsRefresh(pinia, previewTouched);
    await writeCursor(res.next_cursor);

    // Converged: the delta is empty and the cursor didn't advance past where we
    // started this page.
    if (isEmptyDelta(res)) return true;
    if (res.next_cursor <= since) return true;
    since = res.next_cursor;
  }
  // Ran the page bound without converging (write storm) — still a real,
  // server-reaching pull, so count it as synced; the next trigger catches up.
  return true;
}

/**
 * Pull GET /api/changes and apply it into the live stores. The single read path
 * for server truth (WI-10). Serialised so overlapping pokes/reconnects don't
 * race; each call is best-effort (a failed pull leaves the cursor untouched).
 *
 * @param opts.sinceSeq the poke's `server_seq`; skips the pull when the client
 *   is already caught up (§9b).
 */
export function applyDelta(pinia: Pinia, opts?: { sinceSeq?: number }): Promise<void> {
  const next = syncChain
    .then(async () => {
      // Drive the global sync-status indicator (WI-14): spinner on for the
      // duration, last-synced clock advances only on a server-reaching pull.
      beginSync();
      let ok = false;
      try {
        ok = await pullAndApply(pinia, opts);
      } finally {
        endSync(ok);
      }
    })
    .catch((err) => {
      console.warn("[localFirst] applyDelta failed", err);
    });
  syncChain = next;
  return next;
}

/**
 * Boot delta pull (called from pages/index.vue after hydration). Now the real
 * apply path (WI-10): advances from the persisted cursor and folds the delta
 * into the stores. Kept as a named wrapper so the boot call site reads clearly.
 */
export async function runBackgroundSync(pinia: Pinia): Promise<void> {
  await applyDelta(pinia);
}
