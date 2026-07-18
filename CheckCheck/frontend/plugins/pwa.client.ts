// ── PWA update probe (autoUpdate mode) ───────────────────────────────────────
//
// `registerType: "autoUpdate"` (nuxt.config `pwa`) does the actual work: a newly
// deployed service worker activates itself (workbox skipWaiting + clientsClaim)
// and vite-plugin-pwa's register script reloads the page on the `activated`
// update event. So there is nothing here to *apply* — the reload is automatic
// and does not depend on app code (the old "prompt" flow that relied on a toast
// never fired in prod, which is why clients got stuck; see docs/ISSUES.md).
//
// The browser only re-checks the service worker on navigation and its own ~24h
// schedule, so a long-open tab could sit on an old build until it happens to
// navigate. This plugin closes that gap: it nudges `registration.update()` at the
// natural "return" moments — tab regaining focus and the network coming back —
// plus a gentle periodic check. Discovering a new worker is all that's needed;
// autoUpdate + the register script take it from there (install → activate →
// reload).
//
// `$pwa` is only wired in a real build (devOptions.enabled is false), so this is
// a no-op under `nuxt dev`.
export default defineNuxtPlugin(() => {
  const pwa = useNuxtApp().$pwa;
  if (!pwa) return;

  // Throttle so rapid focus/online toggles don't spam update().
  const CHECK_THROTTLE_MS = 20_000;
  // Re-check a long-open, focused tab occasionally so a deploy is picked up
  // without a navigation. Cheap: a conditional GET of sw.js.
  const PERIODIC_PROBE_MS = 15 * 60_000;
  let lastCheck = 0;

  function checkForUpdate() {
    const now = Date.now();
    if (now - lastCheck < CHECK_THROTTLE_MS) return;
    lastCheck = now;
    // getSWRegistration() may be undefined before the worker registers; a later
    // probe retries. Swallow offline/transient update() rejections.
    pwa.getSWRegistration()?.update().catch(() => {});
  }

  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") checkForUpdate();
    });
  }
  if (typeof window !== "undefined") {
    window.addEventListener("online", checkForUpdate);
    setInterval(checkForUpdate, PERIODIC_PROBE_MS);
  }
});
