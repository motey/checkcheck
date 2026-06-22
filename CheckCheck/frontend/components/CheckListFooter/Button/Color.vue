<template>
  <ColorSwatchPicker :model-value="currentColorId" @update:model-value="setCheckListColor">
    <UTooltip text="Change color">
      <CheckListColoredButton
        variant="ghost"
        icon="i-lucide-palette"
        :checkListId="checkListId"
        :disabled="!canEdit"
        @click.stop
      />
    </UTooltip>
  </ColorSwatchPicker>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsColorSchemeStore } from "@/stores/color";

const checkListColorSchemeStore = useCheckListsColorSchemeStore();
const checkListsStore = useCheckListsStore();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

// Changing the shared card's color requires edit access (P0.1 / usePermissions).
const { can } = usePermissions();
const canEdit = computed(() => can(checkListsStore.get(props.checkListId), "edit"));

const currentColorId = computed(() => checkListsStore.get(props.checkListId)?.color?.id ?? null);

function setCheckListColor(colorId: string | null) {
  if (!canEdit.value) return;
  (async () => {
    await checkListColorSchemeStore.updateChecklistColor(props.checkListId, colorId);
  })();
}
</script>

<style scoped></style>
