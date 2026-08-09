<template>
  <ul ref="ItemsView" class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
    <li v-for="item in draggableItems" :key="item.id"
      class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
      <!-- The row itself is the surface's business: the authed card wraps
           CardPartsItemRow in its store-coupled CheckListItem, the public viewer
           in its own anonymous adapter. Both get the same <li>, the same drag
           wiring and the same ref registration. -->
      <slot name="item" :item="item" :register-ref="(el: any) => registerItemRef(item.id, el)" />
    </li>
    <li v-if="showAddRow" class="no-drag px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
      <slot name="add" />
    </li>
  </ul>
</template>

<script setup lang="ts">
// The card's item list, shared by the authed card and the `/p/<token>` public
// viewer (issue #11). Owns the <ul>/<li> markup, the FormKit drag-and-drop wiring
// and the "focus this item's textarea" plumbing, and nothing else: it reads no
// store and performs no write. It takes the items to show and emits `reorder`
// with the dropped order for the surface to persist however it persists things.
import { useDragAndDrop } from "@formkit/drag-and-drop/vue";
import { animations } from "@formkit/drag-and-drop";
import { ref } from "vue";
import type { PropType } from "vue";

const props = defineProps({
  items: { type: Array as PropType<CheckListItemType[]>, required: true },
  // Reordering is an edit. Passed in rather than derived, because "can edit" is
  // resolved differently on each surface (card my_permission vs. link level).
  enableDrag: { type: Boolean, default: true },
  // Render the trailing non-draggable row for the surface's "add new item"
  // control (which is itself the #add slot, since each surface styles its own).
  showAddRow: { type: Boolean, default: false },
});

const emit = defineEmits<{
  reorder: [newOrder: CheckListItemType[], movedItem: CheckListItemType];
}>();

// The list handed to FormKit. Kept as its own array (not the prop) because
// FormKit DnD splices it in place as the user drags.
const checklistItems = ref<CheckListItemType[]>([]);

let dragInProgress = false;

watchEffect(() => {
  const sourceItems = props.items;
  // Never reset the drag list while a drag is in progress: mid-drag source
  // updates (from SSE or from a previous drag's async completing) would call
  // splice() and reset FormKit DnD's internal state, causing event.values in
  // onDragend to report the original order instead of the drop destination.
  if (dragInProgress) return;
  const newList = sourceItems.map(item => ({ ...item }));
  checklistItems.value.splice(0, checklistItems.value.length, ...newList);
});

// Track child row components so the surface can move focus to a freshly created
// item. Whatever the surface renders in the #item slot must expose
// `focusTextarea` (CardPartsItemRow does, and a wrapper should re-expose it).
const itemComponentRefs = new Map<string, { focusTextarea: () => void }>();
function registerItemRef(id: string, el: any) {
  if (el) itemComponentRefs.set(id, el);
  else itemComponentRefs.delete(id);
}
function focusItem(id: string) {
  itemComponentRefs.get(id)?.focusTextarea?.();
}
defineExpose({ focusItem });

const [ItemsView, draggableItems] = useDragAndDrop(checklistItems, {
  dragHandle: ".list-item-drag-handle",
  // Touch reorder inside the (transformed) editor modal: mirror the board-card
  // fix (memory `mobile-dnd-longpress`). Without longPress the synth drag arms
  // on the first pointermove, which the nested overflow-y-auto scroll container
  // tends to claim as a scroll instead — so no sort ever fires. Requiring a
  // press-and-hold to arm lets a normal touch still scroll the item list.
  // Desktop native mouse drag is unaffected.
  longPress: true,
  longPressDuration: 250,
  longPressClass: "list-item-longpress",
  onDragstart: () => { dragInProgress = true; },
  onDragend: (event) => {
    dragInProgress = false;
    const draggedItem = event.draggedNode.data.value as CheckListItemType;
    const allItems = event.values as CheckListItemType[];
    emit("reorder", allItems, draggedItem);
  },
  draggable: (el) => props.enableDrag && !(el && el.classList.contains('no-drag')),
  plugins: [animations()],
});
</script>

<style scoped>
/* Touch "picked up" cue: the longPress timer adds .list-item-longpress to the
   dragged <li> once the press-and-hold threshold is met, before the synthetic
   drag begins — immediate feedback that the row is now grabbable. Kept subtle
   (no scale, to avoid horizontal overflow / a second transformed ancestor for
   the drag clone) and removed automatically the moment the drag starts. */
:deep(.list-item-longpress) {
  background-color: rgb(0 0 0 / 0.06);
  border-radius: 0.375rem;
  transition: background-color 0.12s ease;
}
</style>
