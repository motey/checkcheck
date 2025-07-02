<template>
  <UPopover>
    <CheckListColoredButton
      variant="ghost"
      icon="i-lucide-tags"
      :checkListId="checkListId"
      @click.stop
    />

    <template #content>
      <UContainer class="bg-transparent p-4 lg:p-2 sm:p-2">
        <UInput v-model="query" placeholder="Search labels..." />
        <UCheckboxGroup
          v-model="selectedIds"
          :items="filteredItems"
        />
      </UContainer>
    </template>
  </UPopover>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import type { CheckboxGroupItem, CheckboxGroupValue } from '@nuxt/ui';
import { useCheckListsLabelStore } from '@/stores/label';

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
const query = ref<string>('');

// Map store labels into checkbox items
const items = computed<CheckboxGroupItem[]>(() =>
  labelStore.labels
    .map(label => ({
      label: label.display_name ?? '',
      id: label.id,
    }))
    .sort((a, b) => {
      const la = labelStore.labels.find(l => l.id === a.id)?.sort_order ?? 0;
      const lb = labelStore.labels.find(l => l.id === b.id)?.sort_order ?? 0;
      return la - lb;
    })
);

// Filter items by search query
const filteredItems = computed<CheckboxGroupItem[]>(() => {
  if (!query.value) return items.value;
  return items.value.filter(item =>
    item.label.toLowerCase().includes(query.value.toLowerCase())
  );
});

// Selected checkbox IDs
const selectedIds = ref<CheckboxGroupValue[]>([]);

// Load initial selected label IDs
const initialLabels = await labelStore.getChecklistLabels(props.checkListId);
// getChecklistLabels returns an array of label objects; extract IDs
selectedIds.value = Array.isArray(initialLabels)
  ? initialLabels.map(label => label.id) as CheckboxGroupValue[]
  : [] as CheckboxGroupValue[];

</script>
