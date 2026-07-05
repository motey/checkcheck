<template>
  <CheckListFooterLabelsSelectedList :checkListId="checkListId"></CheckListFooterLabelsSelectedList>
  <CheckListFooterToolbar :checkListId="checkListId" />
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
