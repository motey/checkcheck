<template>
  <nav class="flex flex-col h-full overflow-hidden">
    <div class="flex flex-col gap-0.5 p-2 flex-1 overflow-y-auto">

      <!-- Home -->
      <UTooltip :text="'Home'" :disabled="!collapsed" side="right">
        <NuxtLink
          :to="{ path: '/', query: {} }"
          class="relative flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
          :class="isHome ? 'bg-elevated font-medium' : 'text-muted'"
        >
          <span
            v-if="isHome"
            class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full bg-primary"
          />
          <UIcon name="i-lucide-house" class="shrink-0 size-5" :class="{ 'text-primary': isHome }" />
          <span v-if="!collapsed" class="truncate">Home</span>
        </NuxtLink>
      </UTooltip>

      <!-- Shared section -->
      <div v-if="!collapsed" class="px-2 pt-4 pb-1">
        <span class="text-xs font-semibold text-muted uppercase tracking-wider">Shared</span>
      </div>
      <div v-else class="border-t my-2 mx-1" />

      <UTooltip
        v-for="opt in sharedOptions"
        :key="opt.value"
        :text="opt.label"
        :disabled="!collapsed"
        side="right"
      >
        <NuxtLink
          :data-testid="`shared-filter-${opt.value}`"
          :to="sharedLinkTo(opt.value)"
          class="relative flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
          :class="route.query.shared === opt.value ? 'bg-elevated font-medium' : 'text-muted'"
        >
          <span
            v-if="route.query.shared === opt.value"
            class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full bg-primary"
          />
          <UIcon
            :name="opt.icon"
            class="shrink-0 size-5"
            :class="{ 'text-primary': route.query.shared === opt.value }"
          />
          <span v-if="!collapsed" class="truncate">{{ opt.label }}</span>
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
            :to="{ path: '/', query: { ...route.query, label: label.id } }"
            class="relative flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
            :class="route.query.label === label.id ? 'font-medium' : 'text-muted'"
            :style="route.query.label === label.id ? labelRowStyle(label) : undefined"
          >
            <span
              v-if="route.query.label === label.id"
              class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full"
              :style="{ backgroundColor: labelColors(label).accent }"
            />
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
          :to="{ path: '/', query: { ...route.query, editlabels: 'true' } }"
          class="flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
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

const isHome = computed(
  () =>
    route.path === "/" &&
    !route.query.label &&
    !route.query.editlabels &&
    !route.query.search &&
    !route.query.shared
);

// Mutually-exclusive share filters (a card is either owned by you or not, so it
// can never be both). Driven by ?shared=with_me|by_me, ANDing with any label.
const sharedOptions = [
  { value: "with_me", label: "Shared with me", icon: "i-lucide-users" },
  { value: "by_me", label: "Shared by me", icon: "i-lucide-share-2" },
] as const;

// Toggle: clicking the active filter clears it; otherwise activate it (keeping
// the rest of the query, e.g. an active label, so the filters add up).
function sharedLinkTo(value: string) {
  const query = { ...route.query };
  if (query.shared === value) {
    delete query.shared;
  } else {
    query.shared = value;
  }
  return { path: "/", query };
}

// Resolve a label's background + accent hexes for the current color mode, with a
// neutral fallback for labels that have no color assigned.
function labelColors(label: LabelType) {
  const color = colorStore.colors.find((c) => c.id === label.color_id);
  const dark = colorMode.value === "dark";
  if (!color) {
    return {
      bg: dark ? "#555" : "#ddd",
      accent: dark ? "#666" : "#ccc",
    };
  }
  return {
    bg: dark ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex,
    accent: dark ? color.accentcolor_dark_hex : color.accentcolor_light_hex,
  };
}

function labelDotStyle(label: LabelType) {
  const { bg, accent } = labelColors(label);
  return { backgroundColor: bg, borderColor: accent };
}

// When a label filter is active, tint the whole row with that label's own color
// so the active cue matches the card colors on the board.
function labelRowStyle(label: LabelType) {
  return { backgroundColor: labelColors(label).bg };
}
</script>
