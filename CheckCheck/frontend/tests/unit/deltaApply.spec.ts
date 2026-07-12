// Unit tests for the WI-10 delta-application core (utils/deltaApply). Pure
// functions over plain array/record slices — no Nuxt/Pinia/IndexedDB needed.
import { describe, it, expect } from "vitest";
import { mergeDelta, type DeltaTarget, type ItemCountMaps } from "@/utils/deltaApply";
import type { EditGuard } from "@/utils/editGuard";

// ── Row factories (minimal shapes the core actually touches) ─────────────────

const CL = (id: string, over: any = {}): any => ({
  id,
  name: over.name ?? `card-${id}`,
  text: over.text ?? "",
  labels: over.labels ?? [],
  my_permission: "owner",
  updated_at: "2026-01-01T00:00:00",
  position: { index: over.index ?? 1, pinned: over.pinned ?? false, archived: over.archived ?? false, ...(over.position ?? {}) },
  ...over,
});

const ITEM = (id: string, clId: string, over: any = {}): any => ({
  id,
  checklist_id: clId,
  text: over.text ?? `item-${id}`,
  updated_at: "2026-01-01T00:00:00",
  position: { index: over.index ?? 1, indentation: 0 },
  state: { checked: over.checked ?? false, updated_at: "2026-01-01T00:00:00" },
});

const LABEL = (id: string, sort_order = 10): any => ({ id, name: `label-${id}`, sort_order });

