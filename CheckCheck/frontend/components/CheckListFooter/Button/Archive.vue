<template>
  <UTooltip text="Archive" :popper="{ arrow: true }">
    <CheckListColoredButton
        variant="ghost"
        :padded="false"
        icon="i-lucide-trash-2"
        :checkListId="checkListId"
        @click.stop="archiveCheckList()"
      />
  </UTooltip>
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

function archiveCheckList() {
  (async () => {
    await checkListsStore.archive(props.checkListId);
  })();
}
</script>

<style scoped></style>
