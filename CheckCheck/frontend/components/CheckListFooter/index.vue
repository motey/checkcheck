<template>
  <UButtonGroup>
    <CheckListFooterButtonArchive :checkListId="checkListId" />
    <CheckListFooterButtonColor :checkListId="checkListId" />
    <CheckListFooterButtonShare :checkListId="checkListId" />
    <CheckListFooterMoreOptionsMenu :checkListId="checkListId" />
  </UButtonGroup>
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
