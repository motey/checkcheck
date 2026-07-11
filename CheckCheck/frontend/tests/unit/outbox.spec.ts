import { describe, it, expect, vi } from "vitest";
import {
  OutboxEngine,
  backoffMs,
  classifyError,
  coalesce,
  httpStatusOf,
  outboxFieldGuard,
  partitionResync,
  pendingChecklistIds,
  queuedCreateIds,
  type OutboxEvent,
  type OutboxOp,
  type OutboxOpInput,
  type OutboxStore,
} from "@/utils/outbox";
import { createOutboxStore } from "@/utils/outboxDb";

// ── Helpers ──────────────────────────────────────────────────────────────────

const settle = async (n = 12) => {
  for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
};

/** In-memory OutboxStore for engine-logic tests (no IndexedDB). Per-op like the
 *  real store: keyed by `opId`, so concurrent engines don't clobber each other. */
function memStore(initial: OutboxOp[] = []) {
  const map = new Map<string, OutboxOp>(initial.map((o) => [o.opId, o]));
  const store: OutboxStore = {
    async load() {
      return [...map.values()].sort((a, b) => a.seq - b.seq);
    },
    async put(op) {
      map.set(op.opId, op);
    },
    async remove(opId) {
      map.delete(opId);
    },
    async clear() {
      map.clear();
    },
  };
  // `state.ops` kept as a live view for the existing assertions.
  return {
    store,
    state: {
      get ops() {
        return [...map.values()].sort((a, b) => a.seq - b.seq);
      },
    },
  };
}

/** A backoff scheduler we can flush by hand — deterministic retry timing. */
function manualScheduler() {
  const tasks: { fn: () => void }[] = [];
  return {
    scheduler(fn: () => void, _delayMs: number) {
      const task = { fn };
      tasks.push(task);
      return () => {
        const i = tasks.indexOf(task);
        if (i >= 0) tasks.splice(i, 1);
      };
    },
    flush() {
      tasks.splice(0).forEach((t) => t.fn());
    },
    get size() {
      return tasks.length;
    },
  };
}

const httpError = (status: number) => Object.assign(new Error(`HTTP ${status}`), { status });
const networkError = () => new Error("network down");

// Op input builders (the shape WI-8/WI-9 stores will produce).
function itemCreate(id: string, clId = "cl1"): OutboxOpInput {
  return {
    entityType: "item",
    entityId: id,
    kind: "create",
    request: {
      method: "post",
      path: "/api/checklist/{checklist_id}/item",
      pathParams: { checklist_id: clId },
      body: { id, text: "" },
    },
  };
}
function itemUpdate(id: string, body: Record<string, unknown>, clId = "cl1"): OutboxOpInput {
  return {
    entityType: "item",
    entityId: id,
    kind: "update",
    request: {
      method: "patch",
      path: "/api/checklist/{checklist_id}/item/{checklist_item_id}",
      pathParams: { checklist_id: clId, checklist_item_id: id },
      body,
    },
  };
}
function itemDelete(id: string, clId = "cl1"): OutboxOpInput {
  return {
    entityType: "item",
    entityId: id,
    kind: "delete",
    request: {
      method: "delete",
      path: "/api/checklist/{checklist_id}/item/{checklist_item_id}",
      pathParams: { checklist_id: clId, checklist_item_id: id },
    },
  };
}

function checklistCreate(id: string): OutboxOpInput {
  return {
    entityType: "checklist",
    entityId: id,
    kind: "create",
    request: { method: "post", path: "/api/checklist", body: { id } },
  };
}
function checklistDelete(id: string): OutboxOpInput {
  return {
    entityType: "checklist",
    entityId: id,
    kind: "delete",
    request: {
      method: "delete",
      path: "/api/checklist/{checklist_id}",
      pathParams: { checklist_id: id },
    },
  };
}
/** A checklist⇄label association op (WI-9): entity id is the "{clId}:{labelId}" pair. */
function labelAttach(clId: string, labelId: string): OutboxOpInput {
  return {
    entityType: "label",
    entityId: `${clId}:${labelId}`,
    kind: "create",
    request: {
      method: "put",
      path: "/api/checklist/{checklist_id}/label/{label_id}",
      pathParams: { checklist_id: clId, label_id: labelId },
    },
  };
}

