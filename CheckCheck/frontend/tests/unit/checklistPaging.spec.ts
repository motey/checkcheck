// Regression test for E2E_STABILITY bug B1: "a board page is appended without
// re-sorting".
//
// Report: on a board with more than 5 cards, scrolling to load page 2 could put
// a card in the wrong place, and it stayed wrong until some unrelated action
// re-sorted the board. Root cause: `fetchNextPage()` appended the server's page
// to `checkLists` and never called `_sort()`. Paging is offset based, so page 2
// is ordered against a *newer* server snapshot than page 1; if the order moved
// in between (another device, another tab, a poke-driven change), page 2 can
// legitimately contain rows that belong above page 1's tail, and a plain append
// renders them below it.
//
// We exercise the REAL checklist store with `$checkapi` stubbed to return pages
// whose indexes interleave, and assert the resulting array is in the board's one
// ordering rule: pinned first, then descending index.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const fetchMultipleChecklistsItemsPreview = vi.fn(async () => {});

vi.mock("@/utils/localFirst", () => ({ isLocalFirstEnabled: () => false }));
vi.mock("@/composables/useOutbox", () => ({ useOutbox: () => ({ enqueue: vi.fn() }) }));
vi.mock("@/stores/user", () => ({ useUserStore: () => ({ myId: "user-1" }) }));
// The store reaches the item store through Nuxt's auto-import (no import
// statement), so it has to be stubbed as a global rather than module-mocked.

import { useCheckListsStore } from "@/stores/checklist";

/** Minimal card shaped like the API's CheckListRead. */
const card = (id: string, index: number, pinned = false) => ({
  id,
  name: id,
  text: "",
  color_id: null,
  color: null,
  owner_id: "user-1",
  my_permission: "owner",
  labels: [],
  position: { index, pinned, archived: false },
});

const orderOf = (cards: { id: string }[]) => cards.map((c) => c.id);

/** Stub `useNuxtApp().$checkapi` with a queue of canned responses. */
function stubApi(pages: any[]) {
  const calls: any[] = [];
  const $checkapi = vi.fn(async (_path: string, opts: any) => {
    calls.push(opts);
    return pages.shift();
  });
  vi.stubGlobal("useNuxtApp", () => ({ $checkapi }));
  return calls;
}

beforeEach(() => {
  setActivePinia(createPinia());
  fetchMultipleChecklistsItemsPreview.mockClear();
  vi.unstubAllGlobals();
  vi.stubGlobal("useCheckListsItemStore", () => ({ fetchMultipleChecklistsItemsPreview }));
});

describe("checklist paging keeps the board sorted", () => {
  it("re-sorts after fetchNextPage() appends a page that interleaves with page 1", async () => {
    const store = useCheckListsStore();

    // Page 1 as the board already holds it.
    store.checkLists = [card("a", 10), card("b", 8), card("c", 6)] as any;

    // Page 2 from a server whose order moved on: "d" (index 9) now outranks
    // both "b" and "c", and "e" (index 7) outranks "c".
    stubApi([{ items: [card("d", 9), card("e", 7), card("f", 5)], total_count: 6 }]);

    await store.fetchNextPage();

    expect(orderOf(store.checkLists)).toEqual(["a", "d", "b", "e", "c", "f"]);
    expect(store.total_backend_count).toBe(6);
  });

  it("keeps pinned cards above unpinned ones regardless of which page they arrive on", async () => {
    const store = useCheckListsStore();
    store.checkLists = [card("pin-a", 4, true), card("a", 10)] as any;

    // A pinned card with a LOW index arrives on page 2. Descending index alone
    // would bury it at the bottom; pinned-first must win.
    stubApi([{ items: [card("pin-b", 1, true), card("b", 9)], total_count: 4 }]);

    await store.fetchNextPage();

    expect(orderOf(store.checkLists)).toEqual(["pin-a", "pin-b", "a", "b"]);
  });

  it("does not duplicate a card that page 2 repeats (offset paging over a shifted list)", async () => {
    const store = useCheckListsStore();
    store.checkLists = [card("a", 10), card("b", 8)] as any;

    stubApi([{ items: [card("b", 8), card("c", 7)], total_count: 3 }]);

    await store.fetchNextPage();

    expect(orderOf(store.checkLists)).toEqual(["a", "b", "c"]);
  });

  it("re-sorts the filtered view's appended page too (search / shared / archive)", async () => {
    const store = useCheckListsStore();

    stubApi([
      { items: [card("a", 10), card("c", 6)], total_count: 4 },
      { items: [card("b", 8), card("d", 5)], total_count: 4 },
    ]);

    await store.searchChecklists("q");
    expect(orderOf(store.searchResults!)).toEqual(["a", "c"]);

    await store.fetchMoreFiltered();

    expect(orderOf(store.searchResults!)).toEqual(["a", "b", "c", "d"]);
    expect(store.searchOffset).toBe(4);
  });
});
