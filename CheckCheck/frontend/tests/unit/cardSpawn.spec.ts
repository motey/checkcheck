// Card copy animation (issue #30): the spawn hint the store notes for the board,
// and the pure FLIP plan. The board's measuring/playing is checked in E2E
// (card-copy.spec.ts, "animates the copy out of the source card").
import { afterEach, describe, it, expect } from "vitest";
import {
  CARD_SPAWN_TTL_MS,
  clearCardSpawn,
  noteCardSpawn,
  peekCardSpawn,
  planCardFlip,
  type Box,
} from "@/utils/cardSpawn";

const VP = { width: 1000, height: 800 };

function box(left: number, top: number, width = 200, height = 120): Box {
  return { left, top, width, height };
}

describe("spawn hint", () => {
  afterEach(() => clearCardSpawn());

  it("is returned while fresh and dropped once stale", () => {
    noteCardSpawn("new", "src", 1000);
    expect(peekCardSpawn(1000 + CARD_SPAWN_TTL_MS)).toEqual({ newId: "new", sourceId: "src", at: 1000 });
    expect(peekCardSpawn(1001 + CARD_SPAWN_TTL_MS)).toBeNull();
    // Dropped for good, not just hidden.
    expect(peekCardSpawn(1000)).toBeNull();
  });

  it("keeps only the latest copy and can be cleared", () => {
    noteCardSpawn("a", "src", 0);
    noteCardSpawn("b", "src", 1);
    expect(peekCardSpawn(2)?.newId).toBe("b");
    clearCardSpawn();
    expect(peekCardSpawn(2)).toBeNull();
  });
});

describe("planCardFlip", () => {
  const spawn = { newId: "new", sourceId: "src" };

  it("starts the new card on the source card and slides the pushed cards", () => {
    // Before: src | b. After: new | src | b (the copy lands first).
    const first = new Map([
      ["src", box(0, 0)],
      ["b", box(220, 0)],
    ]);
    const last = new Map([
      ["new", box(0, 0)],
      ["src", box(220, 0)],
      ["b", box(440, 0)],
    ]);
    expect(planCardFlip(first, last, spawn, VP)).toEqual([
      { id: "new", kind: "spawn", dx: 0, dy: 0 },
      { id: "src", kind: "move", dx: -220, dy: 0 },
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
