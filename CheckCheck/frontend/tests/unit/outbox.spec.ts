import { describe, it, expect } from "vitest";
import {
  OutboxEngine,
  backoffMs,
  classifyError,
  coalesce,
  httpStatusOf,
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

/** In-memory OutboxStore for engine-logic tests (no IndexedDB). */
function memStore(initial: OutboxOp[] = []) {
  const state = { ops: initial.slice() };
  const store: OutboxStore = {
    async load() {
      return state.ops.slice();
    },
    async persist(next) {
      state.ops = next.slice();
    },
  };
  return { store, state };
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

/** Materialise an OutboxOp (with seq) from an input — for pure coalesce tests. */
function op(seq: number, input: OutboxOpInput): OutboxOp {
  return { ...input, seq, opId: `op${seq}`, enqueuedAt: seq, attempts: 0 };
}

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
});
