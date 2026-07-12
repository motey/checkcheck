// ── Sync activity signal (WI-14) ─────────────────────────────────────────────
//
// The framework-light state behind the global sync-status indicator: whether a
// delta pull is currently in flight, and when the board last reached server
// truth. Plain module + listener set (connectivity.ts / syncNotices.ts style) so
// the framework-free delta path (utils/localSnapshot) can report activity without
// importing Vue; the UI subscribes through `composables/useSyncStatus.ts`.
//
// `lastSyncedAt` is persisted to localStorage so "last synced 3 min ago" survives
// a reload (the delta cursor already persists; this is just its human-facing
// timestamp). Everything is best-effort — a storage failure degrades to
// in-memory-only, never throws.

type Listener = () => void;

const LS_KEY = "checkcheck:lastSyncedAt";

const listeners = new Set<Listener>();
let syncing = false;
let lastSyncedAt: number | null = loadLastSynced();

function loadLastSynced(): number | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

function persistLastSynced(at: number): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(LS_KEY, String(at));
  } catch {
    // Private mode / quota — the in-memory value still drives the UI this session.
  }
}

function notify(): void {
  for (const l of listeners) {
    try {
      l();
    } catch (err) {
      console.warn("[syncStatus] listener threw", err);
    }
  }
}

/** A delta pull started — flip the "syncing" spinner on. */
export function beginSync(): void {
  if (syncing) return;
  syncing = true;
  notify();
}

/**
 * A delta pull finished. `ok` = it actually reached the server and converged
 * (vs. an offline / error attempt); only then does the last-synced clock advance.
 */
export function endSync(ok: boolean): void {
  const wasSyncing = syncing;
  syncing = false;
  if (ok) {
    lastSyncedAt = Date.now();
    persistLastSynced(lastSyncedAt);
  }
  if (wasSyncing || ok) notify();
}

/** Current sync activity snapshot for the UI (and tests). */
export function getSyncStatus(): { syncing: boolean; lastSyncedAt: number | null } {
  return { syncing, lastSyncedAt };
}

/** Subscribe to sync-status changes; returns an unsubscribe fn. */
export function onSyncStatusChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test-only reset of module state. */
export function __resetSyncStatusForTests(): void {
  syncing = false;
  lastSyncedAt = null;
  listeners.clear();
}
