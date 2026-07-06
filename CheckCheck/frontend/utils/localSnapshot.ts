import type { Pinia } from "pinia";
import { watchDebounced } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useCheckListsLabelStore } from "@/stores/label";
import { useUserStore } from "@/stores/user";
import { usePublicConfigStore } from "@/stores/publicConfig";
import { readSnapshot, writeSnapshots, readCursor, writeCursor, dropSnapshot } from "@/utils/snapshotDb";

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

/**
 * Background delta pull on boot (WI-6 scope). After hydration, advance the
 * device's sync cursor from GET /api/changes?since=<cursor> and persist it, and
 * handle `full_resync` by dropping the disposable cache.
 *
 * NOTE — scope boundary: WI-6 does NOT apply the returned rows into the live
 * stores; the legacy fetch path still loads the board and the debounced
 * persistence above keeps the snapshot fresh from it. Turning this pull into the
 * authoritative store-application path (replacing the useSync.ts refetch) is
 * WI-10. Here it only owns the cursor + full_resync so WI-10 can take over
 * incremental pulls from a correct high-water mark.
 */
export async function runBackgroundSync(pinia: Pinia): Promise<void> {
  const { $checkapi } = useNuxtApp();
  const checkListStore = useCheckListsStore(pinia);
  const cursor = await readCursor();
  // Report cached checklist ids so the server can compute revocations (§7).
  const known = checkListStore.checkLists.map((c) => c.id).join(",");

  let res: any;
  try {
    res = await $checkapi("/api/changes", {
      method: "get",
      query: { since: cursor, ...(known ? { known } : {}) },
      // We own the outcome (best-effort background pull) — no generic toast.
      skipErrorToast: true,
    });
  } catch (err) {
    // Offline / server error — the stored cursor stands; retried next boot or by
    // WI-10's poke-driven pull.
    console.warn("[localFirst] background delta pull failed", err);
    return;
  }

  if (res.full_resync) {
    // The cursor was unusable (client ahead of a reset DB). Drop the disposable
    // cache; the legacy fetch path + debounced persistence rebuild it from the
    // now-current server state.
    await dropSnapshot();
  }
  await writeCursor(res.next_cursor);
}
