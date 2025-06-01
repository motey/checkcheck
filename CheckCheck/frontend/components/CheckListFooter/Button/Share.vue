<template>
  <UTooltip text="Collaborate" :popper="{ arrow: true }">
    <CheckListColoredButton
        variant="ghost"
        :padded="false"
        icon="i-lucide-users"
        :checkListId="checkListId"
        @click.stop="shareDialog()"
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

function shareDialog() {
  (async () => {
    console.log("NOT IMPLEMENTED")
  })();
}
</script>

<style scoped></style>
