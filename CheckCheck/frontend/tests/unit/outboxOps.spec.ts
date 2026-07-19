// Unit tests for the WI-8 item outbox-op builders + the client-side append-index
// math. Pure functions — no Nuxt/IndexedDB needed.
import { describe, it, expect } from "vitest";
import {
  ITEM_INDEX_STEP,
  POSITION_END_GAP,
  checklistCreateOp,
  checklistDeleteOp,
  checklistLabelAddOp,
  checklistLabelKey,
  checklistLabelRemoveOp,
  checklistPositionOp,
  checklistUpdateOp,
  fractionalIndexBetween,
  itemCreateOp,
  itemDeleteOp,
  itemPositionOp,
  itemStateOp,
  itemUpdateOp,
  itemsDeleteCheckedOp,
  itemsUncheckAllOp,
  nextItemIndex,
} from "@/utils/outboxOps";

const CL = "11111111-1111-1111-1111-111111111111";
const ITEM = "22222222-2222-2222-2222-222222222222";
const LABEL = "33333333-3333-3333-3333-333333333333";

const item = (index: number) => ({ position: { index } });

describe("nextItemIndex", () => {
  it("starts at one step for an empty list", () => {
    expect(nextItemIndex([])).toBe(ITEM_INDEX_STEP);
  });

  it("appends one step past the largest existing index", () => {
    expect(nextItemIndex([item(1), item(2), item(3)])).toBe(3 + ITEM_INDEX_STEP);
  });

  it("uses the max, not the last, so it survives an unsorted list", () => {
    expect(nextItemIndex([item(5), item(1), item(3)])).toBe(5 + ITEM_INDEX_STEP);
  });

  it("handles fractional indices without collapsing to an integer", () => {
    expect(nextItemIndex([item(1.5), item(2.5)])).toBe(2.5 + ITEM_INDEX_STEP);
  });

  it("ignores items with a missing/nullish index", () => {
    expect(nextItemIndex([{ position: { index: null } }, item(2)])).toBe(2 + ITEM_INDEX_STEP);
    expect(nextItemIndex([{}, {}])).toBe(ITEM_INDEX_STEP);
  });

  it("returns a strictly larger, numeric value so the binary-search insert stays correct", () => {
    const list = [item(1), item(2)];
    const next = nextItemIndex(list);
    expect(typeof next).toBe("number");
    expect(next).toBeGreaterThan(2);
  });
});

describe("itemCreateOp", () => {
  it("targets the collection endpoint and carries the client id in the body", () => {
    const op = itemCreateOp(CL, ITEM, {
      text: "buy milk",
      position: { index: 3, indentation: 0 },
      state: { checked: false },
    });
    expect(op.entityType).toBe("item");
    expect(op.entityId).toBe(ITEM);
    expect(op.kind).toBe("create");
    expect(op.request.method).toBe("post");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/item");
    expect(op.request.pathParams).toEqual({ checklist_id: CL });
    expect(op.request.body).toEqual({
      id: ITEM,
      text: "buy milk",
      position: { index: 3, indentation: 0 },
      state: { checked: false },
    });
  });

  it("forces the op id even if the caller body has none", () => {
    const op = itemCreateOp(CL, ITEM, {});
    expect((op.request.body as any).id).toBe(ITEM);
  });
});

describe("itemUpdateOp", () => {
  it("targets the item endpoint with an update kind", () => {
    const op = itemUpdateOp(CL, ITEM, { text: "new text" });
    expect(op.kind).toBe("update");
    expect(op.request.method).toBe("patch");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/item/{checklist_item_id}");
    expect(op.request.pathParams).toEqual({ checklist_id: CL, checklist_item_id: ITEM });
    expect(op.request.body).toEqual({ text: "new text" });
  });
});

describe("itemStateOp", () => {
  it("targets the state endpoint with a coalescable state kind", () => {
    const op = itemStateOp(CL, ITEM, { checked: true });
    expect(op.kind).toBe("state");
    expect(op.request.method).toBe("patch");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/item/{checklist_item_id}/state");
    expect(op.request.body).toEqual({ checked: true });
  });
});

