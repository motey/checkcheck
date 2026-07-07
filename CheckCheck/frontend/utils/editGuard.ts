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

/** The shared, module-level guard the live app wires into deltaApply. */
export const defaultEditGuard: EditGuard = { isEditing };

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
  };
}
