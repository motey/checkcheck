// Unit tests for the local-first read-path composition root (utils/localSnapshot).
//
// Chunk D finding #1: `localSnapshot.ts` — `pullAndApply` cursor walk, poke-skip
// (`sinceSeq <= since`), `known=` assembly (excluding queued creates),
// `rebuildFromFull`, and the preview-count refresh trigger — had no direct tests
// despite being the seam that wires the (well-covered) pure delta engine to the
// Nuxt stores. We exercise the REAL localSnapshot module here: the Nuxt-bound
// bits (the five stores, `useOutbox`, and the `$checkapi` transport reached via
// `useNuxtApp`) are mocked, while the framework-light collaborators it composes —
// `deltaApply` (real merge), `snapshotDb` (real cursor over fake-indexeddb),
// `syncNotices`, and `syncStatus` — run unchanged.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Hoisted so both the vi.mock factories and the test bodies share the same
// controllable store / outbox / transport instances.
const h = vi.hoisted(() => {
  const checkListStore = {
    checkLists: [] as any[],
    total_backend_count: 0,
    searchResults: null as any[] | null,
    searchTotalCount: 0,
    searchOffset: 0,
    clearSearch: vi.fn(function (this: any) {
      this.searchResults = null;
    }),
    fetchCounts: vi.fn(async () => {}),
    _sort: vi.fn(async () => {}),
  };
  const itemStore = {
    checkListsItems: {} as Record<string, any[]>,
    total_backend_count_per_checklist: {} as Record<string, number>,
    total_backend_count_checked_per_checklist: {} as Record<string, number>,
    total_backend_count_unchecked_per_checklist: {} as Record<string, number>,
    checklistWasFullLoadedOnce: {} as Record<string, boolean>,
    fetchMultipleChecklistsItemsPreview: vi.fn(async () => {}),
  };
  const labelStore = { labels: [] as any[] };
  const outbox = {
    queuedCreateIds: vi.fn((_t: string) => new Set<string>()),
    fieldGuard: vi.fn(() => ({ isEditing: () => false })),
    reconcileResync: vi.fn(async (_known: ReadonlySet<string>) => [] as any[]),
    clearAll: vi.fn(async () => {}),
  };
  const checkapi = vi.fn();
  // In-memory stand-in for the snapshotDb cursor (fake-indexeddb hangs under
  // vitest fake timers, and we need fake timers for the debounced preview test).
  const cursor = { value: 0 };
  const dropSnapshot = vi.fn(async () => {
    cursor.value = 0;
  });
  return { checkListStore, itemStore, labelStore, outbox, checkapi, cursor, dropSnapshot };
});

vi.mock("@/stores/checklist", () => ({ useCheckListsStore: () => h.checkListStore }));
vi.mock("@/stores/checklist_item", () => ({ useCheckListsItemStore: () => h.itemStore }));
vi.mock("@/stores/label", () => ({ useCheckListsLabelStore: () => h.labelStore }));
vi.mock("@/stores/user", () => ({ useUserStore: () => ({ me: null }) }));
vi.mock("@/stores/publicConfig", () => ({ usePublicConfigStore: () => ({ config: null }) }));
vi.mock("@/composables/useOutbox", () => ({ useOutbox: () => h.outbox }));
vi.mock("@/utils/snapshotDb", () => ({
  readCursor: async () => h.cursor.value,
  writeCursor: async (c: number) => {
    h.cursor.value = c;
  },
  dropSnapshot: h.dropSnapshot,
  // Unused by the read path under test, but imported by localSnapshot's module.
  readSnapshot: async () => undefined,
  writeSnapshots: async () => {},
  readSnapshotOwner: async () => null,
  writeSnapshotOwner: async () => {},
}));

// `useNuxtApp` is a Nuxt auto-import (a runtime global under Nuxt); stub it so
// `pullAndApply` can reach the mocked `$checkapi` transport.
vi.stubGlobal("useNuxtApp", () => ({ $checkapi: h.checkapi }));

