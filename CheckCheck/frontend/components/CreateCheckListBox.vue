<template>
  
  <div class="flex space-x-2">
  <UInput icon="i-lucide-search" size="xl" variant="outline" placeholder="Search..." />
  <UButton icon="i-lucide-list-plus" size="xl" color="primary" @click="openCheckListEditor()" variant="solid"
    >New Check List</UButton
  >
</div>
</template>

<script setup lang="ts">
import { CheckListEditModal } from "#components";
const checkListsStore = useCheckListsStore();
const checkListsItemStore = useCheckListsItemStore();

const overlayCheckListCreate = useOverlay();
const modalCheckListCreate = overlayCheckListCreate.create(CheckListEditModal);

function openCheckListEditor() {
  (async () => {
    const checkList = ref(await checkListsStore.create({} as CheckListCreateType));
    console.log("checkListsItemStore", checkList.value.id, checkListsItemStore.checkListsItems[checkList.value.id]);
    modalCheckListCreate.open({
      checkListId: checkList.value.id,
    });
  })();
}
</script>
