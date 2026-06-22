<template>
  <div class="flex items-center gap-2 py-1 px-1">
    <!-- Drag handle -->
    <UIcon
      name="i-lucide-grip-vertical"
      class="label-item-drag-handle cursor-grab text-muted shrink-0"
    />

    <!-- Color swatch / picker -->
    <ColorSwatchPicker :model-value="label.color_id ?? null" @update:model-value="setColor">
      <UTooltip text="Change color">
        <button
          class="size-5 rounded-full border-2 shrink-0 transition-colors"
          :style="swatchStyle"
          @click.stop
        />
      </UTooltip>
    </ColorSwatchPicker>

    <!-- Label name input -->
    <UInput
      v-model="displayName"
      size="sm"
      class="flex-1 min-w-0"
      placeholder="Label name"
      @update:model-value="debouncedSave"
    />

    <!-- Delete button -->
    <UButton
      icon="i-lucide-trash-2"
      variant="ghost"
      color="error"
      size="sm"
      class="shrink-0"
      @click.stop="handleDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { useDebounceFn } from "@vueuse/core";
import type { PropType } from "vue";
import { useCheckListsLabelStore } from "@/stores/label";
import { useCheckListsColorSchemeStore } from "@/stores/color";

const props = defineProps({
  label: { type: Object as PropType<LabelType>, required: true },
});

const colorMode = useColorMode();
const labelStore = useCheckListsLabelStore();
const colorStore = useCheckListsColorSchemeStore();

const isDark = computed(() => colorMode.value === "dark");
const displayName = ref(props.label.display_name ?? "");

const currentColor = computed(() =>
  colorStore.colors.find((c) => c.id === props.label.color_id)
);

const swatchStyle = computed(() => {
  const color = currentColor.value;
  if (!color) {
    return {
      backgroundColor: isDark.value ? "#333" : "#eee",
      borderColor: isDark.value ? "#666" : "#ccc",
    };
  }
  return {
    backgroundColor: isDark.value ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex,
    borderColor: isDark.value ? color.accentcolor_dark_hex : color.accentcolor_light_hex,
  };
});

const debouncedSave = useDebounceFn(async (value: string) => {
  await labelStore.updateLabel(props.label.id, { display_name: value });
}, 600);

async function setColor(colorId: string | null) {
  await labelStore.updateLabel(props.label.id, { color_id: colorId });
}

async function handleDelete() {
  await labelStore.deleteLabel(props.label.id);
}
</script>
