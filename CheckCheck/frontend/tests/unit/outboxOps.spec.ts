// Unit tests for the WI-8 item outbox-op builders + the client-side append-index
// math. Pure functions — no Nuxt/IndexedDB needed.
import { describe, it, expect } from "vitest";
import {
  ITEM_INDEX_STEP,
  itemCreateOp,
  itemDeleteOp,
  itemStateOp,
  itemUpdateOp,
  nextItemIndex,
} from "@/utils/outboxOps";

const CL = "11111111-1111-1111-1111-111111111111";
const ITEM = "22222222-2222-2222-2222-222222222222";

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
