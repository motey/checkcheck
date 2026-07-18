<template>
  <ul ref="ItemsView" class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
    <li v-for="item in draggableItems" :key="item.id"
      class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
      <CheckListItem class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0 text-[13px] sm:text-sm"
        :ref="(el) => registerItemRef(item.id, el)"
        :parentCheckList="parentCheckList" :checkListItem="item" :parentEditMode="true"
        @add-item-after="addItemAfter" @delete-item="deleteItem" @accept-suggestion="acceptSuggestion"></CheckListItem>
    </li>
    <li v-if="filterCheckedItems!=true" class="no-drag px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
      <CheckListItemCollectionAddNewButton  :parentCheckList="parentCheckList" @add-item="addItemAtEnd">
      </CheckListItemCollectionAddNewButton>
    </li>
    
  </ul>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
import { useDragAndDrop, dragAndDrop } from "@formkit/drag-and-drop/vue";
import { animations } from "@formkit/drag-and-drop";
import { state } from "@formkit/drag-and-drop";
import { ref } from 'vue';
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useCheckListsStore } from "@/stores/checklist";
import type { PropType } from "vue";

const checkListsItemStore = useCheckListsItemStore();
const checkListStore = useCheckListsStore();

const props = defineProps({
  parentCheckList: { type: Object as PropType<CheckListType>, required: true },
  filterCheckedItems: { type: Boolean, required: false },
  showMaxItems: { type: Number, required: false, watch: true },
});
const checklistItems = ref<CheckListItemType[]>([]);

let dragInProgress = false;

watchEffect(() => {
  const sourceItems = checkListsItemStore.getCheckListItems(
    props.parentCheckList.id,
    props.filterCheckedItems,
    props.showMaxItems
  );
  // Never reset the drag list while a drag is in progress: mid-drag store
  // updates (from SSE or from a previous drag's async completing) would call
  // splice() and reset FormKit DnD's internal state, causing event.values in
  // onDragend to report the original order instead of the drop destination.
  if (dragInProgress) return;
  const newList = sourceItems.map(item => ({ ...item }));
  checklistItems.value.splice(0, checklistItems.value.length, ...newList);
});

// Track child item components so we can move focus to a freshly created item.
const itemComponentRefs = new Map<string, { focusTextarea: () => void }>();
function registerItemRef(id: string, el: any) {
  if (el) itemComponentRefs.set(id, el);
  else itemComponentRefs.delete(id);
}

// Enter on an item textarea: insert a new item right after it and focus it.
async function addItemAfter(afterItemId: string) {
  const list = checkListsItemStore.getCheckListItems(props.parentCheckList.id);
  const idx = list.findIndex((i) => i.id === afterItemId);
  if (idx === -1) return;
  const current = list[idx]!;
  const next = list[idx + 1];
  const newIndex = next
    ? (current.position.index + next.position.index) / 2
    : current.position.index + 1;
  const created = await checkListsItemStore.create(props.parentCheckList.id, {
    position: { index: newIndex },
  } as CheckListItemCreateType);
  await nextTick();
  itemComponentRefs.get(created.id)?.focusTextarea?.();
}

// "Add new item" button: append an item to the end and focus its textarea.
async function addItemAtEnd() {
  const created = await checkListsItemStore.create(props.parentCheckList.id);
  await nextTick();
  itemComponentRefs.get(created.id)?.focusTextarea?.();
}

// Keep-style dedup: the user typed a new item that matches an existing checked
// item, and accepted the suggestion. Uncheck the match (it reactively moves into
// the unchecked section) instead of keeping a duplicate, drop the just-typed
// item (its still-queued create coalesces away in the outbox), and move focus to
// the now-unchecked match.
async function acceptSuggestion(payload: { currentItemId: string; matchedItemId: string }) {
  const { currentItemId, matchedItemId } = payload;
  await checkListsItemStore.updateState(props.parentCheckList.id, matchedItemId, {
    checked: false,
  } as CheckListItemStateUpdateType);
  await deleteItem(currentItemId, false);
  await nextTick();
  itemComponentRefs.get(matchedItemId)?.focusTextarea?.();
}

// Delete an item. When triggered by backspace-on-empty, move focus to the end
// of the previous visible item so keyboard editing flows uninterrupted.
async function deleteItem(itemId: string, focusPrev: boolean) {
  const idx = checklistItems.value.findIndex((i) => i.id === itemId);
  const prev = idx > 0 ? checklistItems.value[idx - 1] : undefined;
  await checkListsItemStore.delete(props.parentCheckList.id, itemId);
  if (focusPrev && prev) {
    await nextTick();
    itemComponentRefs.get(prev.id)?.focusTextarea?.();
  }
}

const [ItemsView, draggableItems] = useDragAndDrop(checklistItems, {
  dragHandle: ".list-item-drag-handle",
  onDragstart: () => { dragInProgress = true; },
  onDragend: (event) => {
    dragInProgress = false;
    const draggedItem = event.draggedNode.data.value as CheckListItemType;
    const allItems = event.values as CheckListItemType[];
    (async () => {
      checkListsItemStore.reorderChecklistItems(props.parentCheckList.id, allItems, draggedItem);
    })();
  },
  draggable: (el) => !(el && el.classList.contains('no-drag')),
  plugins: [animations()],
});

</script>

<style scoped>

</style>