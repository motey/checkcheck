// ── Delta application core (WI-10) ───────────────────────────────────────────
//
// The ONE code path that folds a `GET /api/changes` response into the local
// stores — live (SSE `changes_available` poke) or after an offline gap
// (reconnect / boot). Framework-free so it unit-tests in plain vitest without a
// Nuxt harness (like the WI-7 outbox engine): it mutates plain array/record
// slices that the caller happens to back with the live Pinia store state.
//
// Contract: docs/SYNC_PROTOCOL.md §3 ("Applying a delta"), §4 (LWW +
// focused-edit protection), §7 (`removed_checklist_ids`). Application is
// IDEMPOTENT (re-applying the same delta is a no-op) because every row is keyed
// by a stable id and every optimistic create carried its client UUID into the
// write (§8), so the delta upsert lands on the same id — never a duplicate.

import { noopEditGuard, type EditGuard, type EditGuardField } from "@/utils/editGuard";

/** The store slices a delta folds into. Backed by live Pinia state in the app. */
export interface DeltaTarget {
  /** checkListStore.checkLists — sorted pinned-first, then descending index. */
  checkLists: CheckListType[];
  /** checkListItemStore.checkListsItems — per-checklist item arrays. */
  items: Record<string, CheckListItemType[]>;
  /** labelStore.labels — sorted by descending sort_order. */
  labels: LabelType[];
  /** Optional item-store count bookkeeping (omit in pure tests that ignore it). */
  itemCounts?: ItemCountMaps;
}

/** The item store's per-checklist preview count maps (WI-8 `_adjustCounts`). */
export interface ItemCountMaps {
  total: Record<string, number>;
  checked: Record<string, number>;
  unchecked: Record<string, number>;
  /** checklistWasFullLoadedOnce — true ⇒ counts are derived from the array. */
  fullyLoaded: Record<string, boolean>;
}

/** What changed, so the caller can drive side effects (sidebar counts, sort). */
export interface DeltaSummary {
  /** A card row/position/label/tombstone/revocation changed → refresh sidebar. */
  cardLevelChanged: boolean;
  /** Net change to `total_backend_count` (cards added − cards removed). */
  cardCountDelta: number;
  /** Item lists that were touched (upsert/tombstone) — re-sorted + recounted. */
  touchedItemChecklistIds: Set<string>;
  /** Cards removed (tombstone or revoked access). */
  removedCheckListIds: Set<string>;
  /**
   * Concurrent-edit collisions (WI-11): a field the local user is protecting
   * (focused edit or queued op) whose incoming server value *differed* from the
   * value we kept. The caller surfaces these as an unobtrusive "also edited
   * elsewhere" toast — the local value is preserved (no flap / no lost write;
   * LWW converges once the op drains), so the message is informational.
   */
  conflicts: DeltaConflict[];
}

/** One protected field whose server value diverged from the kept local value. */
export interface DeltaConflict {
  kind: "checklist" | "item";
  id: string;
  field: EditGuardField;
}

// Pinned first, then DESCENDING index — mirrors checklist store `_sort`.
function compareCheckLists(a: CheckListType, b: CheckListType): number {
  return (
    Number(b.position?.pinned ?? false) - Number(a.position?.pinned ?? false) ||
    (b.position?.index ?? 0) - (a.position?.index ?? 0)
  );
}

