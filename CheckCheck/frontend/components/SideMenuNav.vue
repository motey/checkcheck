<template>
  <nav class="flex flex-col h-full overflow-hidden">
    <div class="flex flex-col gap-0.5 p-2 flex-1 overflow-y-auto">

      <!-- Home -->
      <UTooltip :text="'Home'" :disabled="!collapsed" side="right">
        <NuxtLink
          :to="{ query: {} }"
          class="flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated"
          :class="isHome ? 'bg-elevated font-medium' : 'text-muted'"
        >
          <UIcon name="i-lucide-house" class="shrink-0 size-5" />
          <span v-if="!collapsed" class="truncate">Home</span>
        </NuxtLink>
      </UTooltip>

      <!-- Labels section -->
      <template v-if="labelStore.labels.length">
        <div v-if="!collapsed" class="px-2 pt-4 pb-1">
          <span class="text-xs font-semibold text-muted uppercase tracking-wider">Labels</span>
        </div>
        <div v-else class="border-t my-2 mx-1" />

        <UTooltip
          v-for="label in labelStore.labels"
          :key="label.id"
          :text="label.display_name ?? ''"
          :disabled="!collapsed"
          side="right"
        >
          <NuxtLink
            :to="{ query: { ...route.query, label: label.id } }"
            class="flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated"
            :class="route.query.label === label.id ? 'bg-elevated font-medium' : 'text-muted'"
          >
            <span class="shrink-0 size-4 rounded-full border" :style="labelDotStyle(label)" />
            <span v-if="!collapsed" class="truncate">{{ label.display_name }}</span>
          </NuxtLink>
        </UTooltip>
      </template>

    </div>

    <!-- Footer: Edit Labels -->
    <div class="p-2 border-t">
      <UTooltip text="Edit Labels" :disabled="!collapsed" side="right">
        <NuxtLink
          :to="{ query: { ...route.query, editlabels: 'true' } }"
          class="flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated text-muted"
        >
          <UIcon name="i-lucide-pencil" class="shrink-0 size-5" />
          <span v-if="!collapsed" class="truncate">Edit Labels</span>
        </NuxtLink>
      </UTooltip>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useCheckListsLabelStore } from "@/stores/label";
import { useCheckListsColorSchemeStore } from "@/stores/color";

const props = defineProps<{ collapsed?: boolean }>();

const route = useRoute();
const colorMode = useColorMode();
const labelStore = useCheckListsLabelStore();
const colorStore = useCheckListsColorSchemeStore();

const isHome = computed(() => !route.query.label && !route.query.editlabels && !route.query.search);

function labelDotStyle(label: LabelType) {
  const color = colorStore.colors.find((c) => c.id === label.color_id);
  const dark = colorMode.value === "dark";
  if (!color) {
    return {
      backgroundColor: dark ? "#555" : "#ddd",
      borderColor: dark ? "#666" : "#ccc",
    };
  }
  return {
    backgroundColor: dark ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex,
    borderColor: dark ? color.accentcolor_dark_hex : color.accentcolor_light_hex,
  };
}
</script>
