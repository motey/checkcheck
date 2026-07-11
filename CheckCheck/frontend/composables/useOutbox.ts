import { createSharedComposable } from "@vueuse/core";
import { ref } from "vue";
import {
  OutboxEngine,
  outboxFieldGuard,
  pendingChecklistIds,
  queuedCreateIds,
  type OutboxEntityType,
  type OutboxEvent,
  type OutboxOp,
  type OutboxOpInput,
} from "@/utils/outbox";
import type { EditGuard } from "@/utils/editGuard";
import { createOutboxStore } from "@/utils/outboxDb";
import { emitSyncNotice } from "@/utils/syncNotices";
import {
  initConnectivity,
  isOnline,
  onConnectivityChange,
} from "@/utils/connectivity";

// ── Outbox composable (WI-7) ─────────────────────────────────────────────────
//
// The single, app-wide outbox instance. Wires the framework-free engine
// (utils/outbox.ts) to its real dependencies: the IndexedDB store, the generated
// `$checkapi` transport, and the connectivity signal. `createSharedComposable`
// keeps it a singleton across every call site.
//
// SCOPE (WI-7): this is standalone infrastructure. The item / checklist / label
// stores do NOT enqueue to it yet — that is WI-8 (items) and WI-9
// (positions/checklists), which will call `enqueue(...)` from their optimistic
// actions. Surfacing terminal `op-dropped` events in the UI is WI-11. Here we
// only stand the queue up, load any persisted ops, and start draining, so those
// items can drop in.

/**
 * Turn a stored op into the generated `$checkapi` call it describes and send it.
 * Throws an ofetch `FetchError` on non-2xx, which the engine classifies
 * (utils/outbox.ts `classifyError`). `skipErrorToast` — the outbox owns its own
 * error handling (retry / terminal-drop → WI-11); it must not stack the global
 * "Error <code>" toast (plugins/api.ts).
 */
async function sendOp($checkapi: any, op: OutboxOp): Promise<void> {
  const { method, path, pathParams, query, body } = op.request;
  await $checkapi(path, {
    method,
    ...(pathParams ? { path: pathParams } : {}),
    ...(query ? { query } : {}),
    ...(body !== undefined ? { body } : {}),
    skipErrorToast: true,
  });
}

export const useOutbox = createSharedComposable(() => {
  const { $checkapi } = useNuxtApp();

  const pendingCount = ref(0);
  // Reactive set of checklist ids with an undrained op — the per-card "not yet
  // synced" indicator (WI-11) reads this; recomputed whenever the queue changes.
  const pendingCardIds = ref<Set<string>>(new Set());
  const online = ref(isOnline());
  const listeners = new Set<(e: OutboxEvent) => void>();

  const engine = new OutboxEngine({
    // A durable-storage failure (finding #9) surfaces as a one-time toast via the
    // sync-notice consumer (composables/useSyncNotices) — the same place drops and
    // conflicts land, so the user learns their offline writes may not survive a reload.
    store: createOutboxStore({ onStorageError: () => emitSyncNotice({ type: "storage-failed" }) }),
    transport: (op) => sendOp($checkapi, op),
    isOnline,
    onChange: (pending) => {
      pendingCount.value = pending;
      pendingCardIds.value = pendingChecklistIds(engine.queue);
    },
    emit: (event) => {
      for (const l of listeners) {
        try {
          l(event);
        } catch (err) {
          console.warn("[outbox] event listener threw", err);
        }
      }
    },
  });

  // Connectivity → engine. `initConnectivity` also lets the SSE `onopen` signal
  // (composables/useSync.ts) feed reachability in via `setConnectivity`.
  initConnectivity();
  onConnectivityChange((isUp) => {
    online.value = isUp;
    engine.setOnline(isUp);
  });

  // Load persisted ops and start draining (best-effort; no-op offline).
  void engine.init();

  return {
    /** Queue a write (WI-8/WI-9 stores call this from their optimistic actions). */
    enqueue: (input: OutboxOpInput): Promise<OutboxOp> => engine.enqueue(input),
    /** Reactive count of queued (unsynced) ops — feeds the WI-14 status UI. */
    pendingCount,
    /** Reactive set of checklist ids with a pending op — the per-card WI-11 indicator. */
    pendingCardIds,
    /** Reactive connectivity for the UI. */
    online,
    /** Force an immediate drain of queued writes — the WI-14 manual "Sync now". */
    drainNow: (): void => engine.kickDrain(),
    /**
     * Entity ids with a queued, not-yet-drained `create` op. The delta pull
     * (utils/localSnapshot) excludes these from `known=` so the server doesn't
     * report an offline-created card as revoked before its create replays.
     */
    queuedCreateIds: (entityType: OutboxEntityType): Set<string> =>
      queuedCreateIds(engine.queue, entityType),
    /**
     * An `EditGuard` over the current queue — protects fields with an undrained
     * op so a delta for a *different* field of the same row doesn't revert them
     * (WI-11 finding #2). Composed with the focus registry in the delta pull.
     */
    fieldGuard: (): EditGuard => outboxFieldGuard(engine.queue),
    /**
     * Reconcile the queue against a `full_resync` (server reset): drop ops the
     * reset server can no longer accept and return them so the caller surfaces a
     * single notice (WI-11 finding #5).
     */
    reconcileResync: (knownIds: ReadonlySet<string>): Promise<OutboxOp[]> =>
      engine.reconcileResync(knownIds),
    /** Subscribe to outbox events (`op-dropped` / `idle`); returns unsubscribe. */
    onEvent(listener: (e: OutboxEvent) => void): () => void {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
});