// Ascending fractional index, id tiebreak — mirrors item store
// `compareByPositionThenId` (two items can transiently share an index).
function compareItems(a: CheckListItemType, b: CheckListItemType): number {
  return a.position.index - b.position.index || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

/** True if the two label sets (by id) differ — a card-level change (§3). */
function labelIdsDiffer(a: LabelType[] = [], b: LabelType[] = []): boolean {
  if (a.length !== b.length) return true;
  const bIds = new Set(b.map((l) => l.id));
  return a.some((l) => !bIds.has(l.id));
}

/**
 * Fold a `GET /api/changes` delta into the target stores in place. Returns a
 * summary of what changed. Pass a `guard` so an incoming server value never
 * clobbers a field the user is protecting — actively editing (focus registry,
 * §4) or holding an undrained outbox op for (WI-11 finding #2) — nor resurrects
 * a row they deleted offline. The live app composes both guards
 * (`combineGuards`); the default protects nothing (bootstrap / tests).
 *
 * NOTE: `full_resync` is NOT handled here — that is a cache drop + wholesale
 * rebuild the caller owns (utils/localSnapshot). This applies an incremental
 * (or since=0 bootstrap) delta as an upsert/remove.
 */
export function mergeDelta(
  target: DeltaTarget,
  delta: ChangesResponseType,
  guard: EditGuard = noopEditGuard
): DeltaSummary {
  const summary: DeltaSummary = {
    cardLevelChanged: false,
    cardCountDelta: 0,
    touchedItemChecklistIds: new Set(),
    removedCheckListIds: new Set(),
    conflicts: [],
  };

  // ── 1. Upsert checklists (server wins, except protected local fields) ──────
  for (const incoming of delta.checklists ?? []) {
    const idx = target.checkLists.findIndex((c) => c.id === incoming.id);
    const existing = idx !== -1 ? target.checkLists[idx]! : undefined;
    if (!existing) {
      // A row the user deleted offline (queued delete) must not be resurrected by
      // a concurrent-edit delta — keep it gone until the delete drains (§7 / WI-11).
      if (guard.isRemoved?.("checklist", incoming.id)) continue;
      target.checkLists.push({ ...incoming });
      summary.cardLevelChanged = true;
      summary.cardCountDelta += 1;
      // A brand-new card needs an item list so the board can render it; a normal
      // delta ships the card's changed items in the same response (all of them
      // on access-gain, §7). Start it empty and let the item upserts fill it.
      if (!(incoming.id in target.items)) target.items[incoming.id] = [];
      continue;
    }
    // Preserve any field the local user is protecting (focused edit or queued op,
    // §4 / WI-11 finding #2), recording a conflict where the server value diverged.
    const merged = preserveChecklistFields(incoming, existing, guard, summary);
    // A card-level change only if something the sidebar counts depend on moved
    // (archived / pinned / label set) — a pure name/text edit must not refetch.
    if (
      existing.position?.archived !== merged.position?.archived ||
      (existing.position?.pinned ?? false) !== (merged.position?.pinned ?? false) ||
      labelIdsDiffer(existing.labels, merged.labels)
    ) {
      summary.cardLevelChanged = true;
    }
    target.checkLists.splice(idx, 1, merged);
  }
  if (delta.checklists?.length) target.checkLists.sort(compareCheckLists);

  // ── 2. Upsert items (server wins, except protected local fields) ───────────
  for (const incoming of delta.items ?? []) {
    const clId = incoming.checklist_id;
    // Only track items for a card we hold (in the item map or as a checklist row).
    let list = target.items[clId];
    if (!list) {
      const knownCard = target.checkLists.some((c) => c.id === clId);
      if (!knownCard) continue; // not our card — ignore
      list = target.items[clId] = [];
    }
    const idx = list.findIndex((i) => i.id === incoming.id);
    const existing = idx !== -1 ? list[idx]! : undefined;
    if (!existing) {
      // Don't resurrect an item the user deleted offline (queued delete).
      if (guard.isRemoved?.("item", incoming.id)) continue;
      insertSortedItem(list, { ...incoming });
      if (target.itemCounts && !target.itemCounts.fullyLoaded[clId]) {
        adjustItemCounts(target.itemCounts, clId, 1, incoming.state.checked ? 1 : 0);
      }
      summary.touchedItemChecklistIds.add(clId);
      continue;
    }
    const merged = preserveItemFields(incoming, existing, guard, summary);
    if (target.itemCounts && !target.itemCounts.fullyLoaded[clId]) {
      adjustItemCounts(target.itemCounts, clId, 0, stateDelta(existing, merged));
    }
    list.splice(idx, 1, merged);
    summary.touchedItemChecklistIds.add(clId);
  }

  // ── 3. Upsert labels ───────────────────────────────────────────────────────
  for (const incoming of delta.labels ?? []) {
    const idx = target.labels.findIndex((l) => l.id === incoming.id);
    if (idx !== -1) target.labels.splice(idx, 1, incoming);
    else target.labels.push(incoming);
  }
  if (delta.labels?.length) {
    target.labels.sort((a, b) => (b.sort_order ?? 0) - (a.sort_order ?? 0));
  }

  // ── 4. Removals: checklist tombstones + revoked access (§7) ────────────────
  for (const id of [...(delta.checklist_tombstones ?? []), ...(delta.removed_checklist_ids ?? [])]) {
    const sid = String(id);
    summary.removedCheckListIds.add(sid);
    const idx = target.checkLists.findIndex((c) => c.id === sid);
    if (idx !== -1) {
      target.checkLists.splice(idx, 1);
      summary.cardLevelChanged = true;
      summary.cardCountDelta -= 1;
    }
    if (sid in target.items) delete target.items[sid];
    if (target.itemCounts) dropItemCounts(target.itemCounts, sid);
  }

  // ── 5. Item tombstones (id-only — scan for the owning list) ────────────────
  for (const id of delta.item_tombstones ?? []) {
    const sid = String(id);
    for (const clId of Object.keys(target.items)) {
      const list = target.items[clId]!;
      const idx = list.findIndex((i) => i.id === sid);
      if (idx !== -1) {
        const removed = list[idx]!;
        list.splice(idx, 1);
        summary.touchedItemChecklistIds.add(clId);
        if (target.itemCounts && !target.itemCounts.fullyLoaded[clId]) {
          adjustItemCounts(target.itemCounts, clId, -1, removed.state.checked ? -1 : 0);
        }
        break;
      }
    }
  }

  // ── 6. Label tombstones ────────────────────────────────────────────────────
  for (const id of delta.label_tombstones ?? []) {
    const sid = String(id);
    const idx = target.labels.findIndex((l) => l.id === sid);
    if (idx !== -1) target.labels.splice(idx, 1);
    // Also strip the chip from every card: the server does NOT re-emit cards on
    // a label delete (the link rows are only masked at read time, no fresh
    // server_seq), so without this a deleted label's chip lingers on cached
    // cards forever.
    for (const card of target.checkLists) {
      const cIdx = card.labels?.findIndex((l) => l.id === sid) ?? -1;
      if (cIdx !== -1) {
        card.labels!.splice(cIdx, 1);
        summary.cardLevelChanged = true;
      }
    }
  }

  // ── 7. Re-sort + recount every touched item list ───────────────────────────
  for (const clId of summary.touchedItemChecklistIds) {
    const list = target.items[clId];
    if (!list) continue;
    list.sort(compareItems);
    // Fully-loaded lists carry the whole item set → derive exact counts from it,
    // overriding any incremental drift. Preview lists keep the adjusted counts.
    if (target.itemCounts?.fullyLoaded[clId]) recountFromArray(target.itemCounts, clId, list);
  }

  return summary;
}

/**
 * Build the merged checklist row: the incoming server row, but with any field
 * the local user is protecting (focused edit or queued op) kept at its local
 * value. Records a `DeltaConflict` when the server's value for a protected field
 * differed — a concurrent edit the caller surfaces (WI-11). Clones the nested
 * `position` so overriding a position field never mutates the shared server DTO.
 */
function preserveChecklistFields(
  incoming: CheckListType,
  existing: CheckListType,
  guard: EditGuard,
  summary: DeltaSummary
): CheckListType {
  const merged: CheckListType = { ...incoming, position: { ...incoming.position } };
  const keep = (field: EditGuardField, differs: boolean, apply: () => void): void => {
    if (!guard.isEditing("checklist", incoming.id, field)) return;
    apply();
    if (differs) summary.conflicts.push({ kind: "checklist", id: incoming.id, field });
  };
  keep("name", incoming.name !== existing.name, () => (merged.name = existing.name));
  keep("text", incoming.text !== existing.text, () => (merged.text = existing.text));
  keep("color_id", incoming.color_id !== existing.color_id, () => (merged.color_id = existing.color_id));
  keep("labels", labelIdsDiffer(existing.labels, incoming.labels), () => (merged.labels = existing.labels));
  if (merged.position && existing.position) {
    keep(
      "position.index",
      incoming.position?.index !== existing.position.index,
      () => (merged.position!.index = existing.position!.index)
    );
    keep(
      "position.pinned",
      (incoming.position?.pinned ?? false) !== (existing.position.pinned ?? false),
      () => (merged.position!.pinned = existing.position!.pinned)
    );
    keep(
      "position.archived",
      (incoming.position?.archived ?? false) !== (existing.position.archived ?? false),
      () => (merged.position!.archived = existing.position!.archived)
    );
  }
  return merged;
}

/** Item twin of `preserveChecklistFields` — protects `text` / `state.checked` / `position.*`. */
function preserveItemFields(
  incoming: CheckListItemType,
  existing: CheckListItemType,
  guard: EditGuard,
  summary: DeltaSummary
): CheckListItemType {
  const merged: CheckListItemType = {
    ...incoming,
    position: { ...incoming.position },
    state: { ...incoming.state },
  };
  const keep = (field: EditGuardField, differs: boolean, apply: () => void): void => {
    if (!guard.isEditing("item", incoming.id, field)) return;
    apply();
    if (differs) summary.conflicts.push({ kind: "item", id: incoming.id, field });
  };
  keep("text", incoming.text !== existing.text, () => (merged.text = existing.text));
  keep(
    "state.checked",
    incoming.state.checked !== existing.state.checked,
    () => (merged.state.checked = existing.state.checked)
  );
  keep(
    "position.index",
    incoming.position.index !== existing.position.index,
    () => (merged.position.index = existing.position.index)
  );
  keep(
    "position.indentation",
    incoming.position.indentation !== existing.position.indentation,
    () => (merged.position.indentation = existing.position.indentation)
  );
  return merged;
}

/** Net checked-count change when an item's state flips between two versions. */
function stateDelta(before: CheckListItemType, after: CheckListItemType): number {
  const b = before.state.checked ? 1 : 0;
  const a = after.state.checked ? 1 : 0;
  return a - b;
}

function adjustItemCounts(c: ItemCountMaps, clId: string, dTotal: number, dChecked: number): void {
  c.total[clId] = (c.total[clId] ?? 0) + dTotal;
  c.checked[clId] = (c.checked[clId] ?? 0) + dChecked;
  c.unchecked[clId] = (c.unchecked[clId] ?? 0) + (dTotal - dChecked);
}

function recountFromArray(c: ItemCountMaps, clId: string, list: CheckListItemType[]): void {
  const checked = list.filter((i) => i.state.checked).length;
  c.total[clId] = list.length;
  c.checked[clId] = checked;
  c.unchecked[clId] = list.length - checked;
}

function dropItemCounts(c: ItemCountMaps, clId: string): void {
  delete c.total[clId];
  delete c.checked[clId];
  delete c.unchecked[clId];
  delete c.fullyLoaded[clId];
}

/** Binary-insert into an index-sorted list — mirrors item store `_insertNewAtCorrectIndex`. */
function insertSortedItem(list: CheckListItemType[], item: CheckListItemType): void {
  let low = 0;
  let high = list.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (compareItems(list[mid]!, item) < 0) low = mid + 1;
    else high = mid;
  }
  list.splice(low, 0, item);
}
