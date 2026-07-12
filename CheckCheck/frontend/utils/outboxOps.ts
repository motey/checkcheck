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

import { Decimal } from "decimal.js";
import type { OutboxOpInput } from "@/utils/outbox";

const ITEM_COLLECTION_PATH = "/api/checklist/{checklist_id}/item";
const ITEM_PATH = "/api/checklist/{checklist_id}/item/{checklist_item_id}";
const ITEM_STATE_PATH = "/api/checklist/{checklist_id}/item/{checklist_item_id}/state";
const ITEM_POSITION_PATH = "/api/checklist/{checklist_id}/item/{checklist_item_id}/position";

const CHECKLIST_COLLECTION_PATH = "/api/checklist";
const CHECKLIST_PATH = "/api/checklist/{checklist_id}";
const CHECKLIST_POSITION_PATH = "/api/checklist/{checklist_id}/position";
const CHECKLIST_LABEL_PATH = "/api/checklist/{checklist_id}/label/{label_id}";

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

/**
 * The gap the server leaves when an item/checklist is dropped at either end of a
 * list (`move/{above,under}` with no neighbour). Mirrors the backend's
 * `decimal.Decimal("0.4")` so a client-computed offline reorder lands on the same
 * fractional key the server would have assigned online. Kept as a string so
 * decimal.js parses it exactly (not the lossy `0.4` binary float).
 */
export const POSITION_END_GAP = "0.4";

/**
 * The fractional index for an entity dropped **between** two neighbours (whose
 * current indices are `before` < `after`). This is the client-side twin of the
 * server's `move/{above,under}` math (routes_checklist_item_pos.py /
 * routes_checklist_position.py): the midpoint of two present neighbours, or one
 * `POSITION_END_GAP` past the single neighbour when dropping at an end. Computed
 * with decimal.js so repeated mid-list inserts stay exact instead of drifting on
 * binary-float rounding — the resulting number is PATCHed verbatim (the plain
 * position endpoint stores whatever index it is given), so the client and server
 * converge on the same key.
 *
 * `before` / `after` are the index *values* of the neighbours bracketing the
 * target slot (`null` = that side is the end of the list). Both `null` (a
 * one-element list, so nothing to order against) yields `0`.
 */
