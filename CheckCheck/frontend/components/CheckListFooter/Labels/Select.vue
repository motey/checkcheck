<template>
  <UPopover v-model:open="isOpen">
    <CheckListColoredButton variant="ghost" icon="i-lucide-tags" :checkListId="checkListId" @click.stop />

    <template #content>
      <UContainer v-if="isOpen" class="bg-transparent p-4 lg:p-2 sm:p-2">
        <UInput v-model="query" placeholder="Enter Label name..." />
        <UCheckboxGroup
          v-model="selectedItems"
          value-key="id"
          :items="filteredItems"
          class="pl-1 lg:pl-1 sm:pl-1 pt-2 lg:pt-2 sm:pt-2"
        />

        <USeparator
          v-if="query && !filteredItems.some((obj) => obj.label === query)"
          class="p-0 lg:p-0 sm:p-0 pt-2 lg:pt-2 sm:pt-2"
        ></USeparator>
        <UTooltip text="Create New Label">
          <UButton
            v-if="query && !filteredItems.some((obj) => obj.label === query)"
            icon="i-lucide-plus"
            color="neutral"
            variant="link"
            size="md"
            class="p-0 lg:p-0 sm:p-0 pt-2 lg:pt-2 sm:pt-2 pl-0.5 lg:pl-0.5 sm:pl-0.5"
          >
            {{ query }}
          </UButton>
        </UTooltip>
      </UContainer>
    </template>
  </UPopover>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import type { CheckboxGroupItem, CheckboxGroupValue } from "@nuxt/ui";
import { useCheckListsLabelStore } from "@/stores/label";
const isOpen = ref(false);
// Props
const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

// Store
const labelStore = useCheckListsLabelStore();

// Reactive search query
const query = ref<string>("");

// Reactive selected items
const selectedItems = ref<CheckboxGroupValue[]>([]);
// Flag to indicate initial data load
const initialized = ref(false);
// Map store labels into checkbox items
const items = computed<CheckboxGroupItem[]>(() =>
  labelStore.labels
    .map((label) => ({
      label: label.display_name ?? "",
      id: label.id,
    }))
    
);

// Filter items by search query
const filteredItems = computed<CheckboxGroupItem[]>(() => {
  if (!query.value) return items.value;
  return items.value.filter((item) => item.label.toLowerCase().includes(query.value.toLowerCase()));
});

// Load checklist labels when component mounts or checkListId changes
const loadSelectedItems = async () => {
  const checklistLabels = await labelStore.getChecklistLabels(props.checkListId);
  selectedItems.value = Array.isArray(checklistLabels)
    ? (checklistLabels.map((label) => label.id) as CheckboxGroupValue[])
    : [];
    initialized.value = true; // Allow watcher to run after initial load
};

// Watch for selection changes AFTER initial load
watch(
  selectedItems,
  async (newVal, oldVal) => {
    if (!initialized.value) return;

    const added = newVal.filter((id) => !oldVal.includes(id));
    const removed = oldVal.filter((id) => !newVal.includes(id));

    for (const id of added) {
      await labelStore.addCheckListLabel(props.checkListId, id);
    }

    for (const id of removed) {
      await labelStore.removeCheckListLabel(props.checkListId, id);
    }
  }
);

// Initial load
onMounted(loadSelectedItems);



</script>
