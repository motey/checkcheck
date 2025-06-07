<template>
  <CheckListFooterLabelsPreviewButton :checkListId="checkListId"></CheckListFooterLabelsPreviewButton>
  <div class="flex justify-center">
  <UButtonGroup>
    <CheckListFooterButtonArchive :checkListId="checkListId" />
    <CheckListFooterButtonColor :checkListId="checkListId" />
    <CheckListFooterButtonShare :checkListId="checkListId" />
    <CheckListFooterButtonMoreOptionsMenu :checkListId="checkListId" />
  </UButtonGroup>
</div>
  
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useCheckListsStore } from "@/stores/checklist";

const checkListsStore = useCheckListsStore();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
  
});
</script>

<style scoped></style>