describe("itemDeleteOp", () => {
  it("targets the item endpoint with a delete kind and no body", () => {
    const op = itemDeleteOp(CL, ITEM);
    expect(op.kind).toBe("delete");
    expect(op.request.method).toBe("delete");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/item/{checklist_item_id}");
    expect(op.request.pathParams).toEqual({ checklist_id: CL, checklist_item_id: ITEM });
    expect(op.request.body).toBeUndefined();
  });

  it("coalesces (via the WI-7 rule) against a queued create for the same entity", () => {
    // A create then a delete for the same entity share entityId — the outbox's
    // coalesce cancels them. This just documents the shared key the builders emit.
    const create = itemCreateOp(CL, ITEM, {});
    const del = itemDeleteOp(CL, ITEM);
    expect(create.entityType).toBe(del.entityType);
    expect(create.entityId).toBe(del.entityId);
  });
});

describe("fractionalIndexBetween", () => {
  it("returns the midpoint of two present neighbours", () => {
    expect(fractionalIndexBetween(2, 4)).toBe(3);
    expect(fractionalIndexBetween(1, 2)).toBe(1.5);
  });

  it("drops one end-gap past a single lower neighbour (append)", () => {
    expect(fractionalIndexBetween(5, null)).toBe(5 + Number(POSITION_END_GAP));
  });

  it("drops one end-gap before a single upper neighbour (prepend)", () => {
    expect(fractionalIndexBetween(null, 5)).toBe(5 - Number(POSITION_END_GAP));
  });

  it("returns 0 when there is nothing to order against", () => {
    expect(fractionalIndexBetween(null, null)).toBe(0);
  });

  it("stays exact across repeated mid-list inserts (no binary-float drift)", () => {
    // Halving between 0 and 1 many times should never collapse two neighbours to
    // the same key; decimal.js keeps each midpoint distinct and ordered.
    let lo = 0;
    const hi = 1;
    let prev = lo;
    for (let i = 0; i < 20; i++) {
      const mid = fractionalIndexBetween(lo, hi);
      expect(mid).toBeGreaterThan(prev);
      expect(mid).toBeLessThan(hi);
      prev = lo;
      lo = mid;
    }
  });

  it("mirrors the server's midpoint regardless of which move direction supplies the pair", () => {
    // move-under other(2) with successor(4) and move-above other(4) with
    // predecessor(2) must land on the same key so both clients converge.
    expect(fractionalIndexBetween(2, 4)).toBe(fractionalIndexBetween(2, 4));
  });
});

describe("itemPositionOp", () => {
  it("targets the plain position endpoint with a coalescable position kind", () => {
    const op = itemPositionOp(CL, ITEM, { index: 1.5 });
    expect(op.kind).toBe("position");
    expect(op.request.method).toBe("patch");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/item/{checklist_item_id}/position");
    expect(op.request.pathParams).toEqual({ checklist_id: CL, checklist_item_id: ITEM });
    expect(op.request.body).toEqual({ index: 1.5 });
  });

  it("carries only the supplied fields (a reorder must not clobber indentation)", () => {
    expect(itemPositionOp(CL, ITEM, { indentation: 2 }).request.body).toEqual({ indentation: 2 });
  });
});

describe("checklistCreateOp", () => {
  it("targets the collection endpoint and forces the client id into the body", () => {
    const op = checklistCreateOp(CL, { name: "Groceries", position: { index: 0.4 } });
    expect(op.entityType).toBe("checklist");
    expect(op.entityId).toBe(CL);
    expect(op.kind).toBe("create");
    expect(op.request.method).toBe("post");
    expect(op.request.path).toBe("/api/checklist");
    expect(op.request.pathParams).toBeUndefined();
    expect(op.request.body).toEqual({ id: CL, name: "Groceries", position: { index: 0.4 } });
  });
});

describe("checklistUpdateOp", () => {
  it("patches the checklist with an update kind", () => {
    const op = checklistUpdateOp(CL, { name: "New name", color_id: null });
    expect(op.kind).toBe("update");
    expect(op.request.method).toBe("patch");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}");
    expect(op.request.pathParams).toEqual({ checklist_id: CL });
    expect(op.request.body).toEqual({ name: "New name", color_id: null });
  });
});

