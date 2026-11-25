// Card motion on the board (issues #30 and #34): the hints the stores note for
// the board, and the pure FLIP plan. The board's measuring/playing is checked in
// E2E (card-copy.spec.ts "animates the copy out of the source card",
// archive.spec.ts "animates a card leaving the board").
import { afterEach, describe, it, expect } from "vitest";
import {
  CARD_LEAVE_SYNC_TTL_MS,
  CARD_MOTION_TTL_MS,
  clearCardMotions,
  matchCardMotions,
  noteCardLeave,
  noteCardSpawn,
  pendingCardMotions,
  planCardFlip,
  type Box,
  type CardMotion,
} from "@/utils/cardMotion";

const VP = { width: 1000, height: 800 };

function box(left: number, top: number, width = 200, height = 120): Box {
  return { left, top, width, height };
}

const spawnOf = (id: string, sourceId: string): CardMotion => ({ kind: "spawn", id, sourceId, at: 0, ttl: 1 });
const leaveOf = (id: string): CardMotion => ({ kind: "leave", id, at: 0, ttl: 1 });

describe("motion hints", () => {
  afterEach(() => clearCardMotions());

  it("are returned while fresh and dropped once stale", () => {
    noteCardSpawn("new", "src", 1000);
    expect(pendingCardMotions(1000 + CARD_MOTION_TTL_MS)).toEqual([
      { kind: "spawn", id: "new", sourceId: "src", at: 1000, ttl: CARD_MOTION_TTL_MS },
    ]);
    expect(pendingCardMotions(1001 + CARD_MOTION_TTL_MS)).toEqual([]);
    // Dropped for good, not just hidden.
    expect(pendingCardMotions(1000)).toEqual([]);
  });

  it("keep one hint per card, so two quick actions both animate", () => {
    noteCardSpawn("a", "src", 0);
    noteCardSpawn("b", "src", 1);
    noteCardLeave("c", { now: 2 });
    noteCardLeave("c", { now: 3 });
    expect(pendingCardMotions(4).map((m) => [m.kind, m.id, m.at])).toEqual([
      ["spawn", "a", 0],
      ["spawn", "b", 1],
      ["leave", "c", 3],
    ]);
  });

  it("a leave that arrives through the sync waits longer", () => {
    noteCardLeave("x", { now: 0, ttl: CARD_LEAVE_SYNC_TTL_MS });
    expect(pendingCardMotions(CARD_MOTION_TTL_MS + 1)).toHaveLength(1);
    expect(pendingCardMotions(CARD_LEAVE_SYNC_TTL_MS + 1)).toEqual([]);
  });

  it("clears only the hints it is given, unless a newer one replaced them", () => {
    noteCardLeave("a", { now: 0 });
    noteCardLeave("b", { now: 0 });
    const [a] = pendingCardMotions(1);
    noteCardLeave("a", { now: 1 }); // a newer hint for the same card
    clearCardMotions([a!, pendingCardMotions(1).find((m) => m.id === "b")!]);
    expect(pendingCardMotions(2).map((m) => [m.id, m.at])).toEqual([["a", 1]]);
    clearCardMotions();
    expect(pendingCardMotions(2)).toEqual([]);
  });
});

describe("matchCardMotions", () => {
  const ids = (...xs: string[]) => new Set(xs);

  it("carries out a spawn once its card is there, and animates it only if it is new", () => {
    const fresh = spawnOf("new", "src");
    expect(matchCardMotions([fresh], ids("src"), ids("src", "new"))).toEqual({ done: [fresh], animate: [fresh] });
    // Already on the board (a re-render after the insert): consumed, not animated.
    expect(matchCardMotions([fresh], ids("src", "new"), ids("src", "new"))).toEqual({ done: [fresh], animate: [] });
    // Not on this board (a filtered view): stays pending.
    expect(matchCardMotions([fresh], ids("src"), ids("src"))).toEqual({ done: [], animate: [] });
  });

  it("carries out a leave when a shown card is gone", () => {
    const leave = leaveOf("x");
    expect(matchCardMotions([leave], ids("x", "y"), ids("y"))).toEqual({ done: [leave], animate: [leave] });
    // Still there (the removal comes later through the sync): stays pending.
    expect(matchCardMotions([leave], ids("x", "y"), ids("x", "y"))).toEqual({ done: [], animate: [] });
    // Never shown here: nothing to animate, stays pending until it goes stale.
    expect(matchCardMotions([leave], ids("y"), ids("y"))).toEqual({ done: [], animate: [] });
  });
});

