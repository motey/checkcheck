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
import { state } from "@formkit/drag-and-drop";
import { ref } from 'vue';
import { animations } from "@formkit/drag-and-drop";
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

watchEffect(() => {
  const sourceItems = checkListsItemStore.getCheckListItems(
    props.parentCheckList.id,
    props.filterCheckedItems,
    props.showMaxItems
  );

  // Only copy if it's different to avoid resetting drag
  checklistItems.value = sourceItems.map(item => ({ ...item }));
});

const [ItemsView, draggableItems] = useDragAndDrop(checklistItems, {
  //group: "checkListItems",
  dragHandle: ".list-item-drag-handle",

  onDragend: (event) => {
    (async () => {
      
      const draggedItem = event.draggedNode.data.value as CheckListItemType;
      const allItems = event.values as CheckListItemType[];
      console.log("WE MOVE")
      checkListsItemStore.reorderChecklistItems(props.parentCheckList.id, allItems, draggedItem)
    })();
    //valuesChanged.value = `${event.previousValues} -> ${event.values}`;
  },
  draggable: (el) => !(el && el.classList.contains('no-drag')),
  plugins: [animations()],
});

</script>

<style scoped>

</style>