import { applyDelta, runBackgroundSync } from "@/utils/localSnapshot";
import { onSyncNotice, type SyncNotice } from "@/utils/syncNotices";
import { __resetSyncStatusForTests } from "@/utils/syncStatus";

const readCursor = async () => h.cursor.value;
const writeCursor = async (c: number) => {
  h.cursor.value = c;
};

// A dummy Pinia handle — the mocked store hooks ignore their argument.
const PINIA = {} as any;

// ── Delta-response factories (mirror tests/unit/deltaApply.spec.ts) ───────────

function delta(over: Partial<any> = {}): any {
  return {
    next_cursor: 1,
    full_resync: false,
    checklists: [],
    items: [],
    labels: [],
    checklist_tombstones: [],
    item_tombstones: [],
    label_tombstones: [],
    removed_checklist_ids: [],
    ...over,
  };
}
const CL = (id: string, over: any = {}): any => ({
  id,
  name: over.name ?? `card-${id}`,
  text: over.text ?? "",
  labels: over.labels ?? [],
  my_permission: over.my_permission ?? "owner",
  updated_at: over.updated_at ?? "2026-01-01T00:00:00",
  position: { index: over.index ?? 1, pinned: over.pinned ?? false, archived: over.archived ?? false },
});
const ITEM = (id: string, clId: string, over: any = {}): any => ({
  id,
  checklist_id: clId,
  text: over.text ?? `item-${id}`,
  updated_at: over.updated_at ?? "2026-01-01T00:00:00",
  position: { index: over.index ?? 1, indentation: 0 },
  state: { checked: over.checked ?? false, updated_at: "2026-01-01T00:00:00" },
});

// ── Reset shared state between tests ──────────────────────────────────────────

beforeEach(async () => {
  // Wipe every field of the shared fake stores (they persist across tests).
  Object.assign(h.checkListStore, {
    checkLists: [],
    total_backend_count: 0,
    searchResults: null,
    searchTotalCount: 0,
    searchOffset: 0,
  });
  Object.assign(h.itemStore, {
    checkListsItems: {},
    total_backend_count_per_checklist: {},
    total_backend_count_checked_per_checklist: {},
    total_backend_count_unchecked_per_checklist: {},
    checklistWasFullLoadedOnce: {},
  });
  h.labelStore.labels = [];
  vi.clearAllMocks();
  h.outbox.queuedCreateIds.mockImplementation(() => new Set<string>());
  h.outbox.fieldGuard.mockImplementation(() => ({ isEditing: () => false }));
  h.outbox.reconcileResync.mockImplementation(async () => []);
  h.cursor.value = 0;
  __resetSyncStatusForTests();
});

// ── Poke-skip (§9b) ───────────────────────────────────────────────────────────

describe("applyDelta poke-skip", () => {
  it("does NOT hit /api/changes when the poke's server_seq is not ahead of the cursor", async () => {
    await writeCursor(5);
    await applyDelta(PINIA, { sinceSeq: 5 }); // equal → already caught up
    expect(h.checkapi).not.toHaveBeenCalled();

    await applyDelta(PINIA, { sinceSeq: 3 }); // behind → still skip
    expect(h.checkapi).not.toHaveBeenCalled();
  });

  it("DOES pull when the poke's server_seq is ahead of the cursor", async () => {
    await writeCursor(5);
    h.checkapi.mockResolvedValue(delta({ next_cursor: 9 }));
    await applyDelta(PINIA, { sinceSeq: 9 });
    expect(h.checkapi).toHaveBeenCalledTimes(1);
  });
});

// ── `known=` assembly (§7) ────────────────────────────────────────────────────