describe("planCardFlip: spawn", () => {
  const spawn = [spawnOf("new", "src")];

  it("starts the new card on the source card and slides the pushed cards", () => {
    // Before: src | b. After: src | new | b (the copy lands right after its source).
    const first = new Map([
      ["src", box(0, 0)],
      ["b", box(220, 0)],
    ]);
    const last = new Map([
      ["src", box(0, 0)],
      ["new", box(220, 0)],
      ["b", box(440, 0)],
    ]);
    expect(planCardFlip(first, last, spawn, VP)).toEqual([
      { id: "new", kind: "spawn", dx: -220, dy: 0 },
      { id: "b", kind: "move", dx: -220, dy: 0 },
    ]);
  });

  it("offsets the new card by the source's distance when the source is further down", () => {
    const first = new Map([["src", box(220, 140)]]);
    const last = new Map([
      ["new", box(0, 0)],
      ["src", box(440, 140)],
    ]);
    expect(planCardFlip(first, last, spawn, VP)[0]).toEqual({ id: "new", kind: "spawn", dx: 220, dy: 140 });
  });

  it("grows the new card in when the source is not on the board", () => {
    const last = new Map([["new", box(0, 0)]]);
    expect(planCardFlip(new Map(), last, spawn, VP)).toEqual([{ id: "new", kind: "appear" }]);
  });

  it("leaves cards alone that did not move or stay off screen", () => {
    const first = new Map([
      ["src", box(0, 0)],
      ["still", box(220, 0)],
      ["far", box(0, 2000)],
    ]);
    const last = new Map([
      ["new", box(440, 0)],
      ["src", box(0, 0)],
      ["still", box(220.4, 0)],
      ["far", box(220, 2000)],
    ]);
    expect(planCardFlip(first, last, spawn, VP)).toEqual([{ id: "new", kind: "spawn", dx: -440, dy: 0 }]);
  });

  it("animates a card that moves into or out of view", () => {
    const first = new Map([
      ["src", box(0, 0)],
      ["edge", box(800, 700)],
    ]);
    const last = new Map([
      ["new", box(0, 0)],
      ["src", box(220, 0)],
      ["edge", box(0, 840)],
    ]);
    const edge = planCardFlip(first, last, spawn, VP).find((f) => f.id === "edge");
    expect(edge).toEqual({ id: "edge", kind: "move", dx: 800, dy: -140 });
  });

  it("does nothing for an off-screen copy of an off-screen source", () => {
    const first = new Map([["src", box(0, 3000)]]);
    const last = new Map([
      ["new", box(0, 2000)],
      ["src", box(220, 3000)],
    ]);
    expect(planCardFlip(first, last, spawn, VP)).toEqual([]);
  });
});

describe("planCardFlip: leave", () => {
  it("fades the leaving card out where it was and slides the others into the gap", () => {
    // Before: a | x | b | c. After: a | b | c.
    const first = new Map([
      ["a", box(0, 0)],
      ["x", box(220, 0)],
      ["b", box(440, 0)],
      ["c", box(0, 140)],
    ]);
    const last = new Map([
      ["a", box(0, 0)],
      ["b", box(220, 0)],
      ["c", box(440, 0)],
    ]);
    expect(planCardFlip(first, last, [leaveOf("x")], VP)).toEqual([
      { id: "x", kind: "leave", box: box(220, 0) },
      { id: "b", kind: "move", dx: 220, dy: 0 },
      { id: "c", kind: "move", dx: -440, dy: 140 },
    ]);
  });

  it("no ghost for a leaving card that was off screen, but the visible cards still slide", () => {
    const first = new Map([
      ["x", box(0, -500)],
      ["b", box(0, 100)],
    ]);
    const last = new Map([["b", box(0, -40)]]);
    expect(planCardFlip(first, last, [leaveOf("x")], VP)).toEqual([{ id: "b", kind: "move", dx: 0, dy: 140 }]);
  });

  it("a card that vanished without a hint just goes (another device, a filter)", () => {
    const first = new Map([
      ["gone", box(0, 0)],
      ["x", box(220, 0)],
    ]);
    const last = new Map<string, Box>();
    expect(planCardFlip(first, last, [leaveOf("x")], VP)).toEqual([{ id: "x", kind: "leave", box: box(220, 0) }]);
  });

  it("a hinted card that is still on the board gets no ghost", () => {
    const first = new Map([["x", box(0, 0)]]);
    const last = new Map([["x", box(0, 0)]]);
    expect(planCardFlip(first, last, [leaveOf("x")], VP)).toEqual([]);
  });

  it("two cards leaving at once both fade out", () => {
    const first = new Map([
      ["x", box(0, 0)],
      ["y", box(220, 0)],
      ["b", box(440, 0)],
    ]);
    const last = new Map([["b", box(0, 0)]]);
    expect(planCardFlip(first, last, [leaveOf("x"), leaveOf("y")], VP)).toEqual([
      { id: "x", kind: "leave", box: box(0, 0) },
      { id: "y", kind: "leave", box: box(220, 0) },
      { id: "b", kind: "move", dx: 440, dy: 0 },
    ]);
  });
});
