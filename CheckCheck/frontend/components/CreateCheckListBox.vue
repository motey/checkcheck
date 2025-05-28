<template>
  <div class="note-taker" @click="openCheckListEditor()">
      <UTextarea :rows="1" color="primary" variant="outline" placeholder="Take a note..." />
  </div>
</template>

<script setup lang="ts">
import { CheckListEditModal } from '#components'
const checkListsStore = useCheckListsStore();
const checkListsItemStore = useCheckListsItemStore();

const overlayCheckListCreate = useOverlay()
const modalCheckListCreate = overlayCheckListCreate.create(CheckListEditModal)

function openCheckListEditor() {
    (async () => {
        const checkList = ref(await checkListsStore.create({} as CheckListCreateType))
        console.log("checkListsItemStore", checkList.value.id, checkListsItemStore.checkListsItems[checkList.value.id])
        modalCheckListCreate.open({
            checkListId: checkList.value.id,
            'onClose': () => modalCheckListCreate.close()
        })
    })();
}
</script>