/** Materialise an OutboxOp (with seq) from an input — for pure coalesce tests. */
function op(seq: number, input: OutboxOpInput): OutboxOp {
  return { ...input, seq, opId: `op${seq}`, enqueuedAt: seq, attempts: 0 };
}

// ── queuedCreateIds (the `known=` filter for the delta pull) ─────────────────

describe("queuedCreateIds", () => {
  it("returns only ids with a queued create of the requested entity type", () => {
    const queue = [
      op(1, itemCreate("i1")),
      op(2, itemUpdate("i1", { text: "t" })),
      op(3, {
        entityType: "checklist",
        entityId: "cl-new",
        kind: "create",
        request: { method: "post", path: "/api/checklist", body: { id: "cl-new" } },
      }),
      op(4, {
        entityType: "checklist",
        entityId: "cl-old",
        kind: "update",
        request: {
          method: "patch",
          path: "/api/checklist/{checklist_id}",
          pathParams: { checklist_id: "cl-old" },
          body: { name: "n" },
        },
      }),
    ];
    // The delta pull excludes these from `known=` so the server can't report an
    // offline-created card as revoked before its create drains.
    expect(queuedCreateIds(queue, "checklist")).toEqual(new Set(["cl-new"]));
    expect(queuedCreateIds(queue, "item")).toEqual(new Set(["i1"]));
  });
});

// ── Error classification (protocol §8) ───────────────────────────────────────

describe("classifyError", () => {
  it("treats the named terminal statuses as terminal", () => {
    for (const s of [400, 403, 404, 409, 410, 422]) {
      expect(classifyError(s)).toBe("terminal");
    }
  });
  it("treats network (no status) and 5xx as retryable", () => {
    expect(classifyError(undefined)).toBe("retryable");
    for (const s of [500, 502, 503, 504]) expect(classifyError(s)).toBe("retryable");
  });
  it("keeps auth / timeout / rate-limit retryable (documented defaults)", () => {
    for (const s of [401, 408, 429]) expect(classifyError(s)).toBe("retryable");
  });
});

describe("httpStatusOf", () => {
  it("reads status from ofetch-style errors", () => {
    expect(httpStatusOf(Object.assign(new Error(), { status: 409 }))).toBe(409);
    expect(httpStatusOf(Object.assign(new Error(), { statusCode: 410 }))).toBe(410);
    expect(httpStatusOf({ response: { status: 403 } })).toBe(403);
    expect(httpStatusOf(new Error("boom"))).toBeUndefined();
  });
});

describe("backoffMs", () => {
  it("grows exponentially and caps at 30s", () => {
    const noJitter = () => 1; // rand=1 → full delay
    expect(backoffMs(1, noJitter)).toBe(1000);
    expect(backoffMs(2, noJitter)).toBe(2000);
    expect(backoffMs(3, noJitter)).toBe(4000);
    expect(backoffMs(20, noJitter)).toBe(30000); // capped
  });
  it("applies jitter in [0.5x, 1x]", () => {
    expect(backoffMs(1, () => 0)).toBe(500);
    expect(backoffMs(1, () => 1)).toBe(1000);
  });
});

// ── Coalescing ───────────────────────────────────────────────────────────────

