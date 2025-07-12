<template>
  <UContainer>
    LABEL EDITOR
    <ul ref="ItemsView" class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0">
      <li
        v-for="item in draggableItems"
        :key="item.id"
        class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0"
      >
        <LabelManagerEditItem
          class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0 text-sm font-light"
          :label="item"
        ></LabelManagerEditItem>
      </li>
    </ul>
  </UContainer>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useDragAndDrop, dragAndDrop } from "@formkit/drag-and-drop/vue";
import { animations } from "@formkit/drag-and-drop";
import { useDebounceFn } from "@vueuse/core";
import { useCheckListsColorSchemeStore } from "@/stores/color";
import { useCheckListsLabelStore } from "@/stores/label";
const checkListsColorSchemeStore = useCheckListsColorSchemeStore();
const checkListsLabelStore = useCheckListsLabelStore();
const colorMode = useColorMode();

const checkListsStore = useCheckListsStore();
const checkListsItemStore = useCheckListsItemStore();

const props = defineProps({});

const [ItemsView, draggableItems] = useDragAndDrop(checkListsLabelStore.labels, {
  //group: "checkListItems",
  dragHandle: ".label-item-drag-handle",

  onDragend: (event) => {
    (async () => {
      
      const draggedItem = event.draggedNode.data.value as LabelType;
      const allItems = event.values as LabelType[];
      console.log("WE MOVE")
      //checkListsLabelStore.reorderChecklistItems(props.parentCheckList.id, allItems, draggedItem)
    })();
    //valuesChanged.value = `${event.previousValues} -> ${event.values}`;
  },
  draggable: (el) => !(el && el.classList.contains('no-drag')),
  plugins: [animations()],
});

</script>
 
<style scoped>
</style>
