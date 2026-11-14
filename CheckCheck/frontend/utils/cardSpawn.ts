// ── Card spawn animation: the hint and the pure FLIP plan ────────────────────
//
// "Duplicate card" and "Move checked items to new card" put a new card at the
// top of the board, so every other card moves one slot. Without help the grid
// jumps into the new order in one frame and the eye loses both cards (issue
// #30). The board (CheckListBoard.vue) animates that change instead: the new
// card floats out of the source card's spot to its own, the others slide.
//
// Two parts live here, both store-free so they are unit-testable:
//   • a short-lived, non-reactive hint "card X was just made from card Y",
//     noted by the checklist store right before it inserts the copy. Only a
//     change carrying this hint animates: paging, filters, search and changes
//     from other devices stay instant.
//   • planCardFlip(): from the card rects before and after the change, which
//     card moves how. The board only measures and plays.

/** How long a noted spawn waits for the board to pick it up. */
export const CARD_SPAWN_TTL_MS = 2000;

export interface CardSpawn {
  newId: string;
  sourceId: string;
  at: number;
}

let pending: CardSpawn | null = null;

/** The store just inserted card `newId`, made from card `sourceId`. */
export function noteCardSpawn(newId: string, sourceId: string, now: number = Date.now()): void {
  pending = { newId, sourceId, at: now };
}

/** The pending spawn if it is still fresh, else null (a stale one is dropped). */
export function peekCardSpawn(now: number = Date.now()): CardSpawn | null {
  if (pending && now - pending.at > CARD_SPAWN_TTL_MS) pending = null;
  return pending;
}

export function clearCardSpawn(): void {
  pending = null;
}

/** The part of a DOMRect the plan needs (plain objects in tests). */
export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export type CardFlip =
  /** The new card: start offset by (dx, dy) from its slot, i.e. on the source card. */
  | { id: string; kind: "spawn"; dx: number; dy: number }
  /** The new card when the source is not on the board: grow in on its slot. */
  | { id: string; kind: "appear" }
  /** Any other card that changed slot: start offset by (dx, dy), slide home. */
  | { id: string; kind: "move"; dx: number; dy: number };

function inViewport(b: Box, vp: Viewport): boolean {
  return b.top < vp.height && b.top + b.height > 0 && b.left < vp.width && b.left + b.width > 0;
}

/**
 * Which cards to animate. `first`/`last` map card id -> box before/after the
 * board re-rendered. Cards that did not move, and cards off screen both before
 * and after, are left alone (a long board must not start hundreds of
 * animations nobody sees).
 */
export function planCardFlip(
  first: ReadonlyMap<string, Box>,
  last: ReadonlyMap<string, Box>,
  spawn: Pick<CardSpawn, "newId" | "sourceId">,
  vp: Viewport
): CardFlip[] {
  const flips: CardFlip[] = [];
  for (const [id, to] of last) {
    if (id === spawn.newId) {
      const from = first.get(spawn.sourceId);
      if (from && (inViewport(from, vp) || inViewport(to, vp))) {
        flips.push({ id, kind: "spawn", dx: from.left - to.left, dy: from.top - to.top });
      } else if (inViewport(to, vp)) {
        flips.push({ id, kind: "appear" });
      }
      continue;
    }
    const from = first.get(id);
    if (!from) continue;
    const dx = from.left - to.left;
    const dy = from.top - to.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
    if (!inViewport(from, vp) && !inViewport(to, vp)) continue;
    flips.push({ id, kind: "move", dx, dy });
  }
  return flips;
}
