/**
 * Plain utility functions used by Pinia stores.
 * Previously injected as Nuxt plugin globals — kept here as regular imports
 * so they are properly typed, tree-shakeable, and importable in tests.
 */

/** Copy all properties from `from` onto `to` in-place, preserving the `to` reference. */
export function transferAttrs(from: object, to: object): void {
  for (const key of Object.keys(from)) {
    const fromVal = (from as Record<string, unknown>)[key];
    const toKey = key as keyof typeof to;
    if (typeof fromVal === "object" && fromVal !== null) {
      if (Array.isArray(fromVal)) {
        (to as Record<string, unknown>)[key] = fromVal;
      } else {
        if (!to[toKey] || typeof to[toKey] !== "object" || Array.isArray(to[toKey])) {
          (to as Record<string, unknown>)[key] = {};
        }
        transferAttrs(fromVal as object, to[toKey] as object);
      }
    } else {
      (to as Record<string, unknown>)[key] = fromVal;
    }
  }
}

/**
 * Given an item and its new position in a reordered list, determine whether
 * it should be placed above or below its nearest neighbour so the backend can
 * update the fractional index.
 */
export function findNewPlacementForItem(
  item: CheckListItemType | CheckListType,
  items: (CheckListItemType | CheckListType)[]
): {
  item: CheckListItemType | CheckListType;
  target_neighbor_item: CheckListItemType | CheckListType | null;
  placement: "above" | "below" | null;
} {
  if (items.length < 2) {
    return { item, target_neighbor_item: null, placement: null };
  }
  const index = items.findIndex((i) => i.id === item.id);
  if (index === -1) throw new Error(`Item with id "${item.id}" not found in the list.`);
  if (index === 0) {
    return { item, target_neighbor_item: items[1]!, placement: "above" };
  }
  return { item, target_neighbor_item: items[index - 1]!, placement: "below" };
}

/**
 * Sort `mainList` in-place so that items appearing in `subList` come first,
 * in the same order as `subList`. Items not in `subList` keep their relative order.
 */
export function sortBySubset<T extends { id: string }>(mainList: T[], subList: T[]): T[] {
  const subListOrder = new Map(subList.map((item, index) => [item.id, index]));
  return mainList.sort((a, b) => {
    const ia = subListOrder.has(a.id) ? subListOrder.get(a.id)! : Infinity;
    const ib = subListOrder.has(b.id) ? subListOrder.get(b.id)! : Infinity;
    return ia !== ib ? ia - ib : 0;
  });
}
