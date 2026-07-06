// ── Offline write outbox — engine core (WI-7) ────────────────────────────────
//
// A persisted queue of writes made while offline (or that failed transiently),
// replayed against the existing permission-checked REST endpoints when
// connectivity returns. This module is the FRAMEWORK-FREE core: op shape, error
// classification, op coalescing, and the replay engine. It imports nothing from
// Nuxt/Vue/idb so it is unit-testable in plain vitest; the IndexedDB store, the
// `$checkapi` transport and the connectivity signal are injected as deps
// (see composables/useOutbox.ts for the real wiring).
//
// Contract: docs/SYNC_PROTOCOL.md §8 (terminal vs retryable) and §10 item 4.
// Every op carries a client-generated entity id, so every REST endpoint is
// replay-safe (protocol §8 "idempotent writes") — the engine can retry freely
// without duplicating or corrupting state.

// ── Op shape ─────────────────────────────────────────────────────────────────

/** The store an op targets — its per-entity ordering domain. */
export type OutboxEntityType = "checklist" | "item" | "label";

/**
 * The kind of mutation. Update-like kinds (`update` / `state` / `position`) are
 * coalescable — a later op of the same kind for the same entity collapses into
 * the earlier queued one (see `coalesce`). `create` / `delete` are not merged
 * (but a `delete` cancels a still-queued `create`).
 */
export type OutboxOpKind = "create" | "update" | "delete" | "state" | "position";

/**
 * A self-contained description of one REST call, in the shape the generated
 * `$checkapi` (openFetch) client takes. `path` is the openFetch path *template*
 * (`/api/checklist/{checklist_id}/item`), with `pathParams` filling the braces —
 * storing the template + params (rather than a pre-interpolated URL) keeps the op
 * transport-agnostic and easy to inspect/coalesce.
 */
export interface OutboxRequest {
  method: "post" | "patch" | "put" | "delete";
  path: string;
  pathParams?: Record<string, string>;
  query?: Record<string, unknown>;
  body?: Record<string, unknown>;
}

/** A queued write. `seq` is the monotonic enqueue order and the persisted key. */
export interface OutboxOp {
  /** Monotonic enqueue sequence — defines global drain order and per-entity order. */
  seq: number;
  /** Stable id for this op (logging / event correlation). */
  opId: string;
  entityType: OutboxEntityType;
  /**
   * The client-generated id of the target entity. For a `create` this is the
   * client-supplied UUID the endpoint accepts (protocol §8), so a replay is
   * idempotent; for edits/deletes it is the existing row's id. Together with
   * `entityType` it forms the per-entity ordering key.
   */
  entityId: string;
  kind: OutboxOpKind;
  request: OutboxRequest;
  enqueuedAt: number;
  /** Retry counter — drives exponential backoff; survives restarts. */
  attempts: number;
}

/** What a caller (WI-8/WI-9 stores) supplies; the engine stamps the rest. */
export type OutboxOpInput = Omit<OutboxOp, "seq" | "opId" | "enqueuedAt" | "attempts">;

// ── Error classification (protocol §8) ───────────────────────────────────────

export type OutboxErrorClass = "retryable" | "terminal";

/**
 * Map an HTTP status (or `undefined` for a transport/network failure) to the
 * outbox action. Per protocol §8: network + `5xx` are **retryable**; the named
 * `403/404/409/410` are **terminal** (drop + surface via WI-11).
 *
 * For statuses the contract leaves unspecified we pick the safe default:
 * - `401` (session expired) → **retryable**: WI-13 adds an offline-auth grace;
 *   dropping a user's queued write on a transient session blip is worse than
 *   holding it until re-auth. It spins with backoff, which is the intended
 *   "block sync upload, keep working, refresh on reconnect" behaviour.
 * - `408` / `429` → **retryable** (timeout / rate-limit — self-heals).
 * - every other `4xx` (e.g. `400`/`422` validation) → **terminal**: a malformed
 *   op will never succeed on replay, so drop it rather than loop forever.
 */
export function classifyError(status: number | undefined): OutboxErrorClass {
  if (status === undefined) return "retryable"; // network / transport failure
  if (status >= 500) return "retryable";
  if (status === 401 || status === 408 || status === 429) return "retryable";
  return "terminal";
}

/** Pull an HTTP status out of an ofetch `FetchError` (or anything error-ish). */
export function httpStatusOf(err: unknown): number | undefined {
  const e = err as any;
  const status = e?.status ?? e?.statusCode ?? e?.response?.status;
  return typeof status === "number" ? status : undefined;
}