describe("coalesce", () => {
  it("collapses consecutive text edits to the same item, latest field wins", () => {
    const queue = [op(1, itemUpdate("i1", { text: "a" }))];
    const next = coalesce(queue, op(2, itemUpdate("i1", { text: "b" })));
    expect(next).toHaveLength(1);
    expect(next[0]!.seq).toBe(1); // merged into the existing op, keeps its slot
    expect(next[0]!.request.body).toEqual({ text: "b" });
  });

  it("merges field-by-field, preserving untouched fields", () => {
    const queue = [op(1, itemUpdate("i1", { text: "a", note: "keep" }))];
    const next = coalesce(queue, op(2, itemUpdate("i1", { text: "b" })));
    expect(next[0]!.request.body).toEqual({ text: "b", note: "keep" });
  });

  it("does not merge edits for different items", () => {
    const queue = [op(1, itemUpdate("i1", { text: "a" }))];
    const next = coalesce(queue, op(2, itemUpdate("i2", { text: "b" })));
    expect(next).toHaveLength(2);
  });

  it("does not merge different kinds for the same item", () => {
    const queue = [op(1, itemUpdate("i1", { text: "a" }))];
    const stateOp: OutboxOpInput = {
      entityType: "item",
      entityId: "i1",
      kind: "state",
      request: { method: "patch", path: "/x", body: { checked: true } },
    };
    const next = coalesce(queue, op(2, stateOp));
    expect(next).toHaveLength(2);
  });

  it("cancels a queued create when a delete for the same item arrives", () => {
    const queue = [op(1, itemCreate("i1")), op(2, itemUpdate("i1", { text: "a" }))];
    const next = coalesce(queue, op(3, itemDelete("i1")));
    expect(next).toHaveLength(0); // create + edits + delete all vanish
  });

  it("delete supersedes queued edits when there is no queued create", () => {
    const queue = [op(1, itemUpdate("i1", { text: "a" }))];
    const next = coalesce(queue, op(2, itemDelete("i1")));
    expect(next).toHaveLength(1);
    expect(next[0]!.kind).toBe("delete");
  });

  it("leaves other entities untouched when cancelling a create+delete", () => {
    const queue = [op(1, itemCreate("i1")), op(2, itemCreate("i2"))];
    const next = coalesce(queue, op(3, itemDelete("i1")));
    expect(next.map((o) => o.entityId)).toEqual(["i2"]);
  });

  it("cancelling a checklist create drops its queued child item + label ops", () => {
    // Offline: create card, add two items, attach a label, then delete the card.
    const queue = [
      op(1, checklistCreate("cl1")),
      op(2, itemCreate("i1", "cl1")),
      op(3, itemCreate("i2", "cl1")),
      op(4, itemUpdate("i1", { text: "typo" }, "cl1")),
      op(5, labelAttach("cl1", "lab1")),
      // An unrelated item in a different card must survive.
      op(6, itemCreate("i9", "cl2")),
    ];
    const next = coalesce(queue, op(7, checklistDelete("cl1")));
    // Card create + all its children + the delete vanish; the other card is kept.
    expect(next.map((o) => o.entityId)).toEqual(["i9"]);
  });

  it("does not cascade to child ops when the checklist create already drained", () => {
    // No queued create for cl1 → the card exists server-side, so its child ops
    // are legitimate writes and the delete is simply appended.
    const queue = [op(1, itemCreate("i1", "cl1")), op(2, labelAttach("cl1", "lab1"))];
    const next = coalesce(queue, op(3, checklistDelete("cl1")));
    expect(next.map((o) => o.kind)).toEqual(["create", "create", "delete"]);
  });

  it("keeps an in-flight child op when cancelling a checklist create", () => {
    const locked = new Set([2]); // i1's create is mid-flight
    const queue = [op(1, checklistCreate("cl1")), op(2, itemCreate("i1", "cl1")), op(3, itemCreate("i2", "cl1"))];
    const next = coalesce(queue, op(4, checklistDelete("cl1")), locked);
    // The locked child survives; the card create + the other child are dropped.
    expect(next.map((o) => o.entityId)).toEqual(["i1"]);
  });

  it("does NOT merge into or cancel a locked (in-flight) op", () => {
    const locked = new Set([1]);
    // update while an update is in-flight → append, don't clobber the sent body
    const q1 = coalesce([op(1, itemUpdate("i1", { text: "a" }))], op(2, itemUpdate("i1", { text: "b" })), locked);
    expect(q1).toHaveLength(2);
    // delete while the create is in-flight → cannot cancel it, append the delete
    const q2 = coalesce([op(1, itemCreate("i1"))], op(2, itemDelete("i1")), locked);
    expect(q2.map((o) => o.kind)).toEqual(["create", "delete"]);
  });
});

