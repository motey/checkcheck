// The checklist store's local-first card copy actions (duplicate, move checked):
// the REAL checklist + item stores, with the outbox captured. Checks that the
// optimistic board state and the ONE enqueued op agree, which is what lets the
// later delta pull update the rows in place instead of duplicating them.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const enqueue = vi.fn();

vi.mock("@/utils/localFirst", () => ({ isLocalFirstEnabled: () => true }));
vi.mock("@/composables/useOutbox", () => ({ useOutbox: () => ({ enqueue }) }));
vi.mock("@/stores/user", () => ({ useUserStore: () => ({ myId: "me" }) }));

import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";

const SRC = "11111111-1111-1111-1111-111111111111";
const NOW = new Date().toISOString();

const card = (id: string, index: number, pinned = false) => ({
  id,
  name: "Shopping",
  text: "weekly",
  color_id: "green",
  color: { id: "green", display_name: "Green" },
  checked_items_seperated: true,
  checked_items_collapsed: true,
  suggest_existing_items: true,
  owner_id: "me",
  my_permission: "owner",
  updated_at: NOW,
  position: { index, pinned, archived: false, checked_items_collapsed: true, updated_at: NOW },
  labels: [{ id: "lbl", display_name: "food" }],
});

const item = (id: string, index: number, checked: boolean) => ({
  id,
  checklist_id: SRC,
  text: id,
  updated_at: NOW,
  position: { index, indentation: 0, updated_at: NOW },
  state: { checked, updated_at: NOW },
});

function seed(fullyLoaded: boolean) {
  const store = useCheckListsStore();
  const items = useCheckListsItemStore();
  // Board order (descending index): above (9), SRC (5), below (1).
  store.checkLists = [card(SRC, 5) as any, card("above", 9) as any, card("below", 1) as any];
  items.checkListsItems[SRC] = [item("milk", 1, true), item("bread", 2, false), item("eggs", 3, true)] as any;
  items.checklistWasFullLoadedOnce[SRC] = fullyLoaded;
  // A preview shows only part of the card; the counts are the whole card.
  items.total_backend_count_per_checklist[SRC] = fullyLoaded ? 3 : 30;
  items.total_backend_count_checked_per_checklist[SRC] = fullyLoaded ? 2 : 12;
  items.total_backend_count_unchecked_per_checklist[SRC] = fullyLoaded ? 1 : 18;
  return { store, items };
}

beforeEach(() => {
  setActivePinia(createPinia());
  enqueue.mockClear();
  vi.unstubAllGlobals();
  vi.stubGlobal("useCheckListsItemStore", useCheckListsItemStore);
});

describe("checklist store: duplicate (local-first)", () => {
  it("shows the copy right after the source and enqueues one duplicate op with matching ids", async () => {
    const { store, items } = seed(true);

    const copy = await store.duplicate(SRC);

    expect(copy.name).toBe("Shopping (copy)");
    // Between the source (5) and the card after it (1).
    expect(store.checkLists.map((c) => c.id)).toEqual(["above", SRC, copy.id, "below"]);
    expect(copy.position.index).toBe(3);
    expect(copy.position.pinned).toBe(false);
    const copied = items.checkListsItems[copy.id]!;
    expect(copied.map((i) => i.text)).toEqual(["milk", "bread", "eggs"]);
    expect(copied.every((i) => !i.state.checked)).toBe(true);
    expect(items.checklistWasFullLoadedOnce[copy.id]).toBe(true);
    expect(items.getItemCount(copy.id)).toBe(3);
    expect(items.getItemCount(copy.id, true)).toBe(0);
    // The source is untouched.
    expect(items.checkListsItems[SRC]!.map((i) => i.id)).toEqual(["milk", "bread", "eggs"]);

    expect(enqueue).toHaveBeenCalledTimes(1);
    const op = enqueue.mock.calls[0]![0];
    expect(op).toMatchObject({ entityType: "checklist", entityId: copy.id, kind: "duplicate" });
    expect(op.request.pathParams).toEqual({ checklist_id: SRC });
    expect(op.request.body.id).toBe(copy.id);
    expect(op.request.body.position).toEqual({ index: 3, pinned: false });
    // Every optimistic item id is the one the server is told to use.
    const sentIds = op.request.body.item_ids as Record<string, string>;
    expect(Object.keys(sentIds).sort()).toEqual(["bread", "eggs", "milk"]);
    expect(copied.map((i) => i.id).sort()).toEqual(Object.values(sentIds).sort());
  });

  it("from a preview keeps the whole card's counts and stays a preview", async () => {
    const { store, items } = seed(false);
    const copy = await store.duplicate(SRC);
    expect(items.checklistWasFullLoadedOnce[copy.id]).toBe(false);
    expect(items.getItemCount(copy.id)).toBe(30);
    expect(items.getItemCount(copy.id, false)).toBe(30);
    expect(items.getItemCount(copy.id, true)).toBe(0);
  });
});

