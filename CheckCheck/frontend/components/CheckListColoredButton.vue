<template>
  <UButton
    @mouseover="handleMouseOver"
    @mouseleave="handleMouseLeave"
    :style="{
      color: textColor,
    }"
    v-bind="uButtonProps"
  >
    <slot />
  </UButton>
</template>

<script setup lang="ts">
import { computed, toRefs } from "vue";
import { UButton } from "#components";
import { ref, defineExpose } from "vue";
import { colord } from "colord";
const checkListsStore = useCheckListsStore();

// Inherit all UButton props
const colorMode = useColorMode();
// Expose the ref to allow parent components to access it


const props = defineProps({
  ...UButton.props,
  checkListId: { type: String }, // your custom prop
});
const { checkListId, ...uButtonProps } = props;
const checkList = ref(checkListsStore.get(props.checkListId));
const textColor = computed(() => {
  const { color } = checkList.value!;
  const isDarkModeEnabled = colorMode.value === "dark";

  if (color) {
    return isDarkModeEnabled ? color.textcolor_dark_hex : color.textcolor_light_hex;
  }
  // Checklist has not color theme applied. lets just return a contrast color the background
  return isDarkModeEnabled ? "#ffffff" : "#000000";
});
const backgroundColor = computed(() => {
  const { color } = checkList.value!;
  const isDarkModeEnabled = colorMode.value === "dark";
  if (color) {
    return isDarkModeEnabled ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex;
  }
  // Checklist has not color theme applied. lets just return a contrast color the background
  return isDarkModeEnabled ? "#ffffff" : "#000000";
});
// Hover logic (or use a composable here)
function handleMouseOver(e: MouseEvent) {
  if (e.target !== e.currentTarget) return;
  const target = e.currentTarget as HTMLElement;
  const transparentBackgroundColor = colord(textColor.value).alpha(0.1).toHex();
  target.style.setProperty("background-color", transparentBackgroundColor, "important");
}
function handleMouseLeave(e: MouseEvent) {
  if (e.target !== e.currentTarget) return;
  const target = e.currentTarget as HTMLElement;
  target.style.setProperty("background-color", "transparent", "important");
}
</script>
