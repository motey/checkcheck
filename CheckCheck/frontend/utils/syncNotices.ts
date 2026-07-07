// ── Sync notices (WI-11) ─────────────────────────────────────────────────────
//
// The user-facing signals the offline-sync machinery raises: a concurrent edit
// collided with a local one, a queued write was terminally dropped (access
// revoked / row deleted while offline), or a server reset discarded pending
// changes. Framework-light (a plain listener set, connectivity.ts-style) so the
// framework-free delta core (utils/deltaApply via localSnapshot) and the outbox
// engine can both raise notices without importing Vue; the UI subscribes through
// `composables/useSyncNotices.ts` and renders them as toasts.
//
// These are *informational*: no data is lost when they fire (LWW converges for
// conflicts; a terminal drop is the correct outcome for a revoked/deleted row).
// The point is to explain a visible change rather than let it look like a glitch.

import type { EditGuardField } from "@/utils/editGuard";

export type SyncNotice =
  /**
   * A field the local user was protecting (focused edit or queued op) was also
   * changed on the server. The local value is kept; this just surfaces the
   * concurrent edit so the change isn't mistaken for a lost write.
   */
  | { type: "conflict"; entity: "checklist" | "item"; id: string; field: EditGuardField }
  /**
   * A queued write was terminally dropped on replay (WI-7 `op-dropped`): the
   * target list/item is gone or no longer writable (access revoked, deleted).
   * `checklistId` names the affected card so the UI can message it once.
   */
  | { type: "dropped"; entity: "checklist" | "item" | "label"; checklistId?: string; status: number | undefined }
  /** A `full_resync` (server reset/restore) discarded `count` queued writes it no longer knew. */
  | { type: "resync-dropped"; count: number };

type Listener = (notice: SyncNotice) => void;

const listeners = new Set<Listener>();

/** Raise a notice for the UI. Best-effort; a throwing listener never blocks others. */
export function emitSyncNotice(notice: SyncNotice): void {
  for (const l of listeners) {
    try {
      l(notice);
    } catch (err) {
      console.warn("[syncNotices] listener threw", err);
    }
  }
}

/** Subscribe to sync notices; returns an unsubscribe fn. */
export function onSyncNotice(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
