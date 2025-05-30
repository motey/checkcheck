<template>
  <UTooltip text="Collaborate" :shortcuts="['⌘', 'ToDo']" :popper="{ arrow: true }">
    <UButton
      :padded="false"
      color="neutral"
      variant="outline"
      icon="i-lucide-users"
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
