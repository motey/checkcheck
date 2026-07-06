// ── localFirst feature flag (WI-6) ───────────────────────────────────────────
//
// The CLIENT-SIDE rollout gate for all of Phase 2 (local persistence, outbox,
// optimistic stores, delta application). This is distinct from the SERVER
// capability flags in stores/publicConfig.ts (those come from GET
// /api/public-config and switch sharing features on/off); `localFirst` is a
// build/deploy-time client rollout gate that WI-15 flips on by default.
//
// Resolution order (first hit wins):
//   1. `?localFirst=1` / `?localFirst=0` query param — flips the flag AND
//      persists the choice to localStorage, so it survives reloads. This is the
//      hook the E2E suite and manual testing use, since the E2E bundle is a
//      static `nuxt generate` build where runtimeConfig can't be re-set per run.
//   2. The persisted localStorage override from a previous (1).
//   3. `runtimeConfig.public.localFirst` — the deploy default (env
//      NUXT_PUBLIC_LOCAL_FIRST). Defaults to false until WI-15.

const LS_KEY = "checkcheck:localFirst";

export function isLocalFirstEnabled(): boolean {
  // Server has no IndexedDB/localStorage; the whole layer is client-only.
  if (import.meta.server || typeof window === "undefined") return false;

  try {
    const params = new URLSearchParams(window.location.search);
    if (params.has("localFirst")) {
      const raw = params.get("localFirst");
      const on = raw !== "0" && raw !== "false";
      window.localStorage.setItem(LS_KEY, on ? "1" : "0");
      return on;
    }
    const stored = window.localStorage.getItem(LS_KEY);
    if (stored !== null) return stored === "1";
  } catch {
    // localStorage can be unavailable (private mode / blocked) — fall through
    // to the deploy default.
  }

  return Boolean(useRuntimeConfig().public.localFirst);
}
