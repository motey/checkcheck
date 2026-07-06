// ── Item outbox-op builders (WI-8) ───────────────────────────────────────────
//
// Maps the checklist-item REST endpoints to the WI-7 outbox op shape
// (`OutboxOpInput`). WI-7 deliberately shipped no builders; this is the first
// batch (items). WI-9 reuses/extends it for positions and the checklist store.
//
// Keeping these in one framework-free module (no Nuxt/Vue/idb imports) means the
// optimistic store actions stay readable — they mutate local state and hand a
// built op to `useOutbox().enqueue(...)` — and the mapping is unit-testable in
// plain vitest.
//
// `path` is the openFetch path *template* with `pathParams` filling the braces
// (the shape `$checkapi` takes and the outbox transport replays), matching how
// stores/checklist_item.ts calls the endpoints on the legacy path.

import type { OutboxOpInput } from "@/utils/outbox";

const ITEM_COLLECTION_PATH = "/api/checklist/{checklist_id}/item";
const ITEM_PATH = "/api/checklist/{checklist_id}/item/{checklist_item_id}";
const ITEM_STATE_PATH = "/api/checklist/{checklist_id}/item/{checklist_item_id}/state";

/**
 * The step between two client-appended item indices (WI-8 decision).
 *
 * Online, the server assigns `position.index` on create. Offline there is no
 * response, so `create` synthesises an append index: `max(existing index) +
 * ITEM_INDEX_STEP`. This is the *minimal* placement WI-8 needs — a new item
 * lands at the end of the list and the binary-search insert
 * (`_insertNewAtCorrectIndex`) stays correct because the value is numeric and
 * larger than every existing one. Full fractional-index reordering (mid-list
 * insert, drag) is WI-9; this is not that.
 */
export const ITEM_INDEX_STEP = 1;

/**
 * The append index for a new item: one step past the largest existing index, or
 * the first step for an empty list. Pure and numeric so the store's binary
 * search over `position.index` keeps working offline.
 */
export function nextItemIndex(
  items: ReadonlyArray<{ position?: { index?: number | null } }>
): number {
  let max = 0;
  let seen = false;
  for (const it of items) {
    const idx = it?.position?.index;
    if (typeof idx === "number" && (!seen || idx > max)) {
      max = idx;
      seen = true;
    }
  }
  return seen ? max + ITEM_INDEX_STEP : ITEM_INDEX_STEP;
}

/** Body for a client-generated item create — carries the client `id` (protocol §8). */
export interface ItemCreateOpBody {
  text?: string | null;
  position?: { index: number; indentation?: number | null };
  state?: { checked?: boolean };
}

/**
 * `POST /api/checklist/{id}/item` with a client-supplied `id`, so a replay is an
 * idempotent no-op (returns the existing row) rather than a duplicate.
 */
export function itemCreateOp(
  checkListId: string,
  itemId: string,
  body: ItemCreateOpBody
): OutboxOpInput {
  return {
    entityType: "item",
    entityId: itemId,
    kind: "create",
    request: {
      method: "post",
      path: ITEM_COLLECTION_PATH,
      pathParams: { checklist_id: checkListId },
      body: { ...body, id: itemId },
    },
  };
}

/** `PATCH /api/checklist/{id}/item/{itemId}` — content edit (text). Replay-safe LWW. */
export function itemUpdateOp(
  checkListId: string,
  itemId: string,
  body: { text?: string | null }
): OutboxOpInput {
  return {
    entityType: "item",
    entityId: itemId,
    kind: "update",
    request: {
      method: "patch",
      path: ITEM_PATH,
      pathParams: { checklist_id: checkListId, checklist_item_id: itemId },
      body: { ...body },
    },
  };
}

/** `PATCH /api/checklist/{id}/item/{itemId}/state` — check/uncheck. Replay-safe LWW. */
export function itemStateOp(
  checkListId: string,
  itemId: string,
  body: { checked: boolean }
): OutboxOpInput {
  return {
    entityType: "item",
    entityId: itemId,
    kind: "state",
    request: {
      method: "patch",
      path: ITEM_STATE_PATH,
      pathParams: { checklist_id: checkListId, checklist_item_id: itemId },
      body: { ...body },
    },
  };
}

/** `DELETE /api/checklist/{id}/item/{itemId}` — tombstone. Re-issuing is idempotent. */
export function itemDeleteOp(checkListId: string, itemId: string): OutboxOpInput {
  return {
    entityType: "item",
    entityId: itemId,
    kind: "delete",
    request: {
      method: "delete",
      path: ITEM_PATH,
      pathParams: { checklist_id: checkListId, checklist_item_id: itemId },
    },
  };
}
