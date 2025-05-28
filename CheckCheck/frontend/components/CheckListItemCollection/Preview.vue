<template>
  <!--<ul ref="ItemsView" :v-if="draggableItems" class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0" :class="!editModeActive ? ['max-h-52', 'overflow-hidden'] : []">-->
    
  <ul class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
    <li v-for="item in checklistItems" :key="item.id"
      class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
      <CheckListItem class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0 text-sm font-light"
        :parentCheckList="parentCheckList" :checkListItem="item" :parentEditMode="false"></CheckListItem>
    </li>
    <div v-if="showThereIsMoreHint && showMaxItems" class="no-drag pl-4 pt-2">
      <b>...</b> <span class="opacity-40">+ {{ thereIsMoreCount }} items</span>
    </div>
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
let ItemsView: Ref<HTMLElement | undefined> | undefined;
let draggableItems: Ref<CheckListItemType[]> | CheckListItemType[] = [];
const props = defineProps({
  parentCheckList: { type: Object as PropType<CheckListType>, required: true },
  filterCheckedItems: { type: Boolean, required: false },
  showMaxItems: { type: Number, required: false, watch: true },
});
const checklistItems = ref<CheckListItemType[]>([]);
watchEffect(() => {
  checklistItems.value = [
    ...checkListsItemStore.getCheckListItems(
      props.parentCheckList.id,
      props.filterCheckedItems,
      props.showMaxItems
    )
  ];
});
const showThereIsMoreHint = computed(() => {
  return checklistItems.value.length < checkListsItemStore.getItemCount(props.parentCheckList.id, props.filterCheckedItems);
});
const thereIsMoreCount = computed(() => {
  const totalCount = checkListsItemStore.getItemCount(props.parentCheckList.id, props.filterCheckedItems);
  return totalCount - checklistItems.value.length;
});
/*
watch(
  () => props.showMaxItems,
  (newShowMaxItems) => {
    console.log("NEW newShowMaxItems", newShowMaxItems);
    //getCheckListItems()
    //draggableItems.value = localItems.value
  }
);
*/
</script>

<style scoped></style>
