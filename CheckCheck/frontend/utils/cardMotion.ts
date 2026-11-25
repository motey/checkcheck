// ── Card motion on the board: the hints and the pure FLIP plan ───────────────
//
// Without help the board grid jumps into a new order in one frame and the eye
// loses track of what changed. The board (CheckListBoard.vue) animates two kinds
// of change instead:
//   • spawn (issue #30): "Duplicate card" and "Move checked items to new card"
//     insert a card right after its source. The new card floats out of the
//     source card's spot to its own, the cards after it slide one slot on.
//   • leave (issue #34): the user deletes, archives, restores (in the archive
//     view) or leaves a card. It fades and shrinks out where it was, the other
//     cards slide into the gap.
//
// Two parts live here, both store-free so they are unit-testable:
//   • short-lived, non-reactive hints "card X was just made from card Y" and
//     "card X is leaving because of the user", noted by the stores right before
//     they change the list. Only a change carrying a hint animates: paging,
//     filters, search and changes from other devices or users stay instant.
//     Hints are kept per card, so two quick actions both animate.
//   • planCardFlip(): from the card rects before and after the change, which
//     card moves how. The board only measures and plays.

/** How long a noted hint waits for the board to pick it up. */
export const CARD_MOTION_TTL_MS = 2000;
/**
 * For a leave whose removal arrives through the sync instead of the store action
 * itself (leaving a shared card: the server reports the card as gone in the next
 * delta pull or SSE event), so the hint has to outlive a round trip.
 */
export const CARD_LEAVE_SYNC_TTL_MS = 10_000;

export type CardMotion =
  /** Card `id` was just made from card `sourceId`. */
  | { kind: "spawn"; id: string; sourceId: string; at: number; ttl: number }
  /** Card `id` is about to leave the board because of the user. */
  | { kind: "leave"; id: string; at: number; ttl: number };

const pending = new Map<string, CardMotion>();

/** The store just inserted card `newId`, made from card `sourceId`. */
export function noteCardSpawn(newId: string, sourceId: string, now: number = Date.now()): void {
  pending.set(newId, { kind: "spawn", id: newId, sourceId, at: now, ttl: CARD_MOTION_TTL_MS });
}

/** Card `id` is about to leave the board because of the user's own action. */
export function noteCardLeave(id: string, opts: { ttl?: number; now?: number } = {}): void {
  pending.set(id, { kind: "leave", id, at: opts.now ?? Date.now(), ttl: opts.ttl ?? CARD_MOTION_TTL_MS });
}

/** The hints that are still fresh (stale ones are dropped). */
export function pendingCardMotions(now: number = Date.now()): CardMotion[] {
  for (const [id, m] of pending) if (now - m.at > m.ttl) pending.delete(id);
  return [...pending.values()];
}

/** The board picked these hints up (or they can no longer apply). */
export function clearCardMotions(motions?: Iterable<CardMotion>): void {
  if (!motions) {
    pending.clear();
    return;
  }
  for (const m of motions) if (pending.get(m.id) === m) pending.delete(m.id);
}

/**
 * Of the pending hints, the ones this board change carries out: a spawn whose
 * card is now on the board (`animate` only if it was not shown before), a leave
 * whose card was shown and is gone now. Everything else stays pending: a leave
 * that arrives through the sync is not here yet.
 */
export function matchCardMotions(
  motions: readonly CardMotion[],
  shownIds: ReadonlySet<string>,
  nextIds: ReadonlySet<string>
): { done: CardMotion[]; animate: CardMotion[] } {
  const done: CardMotion[] = [];
  const animate: CardMotion[] = [];
  for (const m of motions) {
    if (m.kind === "spawn") {
      if (!nextIds.has(m.id)) continue;
      done.push(m);
      if (!shownIds.has(m.id)) animate.push(m);
    } else if (shownIds.has(m.id) && !nextIds.has(m.id)) {
      done.push(m);
      animate.push(m);
    }
  }
  return { done, animate };
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
  /** A leaving card: fade and shrink out at `box`, where it was. */
  | { id: string; kind: "leave"; box: Box }
  /** Any other card that changed slot: start offset by (dx, dy), slide home. */
  | { id: string; kind: "move"; dx: number; dy: number };

function inViewport(b: Box, vp: Viewport): boolean {
  return b.top < vp.height && b.top + b.height > 0 && b.left < vp.width && b.left + b.width > 0;
}

/**
 * Which cards to animate. `first`/`last` map card id -> box before/after the
 * board re-rendered; `motions` are the hints this change carries out (see
 * matchCardMotions). Cards that did not move, and cards off screen both before
 * and after, are left alone (a long board must not start hundreds of
 * animations nobody sees). A card that vanished without a leave hint just goes.
 */
export function planCardFlip(
  first: ReadonlyMap<string, Box>,
  last: ReadonlyMap<string, Box>,
  motions: readonly CardMotion[],
  vp: Viewport
): CardFlip[] {
  const spawnSource = new Map<string, string>();
  const leaving = new Set<string>();
  for (const m of motions) {
    if (m.kind === "spawn") spawnSource.set(m.id, m.sourceId);
    else leaving.add(m.id);
  }
  const flips: CardFlip[] = [];
  for (const id of leaving) {
    const from = first.get(id);
    if (from && !last.has(id) && inViewport(from, vp)) flips.push({ id, kind: "leave", box: from });
  }
  for (const [id, to] of last) {
    const sourceId = spawnSource.get(id);
    if (sourceId !== undefined) {
      const from = first.get(sourceId);
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
