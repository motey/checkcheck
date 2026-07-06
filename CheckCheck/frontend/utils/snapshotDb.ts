import { openDB, deleteDB, type IDBPDatabase } from "idb";

// ── Local-first snapshot store (WI-6) ────────────────────────────────────────
//
// A thin IndexedDB layer that persists a disposable snapshot of the local-first
// Pinia stores (checklist, checklist_item, label, user, publicConfig) plus the
// sync cursor (`next_cursor` from GET /api/changes, see docs/SYNC_PROTOCOL.md).
//
// The snapshot is DISPOSABLE and carries a schema version: on any version
// mismatch (or an unreadable DB) it is dropped wholesale and boot falls back to
// a fresh since=0 bootstrap (protocol §6). There are deliberately NO client-side
// migrations to maintain — bump SNAPSHOT_SCHEMA_VERSION and the old cache is
// thrown away.

const DB_NAME = "checkcheck-localfirst";
const STORE = "kv";

// Bump this whenever the persisted shape of any snapshotted store (or the
// snapshot envelope itself) changes. It is used as the IndexedDB version, so an
// increment triggers `upgrade`, which drops and recreates the object store —
// the disposable-cache contract. No migrations.
export const SNAPSHOT_SCHEMA_VERSION = 1;

// Reserved keys inside the single kv object store. Store snapshots live under
// their Pinia `$id`; these two are metadata.
const CURSOR_KEY = "__cursor__";

function upgrade(db: IDBPDatabase) {
  // Any version change (first create or a schema bump) resets the store — the
  // snapshot is never migrated, only discarded and rebuilt from the server.
  if (db.objectStoreNames.contains(STORE)) db.deleteObjectStore(STORE);
  db.createObjectStore(STORE);
}

let dbPromise: Promise<IDBPDatabase> | null = null;

function open(): Promise<IDBPDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = (async () => {
    try {
      return await openDB(DB_NAME, SNAPSHOT_SCHEMA_VERSION, { upgrade });
    } catch (err) {
      // A DB left on disk at a HIGHER version (app was downgraded) makes openDB
      // throw a VersionError. The cache is disposable — nuke it and recreate at
      // the current version.
      console.warn("[localFirst] snapshot DB open failed — resetting cache", err);
      await deleteDB(DB_NAME).catch(() => {});
      return openDB(DB_NAME, SNAPSHOT_SCHEMA_VERSION, { upgrade });
    }
  })();
  return dbPromise;
}

function available(): boolean {
  return typeof indexedDB !== "undefined";
}

/** Read one store's persisted snapshot (or undefined if absent). */
export async function readSnapshot<T = unknown>(storeId: string): Promise<T | undefined> {
  if (!available()) return undefined;
  try {
    const db = await open();
    return (await db.get(STORE, storeId)) as T | undefined;
  } catch (err) {
    console.warn(`[localFirst] failed to read snapshot for "${storeId}"`, err);
    return undefined;
  }
}

/** Persist one or more store snapshots in a single transaction. */
export async function writeSnapshots(entries: Record<string, unknown>): Promise<void> {
  if (!available()) return;
  try {
    const db = await open();
    const tx = db.transaction(STORE, "readwrite");
    await Promise.all([
      ...Object.entries(entries).map(([key, value]) => tx.store.put(value, key)),
      tx.done,
    ]);
  } catch (err) {
    console.warn("[localFirst] failed to write snapshots", err);
  }
}

/** The device's stored sync cursor (0 for a fresh device). */
export async function readCursor(): Promise<number> {
  if (!available()) return 0;
  try {
    const db = await open();
    const val = (await db.get(STORE, CURSOR_KEY)) as number | undefined;
    return typeof val === "number" ? val : 0;
  } catch (err) {
    console.warn("[localFirst] failed to read cursor", err);
    return 0;
  }
}

/** Persist the sync cursor (`next_cursor` from GET /api/changes). */
export async function writeCursor(cursor: number): Promise<void> {
  await writeSnapshots({ [CURSOR_KEY]: cursor });
}

/**
 * Drop the entire snapshot (all store state AND the cursor). Used on
 * `full_resync` (protocol §5) — the cache is disposable and rebuilt from the
 * server's full state.
 */
export async function dropSnapshot(): Promise<void> {
  if (!available()) return;
  try {
    const db = await open();
    await db.clear(STORE);
  } catch (err) {
    console.warn("[localFirst] failed to drop snapshot", err);
  }
}
