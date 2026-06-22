<template>
  <UPopover v-model:open="open">
    <!-- Trigger is provided by the caller (a swatch, a palette button, …) -->
    <slot :open="open" />

    <template #content>
      <div class="flex flex-wrap gap-1.5 p-2 max-w-56">
        <!-- No color -->
        <UTooltip text="No color">
          <button
            type="button"
            class="size-6 rounded-full border-2 flex items-center justify-center hover:scale-110 transition-transform"
            :class="{ 'ring-2 ring-offset-1 ring-primary': modelValue == null }"
            :style="{ borderColor: isDark ? '#666' : '#ccc', backgroundColor: isDark ? '#333' : '#eee' }"
            @click.stop="select(null)"
          >
            <UIcon name="i-lucide-x" class="text-xs" />
          </button>
        </UTooltip>

        <UTooltip
          v-for="color in colorStore.colors"
          :key="color.id"
          :text="color.display_name"
        >
          <button
            type="button"
            class="size-6 rounded-full border-2 hover:scale-110 transition-transform"
            :class="{ 'ring-2 ring-offset-1 ring-primary': color.id === modelValue }"
            :style="optionStyle(color)"
            @click.stop="select(color.id)"
          />
        </UTooltip>
      </div>
    </template>
  </UPopover>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useCheckListsColorSchemeStore } from "@/stores/color";

const props = defineProps<{
  /** Currently selected color id, or null for "no color". */
  modelValue: string | null;
  /** Close the popover after a selection (default true). */
  closeOnSelect?: boolean;
}>();

const emit = defineEmits<{ "update:modelValue": [string | null] }>();

const colorMode = useColorMode();
const colorStore = useCheckListsColorSchemeStore();

const open = ref(false);
const isDark = computed(() => colorMode.value === "dark");

function optionStyle(color: ChecklistColorSchemeType) {
  return {
    backgroundColor: isDark.value ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex,
    borderColor: isDark.value ? color.accentcolor_dark_hex : color.accentcolor_light_hex,
  };
}

function select(colorId: string | null) {
  emit("update:modelValue", colorId);
  if (props.closeOnSelect !== false) open.value = false;
}
</script>
