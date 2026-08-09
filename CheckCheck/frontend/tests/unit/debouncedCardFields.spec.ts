// Regression test for the "editing the notes drops the title write" bug.
//
// Report: in the open card, typing a title and then touching the notes within the
// 500ms debounce window persisted the notes only. The title stayed on screen (the
// local copy is decoupled from the store) until the next delta pull snapped it
// back to the old value, so the loss was invisible at the moment it happened.
//
// Root cause: `CheckList.vue` pushed both fields through ONE `useDebounceFn`,
// which keeps only the last call's arguments: the notes call replaced the queued
// title call. `useDebouncedCardFields` gives each field its own timer, so this
// spec pins exactly that: writes to different fields never cancel each other,
// writes to the same field still coalesce.
//
// Fake timers make the debounce window exact; the E2E case in
// `tests/e2e/card-editor.spec.ts` covers the same behaviour through the real UI.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useDebouncedCardFields } from "@/composables/useDebouncedCardFields";

describe("useDebouncedCardFields", () => {
  let write: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    write = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("persists BOTH fields when the notes are edited inside the title's debounce window", () => {
    const queue = useDebouncedCardFields(write, 500, 3000);

    queue("name", "Groceries");
    vi.advanceTimersByTime(100); // well inside the 500ms window
    queue("text", "for the weekend");

    vi.advanceTimersByTime(500);

    expect(write).toHaveBeenCalledTimes(2);
    expect(write).toHaveBeenCalledWith("name", "Groceries");
    expect(write).toHaveBeenCalledWith("text", "for the weekend");
  });

  it("coalesces repeated writes to the same field down to the newest value", () => {
    const queue = useDebouncedCardFields(write, 500, 3000);

    queue("name", "G");
    vi.advanceTimersByTime(100);
    queue("name", "Gro");
    vi.advanceTimersByTime(100);
    queue("name", "Groceries");

    vi.advanceTimersByTime(500);

    expect(write).toHaveBeenCalledTimes(1);
    expect(write).toHaveBeenCalledWith("name", "Groceries");
  });

  it("writes nothing before the debounce window elapses", () => {
    const queue = useDebouncedCardFields(write, 500, 3000);

    queue("name", "Groceries");
    vi.advanceTimersByTime(499);

    expect(write).not.toHaveBeenCalled();
  });

  it("keeps the fields independent when edits interleave", () => {
    const queue = useDebouncedCardFields(write, 500, 3000);

    queue("name", "Grocer");
    vi.advanceTimersByTime(50);
    queue("text", "for the");
    vi.advanceTimersByTime(50);
    queue("name", "Groceries");
    vi.advanceTimersByTime(50);
    queue("text", "for the weekend");

    vi.advanceTimersByTime(500);

    expect(write).toHaveBeenCalledTimes(2);
    expect(write).toHaveBeenCalledWith("name", "Groceries");
    expect(write).toHaveBeenCalledWith("text", "for the weekend");
  });

  it("flushes at maxWait while one field is typed continuously", () => {
    const queue = useDebouncedCardFields(write, 500, 3000);

    // A keystroke every 100ms never lets the 500ms timer expire on its own.
    for (let i = 0; i < 40; i++) {
      queue("text", `note-${i}`);
      vi.advanceTimersByTime(100);
    }

    expect(write).toHaveBeenCalled();
    expect(write.mock.calls.every(([field]) => field === "text")).toBe(true);
  });
});
