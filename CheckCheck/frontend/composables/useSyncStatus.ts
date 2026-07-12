import type { Pinia } from "pinia";
import { createSharedComposable } from "@vueuse/core";
import { onScopeDispose, ref } from "vue";
import { useOutbox } from "@/composables/useOutbox";
import { getSyncStatus, onSyncStatusChange } from "@/utils/syncStatus";
import { probe } from "@/utils/connectivity";
import { applyDelta } from "@/utils/localSnapshot";

// ── Global sync status (WI-14) ───────────────────────────────────────────────
//
// The one reactive rollup behind the navbar sync indicator. It composes existing
// primitives rather than re-deriving state:
//
//   • online / pendingCount  → useOutbox() (the outbox owns connectivity + queue)
//   • syncing / lastSyncedAt  → utils/syncStatus (fed by the delta pull)
//   • syncNow()               → probe reachability, then drain the outbox and pull
//     a fresh delta — the manual "Sync now".
//
// Flag-on only: the outbox / delta layer only exists under `localFirst`, so the
// indicator component gates on the flag before touching this. A singleton via
// `createSharedComposable`.
export const useSyncStatus = createSharedComposable(() => {
  const pinia = useNuxtApp().$pinia as Pinia;
  const outbox = useOutbox();

  const status = getSyncStatus();
  const syncing = ref(status.syncing);
  const lastSyncedAt = ref<number | null>(status.lastSyncedAt);
  const stop = onSyncStatusChange(() => {
    const s = getSyncStatus();
    syncing.value = s.syncing;
    lastSyncedAt.value = s.lastSyncedAt;
  });
  onScopeDispose(stop);

  /**
   * Manual sync: confirm reachability, then kick the outbox drain and pull a
   * fresh delta. Best-effort and idempotent — `applyDelta` serialises, so a
   * double-tap just chains. No-op effect offline (probe reports offline; the
   * drain and pull can't reach the server).
   */
  async function syncNow(): Promise<void> {
    await probe();
    outbox.drainNow();
    await applyDelta(pinia);
  }

  return {
    /** Reactive connectivity. */
    online: outbox.online,
    /** Reactive count of queued (unsynced) writes. */
    pendingCount: outbox.pendingCount,
    /** True while a delta pull is in flight. */
    syncing,
    /** Epoch ms of the last server-reaching pull, or null if never. */
    lastSyncedAt,
    syncNow,
  };
});
