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

const card = (id: string, index: number) => ({
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
  position: { index, pinned: false, archived: false, checked_items_collapsed: true, updated_at: NOW },
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
  store.checkLists = [card(SRC, 5) as any, card("other", 9) as any];
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
  it("shows the copy on top of the board and enqueues one duplicate op with matching ids", async () => {
    const { store, items } = seed(true);

    const copy = await store.duplicate(SRC);

    expect(copy.name).toBe("Shopping (copy)");
    expect(store.checkLists[0]!.id).toBe(copy.id); // highest index, top of the board
    expect(copy.position.index).toBeGreaterThan(9);
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
    expect(op.request.body.position).toEqual({ index: copy.position.index });
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
