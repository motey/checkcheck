// Regression test for the "checked items snaps back" bug.
//
// Report: toggling "Separate checked items" (checked_items_seperated) or
// expanding/collapsing checked items (checked_items_collapsed) applies locally
// but reverts ~200–800ms later. Root cause: the local-first `_localUpdate`
// applies the new field to the in-store row (so the UI flips) but the outbox op
// it enqueues only carries name/text/color_id — the two checked-items flags are
// dropped. The server therefore never persists them, and the next delta pull
// upserts the checklist with the server's stale value, snapping the toggle back.
//
// We exercise the REAL checklist store (`_localUpdate` via `update`) with the
// Nuxt-bound collaborators mocked: `useOutbox` (capture enqueued ops),
// `isLocalFirstEnabled` (force the local-first path), and `useUserStore`.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const enqueue = vi.fn();

vi.mock("@/utils/localFirst", () => ({ isLocalFirstEnabled: () => true }));
vi.mock("@/composables/useOutbox", () => ({ useOutbox: () => ({ enqueue }) }));
vi.mock("@/stores/user", () => ({ useUserStore: () => ({ myId: "user-1" }) }));

import { useCheckListsStore } from "@/stores/checklist";

const CL = "11111111-1111-1111-1111-111111111111";

const seedCheckList = (over: Record<string, any> = {}) => ({
  id: CL,
  name: "Groceries",
  text: "",
  color_id: null,
  color: null,
  checked_items_seperated: true,
  checked_items_collapsed: true,
  owner_id: "user-1",
  my_permission: "owner",
  updated_at: new Date().toISOString(),
  position: { index: 1, pinned: false, archived: false, checked_items_collapsed: true },
  labels: [],
  ...over,
});

describe("checklist _localUpdate — checked-items flags reach the outbox", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    enqueue.mockClear();
  });

  it("persists checked_items_seperated (Separate checked items toggle) to the server", async () => {
    const store = useCheckListsStore();
    store.checkLists = [seedCheckList() as any];

    await store.update(CL, { checked_items_seperated: false } as any);

    // Local optimistic row flips — the UI shows the change.
    expect(store.checkLists[0]!.checked_items_seperated).toBe(false);

    // …and the enqueued outbox op MUST carry the field, or the server never
    // learns about it and the next delta pull snaps it back.
    expect(enqueue).toHaveBeenCalledTimes(1);
    const body = enqueue.mock.calls[0]![0].request.body;
    expect(body).toMatchObject({ checked_items_seperated: false });
  });

  it("persists checked_items_collapsed (expand/collapse checked items) to the server", async () => {
    const store = useCheckListsStore();
    store.checkLists = [seedCheckList() as any];

    await store.update(CL, { checked_items_collapsed: false } as any);

    expect(store.checkLists[0]!.checked_items_collapsed).toBe(false);

    expect(enqueue).toHaveBeenCalledTimes(1);
    const body = enqueue.mock.calls[0]![0].request.body;
    expect(body).toMatchObject({ checked_items_collapsed: false });
  });
});
