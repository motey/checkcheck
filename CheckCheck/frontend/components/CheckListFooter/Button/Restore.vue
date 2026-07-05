<template>
  <UTooltip text="Restore" :popper="{ arrow: true }">
    <CheckListColoredButton
      variant="ghost"
      :padded="false"
      icon="i-lucide-archive-restore"
      :checkListId="checkListId"
      data-testid="card-restore"
      @click.stop="restore()"
    />
  </UTooltip>
</template>

<script setup lang="ts">
import { useCheckListsStore } from "@/stores/checklist";

const checkListsStore = useCheckListsStore();
const toast = useToast();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

async function restore() {
  await checkListsStore.archive(props.checkListId, false);
  toast.add({ title: "List restored", color: "neutral" });
}
</script>

<style scoped></style>
