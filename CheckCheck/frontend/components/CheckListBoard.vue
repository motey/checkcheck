<template>
  <ul v-if="dragCheckLists" ref="checkListBoard" class="grid gap-4 grid-cols-[repeat(auto-fill,minmax(16rem,1fr))]">
    <li v-for="checkList in dragCheckLists" :key="checkList.id" class="w-full sm:w-auto checklist-preview">
      <CheckList :checkListId="checkList.id" @click="openCheckListEditor(checkList.id)" :previewModeActive="true" />
    </li>
    <li
      class="no-drag text-center py-4"
      ref="loadMoreTrigger"
      v-element-visibility="onLoadingTriggerVisibility"
      v-if="checkLists.length < total_backend_count"
    >
      <UButton icon="i-heroicons-arrow-path" :loading="loadingInProcess" variant="ghost"> Load more... </UButton>
    </li>
  </ul>
</template>

<script setup lang="ts">
import { useRoute } from "vue-router";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsColorSchemeStore } from "@/stores/color";
import { useCheckListsLabelStore } from "@/stores/label";
import { useDragAndDrop } from "@formkit/drag-and-drop/vue";
import { animations } from "@formkit/drag-and-drop";
import { CheckListEditModal } from "#components";
import { vElementVisibility } from "@vueuse/components";

const route = useRoute();
const checkListStore = useCheckListsStore();
const checkListsColorSchemeStore = useCheckListsColorSchemeStore();
const checkListsLabelStore = useCheckListsLabelStore();
const { checkLists, total_backend_count } = storeToRefs(checkListStore);

const loadingTriggerIsVisible = ref(false);
const loadingInProcess = ref(false);

const overlayCheckListEditor = useOverlay();
const modalCheckListEditor = overlayCheckListEditor.create(CheckListEditModal);

onMounted(async () => {
  await checkListStore.fetchNextPage();
  await checkListsColorSchemeStore.fetchColors();
  await checkListsLabelStore.fetchLabels();
});

const [checkListBoard, dragCheckLists] = useDragAndDrop<CheckListType>([], {
  onDragend: (event) => {
    const draggedItem = event.draggedNode.data.value as CheckListType;
    const allItems = event.values as CheckListType[];
    checkListStore.reorderCheckLists(allItems, draggedItem);
  },
  draggable: (el) => !(el && el.classList.contains("no-drag")),
  plugins: [animations()],
});

// Sync drag list from store whenever filters or store contents change
watchEffect(() => {
  const labelId = (route.query.label as string) || null;
  dragCheckLists.value = checkListStore.getCheckLists({ archived: false, label_id: labelId });
});

async function onLoadingTriggerVisibility(state: boolean) {
  loadingTriggerIsVisible.value = state;
  if (state && !loadingInProcess.value) {
    loadingInProcess.value = true;
    while (loadingTriggerIsVisible.value) {
      await checkListStore.fetchNextPage();
    }
    loadingInProcess.value = false;
  }
}

function openCheckListEditor(checkListId: string) {
  modalCheckListEditor.open({ checkListId });
}
</script>
<style scoped>
ul {
  margin: 0 auto;
  width: 100%;
  /*max-width: 1400px;*/
  padding: 14px 14px;
}
.checklist-preview:hover {
  transform: translateY(-2px);
}
</style>