// ── Engine: ordering / drain ─────────────────────────────────────────────────

describe("OutboxEngine drain", () => {
  it("replays queued ops sequentially in enqueue order, then goes idle", async () => {
    const { store } = memStore();
    const sent: string[] = [];
    const events: OutboxEvent[] = [];
    let online = false;
    const engine = new OutboxEngine({
      store,
      isOnline: () => online,
      transport: async (o) => {
        sent.push(`${o.entityId}:${o.kind}`);
      },
      emit: (e) => events.push(e),
    });
    await engine.init();

    await engine.enqueue(itemCreate("i1"));
    await engine.enqueue(itemUpdate("i1", { text: "hi" }));
    await engine.enqueue(itemCreate("i2"));
    expect(sent).toEqual([]); // offline: nothing sent yet
    expect(engine.pending).toBe(3);

    online = true;
    engine.setOnline(true);
    await settle();

    expect(sent).toEqual(["i1:create", "i1:update", "i2:create"]);
    expect(engine.pending).toBe(0);
    expect(events.at(-1)).toEqual({ type: "idle" });
  });

  it("does not drain while offline", async () => {
    const { store } = memStore();
    const sent: OutboxOp[] = [];
    const engine = new OutboxEngine({
      store,
      isOnline: () => false,
      transport: async (o) => {
        sent.push(o);
      },
    });
    await engine.init();
    await engine.enqueue(itemCreate("i1"));
    await settle();
    expect(sent).toEqual([]);
    expect(engine.pending).toBe(1);
  });
});

// ── Engine: retry with backoff ───────────────────────────────────────────────

describe("OutboxEngine retry", () => {
  it("keeps the op and retries after a network failure, preserving order", async () => {
    const { store } = memStore();
    const sched = manualScheduler();
    const sent: string[] = [];
    let attempts = 0;
    const engine = new OutboxEngine({
      store,
      isOnline: () => true,
      scheduler: sched.scheduler,
      transport: async (o) => {
        attempts++;
        if (attempts === 1) throw networkError(); // first op's first try fails
        sent.push(o.entityId);
      },
    });
    await engine.init();

    await engine.enqueue(itemCreate("i1"));
    await engine.enqueue(itemUpdate("i1", { text: "x" }));
    await settle();

    // First op failed → nothing delivered, one retry scheduled, both ops kept.
    expect(sent).toEqual([]);
    expect(engine.pending).toBe(2);
    expect(sched.size).toBe(1);

    sched.flush(); // fire the backoff retry
    await settle();

    expect(sent).toEqual(["i1", "i1"]); // create retried, then update, in order
    expect(engine.pending).toBe(0);
  });

  // WI-14 manual "Sync now": bypass the armed backoff timer and drain immediately.
  it("kickDrain cancels an armed backoff and drains right away", async () => {
    const { store } = memStore();
    const sched = manualScheduler();
    const sent: string[] = [];
    let attempts = 0;
    const engine = new OutboxEngine({
      store,
      isOnline: () => true,
      scheduler: sched.scheduler,
      transport: async (o) => {
        attempts++;
        if (attempts === 1) throw networkError(); // arm a backoff retry
        sent.push(o.entityId);
      },
    });
    await engine.init();

    await engine.enqueue(itemCreate("i1"));
    await settle();
    expect(sent).toEqual([]);
    expect(sched.size).toBe(1); // a retry is armed

    engine.kickDrain(); // manual sync — cancel the timer and try now
    await settle();

    expect(sent).toEqual(["i1"]);
    expect(engine.pending).toBe(0);
  });

  it("kickDrain is a no-op while offline", async () => {
    const { store } = memStore();
    const sent: string[] = [];
    const engine = new OutboxEngine({
      store,
      isOnline: () => false,
      transport: async (o) => {
        sent.push(o.entityId);
      },
    });
    await engine.init();
    await engine.enqueue(itemCreate("i1"));
    engine.kickDrain();
    await settle();
    expect(sent).toEqual([]);
    expect(engine.pending).toBe(1);
  });
});

