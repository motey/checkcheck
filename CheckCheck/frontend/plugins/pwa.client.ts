import { watch } from "vue";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { isOnline } from "@/utils/connectivity";
import { useOutbox } from "@/composables/useOutbox";

// ── PWA update flow (WI-13 + reactive auto-apply) ────────────────────────────
//
// `registerType: "prompt"` (nuxt.config `pwa`) means a freshly deployed service
// worker installs but *waits* instead of auto-activating. We keep `prompt`
// (rather than vite-pwa's `autoUpdate`) on purpose: `autoUpdate` bakes
// skipWaiting + an *ungated* reload into the generated worker, which would yank
// the app out from under a user mid-edit or reload with unsynced offline writes.
// Staying on `prompt` lets us own *when* the waiting worker activates —
// `@vite-pwa/nuxt` exposes the waiting state on `$pwa.needRefresh` and applies
// it via `$pwa.updateServiceWorker(true)` (postMessage SKIP_WAITING →
// controllerchange → reload, near-instant so no old-code-vs-new-shell window).
//
// The failure this fixes: the previous version only auto-applied inside a narrow
// 15s window right after a "return" event, and otherwise fell back to a toast the
// user had to click. In practice a new worker's install/precache almost always
// finished *after* that window, so every update surfaced as a toast — and a user
// who never clicked it stayed pinned to an old bundle indefinitely (that stale
// bundle then also reports a stale `server_version` in the corner, and runs an
// old copy of the sync protocol against a newer server).
//
// New model: a waiting worker is applied automatically the moment it is *safe*,
// and safety is re-evaluated whenever a blocking condition clears (outbox drains,
// device comes online, typing stops, tab regains focus) — not just once at the
// instant the worker is discovered. A toast remains only as a manual escape hatch
// if conditions stay unsafe for a long stretch.
//
// `$pwa` is only wired in a real build (devOptions.enabled is false), so this is
// a no-op under `nuxt dev` and never fires on the flag-off legacy path.
export default defineNuxtPlugin(() => {
  const pwa = useNuxtApp().$pwa;
  if (!pwa) return;

  const toast = useToast();

  // Reactive outbox depth, captured here (Nuxt context is available at plugin
  // setup) so the safety check can read it without re-entering `useNuxtApp()`
  // outside a Nuxt context. The legacy path has no offline writes, so we don't
  // stand the outbox up there — pending is always 0.
  const pendingCount = isLocalFirstEnabled() ? useOutbox().pendingCount : null;

  // Throttle the probe so rapid focus/online toggles don't spam update().
  const CHECK_THROTTLE_MS = 20_000;
  // Re-probe an always-open tab periodically so a deploy is discovered without a
  // focus/online event (cheap: a conditional GET of sw.js + precache revalidate).
  const PERIODIC_PROBE_MS = 3 * 60_000;
  // If a waiting worker can't be applied safely for this long (e.g. persistent
  // offline / unsynced writes / continuous typing), surface a manual "Reload".
  const STALE_UPDATE_MS = 2 * 60_000;

  let lastCheck = 0;
  let waitingSince = 0; // when the current waiting worker was first seen
  let applied = false; // guards against a double skipWaiting/reload
  let toastShown = false;

  function checkForUpdate() {
    const now = Date.now();
    if (now - lastCheck < CHECK_THROTTLE_MS) return;
    lastCheck = now;
    // getSWRegistration() may be undefined before the worker registers; a later
    // probe retries. Swallow offline/transient update() rejections.
    pwa.getSWRegistration()?.update().catch(() => {});
  }

  function isEditableFocused(): boolean {
    if (typeof document === "undefined") return false;
    const el = document.activeElement as HTMLElement | null;
    if (!el) return false;
    const tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
  }

  // Only reload when it can't disrupt the user or risk local writes:
  //   • online         — the reload must refetch the shell and resume sync;
  //   • tab visible    — never reload a backgrounded tab out of turn (it upgrades
  //                      the moment it regains focus, via the visibility probe);
  //   • not typing     — don't yank a focused field out from under the user;
  //   • outbox drained — queued writes are persisted + idempotent, so a reload
  //                      wouldn't LOSE them, but we still wait so no "pending"
  //                      badge flickers across the reload and no in-flight replay
  //                      is cut mid-request.
  function safeToApply(): boolean {
    if (!isOnline()) return false;
    if (typeof document !== "undefined" && document.visibilityState !== "visible") return false;
    if (isEditableFocused()) return false;
    if ((pendingCount?.value ?? 0) > 0) return false;
    return true;
  }

  function showUpdateToast() {
    if (toastShown) return;
    toastShown = true;
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

  // Apply the waiting worker if it's safe; otherwise keep waiting and, once it has
  // been blocked long enough, offer the manual "Reload" as an escape hatch. Safe
  // to call repeatedly — `applied` makes the activation idempotent.
  function applyIfSafe() {
    if (applied || !pwa.needRefresh) return;
    if (safeToApply()) {
      applied = true;
      void pwa.updateServiceWorker(true); // skipWaiting + reload
      return;
    }
    if (waitingSince && Date.now() - waitingSince >= STALE_UPDATE_MS) showUpdateToast();
  }

  // A worker moved into the waiting state (or one was already waiting at load).
  watch(
    () => pwa.needRefresh,
    (needRefresh) => {
      if (!needRefresh) return;
      if (!waitingSince) waitingSince = Date.now();
      applyIfSafe();
    },
    { immediate: true },
  );

  // Re-evaluate the moment a blocking condition clears: the outbox finishing its
  // drain is the common one (a reload was deferred only because writes were still
  // pending), so a reactive watch closes the gap without waiting for an event.
  if (pendingCount) watch(pendingCount, () => applyIfSafe());

  // Discover new deploys, and retry the apply, at the natural "return" moments
  // plus a gentle periodic probe for tabs that stay open and focused.
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        checkForUpdate();
        applyIfSafe();
      }
    });
    // Typing ended — a deferred update can now apply without stealing a keystroke.
    document.addEventListener("focusout", () => applyIfSafe());
  }
  if (typeof window !== "undefined") {
    window.addEventListener("online", () => {
      checkForUpdate();
      applyIfSafe();
    });
    setInterval(() => {
      checkForUpdate();
      applyIfSafe();
    }, PERIODIC_PROBE_MS);
  }
});
