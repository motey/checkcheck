<template>
  <div v-if="colorsReady && labels.length" class="flex flex-wrap items-center gap-2">
    <UIcon name="i-lucide-tags" class="shrink-0 text-dimmed"></UIcon>
    <CheckListFooterLabelsItem
      v-for="label in labels"
      :labelId="label.id"
      :fallbackColor="checkListColor"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useCheckListsStore } from "@/stores/checklist";

const props = defineProps({
  checkListId: { type: String, required: true },
});

const checkListStore = useCheckListsStore();
const checkListColorSchemeStore = useCheckListsColorSchemeStore();

const checkList = computed(() => checkListStore.get(props.checkListId));
const labels = computed(() => checkList.value?.labels ?? []);
const checkListColor = computed(() => checkList.value?.color ?? undefined);
const colorsReady = computed(() => checkListColorSchemeStore.colors.length > 0);
</script>
