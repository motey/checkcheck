<template>
  <UPopover v-model:open="isOpen" close="isOpen = false">
    <CheckListColoredButton variant="ghost" icon="i-lucide-tags" :checkListId="checkListId" @click.stop />

    <template #content>
      <UContainer v-if="isOpen" class="bg-transparent p-4 lg:p-2 sm:p-2">
        <UInput v-model="query" placeholder="Enter Label name..." />
        <div v-if="loading" class="grid gap-2 mt-2">
          <div  class="flex gap-2"><USkeleton class="h-4 w-[16px]" /><USkeleton class="h-4 w-[90px]" /></div>
          <div  class="flex gap-2"><USkeleton class="h-4 w-[16px]" /><USkeleton class="h-4 w-[90px]" /></div>
          <div  class="flex gap-2"><USkeleton class="h-4 w-[16px]" /><USkeleton class="h-4 w-[90px]" /></div>
        </div>
        <div v-else>
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
        </div>
        <UTooltip text="Create New Label">
          <UButton
            v-if="query && !filteredItems.some((obj) => obj.label === query)"
            icon="i-lucide-plus"
            color="neutral"
            variant="link"
            size="md"
            class="p-0 lg:p-0 sm:p-0 pt-2 lg:pt-2 sm:pt-2 pl-0.5 lg:pl-0.5 sm:pl-0.5"
            @click="createLabel"
          >
            {{ query }}
          </UButton>
        </UTooltip>
      </UContainer>
    </template>
  </UPopover>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useCheckListsLabelStore } from "@/stores/label";
import type { CheckboxGroupItem, CheckboxGroupValue } from '@nuxt/ui'
const props = defineProps({
  checkListId: { type: String, required: true },
});

const labelStore = useCheckListsLabelStore();
const isOpen = ref(false);
const query = ref("");
const loading = ref(true);
// Prepare checkbox items from store labels (reactive)
const items = computed(() =>
  labelStore.labels.map((label) => ({
    label: label.display_name || "",
    id: label.id,
  }))
);

// Filter items by search query
const filteredItems = computed(() =>
  !query.value
    ? items.value
    : items.value.filter((item) => item.label.toLowerCase().includes(query.value.toLowerCase()))
);

// The selected checkbox values (IDs) for this checklist
const selectedItems = ref<CheckboxGroupValue[]>([]);
const initialized = ref(false);

// Function to load the current checklist's labels
async function loadSelectedItems() {
  const checklistLabels = await labelStore.getChecklistLabels(props.checkListId);
  // Set the selected IDs (this will change selectedItems.value)
  selectedItems.value = Array.isArray(checklistLabels) ? checklistLabels.map((label) => label.id) : [];

}

// Watch for popover open: load data on demand
watch(isOpen, async (open) => {
  if (open) {
    loading.value = true;
    initialized.value = false; // reset skip-logic flag
    try {
      await loadSelectedItems();
    } finally {
      loading.value = false;
    }
  }
});

// Watch for changes in the selected checkboxes (deep to catch array mutations)
watch(
  selectedItems,
  async (newVal, oldVal) => {
    if (!initialized.value) {
      // First run after loading data: skip update logic and mark initialized
      initialized.value = true;
      return;
    }
    // Determine added/removed IDs
    const added = newVal.filter((id) => !oldVal.includes(id));
    const removed = oldVal.filter((id) => !newVal.includes(id));

    // Add new labels
    await Promise.all(added.map((id) => labelStore.addCheckListLabel(props.checkListId, id as string)));
    // Remove unchecked labels
    await Promise.all(removed.map((id) => labelStore.removeCheckListLabel(props.checkListId, id as string)));
  },
  { deep: true }
);

async function createLabel() {
  const newLabelCreate: LabelCreateType = {display_name:query.value}
  const newLabel = await labelStore.createLabel(newLabelCreate)
  await labelStore.addCheckListLabel(props.checkListId, newLabel.id)
  await loadSelectedItems();
  query.value = ""
}
</script>