// ── Engine: terminal drops ───────────────────────────────────────────────────

describe("OutboxEngine terminal drops", () => {
  it("drops a terminally-failing op, emits op-dropped, and keeps draining", async () => {
    const { store } = memStore();
    const sent: string[] = [];
    const events: OutboxEvent[] = [];
    let online = false;
    const engine = new OutboxEngine({
      store,
      isOnline: () => online,
      emit: (e) => events.push(e),
      transport: async (o) => {
        if (o.entityId === "bad") throw httpError(409); // id collision → terminal
        sent.push(o.entityId);
      },
    });
    await engine.init();

    await engine.enqueue(itemCreate("bad"));
    await engine.enqueue(itemCreate("good"));
    online = true;
    engine.setOnline(true);
    await settle();

    const dropped = events.find((e) => e.type === "op-dropped");
    expect(dropped).toMatchObject({ type: "op-dropped", status: 409 });
    expect((dropped as any).op.entityId).toBe("bad");
    expect(sent).toEqual(["good"]); // drain continued past the drop
    expect(engine.pending).toBe(0);
  });

  it("classifies each terminal status as a drop, retryable as a keep", async () => {
    for (const [status, expectDropped] of [
      [403, true],
      [404, true],
      [410, true],
      [503, false],
    ] as const) {
      const { store } = memStore();
      const events: OutboxEvent[] = [];
      const engine = new OutboxEngine({
        store,
        isOnline: () => true,
        scheduler: () => () => {}, // swallow retries
        emit: (e) => events.push(e),
        transport: async () => {
          throw httpError(status);
        },
      });
      await engine.init();
      await engine.enqueue(itemUpdate("i1", { text: "x" }));
      await settle();
      const dropped = events.some((e) => e.type === "op-dropped");
      expect(dropped, `status ${status}`).toBe(expectDropped);
      expect(engine.pending, `status ${status}`).toBe(expectDropped ? 0 : 1);
    }
  });
});

// ── Engine: coalescing through the queue ─────────────────────────────────────

describe("OutboxEngine coalescing on enqueue", () => {
  it("collapses offline text edits so only the final value is sent", async () => {
    const { store } = memStore();
    const bodies: unknown[] = [];
    let online = false;
    const engine = new OutboxEngine({
      store,
      isOnline: () => online,
      transport: async (o) => {
        bodies.push(o.request.body);
      },
    });
    await engine.init();

    await engine.enqueue(itemUpdate("i1", { text: "a" }));
    await engine.enqueue(itemUpdate("i1", { text: "ab" }));
    await engine.enqueue(itemUpdate("i1", { text: "abc" }));
    expect(engine.pending).toBe(1); // three edits coalesced to one op

    online = true;
    engine.setOnline(true);
    await settle();

    expect(bodies).toEqual([{ text: "abc" }]);
  });

  it("create-then-delete offline sends nothing", async () => {
    const { store } = memStore();
    const sent: OutboxOp[] = [];
    let online = false;
    const engine = new OutboxEngine({
      store,
      isOnline: () => online,
      transport: async (o) => {
        sent.push(o);
      },
    });
    await engine.init();

    await engine.enqueue(itemCreate("i1"));
    await engine.enqueue(itemUpdate("i1", { text: "typo" }));
    await engine.enqueue(itemDelete("i1"));
    expect(engine.pending).toBe(0);

    online = true;
    engine.setOnline(true);
    await settle();
    expect(sent).toEqual([]);
  });
});

