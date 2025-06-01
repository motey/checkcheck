export default defineNuxtPlugin(() => {
  const transferAttrs = (from: object, to: object) => {
    // Apply attributes from one object to another
    // this is needed if an object need to be replaced but we dont want to lose reference
    for (const key of Object.keys(from)) {
      const fromVal = from[key as keyof typeof from];
      const toKey = key as keyof typeof to;

      if (typeof fromVal === "object" && fromVal !== null) {
        // Handle arrays specially - replace entirely
        if (Array.isArray(fromVal)) {
          to[toKey] = fromVal;
        } else {
          // Handle nested objects
          if (!to[toKey] || typeof to[toKey] !== "object" || Array.isArray(to[toKey])) {
            // If target doesn't have this property, or it's not an object, or it's an array
            // create a new object
            to[toKey] = {} as any;
          }
          // Recursively transfer attributes
          transferAttrs(fromVal, to[toKey] as object);
        }
      } else {
        // Handle primitive values
        to[toKey] = fromVal;
      }
    }
  };

  const findOutOfOrder = (
    items: CheckListItemType[]
  ): {
    index: number;
    direction: "up" | "down";
    item: CheckListItemType;
    prev_item: CheckListItemType | null;
    next_item: CheckListItemType | null;
  } | null => {
    if (items.length < 2) {
      return null; // No movement possible with one or zero items
    }
    for (let i = 0; i < items.length; i++) {
      const currItem = items[i];
      let prevItem = null;
      if (i > 0) {
        prevItem = items[i - 1];
      }
      let nextItem = null;
      if (i + 1 < items.length) {
        nextItem = items[i + 1];
      }
      if (prevItem === null && currItem.position.index < nextItem!.position.index) {
        return { index: i, direction: "up", item: currItem, prev_item: prevItem, next_item: nextItem };
      }
      if (nextItem === null && currItem.position.index > prevItem!.position.index) {
        return { index: i, direction: "down", item: currItem, prev_item: prevItem, next_item: nextItem };
      }
      if (
        nextItem !== null &&
        prevItem !== null &&
        currItem.position.index > prevItem!.position.index &&
        nextItem!.position.index < prevItem!.position.index
      ) {
        return { index: i, direction: "down", item: currItem, prev_item: prevItem, next_item: nextItem };
      }
      if (
        nextItem !== null &&
        prevItem !== null &&
        currItem.position.index < nextItem!.position.index &&
        nextItem!.position.index < prevItem!.position.index
      ) {
        return { index: i, direction: "up", item: currItem, prev_item: prevItem, next_item: nextItem };
      }
    }

    return null; // No out-of-order item detected
  };

  const sortBySubset = (
    mainList: CheckListItemType[] | CheckListType[],
    subList: CheckListItemType[] | CheckListType[]
  ) => {
    // Create a map of ids from the sublist with their index for quick lookup
    const subListOrder = new Map(subList.map((item, index) => [item.id, index]));
    // Sort the main list in place
    return mainList.sort((a, b) => {
      const indexA = subListOrder.has(a.id) ? subListOrder.get(a.id)! : Infinity;
      const indexB = subListOrder.has(b.id) ? subListOrder.get(b.id)! : Infinity;
      // First prioritize based on the sublist order
      if (indexA !== indexB) {
        return indexA - indexB;
      }
      // If both are not in the sublist, keep their relative order unchanged
      return 0;
    });
  };

  const findMovementDirOfItemBasedOnIndexOld = (
    item: CheckListItemType | CheckListType,
    items: CheckListItemType[] | CheckListType[],
    reverse_position_index: boolean = false
  ): {
    index: number;
    movement: "up" | "down" | null;
    prev_item: CheckListItemType | CheckListType | null;
    next_item: CheckListItemType | CheckListType | null;
  } => {
    // Find the index of the item with the given id
    if (items.length < 2) {
      return { index: 0, movement: null, prev_item: null, next_item: null };
    }
    const itemNewIndex = items.findIndex((i) => i.id === item.id);
    console.log(`itemIndex ${itemNewIndex}`);
    if (itemNewIndex === -1) {
      throw new Error(`Item with id "${item}" not found.`);
    }

    let prevItem = null;
    if (itemNewIndex > 0) {
      prevItem = items[itemNewIndex - 1];
    }
    let nextItem = null;
    if (itemNewIndex + 1 < items.length) {
      nextItem = items[itemNewIndex + 1];
    }

    // Check previous and next items to determine movement direction
    if (itemNewIndex === 0 && nextItem!.position.index < item.position.index) {
      // Was moved to first index
      return { index: 0, movement: "up", prev_item: null, next_item: nextItem };
    } else if (itemNewIndex + 1 == items.length && prevItem!.position.index > item.position.index) {
      // Item was moved to last index
      return { index: itemNewIndex, movement: "down", prev_item: prevItem, next_item: null };
    } else if (itemNewIndex > 0 && item.position.index < prevItem!.position.index) {
      // Item was moved down within the boundaries of the list
      return { index: itemNewIndex, movement: "down", prev_item: prevItem, next_item: nextItem };
    } else if (itemNewIndex < items.length - 1 && item.position.index > nextItem!.position.index) {
      return { index: itemNewIndex, movement: "up", prev_item: prevItem, next_item: nextItem };
    } else {
      return { index: itemNewIndex, movement: null, prev_item: prevItem, next_item: nextItem };
    }
  };
  const findNewPlacementForItem = (
    item: CheckListItemType | CheckListType,
    items: (CheckListItemType | CheckListType)[]
  ): {
    item: CheckListItemType | CheckListType;
    target_neighbor_item: CheckListItemType | CheckListType | null;
    placement: "above" | "below" | null;
  } => {
    if (items.length < 2) {
      return { item, target_neighbor_item: null, placement: null };
    }

    const index = items.findIndex((i) => i.id === item.id);
    if (index === -1) {
      throw new Error(`Item with id "${item.id}" not found in the list.`);
    }

    // Determine if the item is at the edge position based on direction
    const isAtEdge = index === 0;

    if (isAtEdge) {
      const neighborItem = items[1];
      return { item, target_neighbor_item: neighborItem, placement: "above" };
    } else {
      const neighborItem = items[index - 1];
      return { item, target_neighbor_item: neighborItem, placement: "below" };
    }
  };
  
  return {
    provide: {
      hello: (msg: string) => `Hello ${msg}!`,
      transferAttrs,
      findOutOfOrder,
      findNewPlacementForItem,
      sortBySubset,
    },
  };
});