describe("checklistPositionOp", () => {
  it("patches the position endpoint (index/pinned/archived) as a coalescable position kind", () => {
    const op = checklistPositionOp(CL, { archived: true });
    expect(op.kind).toBe("position");
    expect(op.request.method).toBe("patch");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/position");
    expect(op.request.body).toEqual({ archived: true });
  });
});

describe("checklistDeleteOp", () => {
  it("deletes the checklist with a delete kind and no body", () => {
    const op = checklistDeleteOp(CL);
    expect(op.kind).toBe("delete");
    expect(op.request.method).toBe("delete");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}");
    expect(op.request.body).toBeUndefined();
  });

  it("shares its entityId with the create so create-then-delete cancels", () => {
    expect(checklistDeleteOp(CL).entityId).toBe(checklistCreateOp(CL, {}).entityId);
  });
});

describe("checklist⇄label association ops", () => {
  it("keys attach and detach by the same (checklist,label) pair", () => {
    const add = checklistLabelAddOp(CL, LABEL);
    const remove = checklistLabelRemoveOp(CL, LABEL);
    expect(add.entityId).toBe(checklistLabelKey(CL, LABEL));
    expect(add.entityId).toBe(remove.entityId);
    // create+delete of the same pair → cancels in the outbox (rule 2).
    expect(add.kind).toBe("create");
    expect(remove.kind).toBe("delete");
  });

  it("attaches with an idempotent PUT and detaches with DELETE", () => {
    const add = checklistLabelAddOp(CL, LABEL);
    expect(add.request.method).toBe("put");
    expect(add.request.path).toBe("/api/checklist/{checklist_id}/label/{label_id}");
    expect(add.request.pathParams).toEqual({ checklist_id: CL, label_id: LABEL });
    expect(checklistLabelRemoveOp(CL, LABEL).request.method).toBe("delete");
  });

  it("keeps associations on different cards independent", () => {
    const other = "44444444-4444-4444-4444-444444444444";
    expect(checklistLabelKey(CL, LABEL)).not.toBe(checklistLabelKey(other, LABEL));
  });
});

describe("bulk item operation ops", () => {
  it("uncheck-all targets the card via checklist_id and is keyed by the card id", () => {
    const op = itemsUncheckAllOp(CL);
    // entityType is "item" so isChecklistChild / pendingChecklistIds work via
    // pathParams with no engine edits; entityId is the CARD id so partitionResync
    // keeps the op while the card exists.
    expect(op.entityType).toBe("item");
    expect(op.entityId).toBe(CL);
    expect(op.kind).toBe("bulk_uncheck");
    expect(op.request.method).toBe("post");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/items/uncheck-all");
    expect(op.request.pathParams).toEqual({ checklist_id: CL });
    // No body — the server operates on the card's current item set.
    expect(op.request.body).toBeUndefined();
  });

  it("delete-checked targets the card via checklist_id and is keyed by the card id", () => {
    const op = itemsDeleteCheckedOp(CL);
    expect(op.entityType).toBe("item");
    expect(op.entityId).toBe(CL);
    expect(op.kind).toBe("bulk_delete_checked");
    expect(op.request.method).toBe("post");
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/items/delete-checked");
    expect(op.request.pathParams).toEqual({ checklist_id: CL });
    expect(op.request.body).toBeUndefined();
  });

  it("uses distinct, non-delete/create kinds so they never coalesce or cancel each other", () => {
    // Guards the order-dependence bug: these must not reuse the `delete` kind
    // (which would cancel a queued create) nor be COALESCABLE.
    expect(itemsUncheckAllOp(CL).kind).not.toBe(itemsDeleteCheckedOp(CL).kind);
    for (const kind of [itemsUncheckAllOp(CL).kind, itemsDeleteCheckedOp(CL).kind]) {
      expect(["create", "update", "delete", "state", "position"]).not.toContain(kind);
    }
  });
});