// ── Persistence: survive a restart, replay on reconnect ──────────────────────
//
// Uses the REAL IndexedDB store (fake-indexeddb) so this exercises the same code
// path the app runs. Engine A queues writes offline; a fresh Engine B (a new app
// session, same DB) loads and drains them once online — the WI-7 core guarantee.

describe("outbox persistence across a restart", () => {
  it("replays writes queued in a previous offline session", async () => {
    const store = createOutboxStore();

    // Session A — offline: queue a create + an edit, nothing sent.
    const engineA = new OutboxEngine({ store, isOnline: () => false, transport: async () => {} });
    await engineA.init();
    await engineA.enqueue(itemCreate("i1"));
    await engineA.enqueue(itemUpdate("i1", { text: "survives" }));

    // Persisted to IndexedDB (create + update, not coalescable together).
    const persisted = await store.load();
    expect(persisted.map((o) => o.kind)).toEqual(["create", "update"]);

    // Session B — a fresh engine over the same DB, now online.
    const sent: string[] = [];
    const engineB = new OutboxEngine({
      store,
      isOnline: () => true,
      transport: async (o) => {
        sent.push(`${o.entityId}:${o.kind}`);
      },
    });
    await engineB.init(); // loads the two persisted ops and starts draining
    await settle();

    expect(sent).toEqual(["i1:create", "i1:update"]);
    expect((await store.load())).toHaveLength(0); // queue drained + persisted empty
  });

  // WI-14 / review finding #9: a persist failure must reach the UI, not just the
  // console. A value IndexedDB can't structured-clone makes the write throw.
  it("invokes onStorageError when a persist write fails", async () => {
    const onStorageError = vi.fn();
    const store = createOutboxStore({ onStorageError });
    // A value IndexedDB can't structured-clone makes the put throw.
    await store.put({ opId: "boom", seq: 1, boom: () => {} } as unknown as OutboxOp);
    expect(onStorageError).toHaveBeenCalledTimes(1);
  });

  // Chunk A2: two tabs share ONE IndexedDB queue. Per-op persistence means a tab
  // only ever touches the op it enqueued — it must not erase another tab's
  // queued-but-undrained op (the pre-fix whole-queue `clear()`+rewrite did).
  it("does not clobber another tab's queued ops (multi-tab persistence)", async () => {
    const store = createOutboxStore();
    await store.clear(); // start from a clean shared DB

    // Two independent engines (tabs) over the one store, both offline so neither
    // drains; each only knows about the op it enqueued.
    const tabA = new OutboxEngine({ store, isOnline: () => false, transport: async () => {} });
    const tabB = new OutboxEngine({ store, isOnline: () => false, transport: async () => {} });
    await tabA.init();
    await tabB.init();

    // Interleave: A queues op1, then B — which never loaded op1 — queues op2.
    await tabA.enqueue(itemCreate("iA", "clA"));
    await tabB.enqueue(itemCreate("iB", "clB"));

    // Both survive on disk. Pre-fix, B's clear()+rewrite of its own [op2] would
    // have wiped A's op1 — a lost offline write on a reload/crash of tab A.
    const persisted = await store.load();
    expect(persisted.map((o) => o.entityId).sort()).toEqual(["iA", "iB"]);
  });

  // Chunk A1: account switch / logout wipes the queue in memory and on disk so a
  // new user's session never drains the previous user's writes.
  it("reset() clears the queue in memory and on disk", async () => {
    const store = createOutboxStore();
    await store.clear();
    const engine = new OutboxEngine({ store, isOnline: () => false, transport: async () => {} });
    await engine.init();
    await engine.enqueue(itemCreate("i1"));
    await engine.enqueue(itemCreate("i2"));
    expect(engine.pending).toBe(2);

    await engine.reset();
    expect(engine.pending).toBe(0);
    expect(await store.load()).toHaveLength(0);
  });
});

