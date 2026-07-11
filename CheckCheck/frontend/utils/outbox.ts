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
//      nothing to send. Cancelling a **checklist** create also drops that card's
//      queued *child* ops (item writes and label associations): they target a
//      card the server never saw, so replaying them would 404 (spurious
//      `op-dropped` events for a flow the user intended). A `delete` for an
//      entity with no queued create supersedes any queued edits for it (they are
//      moot) and is appended.

function coalesceKey(op: Pick<OutboxOp, "entityType" | "entityId" | "kind">): string {
  return `${op.entityType}:${op.entityId}:${op.kind}`;
}

/**
 * Whether `op` is a *child* write of the checklist `checklistId` — an item op
 * (its request targets that card via `pathParams.checklist_id`) or a
 * checklist⇄label association op (its composite entity id is
 * `"{checklistId}:{labelId}"`, see `checklistLabelKey`). Used to cascade a
 * create-then-delete cancel: when a card's never-sent create is cancelled, its
 * children never reached the server either and must be dropped with it.
 */
function isChecklistChild(op: OutboxOp, checklistId: string): boolean {
  if (op.entityType === "item") {
    return op.request.pathParams?.checklist_id === checklistId;
  }
  if (op.entityType === "label") {
    return op.entityId.startsWith(`${checklistId}:`);
  }
  return false;
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
      // entity and the delete itself. Cancelling a checklist create also cascades
      // to its never-sent child ops (items + label links), which would otherwise
      // 404 on replay. A locked (in-flight) op is always left alone.
      const isChecklist = incoming.entityType === "checklist";
      return queue.filter(
        (op) =>
          lockedSeqs.has(op.seq) ||
          (op.entityId !== incoming.entityId &&
            !(isChecklist && isChecklistChild(op, incoming.entityId)))
      );
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

/**
 * Entity ids with a queued (not yet successfully sent) `create` op. The delta
 * pull excludes these from the `known=` param it reports to the server: a card
 * created offline is not accessible server-side until its create drains, so the
 * server would otherwise report it in `removed_checklist_ids` and the client
 * would delete its own optimistic row (it reappears after the drain — a visible
 * flap, and a lost card if the user reloads in between).
 */
export function queuedCreateIds(
  queue: readonly OutboxOp[],
  entityType: OutboxEntityType
): Set<string> {
  const ids = new Set<string>();
  for (const op of queue) {
    if (op.entityType === entityType && op.kind === "create") ids.add(op.entityId);
  }
  return ids;
}

// ── Outbox-derived field guard (WI-11, review finding #2) ────────────────────
//
// A queued op is an undrained local edit: until it replays, the server row still
// carries the *old* value of the field(s) it will overwrite. A delta for a
// DIFFERENT field of that same row (another user's concurrent edit) would take
// the server row wholesale and visibly revert the pending field (a reorder snaps
// back, a check un-checks) — no data loss (LWW resolves once the op drains) but
// a user-visible flap that reads as a lost write.
//
// This maps the queue to the DTO field paths each entity has pending, so
// `mergeDelta` (via the injected `EditGuard`) can keep the local value of a
// field with a queued op — exactly the focused-edit protection idea (§4),
// extended from "actively typing" to "typed but not yet synced". A queued
// `delete` is reported via `isRemoved` so a delta can't resurrect a row the user
// removed offline.

import type { EditGuard, EditGuardField, EditGuardKind } from "@/utils/editGuard";

/** The DTO field paths an item op will overwrite (empty for create/delete). */
function itemOpFields(op: OutboxOp): EditGuardField[] {
  const body = op.request.body ?? {};
  switch (op.kind) {
    case "update":
      return "text" in body ? ["text"] : [];
    case "state":
      return ["state.checked"];
    case "position": {
      const fields: EditGuardField[] = [];
      if ("index" in body) fields.push("position.index");
      if ("indentation" in body) fields.push("position.indentation");
      return fields;
    }
    default:
      return [];
  }
}

/** The DTO field paths a checklist op will overwrite (empty for create/delete). */
function checklistOpFields(op: OutboxOp): EditGuardField[] {
  const body = op.request.body ?? {};
  switch (op.kind) {
    case "update": {
      const fields: EditGuardField[] = [];
      if ("name" in body) fields.push("name");
      if ("text" in body) fields.push("text");
      if ("color_id" in body) fields.push("color_id");
      return fields;
    }
    case "position": {
      const fields: EditGuardField[] = [];
      if ("index" in body) fields.push("position.index");
      if ("pinned" in body) fields.push("position.pinned");
      if ("archived" in body) fields.push("position.archived");
      return fields;
    }
    default:
      return [];
  }
}

/**
 * Build an `EditGuard` from the current queue: it protects every field a queued
 * op will overwrite and reports queued-delete rows as removed. Composed with the
 * focus registry (`combineGuards`) in the live delta pull so a field is kept
 * whether the user is typing it or has an undrained op for it.
 */
export function outboxFieldGuard(queue: readonly OutboxOp[]): EditGuard {
  const itemFields = new Map<string, Set<EditGuardField>>();
  const checklistFields = new Map<string, Set<EditGuardField>>();
  const removedItems = new Set<string>();
  const removedChecklists = new Set<string>();

  const addFields = (
    map: Map<string, Set<EditGuardField>>,
    id: string,
    fields: EditGuardField[]
  ): void => {
    if (!fields.length) return;
    let set = map.get(id);
    if (!set) map.set(id, (set = new Set()));
    for (const f of fields) set.add(f);
  };

  for (const op of queue) {
    if (op.entityType === "item") {
      if (op.kind === "delete") removedItems.add(op.entityId);
      else addFields(itemFields, op.entityId, itemOpFields(op));
    } else if (op.entityType === "checklist") {
      if (op.kind === "delete") removedChecklists.add(op.entityId);
      else addFields(checklistFields, op.entityId, checklistOpFields(op));
    } else if (op.entityType === "label") {
      // A checklist⇄label association op (entityId "{checklistId}:{labelId}")
      // changes the card's label set; keep the local `labels` until it drains.
      const checklistId = op.entityId.slice(0, op.entityId.indexOf(":"));
      if (checklistId) addFields(checklistFields, checklistId, ["labels"]);
    }
  }

  return {
    isEditing: (kind: EditGuardKind, id: string, field: EditGuardField): boolean =>
      (kind === "item" ? itemFields : checklistFields).get(id)?.has(field) ?? false,
    isRemoved: (kind: EditGuardKind, id: string): boolean =>
      (kind === "item" ? removedItems : removedChecklists).has(id),
  };
}

/**
 * The checklist ids with any pending (undrained) op — the card itself, any of
 * its items, or a label association. Drives the per-card "changes not yet
 * synced" indicator (WI-11, feeds WI-14). An item op names its card via
 * `pathParams.checklist_id`; a label pair-op via the `"{checklistId}:"` prefix.
 */
export function pendingChecklistIds(queue: readonly OutboxOp[]): Set<string> {
  const ids = new Set<string>();
  for (const op of queue) {
    if (op.entityType === "checklist") ids.add(op.entityId);
    else if (op.entityType === "item") {
      const clId = op.request.pathParams?.checklist_id;
      if (clId) ids.add(clId);
    } else if (op.entityType === "label") {
      const clId = op.entityId.slice(0, op.entityId.indexOf(":"));
      if (clId) ids.add(clId);
    }
  }
  return ids;
}

/**
 * After a `full_resync` (server DB reset/restore), decide which queued ops the
 * reset server can still accept and drop the rest (finding #5). The rule per op:
 *
 * - A **checklist create** re-POSTs its card (idempotent client id) → keep.
 * - An **item op** (any kind) needs its **parent card** to exist — in `knownIds`
 *   or re-created by a surviving queued checklist create. If the card is gone the
 *   `/checklist/{id}/item...` route 404s regardless, so drop it. If the card is
 *   there: a create re-makes the item; an edit/delete additionally needs the item
 *   itself (in `knownIds`, or re-created by a queued item create).
 * - A **label association** needs its card to exist (its `.../label/...` 404s
 *   otherwise).
 * - A **checklist edit/delete** needs the card itself.
 *
 * Returns the pure partition so the engine can persist + surface an aggregate count.
 */
export function partitionResync(
  queue: readonly OutboxOp[],
  knownIds: ReadonlySet<string>,
  lockedSeqs: ReadonlySet<number> = new Set()
): { kept: OutboxOp[]; dropped: OutboxOp[] } {
  const queuedCreates = new Set<string>();
  for (const op of queue) if (op.kind === "create") queuedCreates.add(op.entityId);
  const existsAfterResync = (id: string | undefined): boolean =>
    !!id && (knownIds.has(id) || queuedCreates.has(id));

  const survives = (op: OutboxOp): boolean => {
    if (lockedSeqs.has(op.seq)) return true;
    if (op.entityType === "label") {
      return existsAfterResync(op.entityId.slice(0, op.entityId.indexOf(":")));
    }
    if (op.entityType === "item") {
      // The parent card must survive, or the item route 404s no matter what.
      if (!existsAfterResync(op.request.pathParams?.checklist_id)) return false;
      return op.kind === "create" || existsAfterResync(op.entityId);
    }
    // checklist
    return op.kind === "create" || existsAfterResync(op.entityId);
  };
  const kept: OutboxOp[] = [];
  const dropped: OutboxOp[] = [];
  for (const op of queue) (survives(op) ? kept : dropped).push(op);
  return { kept, dropped };
}

// ── Replay engine ────────────────────────────────────────────────────────────

/**
 * Persisted queue backend (real impl: utils/outboxDb.ts, injected).
 *
 * Per-op `put`/`remove` (keyed by the op's unique `opId`), NOT a whole-queue
 * rewrite: multiple browser tabs share this one IndexedDB queue, and a
 * clear+rewrite from one tab would erase another tab's queued-but-undrained ops
 * (Chunk A2). A tab only ever touches the specific op it enqueued or drained, so
 * concurrent tabs no longer clobber each other's writes.
 */
export interface OutboxStore {
  /** All persisted ops, ascending by `seq`. */
  load(): Promise<OutboxOp[]>;
  /** Upsert one op by its `opId` (add on enqueue, replace on coalesce-merge / retry). */
  put(op: OutboxOp): Promise<void>;
  /** Delete one op by its `opId` (drain success / terminal drop / resync prune). */
  remove(opId: string): Promise<void>;
  /** Wipe the whole queue (account switch / logout — Chunk A1). */
  clear(): Promise<void>;
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
  /**
   * Wrap a drain pass so only one context drains the shared queue at a time
   * (Chunk A2). The real wiring (composables/useOutbox.ts) uses the Web Locks API
   * for cross-tab single-writer election; the framework-free engine stays unaware
   * of it. Default: run the pass directly (single-context / tests).
   */
  drainLock?: <T>(run: () => Promise<T>) => Promise<T>;
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
  private readonly drainLock: <T>(run: () => Promise<T>) => Promise<T>;

  constructor(private readonly deps: OutboxDeps) {
    this.scheduler = deps.scheduler ?? defaultScheduler;
    this.now = deps.now ?? Date.now;
    this.drainLock = deps.drainLock ?? ((run) => run());
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
    const before = this.ops;
    this.ops = coalesce(before, op, locked);
    await this.persistDiff(before, this.ops);
    this.kick();
    return op;
  }

  /**
   * Reconcile the queue against a `full_resync` (server reset/restore): drop ops
   * the reset server can no longer accept (WI-11 finding #5) so they don't drain
   * to a silent 404, and return the dropped ops so the caller can surface a
   * single "N pending changes couldn't be applied" notice. Surviving ops (creates
   * + edits of rows that still exist) keep draining. The in-flight op is spared.
   */
  async reconcileResync(knownIds: ReadonlySet<string>): Promise<OutboxOp[]> {
    const locked = this.inflightSeq != null ? new Set([this.inflightSeq]) : new Set<number>();
    const { kept, dropped } = partitionResync(this.ops, knownIds, locked);
    if (dropped.length > 0) {
      this.ops = kept;
      for (const op of dropped) await this.deps.store.remove(op.opId);
      this.deps.onChange?.(this.ops.length);
      this.kick();
    }
    return dropped;
  }

  /**
   * Wipe the queue entirely — both in memory and on disk (Chunk A1). Called when
   * the browser's local state is invalidated on an account switch or logout, so a
   * new user's session never drains the previous user's queued writes. Cancels
   * any armed retry so a stale op can't fire after the reset.
   */
  async reset(): Promise<void> {
    if (this.retryCancel) {
      this.retryCancel();
      this.retryCancel = null;
    }
    this.ops = [];
    this.nextSeq = 1;
    this.inflightSeq = null;
    await this.deps.store.clear();
    this.deps.onChange?.(this.ops.length);
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

  /**
   * Force an immediate drain, cancelling any armed backoff timer — the manual
   * "Sync now" affordance (WI-14). No-op offline (nothing can drain). Unlike a
   * fresh `enqueue`, this deliberately bypasses the backoff head-of-line block:
   * the user explicitly asked to retry now.
   */
  kickDrain(): void {
    if (!this.isOnline()) return;
    if (this.retryCancel) {
      this.retryCancel();
      this.retryCancel = null;
    }
    void this.drain();
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

  /**
   * Persist the delta between two queue states with per-op `put`/`remove`, so a
   * write never rewrites the whole (tab-shared) queue (Chunk A2). Ops are keyed
   * by `opId`; a coalesce that merges into an existing op replaces it in place
   * (same `opId`, new object → `put`), an append is a `put`, a cancel is a
   * `remove`.
   */
  private async persistDiff(before: OutboxOp[], after: OutboxOp[]): Promise<void> {
    const afterIds = new Set(after.map((o) => o.opId));
    const beforeById = new Map(before.map((o) => [o.opId, o]));
    for (const o of before) {
      if (!afterIds.has(o.opId)) await this.deps.store.remove(o.opId);
    }
    for (const o of after) {
      // Reference inequality catches both a brand-new op and a coalesce-merged
      // one (coalesce replaces the merged op with a fresh object).
      if (beforeById.get(o.opId) !== o) await this.deps.store.put(o);
    }
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
      // Single-writer across tabs (Chunk A2): only one context drains the shared
      // queue at a time. Default (no lock injected) runs the pass directly.
      await this.drainLock(() => this.drainLoop());
    } finally {
      this.draining = false;
    }
  }

  private async drainLoop(): Promise<void> {
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
          await this.deps.store.remove(op.opId);
          this.deps.onChange?.(this.ops.length);
          this.deps.emit?.({ type: "op-dropped", op, status });
          continue;
        }
      } finally {
        this.inflightSeq = null;
      }

      if (failedRetryable) {
        await this.deps.store.put(op); // record the bumped attempt count
        this.deps.onChange?.(this.ops.length);
        this.scheduleRetry(op.attempts);
        return; // stop the drain; the timer (or a reconnect) resumes it
      }

      // Success — the endpoint is idempotent so a duplicate delivery is safe.
      this.remove(op.seq);
      await this.deps.store.remove(op.opId);
      this.deps.onChange?.(this.ops.length);
    }
    if (this.ops.length === 0) this.deps.emit?.({ type: "idle" });
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
