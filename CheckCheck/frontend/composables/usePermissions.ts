// Single source of truth for permission gating in the UI.
//
// Every card the API returns carries `my_permission` (P0.1): the caller's
// effective access on the `view < check < edit < owner` ladder. The UI gates
// affordances on it through this one helper so the logic never drifts across
// components.

type PermissionAction = "check" | "edit";

const LADDER = {
  view: 0,
  check: 1,
  edit: 2,
  owner: 3,
} as const;

type PermissionLevel = keyof typeof LADDER;

function levelOf(card?: CheckListType | null): number {
  // Default to the lowest (view) when the field is somehow absent, so a missing
  // permission never accidentally unlocks an action.
  const perm = (card?.my_permission ?? "view") as PermissionLevel;
  return LADDER[perm] ?? 0;
}

export function usePermissions() {
  // True when the caller may perform `action` on this card.
  function can(card: CheckListType | null | undefined, action: PermissionAction): boolean {
    return levelOf(card) >= LADDER[action];
  }

  function isOwner(card?: CheckListType | null): boolean {
    return card?.my_permission === "owner";
  }

  return { can, isOwner };
}
