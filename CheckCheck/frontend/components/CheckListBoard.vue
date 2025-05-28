<template>
  <ul v-if="dragCheckLists" ref="checkListBoard" class="grid gap-4 grid-cols-[repeat(auto-fill,minmax(16rem,1fr))]">
    <li v-for="checkList in dragCheckLists" :key="checkList.id" class="w-full max-w-64 sm:w-auto checklist-preview">
      <CheckList :checkListId="checkList.id" @click="openCheckListEditor(checkList.id)" :previewModeActive="true" />
    </li>
    <li class="no-drag col-span-full text-center py-4" ref="loadMoreTrigger" v-element-visibility="onLoadingTriggerVisibility"
      v-if="checkLists.length < total_backend_count">
      <UButton icon="i-heroicons-arrow-path" :loading="loadingInProcess" variant="ghost">
        Load more...
      </UButton>
    </li>
  </ul>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
const checkListStore = useCheckListsStore();
const checkListItemStore = useCheckListsItemStore();
const { checkLists, total_backend_count } = storeToRefs(checkListStore);

import { useDragAndDrop } from "@formkit/drag-and-drop/vue";
import { animations } from "@formkit/drag-and-drop";
import { CheckListEditModal } from "#components";

import { vElementVisibility } from "@vueuse/components";

const loadingTriggerIsVisible = ref(false);
const loadingInProcess = ref(false);

const overlayCheckListEditor = useOverlay()
const modalCheckListEditor = overlayCheckListEditor.create(CheckListEditModal)


// Main reactive source for lists
const checklists = ref<CheckListType[]>([]);
//const [checkListBoard, dragCheckLists] = useDragAndDrop(checklists, { plugins: [animations()] })
const [checkListBoard, dragCheckLists] = useDragAndDrop(checklists, {
  onDragend: (event) => {
    (async () => {
      const draggedItem = event.draggedNode.data.value as CheckListType;
      const allItems = event.values as CheckListType[];
      console.log("WE MOVE");
      checkListStore.reorderCheckLists(allItems, draggedItem);
    })();
    //valuesChanged.value = `${event.previousValues} -> ${event.values}`;
  },
  draggable: (el) => !(el && el.classList.contains("no-drag")),
  plugins: [animations()],
});

// Sync from store if changes are detected
watchEffect(() => {
  const latestFromStore = checkListStore.getCheckLists(false);
  // Only copy if it's different to avoid resetting drag
  checklists.value = latestFromStore.map(item => ({ ...item }));
});

async function onLoadingTriggerVisibility(state: boolean) {
  loadingTriggerIsVisible.value = state;
  if (state == true && loadingInProcess.value == false) {
    loadingInProcess.value = true;
    while (loadingTriggerIsVisible.value) {
      // load pages of checklist until loading trigger is not visible anymore
      const newChecklists = await checkListStore.fetchNextPage();
      if (newChecklists) {
        dragCheckLists.value = dragCheckLists.value.concat(newChecklists!);
      }
    }

    loadingInProcess.value = false;
  }
}

function openCheckListEditor(checkListId: string) {
  modalCheckListEditor.open({
    checkListId: checkListId,
    'onClose': () => modalCheckListEditor.close()
  })
}
</script>
<style scoped>
ul {
  margin: 0 auto;
  width: 100%;
  max-width: 1400px;
  padding: 0 16px;
}
.checklist-preview:hover {
  transform: translateY(-2px);
}
</style>
