// ── Card copy actions: the optimistic local plan ─────────────────────────────
//
// "Duplicate" and "Move checked items to a new card" create a new card from an
// existing one in ONE server call (POST /api/checklist/{id}/duplicate and
// .../items/move-checked; the copy rules live in the backend's
// api/checklist_copy.py). Local-first, the client shows the new card right away,
// also offline, and queues the call as one outbox op (utils/outboxOps.ts).
//
// This module computes that optimistic card from what the client holds, without
// touching any store, so it is unit-testable in plain vitest. The client usually
// holds only a preview of a card, so:
//   • the copy gets the items that are loaded, each with a fresh client id that
//     is sent along in `itemIds` (source id -> copy id), so the server gives the
//     copy exactly that id and the delta pull updates the row in place;
//   • the item counts come from the source's counts (the whole card), so the
//     board shows the right totals while offline. The delta pull after the call
//     drained brings the remaining items and the preview count refresh settles
//     the counts.
// A fully loaded source gives an exact, fully loaded copy.

export type CardCopyMode = "duplicate" | "move_checked";

/** Title suffix per action. Mirrors the backend's `*_NAME_SUFFIX` constants. */
export const CARD_COPY_NAME_SUFFIX: Record<CardCopyMode, string> = {
  duplicate: "(copy)",
  move_checked: "(done)",
};

/** `"Packing list"` -> `"Packing list (copy)"`; an empty title -> `"(copy)"`. Mirrors the backend. */
export function suffixedName(name: string | null | undefined, suffix: string): string {
  const base = (name ?? "").trim();
  return base ? `${base} ${suffix}` : suffix;
}

export interface ItemCounts {
  total: number;
  checked: number;
  unchecked: number;
}

/** What the client holds of the source card. */
export interface CardCopySource {
  card: CheckListType;
  /** The loaded items (a preview window unless `fullyLoaded`). */
  items: readonly CheckListItemType[];
  fullyLoaded: boolean;
  /** The source's item counts for the whole card (not just the loaded slice). */
  counts: ItemCounts;
}

export interface CardCopyOptions {
  newId: string;
  ownerId: string;
  /** Board index of the new card (top of the board, see the checklist store). */
  index: number;
  /** ISO timestamp for the optimistic rows. */
  now: string;
  /** Id generator for the item copies (crypto.randomUUID in the app). */
  newItemId: () => string;
}

export interface CardCopyPlan {
  /** The optimistic new card. */
  card: CheckListType;
  /** Its items, in source order. */
  items: CheckListItemType[];
  /** Source item id -> copy id, sent with the op so the server uses the same ids. */
  itemIds: Record<string, string>;
  fullyLoaded: boolean;
  counts: ItemCounts;
  /** Move only: the loaded source items that leave the source card. */
  movedSourceItemIds: Set<string>;
}

/**
 * The optimistic result of a card copy action. Pure: the caller applies it to
 * the stores and enqueues the op.
 *
 * Duplicate: every loaded item, unchecked. Move: only the checked loaded items,
 * still checked (they leave the source).
 */
export function planCardCopy(mode: CardCopyMode, source: CardCopySource, opts: CardCopyOptions): CardCopyPlan {
  const moving = mode === "move_checked";
  const picked = moving ? source.items.filter((it) => it.state.checked) : [...source.items];
  const itemIds: Record<string, string> = {};
  const items: CheckListItemType[] = picked.map((src) => {
    const id = opts.newItemId();
    itemIds[src.id] = id;
    return {
      id,
      checklist_id: opts.newId,
      text: src.text,
      updated_at: opts.now,
      position: {
        index: src.position.index,
        indentation: src.position.indentation,
        updated_at: opts.now,
      },
      state: { checked: moving, updated_at: opts.now },
    };
  });
  items.sort((a, b) => a.position.index - b.position.index);

  const counts: ItemCounts = moving
    ? { total: source.counts.checked, checked: source.counts.checked, unchecked: 0 }
    : { total: source.counts.total, checked: 0, unchecked: source.counts.total };

  const src = source.card;
  const card = {
    id: opts.newId,
    name: suffixedName(src.name, CARD_COPY_NAME_SUFFIX[mode]),
    text: src.text ?? "",
    color_id: src.color_id ?? null,
    color: src.color ?? null,
    checked_items_seperated: src.checked_items_seperated,
    checked_items_collapsed: src.checked_items_collapsed,
    suggest_existing_items: src.suggest_existing_items,
    owner_id: opts.ownerId,
    my_permission: "owner",
    updated_at: opts.now,
    position: {
      index: opts.index,
      pinned: false,
      archived: false,
      checked_items_collapsed: false,
      updated_at: opts.now,
    },
    // The board holds the caller's own labels per card, which is exactly what
    // the server copies.
    labels: [...(src.labels ?? [])],
  } as CheckListType;

  return {
    card,
    items,
    itemIds,
    fullyLoaded: source.fullyLoaded,
    counts,
    movedSourceItemIds: new Set(moving ? picked.map((it) => it.id) : []),
  };
}
