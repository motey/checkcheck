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

import { noopEditGuard, type EditGuard } from "@/utils/editGuard";

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
 * summary of what changed. Pass the live focus registry as `guard` so an
 * incoming server value never clobbers a field the user is actively editing
 * (§4); the default protects nothing (bootstrap / tests).
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
  };

  // ── 1. Upsert checklists (server wins, except focused name/text) ───────────
  for (const incoming of delta.checklists ?? []) {
    const idx = target.checkLists.findIndex((c) => c.id === incoming.id);
    const existing = idx !== -1 ? target.checkLists[idx]! : undefined;
    const merged: CheckListType = {
      ...incoming,
      // Focused-edit protection (§4): keep the user's in-flight value.
      name: existing && guard.isEditing("checklist", incoming.id, "name") ? existing.name : incoming.name,
      text: existing && guard.isEditing("checklist", incoming.id, "text") ? existing.text : incoming.text,
    };
    if (existing) {
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
    } else {
      target.checkLists.push(merged);
      summary.cardLevelChanged = true;
      summary.cardCountDelta += 1;
      // A brand-new card needs an item list so the board can render it; a normal
      // delta ships the card's changed items in the same response (all of them
      // on access-gain, §7). Start it empty and let the item upserts fill it.
      if (!(incoming.id in target.items)) target.items[incoming.id] = [];
    }
  }
  if (delta.checklists?.length) target.checkLists.sort(compareCheckLists);

  // ── 2. Upsert items (server wins, except focused text) ─────────────────────
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
    const merged: CheckListItemType = {
      ...incoming,
      text: existing && guard.isEditing("item", incoming.id, "text") ? existing.text : incoming.text,
    };
    if (existing) {
      if (target.itemCounts && !target.itemCounts.fullyLoaded[clId]) {
        adjustItemCounts(target.itemCounts, clId, 0, stateDelta(existing, merged));
      }
      list.splice(idx, 1, merged);
    } else {
      insertSortedItem(list, merged);
      if (target.itemCounts && !target.itemCounts.fullyLoaded[clId]) {
        adjustItemCounts(target.itemCounts, clId, 1, merged.state.checked ? 1 : 0);
      }
    }
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
