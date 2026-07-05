<template>
  <div class="flex justify-center">
    <UButtonGroup>
      <CheckListFooterButtonRestore v-if="isArchived" :checkListId="checkListId" />
      <CheckListFooterButtonArchive :checkListId="checkListId" />
      <CheckListFooterButtonColor :checkListId="checkListId" />
      <CheckListFooterButtonShare :checkListId="checkListId" />
      <CheckListFooterLabelsSelect :checkListId="checkListId" />
      <CheckListFooterButtonMoreOptionsMenu :checkListId="checkListId" />
    </UButtonGroup>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useCheckListsStore } from "@/stores/checklist";

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

const checkListsStore = useCheckListsStore();
// The Restore button only makes sense for an archived card (shown in the
// Archive view); a live card just gets the normal action set.
const isArchived = computed(() => checkListsStore.get(props.checkListId)?.position.archived ?? false);
</script>

<style scoped></style>
