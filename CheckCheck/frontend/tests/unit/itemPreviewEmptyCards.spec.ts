// Note-only cards on the board (issue #22): the item preview batch
// (`GET /api/item?checklist_ids=...`) leaves a card out of its response map when
// the card has no items. The board only lays a card out as a note card once its
// item count is *known* (an entry in `checklistWasFullLoadedOnce`), so the store
// has to record "no items" for every requested card the response left out.
//
// Exercises the REAL item store with `useNuxtApp().$checkapi` stubbed.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/utils/localFirst", () => ({ isLocalFirstEnabled: () => true }));
vi.mock("@/composables/useOutbox", () => ({ useOutbox: () => ({ enqueue: vi.fn() }) }));

import { useCheckListsItemStore } from "@/stores/checklist_item";

const item = (id: string, checked = false) => ({
  id,
  text: id,
  position: { index: 1, indentation: 0 },
  state: { checked },
});

function stubPreview(response: Record<string, any>) {
  vi.stubGlobal("useNuxtApp", () => ({ $checkapi: vi.fn(async () => response) }));
  vi.stubGlobal("useAppConfig", () => ({ previewItemCount: 10 }));
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.unstubAllGlobals();
});

describe("item preview batch and cards without items", () => {
  it("records zero items for a requested card the response leaves out", async () => {
    stubPreview({
      withItems: { items: [item("i1"), item("i2", true)], item_count: 2, item_checked_count: 1, item_unchecked_count: 1 },
    });
    const store = useCheckListsItemStore();

    await store.fetchMultipleChecklistsItemsPreview(["withItems", "noteOnly"]);

    expect(store.checklistWasFullLoadedOnce).toEqual({ withItems: false, noteOnly: false });
    expect(store.getItemCount("noteOnly")).toBe(0);
    expect(store.getItemCount("noteOnly", true)).toBe(0);
    expect(store.checkListsItems["noteOnly"]).toEqual([]);
    expect(store.getItemCount("withItems")).toBe(2);
  });

  it("zeroes a preview-only card whose items are all gone on a forced refresh", async () => {
    const store = useCheckListsItemStore();
    store.checkListsItems["cl"] = [item("old")] as any;
    store.total_backend_count_per_checklist["cl"] = 1;
    store.checklistWasFullLoadedOnce["cl"] = false;
    stubPreview({});

    await store.fetchMultipleChecklistsItemsPreview(["cl"], null, true);

    expect(store.checkListsItems["cl"]).toEqual([]);
    expect(store.getItemCount("cl")).toBe(0);
  });

  it("leaves a fully loaded card alone: its own item list is the truth", async () => {
    const store = useCheckListsItemStore();
    store.checkListsItems["cl"] = [item("kept")] as any;
    store.checklistWasFullLoadedOnce["cl"] = true;
    stubPreview({});

    await store.fetchMultipleChecklistsItemsPreview(["cl"], null, true);

    expect(store.checkListsItems["cl"]).toHaveLength(1);
    expect(store.checklistWasFullLoadedOnce["cl"]).toBe(true);
    expect(store.getItemCount("cl")).toBe(1);
  });
});
