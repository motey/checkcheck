import { openDB, deleteDB, type IDBPDatabase } from "idb";
import type { OutboxOp, OutboxStore } from "@/utils/outbox";

// ── Outbox persistence (WI-7) ────────────────────────────────────────────────
//
// The outbox lives in its OWN IndexedDB database, deliberately separate from the
// WI-6 snapshot DB (utils/snapshotDb.ts, `checkcheck-localfirst`).
//
// WHY A SEPARATE DB — the disposability tension:
//   The snapshot is DISPOSABLE: its `upgrade` hook drops every object store on a
//   SNAPSHOT_SCHEMA_VERSION bump (no client migrations — protocol §6). Queued
//   offline writes are the OPPOSITE: they are precious and must survive until
//   they replay. Putting the outbox in the snapshot DB would mean a snapshot
//   shape change silently discards a user's unsynced writes. So the outbox gets
//   its own DB, versioned independently by OUTBOX_SCHEMA_VERSION — a snapshot
//   bump can never touch it, and vice-versa.
//
// One object store, `ops`, keyed by the op's monotonic `seq`. The engine treats
// the store as a whole-queue read/rewrite (utils/outbox.ts `OutboxStore`): the
// queue is small, so `persist` clears and rewrites it in one transaction rather
// than maintaining per-op diffs.

const DB_NAME = "checkcheck-outbox";
const STORE = "ops";

// Bump ONLY when the persisted `OutboxOp` shape changes in a way the engine
// can't read. Unlike the snapshot version, a bump here MUST NOT silently drop
// queued writes — if a real migration is ever needed, handle it in `upgrade`.
// (There are none yet; v1 is the first shape.)
export const OUTBOX_SCHEMA_VERSION = 1;

function upgrade(db: IDBPDatabase) {
  if (!db.objectStoreNames.contains(STORE)) {
    db.createObjectStore(STORE, { keyPath: "seq" });
  }
}

let dbPromise: Promise<IDBPDatabase> | null = null;

function open(): Promise<IDBPDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = (async () => {
    try {
      return await openDB(DB_NAME, OUTBOX_SCHEMA_VERSION, { upgrade });
    } catch (err) {
      // A DB left at a HIGHER version (app downgrade) throws VersionError. Unlike
      // the disposable snapshot we would rather not lose queued writes, but a DB
      // we cannot open is unusable — reset it so the app keeps working. Log loudly
      // so this is never a silent data loss in the field.
      console.error("[outbox] DB open failed — resetting outbox (queued writes lost)", err);
      await deleteDB(DB_NAME).catch(() => {});
      return openDB(DB_NAME, OUTBOX_SCHEMA_VERSION, { upgrade });
    }
  })();
  return dbPromise;
}

function available(): boolean {
  return typeof indexedDB !== "undefined";
}

/**
 * Ask the browser to mark our origin's storage as persistent so the queued
 * offline writes aren't first in line for eviction under storage pressure
 * (WI-14 / review finding #9). Best-effort: unsupported or denied is fine — the
 * outbox already degrades gracefully and surfaces a persist failure if one hits.
 * Idempotent-safe to call at every boot; the browser caches the grant.
 */
export async function requestPersistentStorage(): Promise<void> {
  try {
    const storage = (navigator as any)?.storage;
    if (!storage?.persist || !storage.persisted) return;
    if (await storage.persisted()) return;
    await storage.persist();
  } catch {
    // No storage manager / blocked — ignore.
  }
}

/**
 * The real `OutboxStore` backing the engine. Best-effort: if IndexedDB is
 * unavailable (SSR / blocked) the outbox degrades to an in-memory session queue
 * rather than throwing, so writes still work until reload.
 *
 * `onStorageError` (WI-14 / review finding #9) fires when a durability guarantee
 * is broken — a persist write failed (quota / private-mode eviction), or the DB
 * had to be reset on open (queued writes lost). The queue still drains in memory
 * this session, but a reload before it drains would lose those writes, so the
 * caller surfaces this to the user instead of leaving it a console line.
 */
export function createOutboxStore(opts?: { onStorageError?: () => void }): OutboxStore {
  const reportStorageError = () => {
    try {
      opts?.onStorageError?.();
    } catch (err) {
      console.warn("[outbox] onStorageError handler threw", err);
    }
  };
  return {
    async load(): Promise<OutboxOp[]> {
      if (!available()) return [];
      try {
        const db = await open();
        const ops = (await db.getAll(STORE)) as OutboxOp[];
        return ops.sort((a, b) => a.seq - b.seq);
      } catch (err) {
        console.error("[outbox] failed to load queued ops", err);
        return [];
      }
    },

    async persist(ops: OutboxOp[]): Promise<void> {
      if (!available()) return;
      try {
        const db = await open();
        const tx = db.transaction(STORE, "readwrite");
        await tx.store.clear();
        await Promise.all([...ops.map((op) => tx.store.put(op)), tx.done]);
      } catch (err) {
        // Persistence failed — the in-memory queue still drains this session, but
        // a reload before it drains would lose these ops. Surface loudly, and tell
        // the UI (WI-14 finding #9) so the user knows to stay online.
        console.error("[outbox] failed to persist queued ops", err);
        reportStorageError();
      }
    },
  };
}