describe("applyDelta known= assembly", () => {
  it("sends every cached card id EXCEPT those with a queued create", async () => {
    h.checkListStore.checkLists = [CL("a"), CL("b"), CL("c")];
    h.outbox.queuedCreateIds.mockImplementation((t: string) =>
      t === "checklist" ? new Set(["b"]) : new Set()
    );
    h.checkapi.mockResolvedValue(delta({ next_cursor: 1 }));

    await applyDelta(PINIA);

    const [, opts] = h.checkapi.mock.calls[0]!;
    // "b" is an offline-created card the server doesn't know yet — excluded so it
    // isn't reported back as revoked and deleted from under us.
    expect((opts as any).query.known).toBe("a,c");
    expect((opts as any).query.since).toBe(0);
  });

  it("omits the known param entirely when no cards are cached", async () => {
    h.checkapi.mockResolvedValue(delta());
    await applyDelta(PINIA);
    const [, opts] = h.checkapi.mock.calls[0]!;
    expect((opts as any).query.known).toBeUndefined();
  });
});

// ── Cursor walk to convergence + persistence (§3) ────────────────────────────

describe("applyDelta cursor walk", () => {
  it("walks pages until the delta is empty and persists the final cursor", async () => {
    h.checkapi
      .mockResolvedValueOnce(delta({ checklists: [CL("x")], next_cursor: 10 }))
      .mockResolvedValueOnce(delta({ next_cursor: 10 })); // empty → stop

    await applyDelta(PINIA);

    expect(h.checkapi).toHaveBeenCalledTimes(2);
    // Second call resumes from the first page's cursor.
    expect((h.checkapi.mock.calls[1]![1] as any).query.since).toBe(10);
    expect(await readCursor()).toBe(10);
    expect(h.checkListStore.checkLists.map((c) => c.id)).toEqual(["x"]);
  });

  it("stops (one call) when the first delta is already empty", async () => {
    h.checkapi.mockResolvedValue(delta({ next_cursor: 0 }));
    await applyDelta(PINIA);
    expect(h.checkapi).toHaveBeenCalledTimes(1);
  });

  it("leaves the cursor untouched when the pull fails (offline)", async () => {
    await writeCursor(7);
    h.checkapi.mockRejectedValue(new Error("network down"));
    await applyDelta(PINIA);
    expect(await readCursor()).toBe(7);
  });
});

// ── full_resync → rebuildFromFull (§5/§6, finding B4/#5) ──────────────────────