// ── Coalescing ───────────────────────────────────────────────────────────────
//
// Applied at enqueue time over the currently-queued ops (excluding any op that
// is mid-flight — see `lockedSeqs`). Two rules, from the WI-7 spec:
//
//   1. Consecutive update-like edits to the same entity collapse to the latest:
//      merging field-by-field into the earlier queued op (LWW — the incoming
//      values win). Keeps the op at its original position so ordering relative to
//      other kinds is preserved; the endpoint is idempotent so only the final
//      value is ever sent.
//   2. `create`-then-`delete` offline cancels out: a `delete` whose entity still
//      has a queued (un-sent) `create` removes *all* queued ops for that entity
//      and drops the delete too — the row never reached the server, so there is
//      nothing to send. A `delete` for an entity with no queued create supersedes
//      any queued edits for it (they are moot) and is appended.

function coalesceKey(op: Pick<OutboxOp, "entityType" | "entityId" | "kind">): string {
  return `${op.entityType}:${op.entityId}:${op.kind}`;
}

const COALESCABLE: ReadonlySet<OutboxOpKind> = new Set(["update", "state", "position"]);

/**
 * Return the new queue after folding `incoming` in. Pure — no I/O, no mutation of
 * `queue` or its ops (a merged op is replaced with a new object).
 *
 * `lockedSeqs` holds ops that must not be merged into or removed — currently the
 * single in-flight op. Merging into an op whose request has already been sent
 * would silently lose the newer value (the sent request captured the old body),
 * so a new edit that arrives mid-flight is appended as its own op instead.
 */
export function coalesce(
  queue: OutboxOp[],
  incoming: OutboxOp,
  lockedSeqs: ReadonlySet<number> = new Set()
): OutboxOp[] {
  // Rule 2 — delete cancels a queued create.
  if (incoming.kind === "delete") {
    const hasUnlockedCreate = queue.some(
      (op) => op.entityId === incoming.entityId && op.kind === "create" && !lockedSeqs.has(op.seq)
    );
    if (hasUnlockedCreate) {
      // Create never reached the server → drop the whole local history for this
      // entity and the delete itself. A locked (in-flight) create is left alone.
      return queue.filter((op) => op.entityId !== incoming.entityId || lockedSeqs.has(op.seq));
    }
    // No pending create → the delete supersedes any queued edits for this entity.
    const kept = queue.filter(
      (op) => op.entityId !== incoming.entityId || op.kind === "create" || lockedSeqs.has(op.seq)
    );
    return [...kept, incoming];
  }

  // Rule 1 — collapse consecutive update-like edits to the same entity.
  if (COALESCABLE.has(incoming.kind)) {
    const key = coalesceKey(incoming);
    const idx = queue.findIndex((op) => coalesceKey(op) === key && !lockedSeqs.has(op.seq));
    if (idx !== -1) {
      const existing = queue[idx]!;
      const merged: OutboxOp = {
        ...existing,
        request: {
          ...existing.request,
          // Field-level LWW: the newer edit's fields win, older untouched fields
          // survive. Path/method/query come from `existing` (same endpoint).
          body: { ...(existing.request.body ?? {}), ...(incoming.request.body ?? {}) },
        },
      };
      const next = queue.slice();
      next[idx] = merged;
      return next;
    }
  }

  // create, or a first-of-its-kind edit → append.
  return [...queue, incoming];
}

// ── Replay engine ────────────────────────────────────────────────────────────

/** Persisted queue backend (real impl: utils/outboxDb.ts, injected). */
export interface OutboxStore {
  /** All persisted ops, ascending by `seq`. */
  load(): Promise<OutboxOp[]>;
  /** Persist the full queue (small — a full rewrite is fine at this app's scale). */
  persist(ops: OutboxOp[]): Promise<void>;
}

export type OutboxEvent =
  /** A terminal error (403/404/409/410/…) dropped an op — WI-11 surfaces this. */
  | { type: "op-dropped"; op: OutboxOp; status: number | undefined }
  /** The queue drained to empty. */
  | { type: "idle" };

/** Cancel handle for a scheduled retry. */
type Cancel = () => void;

export interface OutboxDeps {
  store: OutboxStore;
  /** Perform one op's REST call; throws an ofetch-style error on failure. */
  transport: (op: OutboxOp) => Promise<void>;
  /** Emit a lifecycle event (op dropped / idle) for WI-11 / WI-14. */
  emit?: (event: OutboxEvent) => void;
  /** Current connectivity — drain only runs when this is true. */
  isOnline?: () => boolean;
  /** Called after every queue change with the new pending count (reactivity). */
  onChange?: (pending: number) => void;
  /** Schedule a backoff retry; returns a cancel handle. Default: setTimeout. */
  scheduler?: (fn: () => void, delayMs: number) => Cancel;
  /** Clock — injectable for deterministic tests. Default: Date.now. */
  now?: () => number;
}

const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;

/** Exponential backoff with full jitter, capped. `attempts` is 1-based. */
export function backoffMs(attempts: number, rand: () => number = Math.random): number {
  const exp = Math.min(BASE_BACKOFF_MS * 2 ** (attempts - 1), MAX_BACKOFF_MS);
  return Math.round(exp * (0.5 + 0.5 * rand())); // jitter in [0.5x, 1.0x]
}

