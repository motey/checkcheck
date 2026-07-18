<template>
  <UDropdownMenu :items="items" :content="{ align: 'end' }" :ui="{ content: 'min-w-56' }">
    <UTooltip text="Options" @click.stop>
      <CheckListColoredButton variant="ghost" icon="i-lucide-ellipsis-vertical" :checkListId="checkListId" />
    </UTooltip>
  </UDropdownMenu>
</template>

<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import { useCheckListsStore } from "@/stores/checklist";

const checkListsStore = useCheckListsStore();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});
const checkList = computed(() => checkListsStore.get(props.checkListId));

const items = computed(
  () =>
    [
      {
        label: "Separate checked items",
        icon: "i-lucide-list-todo",
        type: "checkbox" as const,
        checked: checkList.value?.checked_items_seperated ?? false,
        onUpdateChecked(checked: boolean) {
          checkListsStore.update(props.checkListId, { checked_items_seperated: checked } as CheckListUpdateType);
        },
        onSelect(e: Event) {
          e.preventDefault();
        },
      },
      {
        label: "Suggest existing items",
        icon: "i-lucide-list-checks",
        type: "checkbox" as const,
        checked: checkList.value?.suggest_existing_items ?? true,
        onUpdateChecked(checked: boolean) {
          checkListsStore.update(props.checkListId, { suggest_existing_items: checked } as CheckListUpdateType);
        },
        onSelect(e: Event) {
          e.preventDefault();
        },
      },
    ] satisfies DropdownMenuItem[]
);
</script>
