// Card copy actions ("Duplicate card", "Move checked items to new card"):
// the pure optimistic plan, the op builders, and the outbox engine rules for the
// two new op kinds. The store wiring has its own spec (cardCopyStore.spec.ts).
import { describe, it, expect } from "vitest";
import { planCardCopy, suffixedName, type CardCopySource } from "@/utils/cardCopy";
import { checklistDuplicateOp, checklistMoveCheckedOp, itemStateOp } from "@/utils/outboxOps";
import {
  affectsSidebarCounts,
  coalesce,
  opOwnWrites,
  partitionResync,
  pendingChecklistIds,
  queuedCreateIds,
  type OutboxOp,
  type OutboxOpInput,
} from "@/utils/outbox";

const NOW = "2026-11-14T10:00:00.000Z";

function item(id: string, index: number, checked: boolean, indentation = 0): CheckListItemType {
  return {
    id,
    checklist_id: "src",
    text: `text ${id}`,
    updated_at: NOW,
    position: { index, indentation, updated_at: NOW },
    state: { checked, updated_at: NOW },
  } as CheckListItemType;
}

function sourceCard(): CheckListType {
  return {
    id: "src",
    name: "Packing list",
    text: "notes",
    color_id: "blue",
    color: { id: "blue", display_name: "Blue" },
    checked_items_seperated: false,
    checked_items_collapsed: true,
    suggest_existing_items: false,
    owner_id: "someone-else",
    my_permission: "edit",
    updated_at: NOW,
    position: { index: 3, pinned: true, archived: false, checked_items_collapsed: true, updated_at: NOW },
    labels: [{ id: "lbl", display_name: "trips" }],
  } as unknown as CheckListType;
}

function source(over: Partial<CardCopySource> = {}): CardCopySource {
  return {
    card: sourceCard(),
    items: [item("a", 1, true), item("b", 2, false, 1), item("c", 3, true)],
    fullyLoaded: true,
    counts: { total: 3, checked: 2, unchecked: 1 },
    ...over,
  };
}

function ids() {
  let n = 0;
  return () => `copy-${++n}`;
}

const opts = () => ({ newId: "new", ownerId: "me", index: 42, now: NOW, newItemId: ids() });

function op(seq: number, input: OutboxOpInput): OutboxOp {
  return { ...input, seq, opId: `op${seq}`, enqueuedAt: seq, attempts: 0 };
}

function checklistDelete(id: string): OutboxOpInput {
  return {
    entityType: "checklist",
    entityId: id,
    kind: "delete",
    request: { method: "delete", path: "/api/checklist/{checklist_id}", pathParams: { checklist_id: id } },
  };
}

// ── suffixedName ─────────────────────────────────────────────────────────────

describe("suffixedName", () => {
  it("appends the suffix, and stands alone for an empty title", () => {
    expect(suffixedName("Packing list", "(copy)")).toBe("Packing list (copy)");
    expect(suffixedName("  Shopping ", "(done)")).toBe("Shopping (done)");
    expect(suffixedName("", "(copy)")).toBe("(copy)");
    expect(suffixedName(null, "(done)")).toBe("(done)");
  });
});

// ── planCardCopy ─────────────────────────────────────────────────────────────

describe("planCardCopy: duplicate", () => {
  it("copies the card fields and every loaded item, all unchecked, in order", () => {
    const plan = planCardCopy("duplicate", source(), opts());

    expect(plan.card).toMatchObject({
      id: "new",
      name: "Packing list (copy)",
      text: "notes",
      color_id: "blue",
      checked_items_seperated: false,
      suggest_existing_items: false,
      owner_id: "me",
      my_permission: "owner",
      position: { index: 42, pinned: false, archived: false },
    });
    expect(plan.card.labels.map((l) => l.id)).toEqual(["lbl"]);
    expect(plan.items.map((i) => i.text)).toEqual(["text a", "text b", "text c"]);
    expect(plan.items.every((i) => i.state.checked === false)).toBe(true);
    expect(plan.items.every((i) => i.checklist_id === "new")).toBe(true);
    expect(plan.items.map((i) => i.position.indentation)).toEqual([0, 1, 0]);
    // The client ids of the copies, keyed by source id, go to the server.
    expect(plan.itemIds).toEqual({ a: "copy-1", b: "copy-2", c: "copy-3" });
    expect(plan.items.map((i) => i.id)).toEqual(["copy-1", "copy-2", "copy-3"]);
    expect(plan.counts).toEqual({ total: 3, checked: 0, unchecked: 3 });
    expect(plan.fullyLoaded).toBe(true);
    expect(plan.movedSourceItemIds.size).toBe(0);
  });

  it("for a preview-only source keeps the whole card's counts and stays a preview", () => {
    const plan = planCardCopy(
      "duplicate",
      source({ items: [item("a", 1, false)], fullyLoaded: false, counts: { total: 40, checked: 5, unchecked: 35 } }),
      opts()
    );
    expect(plan.items).toHaveLength(1);
    expect(plan.counts).toEqual({ total: 40, checked: 0, unchecked: 40 });
    expect(plan.fullyLoaded).toBe(false);
  });
});