function emptyDelta(over: any = {}): any {
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

function target(over: Partial<DeltaTarget> = {}): DeltaTarget {
  return { checkLists: [], items: {}, labels: [], ...over };
}

function countMaps(): ItemCountMaps {
  return { total: {}, checked: {}, unchecked: {}, fullyLoaded: {} };
}

const guardEditing = (want: string): EditGuard => ({
  isEditing: (kind, id, field) => `${kind}:${id}:${field}` === want,
});

/** A guard protecting an explicit set of `kind:id:field` keys (WI-11 outbox-op guard). */
const guardFields = (...wanted: string[]): EditGuard => {
  const set = new Set(wanted);
  return { isEditing: (kind, id, field) => set.has(`${kind}:${id}:${field}`) };
};

/** A guard reporting the given `kind:id` keys as locally removed (queued delete). */
const guardRemoved = (...wanted: string[]): EditGuard => {
  const set = new Set(wanted);
  return { isEditing: () => false, isRemoved: (kind, id) => set.has(`${kind}:${id}`) };
};

// ── Checklist upsert ─────────────────────────────────────────────────────────

describe("mergeDelta — checklists", () => {
  it("inserts a new card and reports a card-count delta", () => {
    const t = target();
    const s = mergeDelta(t, emptyDelta({ checklists: [CL("a")] }));
    expect(t.checkLists.map((c) => c.id)).toEqual(["a"]);
    expect(s.cardLevelChanged).toBe(true);
    expect(s.cardCountDelta).toBe(1);
    // A new card gets an empty item list so the board can render it.
    expect(t.items["a"]).toEqual([]);
  });

  it("upserts an existing card in place (no duplicate, no count delta)", () => {
    const t = target({ checkLists: [CL("a", { name: "old" })] });
    const s = mergeDelta(t, emptyDelta({ checklists: [CL("a", { name: "new" })] }));
    expect(t.checkLists).toHaveLength(1);
    expect(t.checkLists[0]!.name).toBe("new");
    expect(s.cardCountDelta).toBe(0);
  });

  it("sorts pinned-first then descending index", () => {
    const t = target();
    mergeDelta(
      t,
      emptyDelta({
        checklists: [CL("a", { index: 1 }), CL("b", { index: 3 }), CL("c", { index: 2, pinned: true })],
      })
    );
    expect(t.checkLists.map((c) => c.id)).toEqual(["c", "b", "a"]);
  });

  it("a pure name/text edit is NOT a card-level change", () => {
    const t = target({ checkLists: [CL("a", { name: "old" })] });
    const s = mergeDelta(t, emptyDelta({ checklists: [CL("a", { name: "new" })] }));
    expect(s.cardLevelChanged).toBe(false);
  });

  it("an archive/pin/label change IS a card-level change", () => {
    const t = target({ checkLists: [CL("a", { archived: false })] });
    const s = mergeDelta(t, emptyDelta({ checklists: [CL("a", { archived: true })] }));
    expect(s.cardLevelChanged).toBe(true);
  });
});

// ── Focused-edit protection (§4 LWW) ─────────────────────────────────────────

describe("mergeDelta — focused-edit protection", () => {
  it("keeps the local checklist name while it is focused", () => {
    const t = target({ checkLists: [CL("a", { name: "typing…" })] });
    mergeDelta(t, emptyDelta({ checklists: [CL("a", { name: "remote" })] }), guardEditing("checklist:a:name"));
    expect(t.checkLists[0]!.name).toBe("typing…");
  });

  it("still applies the rest of the row (position) when a field is guarded", () => {
    const t = target({ checkLists: [CL("a", { name: "typing…", index: 1 })] });
    mergeDelta(
      t,
      emptyDelta({ checklists: [CL("a", { name: "remote", index: 9 })] }),
      guardEditing("checklist:a:name")
    );
    expect(t.checkLists[0]!.name).toBe("typing…");
    expect(t.checkLists[0]!.position.index).toBe(9);
  });

  it("keeps the local item text while it is focused", () => {
    const t = target({ items: { c: [ITEM("i", "c", { text: "typing…" })] } });
    mergeDelta(t, emptyDelta({ items: [ITEM("i", "c", { text: "remote" })] }), guardEditing("item:i:text"));
    expect(t.items["c"]![0]!.text).toBe("typing…");
  });
});

// ── Item upsert + ordering ───────────────────────────────────────────────────

describe("mergeDelta — items", () => {
  it("inserts items into an existing list, sorted by (index, id)", () => {
    const t = target({ items: { c: [ITEM("b", "c", { index: 2 })] } });
    mergeDelta(t, emptyDelta({ items: [ITEM("a", "c", { index: 1 }), ITEM("d", "c", { index: 3 })] }));
    expect(t.items["c"]!.map((i) => i.id)).toEqual(["a", "b", "d"]);
  });

  it("upserts an existing item without duplicating it", () => {
    const t = target({ items: { c: [ITEM("a", "c", { text: "old" })] } });
    mergeDelta(t, emptyDelta({ items: [ITEM("a", "c", { text: "new" })] }));
    expect(t.items["c"]).toHaveLength(1);
    expect(t.items["c"]![0]!.text).toBe("new");
  });

  it("ignores items for a card we do not hold", () => {
    const t = target();
    const s = mergeDelta(t, emptyDelta({ items: [ITEM("a", "ghost")] }));
    expect(t.items["ghost"]).toBeUndefined();
    expect(s.touchedItemChecklistIds.has("ghost")).toBe(false);
  });

  it("creates a list for a card that arrived in the same delta", () => {
    const t = target();
    mergeDelta(t, emptyDelta({ checklists: [CL("c")], items: [ITEM("a", "c")] }));
    expect(t.items["c"]!.map((i) => i.id)).toEqual(["a"]);
  });
});

// ── Counts ───────────────────────────────────────────────────────────────────

describe("mergeDelta — counts", () => {
  it("recomputes exact counts for a fully-loaded list from the array", () => {
    const counts = countMaps();
    counts.fullyLoaded["c"] = true;
    const t = target({ items: { c: [ITEM("a", "c", { checked: true })] }, itemCounts: counts });
    mergeDelta(t, emptyDelta({ items: [ITEM("b", "c", { checked: false })] }));
    expect(counts.total["c"]).toBe(2);
    expect(counts.checked["c"]).toBe(1);
    expect(counts.unchecked["c"]).toBe(1);
  });

  it("adjusts preview counts incrementally for a new item", () => {
    const counts = countMaps();
    counts.total["c"] = 5;
    counts.unchecked["c"] = 5;
    counts.checked["c"] = 0;
    const t = target({ items: { c: [] }, itemCounts: counts });
    mergeDelta(t, emptyDelta({ items: [ITEM("a", "c", { checked: false })] }));
    expect(counts.total["c"]).toBe(6);
    expect(counts.unchecked["c"]).toBe(6);
  });

  it("shifts checked/unchecked when a preview item's state flips", () => {
    const counts = countMaps();
    counts.total["c"] = 1;
    counts.unchecked["c"] = 1;
    counts.checked["c"] = 0;
    const t = target({ items: { c: [ITEM("a", "c", { checked: false })] }, itemCounts: counts });
    mergeDelta(t, emptyDelta({ items: [ITEM("a", "c", { checked: true })] }));
    expect(counts.checked["c"]).toBe(1);
    expect(counts.unchecked["c"]).toBe(0);
    expect(counts.total["c"]).toBe(1);
  });
});

// ── Tombstones + revocations ─────────────────────────────────────────────────

describe("mergeDelta — removals", () => {
  it("removes a tombstoned checklist and its items + counts", () => {
    const counts = countMaps();
    counts.total["a"] = 3;
    const t = target({ checkLists: [CL("a"), CL("b")], items: { a: [ITEM("i", "a")] }, itemCounts: counts });
    const s = mergeDelta(t, emptyDelta({ checklist_tombstones: ["a"] }));
    expect(t.checkLists.map((c) => c.id)).toEqual(["b"]);
    expect(t.items["a"]).toBeUndefined();
    expect(counts.total["a"]).toBeUndefined();
    expect(s.cardCountDelta).toBe(-1);
    expect(s.removedCheckListIds.has("a")).toBe(true);
  });

  it("removes a card the caller lost access to (removed_checklist_ids)", () => {
    const t = target({ checkLists: [CL("a"), CL("b")] });
    const s = mergeDelta(t, emptyDelta({ removed_checklist_ids: ["b"] }));
    expect(t.checkLists.map((c) => c.id)).toEqual(["a"]);
    expect(s.removedCheckListIds.has("b")).toBe(true);
  });

  it("removes a tombstoned item and finds its list by id", () => {
    const t = target({ items: { c: [ITEM("a", "c"), ITEM("b", "c")] } });
    const s = mergeDelta(t, emptyDelta({ item_tombstones: ["a"] }));
    expect(t.items["c"]!.map((i) => i.id)).toEqual(["b"]);
    expect(s.touchedItemChecklistIds.has("c")).toBe(true);
  });

  it("removes a tombstoned label", () => {
    const t = target({ labels: [LABEL("a"), LABEL("b")] });
    mergeDelta(t, emptyDelta({ label_tombstones: ["a"] }));
    expect(t.labels.map((l) => l.id)).toEqual(["b"]);
  });

  it("strips a tombstoned label's chip from cached cards", () => {
    // The server never re-emits cards on a label delete (link rows are only
    // masked at read time), so the client must strip the chip itself.
    const t = target({
      labels: [LABEL("a")],
      checkLists: [CL("c1", { labels: [LABEL("a"), LABEL("x")] }), CL("c2", { labels: [LABEL("a")] })],
    });
    const s = mergeDelta(t, emptyDelta({ label_tombstones: ["a"] }));
    expect(t.checkLists[0]!.labels!.map((l: any) => l.id)).toEqual(["x"]);
    expect(t.checkLists[1]!.labels).toEqual([]);
    expect(s.cardLevelChanged).toBe(true);
  });
});

// ── Labels ───────────────────────────────────────────────────────────────────

describe("mergeDelta — labels", () => {
  it("upserts labels sorted by descending sort_order", () => {
    const t = target({ labels: [LABEL("a", 10)] });
    mergeDelta(t, emptyDelta({ labels: [LABEL("b", 30), LABEL("a", 20)] }));
    expect(t.labels.map((l) => l.id)).toEqual(["b", "a"]);
    expect(t.labels.find((l) => l.id === "a")!.sort_order).toBe(20);
  });
});

// ── Idempotency (§3) ─────────────────────────────────────────────────────────

describe("mergeDelta — idempotency", () => {
  it("re-applying the same delta is a no-op", () => {
    const counts = countMaps();
    counts.fullyLoaded["c"] = true;
    const delta = emptyDelta({
      checklists: [CL("c", { name: "n" })],
      items: [ITEM("a", "c", { checked: true }), ITEM("b", "c", { checked: false })],
      labels: [LABEL("l")],
    });

    const t = target({ itemCounts: counts });
    mergeDelta(t, delta);
    const first = JSON.stringify({ checkLists: t.checkLists, items: t.items, labels: t.labels, counts });

    mergeDelta(t, delta);
    const second = JSON.stringify({ checkLists: t.checkLists, items: t.items, labels: t.labels, counts });

    expect(second).toEqual(first);
    expect(t.checkLists).toHaveLength(1);
    expect(t.items["c"]).toHaveLength(2);
    expect(counts.total["c"]).toBe(2);
  });
});

// ── Outbox-op field protection (WI-11, review finding #2) ────────────────────
//
// An undrained optimistic edit (reorder/check/rename) must not visibly revert
// when a delta for a DIFFERENT field of the same row lands — the guard keeps the
// pending field's local value; the concurrent server edit is reported as a
// conflict (the local value is kept, LWW converges once the op drains).

describe("mergeDelta — outbox-op field protection", () => {
  it("keeps a locally-reordered item index while a delta changes its text", () => {
    const t = target({ items: { c: [ITEM("i", "c", { index: 5, text: "old" })] } });
    const s = mergeDelta(
      t,
      emptyDelta({ items: [ITEM("i", "c", { index: 1, text: "remote" })] }),
      guardFields("item:i:position.index")
    );
    expect(t.items["c"]![0]!.position.index).toBe(5); // local reorder preserved
    expect(t.items["c"]![0]!.text).toBe("remote"); // other field applied
    expect(s.conflicts).toEqual([{ kind: "item", id: "i", field: "position.index" }]);
  });

  it("keeps a locally-checked state while a delta changes text (no revert)", () => {
    const t = target({ items: { c: [ITEM("i", "c", { checked: true, text: "old" })] } });
    mergeDelta(
      t,
      emptyDelta({ items: [ITEM("i", "c", { checked: false, text: "remote" })] }),
      guardFields("item:i:state.checked")
    );
    expect(t.items["c"]![0]!.state.checked).toBe(true);
    expect(t.items["c"]![0]!.text).toBe("remote");
  });

  it("does not clone-mutate the incoming server DTO when preserving a field", () => {
    const t = target({ items: { c: [ITEM("i", "c", { index: 5 })] } });
    const incoming = ITEM("i", "c", { index: 1 });
    mergeDelta(t, emptyDelta({ items: [incoming] }), guardFields("item:i:position.index"));
    expect(incoming.position.index).toBe(1); // untouched original
    expect(t.items["c"]![0]!.position.index).toBe(5);
  });

  it("records NO conflict when the protected field's server value matched", () => {
    const t = target({ items: { c: [ITEM("i", "c", { index: 5, text: "old" })] } });
    const s = mergeDelta(
      t,
      emptyDelta({ items: [ITEM("i", "c", { index: 5, text: "remote" })] }),
      guardFields("item:i:position.index")
    );
    expect(s.conflicts).toEqual([]);
  });

  it("keeps a locally-pinned checklist position while a delta renames it", () => {
    const t = target({ checkLists: [CL("a", { name: "old", pinned: true })] });
    mergeDelta(
      t,
      emptyDelta({ checklists: [CL("a", { name: "remote", pinned: false })] }),
      guardFields("checklist:a:position.pinned")
    );
    expect(t.checkLists[0]!.position.pinned).toBe(true);
    expect(t.checkLists[0]!.name).toBe("remote");
  });

  it("keeps a locally-pending label set (queued attach) over a delta that drops it", () => {
    const t = target({ checkLists: [CL("a", { labels: [LABEL("l1")] })] });
    const s = mergeDelta(
      t,
      emptyDelta({ checklists: [CL("a", { labels: [] })] }),
      guardFields("checklist:a:labels")
    );
    expect(t.checkLists[0]!.labels.map((l: any) => l.id)).toEqual(["l1"]);
    expect(s.conflicts).toContainEqual({ kind: "checklist", id: "a", field: "labels" });
  });
});

// ── Locally-deleted rows are not resurrected (queued delete) ─────────────────

describe("mergeDelta — queued-delete suppression", () => {
  it("does not re-insert a checklist the user deleted offline", () => {
    const t = target();
    const s = mergeDelta(t, emptyDelta({ checklists: [CL("a")] }), guardRemoved("checklist:a"));
    expect(t.checkLists).toEqual([]);
    expect(s.cardCountDelta).toBe(0);
  });

  it("does not re-insert an item the user deleted offline", () => {
    const t = target({ items: { c: [] } });
    mergeDelta(t, emptyDelta({ items: [ITEM("i", "c")] }), guardRemoved("item:i"));
    expect(t.items["c"]).toEqual([]);
  });

  it("still upserts other, non-removed rows in the same delta", () => {
    const t = target();
    mergeDelta(t, emptyDelta({ checklists: [CL("a"), CL("b")] }), guardRemoved("checklist:a"));
    expect(t.checkLists.map((c) => c.id)).toEqual(["b"]);
  });
});
