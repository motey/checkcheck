// Unit tests for the `/p/<token>` viewer's drag-reorder index math
// (`usePublicCard().reorderItems`, issue #11).
//
// The public surface persists a reorder as a plain `PATCH .../position` carrying a
// fractional index the CLIENT computed: there are no anonymous twins of the
// server's `move/above` / `move/under` routes. So the number this composable puts
// on the wire is the whole contract, and a wrong one is invisible until two items
// collide on the same index. Hence a direct test of exactly that number.
//
// Follows the `localSnapshot.spec.ts` seam pattern: the Nuxt-bound bits (the
// `$checkapi` transport reached via `useNuxtApp`, the `usePermissions` gate, and
// the reactivity auto-imports) are stubbed, while the real composable and the real
// `utils/helpers` + `utils/outboxOps` index math run unchanged.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, computed, watch } from "vue";
import { usePermissions } from "@/composables/usePermissions";

const checkapi = vi.fn();

// The composable calls these as Nuxt auto-imports (runtime globals under Nuxt).
vi.stubGlobal("ref", ref);
vi.stubGlobal("computed", computed);
vi.stubGlobal("watch", watch);
vi.stubGlobal("useNuxtApp", () => ({ $checkapi: checkapi }));
vi.stubGlobal("usePermissions", usePermissions);

import { usePublicCard } from "@/composables/usePublicCard";

const ITEM = (id: string, index: number, checked = false): any => ({
  id,
  checklist_id: "card-1",
  text: `item-${id}`,
  updated_at: "2026-01-01T00:00:00",
  position: { index, indentation: 0, updated_at: "2026-01-01T00:00:00" },
  state: { checked, updated_at: "2026-01-01T00:00:00" },
});

/**
 * A composable primed as an edit-level link over `items`, with `$checkapi`
 * answering a position PATCH by echoing the index it was given (what the real
 * route does).
 */
function primed(items: any[], permission = "edit") {
  const pub = usePublicCard("tok");
  pub.card.value = {
    id: "card-1",
    name: "Card",
    my_permission: permission,
    checked_items_seperated: true,
    checked_items_collapsed: false,
  } as any;
  pub.items.value = items;
  checkapi.mockImplementation(async (_path: string, opts: any) => ({
    checklist_item_id: opts.path.checklist_item_id,
    index: opts.body.index,
    indentation: 0,
    updated_at: "2026-01-01T00:00:01",
  }));
  return pub;
}

/** The index handed to the position PATCH by the single expected call. */
function patchedIndex(): number {
  expect(checkapi).toHaveBeenCalledTimes(1);
  const [path, opts] = checkapi.mock.calls[0]!;
  expect(path).toBe("/api/public/checklist/{token}/item/{checklist_item_id}/position");
  expect(opts.method).toBe("patch");
  return opts.body.index;
}

beforeEach(() => {
  checkapi.mockReset();
});

describe("usePublicCard().reorderItems index math", () => {
  it("dropping an item between two others PATCHes the midpoint of their indices", async () => {
    const a = ITEM("a", 0.4);
    const b = ITEM("b", 0.8);
    const c = ITEM("c", 1.2);
    const pub = primed([a, b, c]);

    // c dragged between a and b → it now follows a, whose successor is b.
    await pub.reorderItems([a, c, b], c);

    expect(patchedIndex()).toBe(0.6);
    expect(pub.items.value.map((i) => i.id)).toEqual(["a", "c", "b"]);
  });

  it("dropping an item at the very top PATCHes one gap below the first index", async () => {
    const a = ITEM("a", 0.4);
    const b = ITEM("b", 0.8);
    const pub = primed([a, b]);

    // b dragged to the top → it is now above a, which has no predecessor.
    await pub.reorderItems([b, a], b);

    // POSITION_END_GAP (0.4) below the item it landed above.
    expect(patchedIndex()).toBe(0);
    expect(pub.items.value.map((i) => i.id)).toEqual(["b", "a"]);
  });

  it("dropping an item at the very bottom PATCHes one gap above the last index", async () => {
    const a = ITEM("a", 0.4);
    const b = ITEM("b", 0.8);
    const pub = primed([a, b]);

    // a dragged to the bottom → it now follows b, which has no successor.
    await pub.reorderItems([b, a], a);

    expect(patchedIndex()).toBe(1.2);
    expect(pub.items.value.map((i) => i.id)).toEqual(["b", "a"]);
  });

  it("computes exact decimals, so repeated mid-list drops never drift", async () => {
    // Binary floats would turn (0.1 + 0.3) / 2 into 0.20000000000000004 and each
    // further midpoint would compound it; decimal.js keeps the key exact.
    const a = ITEM("a", 0.1);
    const b = ITEM("b", 0.3);
    const c = ITEM("c", 5);
    const pub = primed([a, b, c]);

    await pub.reorderItems([a, c, b], c);

    expect(patchedIndex()).toBe(0.2);
  });

  it("a single-item list has nothing to order against and PATCHes nothing", async () => {
    const a = ITEM("a", 0.4);
    const pub = primed([a]);

    await pub.reorderItems([a], a);

    expect(checkapi).not.toHaveBeenCalled();
  });

  it("a check-level link cannot reorder at all", async () => {
    const a = ITEM("a", 0.4);
    const b = ITEM("b", 0.8);
    const pub = primed([a, b], "check");

    await pub.reorderItems([b, a], b);

    expect(checkapi).not.toHaveBeenCalled();
    // The local order is left alone too, since nothing was persisted.
    expect(pub.items.value.map((i) => i.id)).toEqual(["a", "b"]);
  });

  it("re-sorts the local list from the index the server echoed back", async () => {
    const a = ITEM("a", 0.4);
    const b = ITEM("b", 0.8);
    const c = ITEM("c", 1.2);
    const pub = primed([a, b, c]);

    await pub.reorderItems([c, a, b], c);

    expect(patchedIndex()).toBe(0);
    expect(pub.items.value[0]!.position.index).toBe(0);
    expect(pub.items.value.map((i) => i.id)).toEqual(["c", "a", "b"]);
  });
});
