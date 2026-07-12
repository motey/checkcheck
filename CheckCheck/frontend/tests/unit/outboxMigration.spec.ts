import { describe, it, expect } from "vitest";
import { openDB } from "idb";
import { createOutboxStore } from "@/utils/outboxDb";
import type { OutboxOp } from "@/utils/outbox";

// ── Outbox schema v1 → v2 migration (Chunk A2) ───────────────────────────────
//
// The outbox is PRECIOUS: a schema bump must NOT drop queued writes. v2 re-keys
// the `ops` store from the per-tab `seq` to the unique `opId` (multi-tab-safe
// per-op persistence). This spec seeds a real v1 database (old keyPath), then
// opens the store through the app code and asserts every queued op survived the
// re-key — the whole reason the migration exists rather than a disposable drop.
//
// A separate spec file so it gets its own fresh `fake-indexeddb` + module state
// (the module-level DB handle is opened once per file at the current version).

const DB_NAME = "checkcheck-outbox";
const STORE = "ops";

function makeOp(seq: number, entityId: string): OutboxOp {
  return {
    seq,
    opId: `op-${seq}`,
    entityType: "item",
    entityId,
    kind: "create",
    request: {
      method: "post",
      path: "/api/checklist/{checklist_id}/item",
      pathParams: { checklist_id: "cl1" },
      body: { id: entityId },
    },
    enqueuedAt: seq,
    attempts: 0,
  };
}

describe("outbox v1 → v2 migration", () => {
  it("re-keys queued ops from seq to opId without losing any", async () => {
    // Seed a v1 DB exactly as the old code wrote it: store keyed by `seq`.
    const v1 = await openDB(DB_NAME, 1, {
      upgrade(db) {
        db.createObjectStore(STORE, { keyPath: "seq" });
      },
    });
    await v1.put(STORE, makeOp(1, "i1"));
    await v1.put(STORE, makeOp(2, "i2"));
    v1.close();

    // Open through the app store → triggers the v2 upgrade (seq → opId).
    const store = createOutboxStore();
    const loaded = await store.load();
    expect(loaded.map((o) => o.entityId).sort()).toEqual(["i1", "i2"]);

    // And the queue is now genuinely keyed by opId: a per-op remove works.
    await store.remove("op-1");
    const after = await store.load();
    expect(after.map((o) => o.entityId)).toEqual(["i2"]);
  });
});
