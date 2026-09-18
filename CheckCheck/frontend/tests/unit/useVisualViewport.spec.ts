// @vitest-environment jsdom
//
// Unit tests for composables/useVisualViewport.ts (mobile editor plan, M1).
//
// jsdom has no `window.visualViewport`, so each test installs a fake one (an
// EventTarget with writable height/offsetTop) and a manual animation-frame
// queue, then drives resize/scroll events the way a phone keyboard would.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { effectScope, nextTick, ref } from "vue";

import { useVisualViewport } from "@/composables/useVisualViewport";

class FakeVisualViewport extends EventTarget {
  height = 800;
  offsetTop = 0;
}

let vv: FakeVisualViewport;
let frames: Map<number, FrameRequestCallback>;
let nextFrame: number;

function flushFrames() {
  const pending = [...frames.values()];
  frames.clear();
  for (const cb of pending) cb(0);
}

/** Simulate the keyboard: shrink the visual viewport and pan it. */
function keyboard(height: number, offsetTop = 0) {
  vv.height = height;
  vv.offsetTop = offsetTop;
  vv.dispatchEvent(new Event("resize"));
  vv.dispatchEvent(new Event("scroll"));
}

const cssVar = (name: string) => document.documentElement.style.getPropertyValue(name);

beforeEach(() => {
  vv = new FakeVisualViewport();
  frames = new Map();
  nextFrame = 1;
  vi.stubGlobal("visualViewport", vv);
  vi.stubGlobal("innerHeight", 800);
  vi.stubGlobal("innerWidth", 400);
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = nextFrame++;
    frames.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("style");
});

describe("useVisualViewport", () => {
  it("writes the variables immediately on start", () => {
    const scope = effectScope();
    const vp = scope.run(() => useVisualViewport())!;
    expect(cssVar("--vv-height")).toBe("800px");
    expect(cssVar("--vv-top")).toBe("0px");
    expect(vp.height.value).toBe(800);
    expect(vp.keyboardOpen.value).toBe(false);
    scope.stop();
  });

  it("updates on resize and scroll, once per animation frame", () => {
    const scope = effectScope();
    const vp = scope.run(() => useVisualViewport())!;

    keyboard(420, 120);
    // Two events, one frame queued; nothing written until it runs.
    expect(frames.size).toBe(1);
    expect(cssVar("--vv-height")).toBe("800px");

    flushFrames();
    expect(cssVar("--vv-height")).toBe("420px");
    expect(cssVar("--vv-top")).toBe("120px");
    expect(vp.height.value).toBe(420);
    expect(vp.offsetTop.value).toBe(120);
    expect(vp.keyboardOpen.value).toBe(true);

    keyboard(800, 0);
    flushFrames();
    expect(cssVar("--vv-height")).toBe("800px");
    expect(vp.keyboardOpen.value).toBe(false);
    scope.stop();
  });

  it("sees the keyboard when the layout viewport shrinks too (resizes-content)", () => {
    const scope = effectScope();
    const vp = scope.run(() => useVisualViewport())!;
    // Android with interactive-widget=resizes-content: innerHeight follows.
    vi.stubGlobal("innerHeight", 450);
    keyboard(450);
    flushFrames();
    expect(vp.keyboardOpen.value).toBe(true);
    scope.stop();
  });

  it("does not count a small resize (URL bar) as the keyboard", () => {
    const scope = effectScope();
    const vp = scope.run(() => useVisualViewport())!;
    keyboard(740);
    flushFrames();
    expect(vp.keyboardOpen.value).toBe(false);
    scope.stop();
  });

  it("resets the baseline on a width change (rotation)", () => {
    const scope = effectScope();
    const vp = scope.run(() => useVisualViewport())!;
    // Rotate to landscape: shorter, wider, no keyboard.
    vi.stubGlobal("innerWidth", 800);
    vi.stubGlobal("innerHeight", 400);
    keyboard(400);
    flushFrames();
    expect(vp.keyboardOpen.value).toBe(false);
    keyboard(200);
    flushFrames();
    expect(vp.keyboardOpen.value).toBe(true);
    scope.stop();
  });

  it("is a no-op without window.visualViewport", () => {
    vi.stubGlobal("visualViewport", undefined);
    const scope = effectScope();
    const vp = scope.run(() => useVisualViewport())!;
    expect(vp.height.value).toBeNull();
    expect(vp.keyboardOpen.value).toBe(false);
    expect(cssVar("--vv-height")).toBe("");
    scope.stop();
  });

  it("shares one listener set and removes it with the last user", () => {
    const add = vi.spyOn(vv, "addEventListener");
    const remove = vi.spyOn(vv, "removeEventListener");
    const a = effectScope();
    const b = effectScope();
    a.run(() => useVisualViewport());
    b.run(() => useVisualViewport());
    expect(add).toHaveBeenCalledTimes(2); // resize + scroll, once

    a.stop();
    expect(remove).not.toHaveBeenCalled();
    expect(cssVar("--vv-height")).toBe("800px");

    b.stop();
    expect(remove).toHaveBeenCalledTimes(2);
    expect(cssVar("--vv-height")).toBe("");
    expect(cssVar("--vv-top")).toBe("");

    // Events after teardown change nothing.
    keyboard(300);
    flushFrames();
    expect(cssVar("--vv-height")).toBe("");
  });

  it("drops a queued frame on teardown", () => {
    const scope = effectScope();
    scope.run(() => useVisualViewport());
    keyboard(300);
    expect(frames.size).toBe(1);
    scope.stop();
    expect(frames.size).toBe(0);
  });

  it("only tracks while `active` is true", async () => {
    const add = vi.spyOn(vv, "addEventListener");
    const remove = vi.spyOn(vv, "removeEventListener");
    const open = ref(false);
    const scope = effectScope();
    const vp = scope.run(() => useVisualViewport(() => open.value))!;
    expect(add).not.toHaveBeenCalled();
    expect(vp.height.value).toBeNull();

    open.value = true;
    await nextTick();
    expect(add).toHaveBeenCalledTimes(2);
    expect(cssVar("--vv-height")).toBe("800px");

    open.value = false;
    await nextTick();
    expect(remove).toHaveBeenCalledTimes(2);
    expect(cssVar("--vv-height")).toBe("");

    // Stopping the scope while inactive must not release a second time.
    open.value = true;
    await nextTick();
    open.value = false;
    await nextTick();
    scope.stop();
    expect(remove).toHaveBeenCalledTimes(4);
  });
});
