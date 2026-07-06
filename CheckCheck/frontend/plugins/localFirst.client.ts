import type { Pinia } from "pinia";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { registerSnapshotPersistence, hydrateStores } from "@/utils/localSnapshot";

// ── Local-first boot (WI-6) ──────────────────────────────────────────────────
//
// When the `localFirst` flag is on, wire debounced snapshot persistence and
// hydrate the snapshotted stores from IndexedDB BEFORE the board mounts, so the
// first paint comes from cache — network or not.
//
// The background delta pull (cursor advance / full_resync) is kicked off from
// the authed board (pages/index.vue), not here: this plugin runs on every route
// (incl. /login and the anonymous /p/<token> viewer) where an /api/changes call
// would 401. Hydration + persistence are harmless everywhere; the network pull
// is not.
export default defineNuxtPlugin(async (nuxtApp) => {
  if (!isLocalFirstEnabled()) return;
  const pinia = nuxtApp.$pinia as Pinia;
  registerSnapshotPersistence(pinia);
  // Awaited so the stores are populated before the first component mounts.
  await hydrateStores(pinia);
});