export function fractionalIndexBetween(
  before: number | null | undefined,
  after: number | null | undefined
): number {
  const hasBefore = typeof before === "number" && Number.isFinite(before);
  const hasAfter = typeof after === "number" && Number.isFinite(after);
  if (hasBefore && hasAfter) {
    return new Decimal(before as number).plus(after as number).div(2).toNumber();
  }
  if (hasBefore) return new Decimal(before as number).plus(POSITION_END_GAP).toNumber();
  if (hasAfter) return new Decimal(after as number).minus(POSITION_END_GAP).toNumber();
  return 0;
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

/**
 * `PATCH /api/checklist/{id}/item/{itemId}/position` — a plain position write
 * (WI-9). Offline reorder computes the fractional `index` client-side
 * (`fractionalIndexBetween`) and enqueues this instead of the legacy
 * `move/{above,under}` PUT, since those recompute the index server-side and can't
 * run offline. `kind:"position"` is coalescable — successive drags of the same
 * item collapse to the final index (LWW), so only one PATCH is ever sent.
 */
export function itemPositionOp(
  checkListId: string,
  itemId: string,
  body: { index?: number; indentation?: number }
): OutboxOpInput {
  return {
    entityType: "item",
    entityId: itemId,
    kind: "position",
    request: {
      method: "patch",
      path: ITEM_POSITION_PATH,
      pathParams: { checklist_id: checkListId, checklist_item_id: itemId },
      body: { ...body },
    },
  };
}

// ── Checklist outbox-op builders (WI-9) ──────────────────────────────────────
//
// The checklist store's twin of the item builders above: create (client UUID) /
// update (name/text/color) / delete / position (index/pinned/archived). Same op
// shape and same replay-idempotency guarantee — a `create` carries the client id
// (protocol §8), edits/positions are LWW-coalescable, a `delete` is a tombstone.

/** Body for a client-generated checklist create — carries the client `id` (protocol §8). */
export interface ChecklistCreateOpBody {
  name?: string | null;
  text?: string | null;
  color_id?: string | null;
  position?: { index?: number; pinned?: boolean; archived?: boolean };
}

/**
 * `POST /api/checklist` with a client-supplied `id`, so a replay returns the
 * existing card instead of duplicating it (protocol §8 / WI-3). We send an
 * explicit `position.index` so the server stores the same index the board is
 * already showing (omitting it makes the server compute `highest + 0.4` at replay
 * time, which could diverge from the optimistic placement).
 */
export function checklistCreateOp(checkListId: string, body: ChecklistCreateOpBody): OutboxOpInput {
  return {
    entityType: "checklist",
    entityId: checkListId,
    kind: "create",
    request: {
      method: "post",
      path: CHECKLIST_COLLECTION_PATH,
      body: { ...body, id: checkListId },
    },
  };
}

/** `PATCH /api/checklist/{id}` — content edit (name/text/color). Replay-safe LWW. */
export function checklistUpdateOp(
  checkListId: string,
  body: {
    name?: string | null;
    text?: string | null;
    color_id?: string | null;
    checked_items_seperated?: boolean | null;
    checked_items_collapsed?: boolean | null;
  }
): OutboxOpInput {
  return {
    entityType: "checklist",
    entityId: checkListId,
    kind: "update",
    request: {
      method: "patch",
      path: CHECKLIST_PATH,
      pathParams: { checklist_id: checkListId },
      body: { ...body },
    },
  };
}

/** `DELETE /api/checklist/{id}` — permanent delete. Re-issuing is idempotent. */
export function checklistDeleteOp(checkListId: string): OutboxOpInput {
  return {
    entityType: "checklist",
    entityId: checkListId,
    kind: "delete",
    request: {
      method: "delete",
      path: CHECKLIST_PATH,
      pathParams: { checklist_id: checkListId },
    },
  };
}

/**
 * `PATCH /api/checklist/{id}/position` — index (reorder), pinned or archived
 * (archive lives on the position row). Offline reorder computes the fractional
 * `index` with `fractionalIndexBetween`; archive/pin just flip their flag. LWW-
 * coalescable so a flurry of drags/toggles collapses to the final position.
 */
export function checklistPositionOp(
  checkListId: string,
  body: { index?: number; pinned?: boolean; archived?: boolean }
): OutboxOpInput {
  return {
    entityType: "checklist",
    entityId: checkListId,
    kind: "position",
    request: {
      method: "patch",
      path: CHECKLIST_POSITION_PATH,
      pathParams: { checklist_id: checkListId },
      body: { ...body },
    },
  };
}

// ── Checklist⇄label association builders (WI-9) ───────────────────────────────
//
// Attaching/detaching a label to a card is a membership toggle, not a label-CRUD
// op (label create/update/delete stay online — WI-12). The op's entity is the
// (checklist, label) PAIR: `entityId = "{checklistId}:{labelId}"` so an
// attach-then-detach of the same pair cancels via the outbox's create/delete
// coalesce (rule 2), while toggles on other pairs are independent. Both endpoints
// are idempotent (PUT upserts the link, DELETE is a no-op if absent), so replay
// is safe.

/** The composite entity id keying a single checklist⇄label association op. */
export function checklistLabelKey(checkListId: string, labelId: string): string {
  return `${checkListId}:${labelId}`;
}

/** `PUT /api/checklist/{id}/label/{labelId}` — attach a label (idempotent upsert). */
export function checklistLabelAddOp(checkListId: string, labelId: string): OutboxOpInput {
  return {
    entityType: "label",
    entityId: checklistLabelKey(checkListId, labelId),
    kind: "create",
    request: {
      method: "put",
      path: CHECKLIST_LABEL_PATH,
      pathParams: { checklist_id: checkListId, label_id: labelId },
    },
  };
}

/** `DELETE /api/checklist/{id}/label/{labelId}` — detach a label. Cancels a queued attach. */
export function checklistLabelRemoveOp(checkListId: string, labelId: string): OutboxOpInput {
  return {
    entityType: "label",
    entityId: checklistLabelKey(checkListId, labelId),
    kind: "delete",
    request: {
      method: "delete",
      path: CHECKLIST_LABEL_PATH,
      pathParams: { checklist_id: checkListId, label_id: labelId },
    },
  };
}