describe("applyDelta full_resync", () => {
  it("rebuilds the stores wholesale from the since=0 payload and persists the cursor", async () => {
    h.checkListStore.checkLists = [CL("stale")]; // pre-reset board
    h.checkapi.mockResolvedValue(
      delta({
        full_resync: true,
        next_cursor: 42,
        checklists: [CL("keep", { index: 2 })],
        items: [ITEM("i1", "keep", { checked: true }), ITEM("i2", "keep")],
        labels: [{ id: "l1", name: "lbl", sort_order: 5 }],
      })
    );

    await applyDelta(PINIA);

    expect(h.checkListStore.checkLists.map((c) => c.id)).toEqual(["keep"]);
    expect(h.checkListStore.total_backend_count).toBe(1);
    expect(h.itemStore.checkListsItems["keep"]!.map((i) => i.id)).toEqual(["i1", "i2"]);
    expect(h.itemStore.total_backend_count_per_checklist["keep"]).toBe(2);
    expect(h.itemStore.total_backend_count_checked_per_checklist["keep"]).toBe(1);
    expect(h.itemStore.checklistWasFullLoadedOnce["keep"]).toBe(true);
    expect(h.labelStore.labels.map((l) => l.id)).toEqual(["l1"]);
    expect(await readCursor()).toBe(42);
    expect(h.checkListStore.fetchCounts).toHaveBeenCalled();
  });

  it("reconciles the outbox against the reset server and surfaces a resync-dropped notice", async () => {
    const notices: SyncNotice[] = [];
    const off = onSyncNotice((n) => notices.push(n));
    h.outbox.reconcileResync.mockResolvedValue([{ opId: "gone1" }, { opId: "gone2" }] as any);
    h.checkapi.mockResolvedValue(
      delta({ full_resync: true, next_cursor: 3, checklists: [CL("keep")], items: [], labels: [] })
    );

    await applyDelta(PINIA);
    off();

    // reconcileResync is fed the ids the reset server carries (cards+items+labels).
    expect(h.outbox.reconcileResync).toHaveBeenCalledWith(new Set(["keep"]));
    expect(notices).toContainEqual({ type: "resync-dropped", count: 2 });
  });

  it("re-inserts an offline-created card the reset server does not yet know (finding B4)", async () => {
    // A card whose `create` op is still queued: absent from the resync payload,
    // but must stay on the board (its create keeps draining).
    h.checkListStore.checkLists = [CL("offline-new"), CL("stale")];
    h.itemStore.checkListsItems["offline-new"] = [ITEM("oi1", "offline-new")];
    h.outbox.queuedCreateIds.mockImplementation((t: string) =>
      t === "checklist" ? new Set(["offline-new"]) : new Set()
    );
    h.checkapi.mockResolvedValue(
      delta({ full_resync: true, next_cursor: 8, checklists: [CL("keep")], items: [], labels: [] })
    );

    await applyDelta(PINIA);

    const ids = h.checkListStore.checkLists.map((c) => c.id).sort();
    expect(ids).toEqual(["keep", "offline-new"]); // stale dropped, offline card survives
    expect(h.itemStore.checkListsItems["offline-new"]!.map((i) => i.id)).toEqual(["oi1"]);
    expect(h.checkListStore.total_backend_count).toBe(2); // 1 from resync + 1 survivor
  });
});

// ── Preview-count refresh trigger ─────────────────────────────────────────────

describe("applyDelta preview-count refresh", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("re-fetches preview counts for a NOT-fully-loaded card the delta touched", async () => {
    // Card "p" exists but is only a preview window (not fully loaded): a delta
    // item for it can't yield an exact count, so its counts are re-read.
    h.checkListStore.checkLists = [CL("p")];
    h.itemStore.checkListsItems["p"] = [];
    h.itemStore.checklistWasFullLoadedOnce["p"] = false;
    h.checkapi
      .mockResolvedValueOnce(delta({ items: [ITEM("np", "p")], next_cursor: 2 }))
      .mockResolvedValueOnce(delta({ next_cursor: 2 }));

    await applyDelta(PINIA);
    await vi.advanceTimersByTimeAsync(500); // fire the debounced refresh

    expect(h.itemStore.fetchMultipleChecklistsItemsPreview).toHaveBeenCalledWith(["p"]);
  });

  it("does NOT re-fetch preview counts for a fully-loaded card (exact counts known)", async () => {
    h.checkListStore.checkLists = [CL("f")];
    h.itemStore.checkListsItems["f"] = [];
    h.itemStore.checklistWasFullLoadedOnce["f"] = true;
    h.checkapi
      .mockResolvedValueOnce(delta({ items: [ITEM("nf", "f")], next_cursor: 2 }))
      .mockResolvedValueOnce(delta({ next_cursor: 2 }));

    await applyDelta(PINIA);
    await vi.advanceTimersByTimeAsync(500);

    expect(h.itemStore.fetchMultipleChecklistsItemsPreview).not.toHaveBeenCalled();
  });
});

// ── runBackgroundSync (boot wrapper) ─────────────────────────────────────────

describe("runBackgroundSync", () => {
  it("drives one pull from the persisted cursor", async () => {
    await writeCursor(4);
    h.checkapi.mockResolvedValue(delta({ next_cursor: 4 }));
    await runBackgroundSync(PINIA);
    expect((h.checkapi.mock.calls[0]![1] as any).query.since).toBe(4);
  });
});