describe("planCardCopy: move checked", () => {
  it("takes only the checked items, still checked, and marks them as leaving the source", () => {
    const plan = planCardCopy("move_checked", source(), opts());

    expect(plan.card.name).toBe("Packing list (done)");
    expect(plan.items.map((i) => i.text)).toEqual(["text a", "text c"]);
    expect(plan.items.every((i) => i.state.checked)).toBe(true);
    expect(plan.itemIds).toEqual({ a: "copy-1", c: "copy-2" });
    expect([...plan.movedSourceItemIds].sort()).toEqual(["a", "c"]);
    expect(plan.counts).toEqual({ total: 2, checked: 2, unchecked: 0 });
  });
});

// ── op builders ──────────────────────────────────────────────────────────────

describe("card copy op builders", () => {
  it("key the op on the new card and point the request at the source", () => {
    const body = { position: { index: 7 }, item_ids: { a: "x" } };
    expect(checklistDuplicateOp("src", "new", body)).toEqual({
      entityType: "checklist",
      entityId: "new",
      kind: "duplicate",
      request: {
        method: "post",
        path: "/api/checklist/{checklist_id}/duplicate",
        pathParams: { checklist_id: "src" },
        body: { position: { index: 7 }, item_ids: { a: "x" }, id: "new" },
      },
    });
    const move = checklistMoveCheckedOp("src", "new", body);
    expect(move.kind).toBe("move_checked");
    expect(move.request.path).toBe("/api/checklist/{checklist_id}/items/move-checked");
    expect(move.request.body?.id).toBe("new");
  });

  it("record no guarded own-write except the board slot of the new card", () => {
    const writes = opOwnWrites(checklistDuplicateOp("src", "new", { position: { index: 7 }, item_ids: { a: "x" } }));
    expect(writes).toEqual([{ kind: "checklist", id: "new", field: "position.index", value: 7 }]);
  });
});

// ── outbox engine rules for the copy kinds ───────────────────────────────────

describe("outbox rules for card copy ops", () => {
  const dup = () => checklistDuplicateOp("src", "new", { item_ids: { a: "copy-a" } });
  const move = () => checklistMoveCheckedOp("src", "new", { item_ids: { a: "copy-a" } });
  const editOnNewCard = () => itemStateOp("new", "copy-a", { checked: true });

  it("count as a queued create of the new card (kept out of known=)", () => {
    expect(queuedCreateIds([op(1, dup())], "checklist")).toEqual(new Set(["new"]));
    expect(queuedCreateIds([op(1, move())], "checklist")).toEqual(new Set(["new"]));
  });

  it("a duplicate is cancelled with its child ops when the new card is deleted before it drained", () => {
    const queue = [op(1, dup()), op(2, editOnNewCard())];
    expect(coalesce(queue, op(3, checklistDelete("new")))).toEqual([]);
  });

  it("a move is NOT cancelled by deleting the new card: the move still runs, then the delete", () => {
    const queue = [op(1, move()), op(2, editOnNewCard())];
    const next = coalesce(queue, op(3, checklistDelete("new")));
    expect(next.map((o) => o.kind)).toEqual(["move_checked", "state", "delete"]);
  });

  it("a duplicate in flight is not cancelled (the server may already have it)", () => {
    const queue = [op(1, dup())];
    const next = coalesce(queue, op(2, checklistDelete("new")), new Set([1]));
    expect(next.map((o) => o.kind)).toEqual(["duplicate", "delete"]);
  });

  it("deleting the SOURCE card keeps a queued copy, which drains first", () => {
    const queue = [op(1, move())];
    const next = coalesce(queue, op(2, checklistDelete("src")));
    expect(next.map((o) => `${o.kind}:${o.entityId}`)).toEqual(["move_checked:new", "delete:src"]);
  });

  it("light the pending badge on the new card, and on the source for a move", () => {
    expect(pendingChecklistIds([op(1, dup())])).toEqual(new Set(["new"]));
    expect(pendingChecklistIds([op(1, move())])).toEqual(new Set(["new", "src"]));
  });

  it("move the sidebar counts (a new card)", () => {
    expect(affectsSidebarCounts(op(1, dup()))).toBe(true);
    expect(affectsSidebarCounts(op(1, move()))).toBe(true);
  });

  it("survive a full resync only while the source card exists, and keep their new card's ops alive", () => {
    const queue = [op(1, dup()), op(2, editOnNewCard())];
    const withSource = partitionResync(queue, new Set(["src"]));
    expect(withSource.kept.map((o) => o.kind)).toEqual(["duplicate", "state"]);
    const withoutSource = partitionResync(queue, new Set());
    expect(withoutSource.kept).toEqual([]);
    expect(withoutSource.dropped.map((o) => o.kind)).toEqual(["duplicate", "state"]);
  });

  it("a copy of a queued copy survives a resync when the first source exists", () => {
    const second = checklistDuplicateOp("new", "newer", {});
    const { kept } = partitionResync([op(1, dup()), op(2, second)], new Set(["src"]));
    expect(kept.map((o) => o.entityId)).toEqual(["new", "newer"]);
  });
});
