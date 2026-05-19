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
import { useDragAndDrop } from "@formkit/drag-and-drop/vue";
import { animations } from "@formkit/drag-and-drop";
import { watch } from "vue";
import { useCheckListsLabelStore } from "@/stores/label";

const checkListsLabelStore = useCheckListsLabelStore();

const [ItemsView, draggableItems] = useDragAndDrop([...checkListsLabelStore.labels], {
  dragHandle: ".label-item-drag-handle",
  draggable: (el) => !(el && el.classList.contains("no-drag")),
  plugins: [animations()],
});

watch(
  () => checkListsLabelStore.labels,
  (newLabels) => {
    draggableItems.value = [...newLabels];
  },
  { deep: true }
);
</script>
 
<style scoped>
</style>
