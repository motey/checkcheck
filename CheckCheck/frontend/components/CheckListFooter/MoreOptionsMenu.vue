<template>
  <UDropdownMenu :items="items" :content="{ align: 'start' }" :ui="{ content: 'w-48' }">
    <UTooltip text="Options" @click.stop>
      <UButton size="md" color="neutral" variant="ghost" icon="i-lucide-ellipsis-vertical" />
    </UTooltip>
  </UDropdownMenu>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import type { DropdownMenuItem } from "@nuxt/ui";
import { useDebounceFn } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";

const checkListsStore = useCheckListsStore();
const checkListsItemStore = useCheckListsItemStore();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

const showBookmarks = ref(true);
const showHistory = ref(false);
const showDownloads = ref(false);

const items = computed(
  () =>
    [
      {
        label: "Interface",
        icon: "i-lucide-app-window",
        type: "label" as const,
      },
      {
        type: "separator" as const,
      },
      {
        label: "Show Bookmarks",
        icon: "i-lucide-bookmark",
        type: "checkbox" as const,
        checked: showBookmarks.value,
        onUpdateChecked(checked: boolean) {
          showBookmarks.value = checked;
        },
        onSelect(e: Event) {
          e.preventDefault();
        },
      },
      {
        label: "Show History",
        icon: "i-lucide-clock",
        type: "checkbox" as const,
        checked: showHistory.value,
        onUpdateChecked(checked: boolean) {
          showHistory.value = checked;
        },
      },
      {
        label: "Show Downloads",
        icon: "i-lucide-download",
        type: "checkbox" as const,
        checked: showDownloads.value,
        onUpdateChecked(checked: boolean) {
          showDownloads.value = checked;
        },
      },
    ] satisfies DropdownMenuItem[]
);
</script>
