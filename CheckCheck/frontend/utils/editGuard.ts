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

/** The entities whose text fields we protect while focused. */
export type EditGuardKind = "checklist" | "item";
/** The protected fields. `name` is checklist-only; `text` applies to both. */
export type EditGuardField = "name" | "text";

/** The focus-registry contract deltaApply depends on (injected for tests). */
export interface EditGuard {
  isEditing(kind: EditGuardKind, id: string, field: EditGuardField): boolean;
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
