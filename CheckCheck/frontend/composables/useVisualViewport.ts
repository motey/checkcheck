import {
  computed,
  getCurrentScope,
  onScopeDispose,
  readonly,
  ref,
  toValue,
  watch,
  type MaybeRefOrGetter,
} from "vue";

// ── Keyboard-aware viewport (mobile editor M1) ───────────────────────────────
//
// Phones open the on-screen keyboard over the page without shrinking `dvh`:
// Chrome on Android (since 108) and iOS Safari only resize the *visual*
// viewport. `interactive-widget=resizes-content` in the viewport meta fixes
// that for Chromium and Firefox on Android, but iOS Safari ignores it. This
// composable covers the rest: it mirrors `window.visualViewport` into two CSS
// variables on <html>,
//
//   --vv-height  visible height in px (what is left above the keyboard)
//   --vv-top     how far the visual viewport is panned down, in px
//
// so a full-screen layer can use `top: var(--vv-top, 0)` and
// `height: var(--vv-height, 100dvh)` and always end at the keyboard edge.
//
// One listener set is shared by every caller and ref-counted: it starts with
// the first active user and is torn down (variables removed, so CSS falls back)
// with the last one. Updates are coalesced to one per animation frame. Where
// `window.visualViewport` does not exist this is a no-op.

/** Visual height this far below the full height counts as "keyboard open". */
export const KEYBOARD_THRESHOLD_PX = 150;

const height = ref<number | null>(null);
const offsetTop = ref(0);
// Largest visual height seen at the current window width. With
// `resizes-content` the layout viewport (window.innerHeight) shrinks together
// with the visual one, so comparing against innerHeight alone would never see
// the keyboard on Android. A width change (rotation) resets the baseline.
const baseline = ref(0);
let baselineWidth = -1;

const keyboardOpen = computed(
  () => height.value !== null && baseline.value - height.value >= KEYBOARD_THRESHOLD_PX,
);

let users = 0;
let frame: number | null = null;
let detach: (() => void) | null = null;

function measure() {
  frame = null;
  const vv = window.visualViewport;
  if (!vv) return;
  if (window.innerWidth !== baselineWidth) {
    baselineWidth = window.innerWidth;
    baseline.value = 0;
  }
  baseline.value = Math.max(baseline.value, window.innerHeight, vv.height);
  height.value = vv.height;
  offsetTop.value = vv.offsetTop;
  const style = document.documentElement.style;
  style.setProperty("--vv-height", `${vv.height}px`);
  style.setProperty("--vv-top", `${vv.offsetTop}px`);
}

function schedule() {
  if (frame !== null) return;
  frame = requestAnimationFrame(measure);
}

function start() {
  const vv = window.visualViewport!;
  vv.addEventListener("resize", schedule);
  vv.addEventListener("scroll", schedule);
  measure();
  detach = () => {
    vv.removeEventListener("resize", schedule);
    vv.removeEventListener("scroll", schedule);
  };
}

function stop() {
  detach?.();
  detach = null;
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  const style = document.documentElement.style;
  style.removeProperty("--vv-height");
  style.removeProperty("--vv-top");
  height.value = null;
  offsetTop.value = 0;
  baseline.value = 0;
  baselineWidth = -1;
}

function acquire() {
  users++;
  if (users === 1) start();
}

function release() {
  users--;
  if (users === 0) stop();
}

/**
 * Track the visual viewport while `active` is true (default: for the lifetime
 * of the calling scope). `height` is null while nothing is tracking.
 */
export function useVisualViewport(active: MaybeRefOrGetter<boolean> = true) {
  const state = {
    height: readonly(height),
    offsetTop: readonly(offsetTop),
    keyboardOpen,
  };
  if (typeof window === "undefined" || !window.visualViewport) return state;

  let holding = false;
  const set = (on: boolean) => {
    if (on === holding) return;
    holding = on;
    if (on) acquire();
    else release();
  };
  // `watch` needs no component, only a scope; outside one the reference is
  // simply held forever.
  watch(() => toValue(active), set, { immediate: true, flush: "sync" });
  if (getCurrentScope()) onScopeDispose(() => set(false));
  return state;
}