// ── Extra op builders for the WI-11 guard / resync tests ─────────────────────

function itemState(id: string, checked: boolean, clId = "cl1"): OutboxOpInput {
  return {
    entityType: "item",
    entityId: id,
    kind: "state",
    request: {
      method: "patch",
      path: "/api/checklist/{checklist_id}/item/{checklist_item_id}/state",
      pathParams: { checklist_id: clId, checklist_item_id: id },
      body: { checked },
    },
  };
}
function itemPosition(id: string, body: Record<string, unknown>, clId = "cl1"): OutboxOpInput {
  return {
    entityType: "item",
    entityId: id,
    kind: "position",
    request: {
      method: "patch",
      path: "/api/checklist/{checklist_id}/item/{checklist_item_id}/position",
      pathParams: { checklist_id: clId, checklist_item_id: id },
      body,
    },
  };
}
function checklistUpdate(id: string, body: Record<string, unknown>): OutboxOpInput {
  return {
    entityType: "checklist",
    entityId: id,
    kind: "update",
    request: { method: "patch", path: "/api/checklist/{checklist_id}", pathParams: { checklist_id: id }, body },
  };
}

// ── outboxFieldGuard (WI-11 finding #2) ──────────────────────────────────────

describe("outboxFieldGuard", () => {
  it("protects the DTO fields a queued op will overwrite", () => {
    const guard = outboxFieldGuard([
      op(1, itemPosition("i1", { index: 3 })),
      op(2, itemState("i2", true)),
      op(3, itemUpdate("i3", { text: "t" })),
      op(4, checklistUpdate("cl1", { name: "n", color_id: "red" })),
    ]);
    expect(guard.isEditing("item", "i1", "position.index")).toBe(true);
    expect(guard.isEditing("item", "i1", "state.checked")).toBe(false);
    expect(guard.isEditing("item", "i2", "state.checked")).toBe(true);
    expect(guard.isEditing("item", "i3", "text")).toBe(true);
    expect(guard.isEditing("checklist", "cl1", "name")).toBe(true);
    expect(guard.isEditing("checklist", "cl1", "color_id")).toBe(true);
    expect(guard.isEditing("checklist", "cl1", "text")).toBe(false);
  });

  it("maps a queued label attach/detach to its card's `labels` field", () => {
    const guard = outboxFieldGuard([op(1, labelAttach("cl9", "lbl1"))]);
    expect(guard.isEditing("checklist", "cl9", "labels")).toBe(true);
  });

  it("reports queued-delete rows as removed", () => {
    const guard = outboxFieldGuard([op(1, itemDelete("i1")), op(2, checklistDelete("cl1"))]);
    expect(guard.isRemoved!("item", "i1")).toBe(true);
    expect(guard.isRemoved!("checklist", "cl1")).toBe(true);
    expect(guard.isRemoved!("item", "other")).toBe(false);
  });
});

// ── pendingChecklistIds (WI-11 per-card indicator) ───────────────────────────

describe("pendingChecklistIds", () => {
  it("collects the card of every pending op (checklist, item, label)", () => {
    const ids = pendingChecklistIds([
      op(1, checklistUpdate("clA", { name: "n" })),
      op(2, itemState("i1", true, "clB")),
      op(3, labelAttach("clC", "lbl1")),
    ]);
    expect([...ids].sort()).toEqual(["clA", "clB", "clC"]);
  });

  it("is empty for an empty queue", () => {
    expect(pendingChecklistIds([]).size).toBe(0);
  });
});

// ── partitionResync (WI-11 finding #5) ───────────────────────────────────────