const defaultScheduler = (fn: () => void, delayMs: number): Cancel => {
  const t = setTimeout(fn, delayMs);
  return () => clearTimeout(t);
};

/**
 * Sequential, per-entity-ordered replay engine.
 *
 * Draining is a single serial loop over the queue in `seq` order — which, since
 * ops for one entity keep their relative enqueue order, trivially satisfies
 * "strict per-entity ordering". A retryable failure stops the whole drain and
 * schedules a backoff retry (head-of-line blocking); at this app's scale a
 * simple global serial drain is preferred over per-entity lanes. A terminal
 * failure drops just that op (emitting `op-dropped`) and the drain continues.
 */
export class OutboxEngine {
  private ops: OutboxOp[] = [];
  private nextSeq = 1;
  private inflightSeq: number | null = null;
  private draining = false;
  private retryCancel: Cancel | null = null;
  private readonly scheduler: (fn: () => void, delayMs: number) => Cancel;
  private readonly now: () => number;

  constructor(private readonly deps: OutboxDeps) {
    this.scheduler = deps.scheduler ?? defaultScheduler;
    this.now = deps.now ?? Date.now;
  }

  /** Number of ops still queued (pending + any in-flight). */
  get pending(): number {
    return this.ops.length;
  }

  /** Snapshot of the queue (defensive copy) — for tests / diagnostics. */
  get queue(): OutboxOp[] {
    return this.ops.slice();
  }

  /** Load persisted ops and start draining. Call once on boot. */
  async init(): Promise<void> {
    this.ops = await this.deps.store.load();
    this.nextSeq = this.ops.reduce((m, o) => Math.max(m, o.seq), 0) + 1;
    this.deps.onChange?.(this.ops.length);
    this.kick();
  }

  /** Queue a write. Coalesces, persists, and kicks the drain. */
  async enqueue(input: OutboxOpInput): Promise<OutboxOp> {
    const op: OutboxOp = {
      ...input,
      seq: this.nextSeq++,
      opId: newOpId(),
      enqueuedAt: this.now(),
      attempts: 0,
    };
    const locked = this.inflightSeq != null ? new Set([this.inflightSeq]) : new Set<number>();
    this.ops = coalesce(this.ops, op, locked);
    await this.persist();
    this.kick();
    return op;
  }

  /** Feed a connectivity change; going online kicks the drain immediately. */
  setOnline(online: boolean): void {
    if (online) {
      if (this.retryCancel) {
        this.retryCancel();
        this.retryCancel = null;
      }
      this.kick();
    }
  }

  private isOnline(): boolean {
    return this.deps.isOnline ? this.deps.isOnline() : true;
  }

  private kick(): void {
    // A backoff retry is already armed — let it fire rather than hammering a
    // failing server on every fresh enqueue. `setOnline(true)` (a real
    // connectivity change) explicitly cancels the timer and kicks past this.
    if (this.retryCancel) return;
    void this.drain();
  }

  private async persist(): Promise<void> {
    await this.deps.store.persist(this.ops.slice());
    this.deps.onChange?.(this.ops.length);
  }

  private remove(seq: number): void {
    this.ops = this.ops.filter((o) => o.seq !== seq);
  }

  private async drain(): Promise<void> {
    if (this.draining) return;
    if (!this.isOnline()) return;
    this.draining = true;
    try {
      while (this.ops.length > 0) {
        if (!this.isOnline()) break;
        const op = this.ops[0]!;
        let failedRetryable = false;
        this.inflightSeq = op.seq;
        try {
          await this.deps.transport(op);
        } catch (err) {
          const status = httpStatusOf(err);
          if (classifyError(status) === "retryable") {
            op.attempts += 1;
            failedRetryable = true;
          } else {
            // Terminal — drop this op and surface it, then keep draining.
            this.remove(op.seq);
            await this.persist();
            this.deps.emit?.({ type: "op-dropped", op, status });
            continue;
          }
        } finally {
          this.inflightSeq = null;
        }

        if (failedRetryable) {
          await this.persist(); // record the bumped attempt count
          this.scheduleRetry(op.attempts);
          return; // stop the drain; the timer (or a reconnect) resumes it
        }

        // Success — the endpoint is idempotent so a duplicate delivery is safe.
        this.remove(op.seq);
        await this.persist();
      }
      if (this.ops.length === 0) this.deps.emit?.({ type: "idle" });
    } finally {
      this.draining = false;
    }
  }

  private scheduleRetry(attempts: number): void {
    if (this.retryCancel) this.retryCancel();
    this.retryCancel = this.scheduler(() => {
      this.retryCancel = null;
      this.kick();
    }, backoffMs(attempts));
  }
}

function newOpId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    /* fall through */
  }
  return `op-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