describe("checklist store: copy placement (local-first)", () => {
  it("a copy of a pinned card is pinned and lands next to it in the pinned section", async () => {
    const { store } = seed(true);
    store.checkLists = [card(SRC, 5, true) as any, card("pinned-low", 2, true) as any, card("normal", 4) as any];
    await store._sort();

    const copy = await store.duplicate(SRC);

    expect(copy.position.pinned).toBe(true);
    // The unpinned card at 4 is in the other section and does not count.
    expect(copy.position.index).toBe(3.5);
    expect(store.checkLists.map((c) => c.id)).toEqual([SRC, copy.id, "pinned-low", "normal"]);
    expect(enqueue.mock.calls[0]![0].request.body.position).toEqual({ index: 3.5, pinned: true });
  });

  it("the last loaded card: a small step down while more pages may follow, an end gap on a full board", async () => {
    const { store } = seed(true);
    store.checkLists = [card("above", 9) as any, card(SRC, 5) as any];
    store.total_backend_count = 10;
    expect((await store.duplicate(SRC)).position.index).toBe(4.9999);

    setActivePinia(createPinia());
    const again = seed(true).store;
    again.checkLists = [card("above", 9) as any, card(SRC, 5) as any];
    again.total_backend_count = 2;
    expect((await again.duplicate(SRC)).position.index).toBe(4.6);
  });

  it("moving checked items places the new card right after the source too", async () => {
    const { store } = seed(true);
    const done = await store.moveCheckedToNewCard(SRC);
    expect(store.checkLists.map((c) => c.id)).toEqual(["above", SRC, done.id, "below"]);
    expect(enqueue.mock.calls[0]![0].request.body.position).toEqual({ index: 3, pinned: false });
  });
});

describe("checklist store: move checked (local-first)", () => {
  it("moves the checked items to the new card and out of the source, one op", async () => {
    const { store, items } = seed(true);

    const done = await store.moveCheckedToNewCard(SRC);

    expect(done.name).toBe("Shopping (done)");
    expect(items.checkListsItems[done.id]!.map((i) => [i.text, i.state.checked])).toEqual([
      ["milk", true],
      ["eggs", true],
    ]);
    expect(items.checkListsItems[SRC]!.map((i) => i.id)).toEqual(["bread"]);
    expect(items.getItemCount(SRC, true)).toBe(0);
    expect(items.getItemCount(done.id, true)).toBe(2);

    expect(enqueue).toHaveBeenCalledTimes(1);
    const op = enqueue.mock.calls[0]![0];
    expect(op).toMatchObject({ entityId: done.id, kind: "move_checked" });
    expect(op.request.path).toBe("/api/checklist/{checklist_id}/items/move-checked");
    expect(Object.keys(op.request.body.item_ids).sort()).toEqual(["eggs", "milk"]);
  });

  it("from a preview moves the source's checked count to the new card", async () => {
    const { store, items } = seed(false);
    const done = await store.moveCheckedToNewCard(SRC);
    expect(items.getItemCount(done.id)).toBe(12);
    expect(items.getItemCount(done.id, true)).toBe(12);
    expect(items.getItemCount(SRC)).toBe(18);
    expect(items.getItemCount(SRC, true)).toBe(0);
    expect(items.getItemCount(SRC, false)).toBe(18);
  });
});