describe("partitionResync", () => {
  it("keeps creates and edits of still-known rows, drops orphaned edits/deletes", () => {
    const queue = [
      op(1, checklistCreate("clNew")), // re-creates → keep
      op(2, itemState("iKnown", true, "clKnown")), // row still exists → keep
      op(3, itemUpdate("iGone", { text: "t" }, "clGone")), // reset server never knew → drop
      op(4, checklistDelete("clGone")), // deleting a row that's gone → drop
    ];
    const known = new Set(["clKnown", "iKnown"]);
    const { kept, dropped } = partitionResync(queue, known);
    expect(kept.map((o) => o.seq)).toEqual([1, 2]);
    expect(dropped.map((o) => o.seq)).toEqual([3, 4]);
  });

  it("keeps edits of a row re-created by a surviving queued create", () => {
    const queue = [op(1, checklistCreate("clNew")), op(2, checklistUpdate("clNew", { name: "n" }))];
    const { kept, dropped } = partitionResync(queue, new Set());
    expect(kept.map((o) => o.seq)).toEqual([1, 2]);
    expect(dropped).toEqual([]);
  });

  it("drops a label attach whose card the reset server no longer knows", () => {
    const queue = [op(1, labelAttach("clGone", "lbl1"))];
    const { kept, dropped } = partitionResync(queue, new Set(["someOtherCard"]));
    expect(kept).toEqual([]);
    expect(dropped.map((o) => o.seq)).toEqual([1]);
  });

  it("keeps a label attach whose card survives the resync", () => {
    const queue = [op(1, labelAttach("clKnown", "lbl1"))];
    const { kept } = partitionResync(queue, new Set(["clKnown"]));
    expect(kept.map((o) => o.seq)).toEqual([1]);
  });

  it("drops an item create whose parent card the reset server no longer knows", () => {
    // Items added offline to a pre-existing card (no queued checklist create) —
    // if that card is gone after the reset, the item route 404s, so pre-drop them.
    const queue = [op(1, itemCreate("iNew", "clGone"))];
    const { kept, dropped } = partitionResync(queue, new Set(["someOtherCard"]));
    expect(kept).toEqual([]);
    expect(dropped.map((o) => o.seq)).toEqual([1]);
  });

  it("keeps an item create whose parent card survives the resync", () => {
    const queue = [op(1, itemCreate("iNew", "clKnown"))];
    const { kept } = partitionResync(queue, new Set(["clKnown"]));
    expect(kept.map((o) => o.seq)).toEqual([1]);
  });

  it("spares a locked (in-flight) op even if its row is gone", () => {
    const queue = [op(1, itemUpdate("iGone", { text: "t" }, "clGone"))];
    const { kept, dropped } = partitionResync(queue, new Set(), new Set([1]));
    expect(kept.map((o) => o.seq)).toEqual([1]);
    expect(dropped).toEqual([]);
  });
});

// ── engine.reconcileResync ───────────────────────────────────────────────────

describe("OutboxEngine.reconcileResync", () => {
  it("drops orphaned ops, persists, and returns them", async () => {
    const { store, state } = memStore();
    const engine = new OutboxEngine({ store, isOnline: () => false, transport: async () => {} });
    await engine.init();
    await engine.enqueue(itemUpdate("iGone", { text: "t" }, "clGone"));
    await engine.enqueue(checklistCreate("clNew"));

    const dropped = await engine.reconcileResync(new Set(["clNew"]));
    expect(dropped.map((o) => o.entityId)).toEqual(["iGone"]);
    expect(engine.queue.map((o) => o.entityId)).toEqual(["clNew"]);
    expect(state.ops.map((o) => o.entityId)).toEqual(["clNew"]); // persisted
  });

  it("returns [] and leaves the queue untouched when nothing is orphaned", async () => {
    const { store } = memStore();
    const engine = new OutboxEngine({ store, isOnline: () => false, transport: async () => {} });
    await engine.init();
    await engine.enqueue(checklistCreate("clNew"));
    const dropped = await engine.reconcileResync(new Set());
    expect(dropped).toEqual([]);
    expect(engine.queue).toHaveLength(1);
  });
});
