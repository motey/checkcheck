import { readonly, ref } from "vue";

// ── PWA install prompt (Chromium `beforeinstallprompt`) ──────────────────────
//
// Chromium browsers (Chrome / Edge / Brave / Samsung Internet on Android and
// desktop) fire a `beforeinstallprompt` event when the app is installable but
// not yet installed. We `preventDefault()` it and stash the event so the app can
// surface its OWN "Install app" button and trigger the native prompt on click,
// instead of relying on users hunting through the browser's ⋮ menu.
//
// This does NOT fire on:
//   • iOS/iPadOS — Safari has no beforeinstallprompt; install is the manual
//     Share → "Add to Home Screen" flow (nothing to wire; button stays hidden).
//   • DuckDuckGo / Firefox — no PWA install support; the event never fires.
//   • an already-installed / standalone launch — nothing to prompt.
//
// State is module-level (a single shared signal) and the listeners are attached
// once via `initPwaInstall()` from a client plugin, so the deferred event is
// captured even if it fires before any component using it has mounted.

// Minimal shape of the non-standard BeforeInstallPromptEvent (not in TS lib.dom).
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

// The captured, deferred prompt event (null until the browser offers one, or
// after it has been consumed / the app was installed).
let deferredPrompt: BeforeInstallPromptEvent | null = null;

// True while a real, un-consumed install prompt is available to trigger.
const canInstall = ref(false);
let listenersAttached = false;

// Already running as an installed app? Then there is nothing to install.
function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    // iOS Safari's legacy standalone flag.
    (window.navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

// Attach the window listeners exactly once. Called from a client plugin at app
// startup so we don't miss an early `beforeinstallprompt`.
export function initPwaInstall() {
  if (listenersAttached || typeof window === "undefined") return;
  listenersAttached = true;

  window.addEventListener("beforeinstallprompt", (e) => {
    // Stop Chrome's own mini-infobar; we render our own affordance instead.
    e.preventDefault();
    deferredPrompt = e as BeforeInstallPromptEvent;
    canInstall.value = !isStandalone();
  });

  // Fired after a successful install (from our button OR the browser menu).
  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    canInstall.value = false;
  });
}

export function usePwaInstall() {
  // Trigger the native install dialog. Resolves to true if the user accepted.
  // The deferred event is single-use — the browser will fire a fresh
  // `beforeinstallprompt` if the app becomes installable again later.
  async function promptInstall(): Promise<boolean> {
    if (!deferredPrompt) return false;
    const evt = deferredPrompt;
    deferredPrompt = null;
    canInstall.value = false;
    await evt.prompt();
    const { outcome } = await evt.userChoice;
    return outcome === "accepted";
  }

  return { canInstall: readonly(canInstall), promptInstall };
}
