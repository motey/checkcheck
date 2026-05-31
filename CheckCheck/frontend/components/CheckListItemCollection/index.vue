<template>
  <ul ref="ItemsView" class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
    <li v-for="item in draggableItems" :key="item.id"
      class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
      <CheckListItem class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0 text-sm font-light"
        :parentCheckList="parentCheckList" :checkListItem="item" :parentEditMode="true"></CheckListItem>
    </li>
    <li v-if="filterCheckedItems!=true" class="no-drag px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
      <CheckListItemCollectionAddNewButton  :parentCheckList="parentCheckList">
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