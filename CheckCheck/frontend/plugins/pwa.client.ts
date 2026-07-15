import { watch } from "vue";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { isOnline } from "@/utils/connectivity";
import { useOutbox } from "@/composables/useOutbox";

// ── PWA update flow (WI-13 + proactive refresh) ──────────────────────────────
//
// `registerType: "prompt"` (nuxt.config `pwa`) means a freshly deployed service
// worker waits instead of auto-activating, so a user mid-edit is never reloaded
// out from under themselves. `@vite-pwa/nuxt` exposes that waiting state on
// `$pwa.needRefresh`.
//
// Two behaviours here:
//
//   1. Proactive probe — the browser only re-checks the service worker on its
//      own schedule (up to ~24h for an installed PWA), so a freshly deployed
//      build can sit undiscovered for a long time. We nudge `registration.update()`
//      at the two moments the user naturally "returns": the tab regaining focus
//      (`visibilitychange`) and the network coming back (`online`) — exactly the
//      events worth catching an update on.
//
//   2. Safe apply — when a new worker is found *within* one of those return
//      windows and it's safe (online, tab visible, not typing, outbox drained),
//      activate + reload silently. Otherwise fall back to a single,
//      non-auto-dismissing toast with a "Reload" action, so a build discovered
//      mid-session (or with unsynced writes) is never applied out from under the
//      user — they decide.
//
// `$pwa` is only wired in a real build (devOptions.enabled is false), so this is
// a no-op under `nuxt dev` and never fires on the flag-off legacy path.
export default defineNuxtPlugin(() => {
  const pwa = useNuxtApp().$pwa;
  if (!pwa) return;

  const toast = useToast();

  // Reactive outbox depth, captured here (Nuxt context is available at plugin
  // setup) so the event-handler safety check can read it without re-entering
  // `useNuxtApp()` outside a Nuxt context. The legacy path has no offline writes,
  // so we don't stand the outbox up there — pending is always 0.
  const pendingCount = isLocalFirstEnabled() ? useOutbox().pendingCount : null;

  // Throttle the probe so rapid focus/online toggles don't spam update().
  const CHECK_THROTTLE_MS = 20_000;
  // Auto-apply is armed only briefly after a return moment; an update discovered
  // outside this window falls back to the toast rather than reloading mid-session.
  const ARM_WINDOW_MS = 15_000;
  let lastCheck = 0;
  let armedUntil = 0;

  function checkForUpdate() {
    const now = Date.now();
    if (now - lastCheck < CHECK_THROTTLE_MS) return;
    lastCheck = now;
    armedUntil = now + ARM_WINDOW_MS;
    // getSWRegistration() may be undefined before the worker registers; the next
    // return moment retries. Swallow offline/transient update() rejections.
    pwa.getSWRegistration()?.update().catch(() => {});
  }

  function isEditableFocused(): boolean {
    if (typeof document === "undefined") return false;
    const el = document.activeElement as HTMLElement | null;
    if (!el) return false;
    const tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
  }

  // Only reload silently when it can't disrupt the user or risk local writes:
  //   • online         — the reload must refetch the shell and resume sync;
  //   • tab visible    — never reload a backgrounded tab;
  //   • not typing     — don't yank a focused field out from under the user;
  //   • outbox drained — queued writes are persisted + idempotent, so a reload
  //                      wouldn't LOSE them, but we still wait so no "pending"
  //                      badge flickers across the reload and no in-flight replay
  //                      is cut mid-request.
  function safeToAutoReload(): boolean {
    if (!isOnline()) return false;
    if (typeof document !== "undefined" && document.visibilityState !== "visible") return false;
    if (isEditableFocused()) return false;
    if ((pendingCount?.value ?? 0) > 0) return false;
    return true;
  }

  function showUpdateToast() {
    toast.add({
      title: "New version available",
      description: "Reload to get the latest CheckCheck.",
      icon: "i-lucide-download",
      color: "primary",
      duration: 0, // stay until the user acts
      actions: [
        {
          label: "Reload",
          onClick: () => pwa.updateServiceWorker(true),
        },
      ],
    });
  }

  watch(
    () => pwa.needRefresh,
    (needRefresh) => {
      if (!needRefresh) return;
      // `immediate` fires with armedUntil === 0, so an update already waiting at
      // load surfaces as a toast (never a surprise reload on first paint).
      if (Date.now() < armedUntil && safeToAutoReload()) {
        void pwa.updateServiceWorker(true);
        return;
      }
      showUpdateToast();
    },
    { immediate: true },
  );

  // Probe for a new build on the two "return" events.
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") checkForUpdate();
    });
  }
  if (typeof window !== "undefined") {
    window.addEventListener("online", checkForUpdate);
  }
});
