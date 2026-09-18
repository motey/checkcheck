// ── Focused-field edit guard (WI-10) ─────────────────────────────────────────
//
// A tiny module-level registry of the text fields the local user is *currently
// editing* (a focused `name`/`text` input). Delta application (utils/deltaApply)
// consults it so an incoming server value never clobbers an in-flight edit —
// SYNC_PROTOCOL §4 "focused-edit protection".
//
// The components already guard their own textarea `localName`/`localText` refs
// behind focus (CheckList.vue / CheckListItem.vue); this registry lifts that
// same signal up to the STORE-apply layer, so the persisted row (and any other
// view of it — board previews, other tabs' mirrors) also keeps the user's value
// while they type, not just the focused textarea. It is the seam WI-11's
// conflict toast plugs into (a superseded focused edit is a conflict to surface).
//
// Framework-light on purpose (a plain Set, no Vue refs) so it is importable from
// the framework-free deltaApply core and trivially unit-testable.

/** The entities whose fields we protect from a clobbering delta. */
export type EditGuardKind = "checklist" | "item";
/**
 * A protected field, as a **DTO-shaped path** so `mergeDelta` can preserve it
 * over the incoming server row. The focus registry only ever marks the two text
 * fields (`name`/`text`); the outbox-derived guard (WI-11, finding #2) extends
 * the same vocabulary to the non-text fields a queued op will overwrite —
 * `state.checked`, `position.index`, a card's `labels`, etc. — so a still-
 * undrained optimistic reorder/check doesn't visibly revert when a delta for a
 * *different* field of the same row lands.
 */
export type EditGuardField =
  | "name"
  | "text"
  | "color_id"
  | "labels"
  | "state.checked"
  | "position.index"
  | "position.indentation"
  | "position.pinned"
  | "position.archived";

/**
 * The guard contract `mergeDelta` depends on (injected for tests). `isEditing`
 * answers "keep the local value of this field" (focused edit OR queued op);
 * `isRemoved` answers "this row is locally deleted (a queued delete) — don't let
 * a delta resurrect it". Both are consulted per-row during application.
 */
export interface EditGuard {
  isEditing(kind: EditGuardKind, id: string, field: EditGuardField): boolean;
  /** Optional: true if the entity has a queued delete, so its local removal stands. */
  isRemoved?(kind: EditGuardKind, id: string): boolean;
  /**
   * Optional: true if `value` is one this client itself wrote to the field. A
   * protected field whose incoming value is our own echo (an earlier save coming
   * back while the user kept typing) is not a concurrent edit, so no conflict.
   */
  isOwnValue?(kind: EditGuardKind, id: string, field: EditGuardField, value: unknown): boolean;
}

function keyOf(kind: EditGuardKind, id: string, field: EditGuardField): string {
  return `${kind}:${id}:${field}`;
}

const editing = new Set<string>();

/** Mark a field as actively edited (call on focus). */
export function markEditing(kind: EditGuardKind, id: string, field: EditGuardField): void {
  editing.add(keyOf(kind, id, field));
}

/** Clear a field's editing mark (call on blur). */
export function clearEditing(kind: EditGuardKind, id: string, field: EditGuardField): void {
  editing.delete(keyOf(kind, id, field));
}

/** True while the user has this field focused — deltaApply must not clobber it. */
export function isEditing(kind: EditGuardKind, id: string, field: EditGuardField): boolean {
  return editing.has(keyOf(kind, id, field));
}

// ── Own-write registry (self-echo suppression) ──────────────────────────────
//
// Every local write reaches the server and comes back to THIS client through the
// delta feed (the poke goes to every session, the author's included). If the user
// kept typing in the meantime, the echo differs from the local value and used to
// be reported as "also edited elsewhere" although nobody else touched the row.
// So each write records the values it sends, and a conflict is only raised when
// the incoming value is not one of ours. Bounded: the last few values per field,
// and a cap on the number of fields tracked (oldest evicted first).

/** Recorded for set-valued fields (a card's `labels`) where any echo counts as ours. */
export const ANY_OWN_VALUE: unique symbol = Symbol("any-own-value");

const OWN_VALUES_PER_FIELD = 20;
const OWN_FIELDS_MAX = 1000;
const ownWrites = new Map<string, unknown[]>();

/** Remember a value this client sent for a field (call when the write is queued). */
export function recordOwnWrite(kind: EditGuardKind, id: string, field: EditGuardField, value: unknown): void {
  const key = keyOf(kind, id, field);
  const values = ownWrites.get(key) ?? [];
  // Re-insert so the Map's insertion order doubles as least-recently-written order.
  ownWrites.delete(key);
  values.push(value);
  if (values.length > OWN_VALUES_PER_FIELD) values.shift();
  ownWrites.set(key, values);
  if (ownWrites.size > OWN_FIELDS_MAX) ownWrites.delete(ownWrites.keys().next().value!);
}

/** True if `value` is one this client wrote to the field (i.e. an incoming echo of our own save). */
export function isOwnWrite(kind: EditGuardKind, id: string, field: EditGuardField, value: unknown): boolean {
  const values = ownWrites.get(keyOf(kind, id, field));
  if (!values) return false;
  return values.some((v) => v === ANY_OWN_VALUE || v === value || (v == null && value == null));
}

/** Forget every recorded write (account switch / tests). */
export function clearOwnWrites(): void {
  ownWrites.clear();
}

/** The shared, module-level guard the live app wires into deltaApply. */
export const defaultEditGuard: EditGuard = { isEditing, isOwnValue: isOwnWrite };

/** A guard that never protects anything — the default for tests / bootstrap. */
export const noopEditGuard: EditGuard = { isEditing: () => false };

/**
 * Fold several guards into one that protects a field if *any* member does (and
 * treats a row as removed if any member does). The live delta pull composes the
 * focus registry (`defaultEditGuard`) with the outbox-derived guard (WI-11) so a
 * field is kept whether the user is actively typing it or has a queued op for it.
 */
export function combineGuards(...guards: EditGuard[]): EditGuard {
  return {
    isEditing: (kind, id, field) => guards.some((g) => g.isEditing(kind, id, field)),
    isRemoved: (kind, id) => guards.some((g) => g.isRemoved?.(kind, id) ?? false),
    isOwnValue: (kind, id, field, value) => guards.some((g) => g.isOwnValue?.(kind, id, field, value) ?? false),
  };
}
