<template>
  <UTooltip @click.stop text="Change Color" :shortcuts="['⌘', 'ToDo']" :popper="{ arrow: true }">
    <UPopover arrow class="border-0">
      <UButton @click.stop :padded="false" color="neutral" variant="ghost" icon="i-lucide-palette" />
      <template #content>
        <UContainer class="flex flex-wrap gap-0 border-0">
          <UTooltip text="No Color" :popper="{ arrow: true }">
            <UButton
              @click.stop="setCheckListColor(null)"
              key="no_color"
              size="xl"
              variant="solid"
              class="rounded-full border-1"
              :style="{
                backgroundColor: 'transparent',
                borderColor: 'transparent',
                transition: 'border-color 0.3s',
                color: 'red',
              }"
              @mouseover="
                (e) => {
                  e.currentTarget.style.borderColor = colorMode.value === 'dark' ? 'white' : 'black';
                  e.currentTarget.style.borderWidth = '1px';
                }
              "
              @mouseleave="
                (e) => {
                  e.currentTarget.style.borderColor = 'transparent';
                  e.currentTarget.style.borderWidth = '1px';
                }
              "
            >
              ❌️
            </UButton>
          </UTooltip>
          <UTooltip v-for="color in checkListColorSchemeStore.colors" :text="color.display_name" :popper="{ arrow: true }">
            <UButton
              
              @click.stop="setCheckListColor(color.id)"
              :key="color.backgroundcolor_light_hex"
              size="xl"
              variant="solid"
              class="rounded-full border-1"
              :style="{
                backgroundColor:
                  colorMode.value === 'dark' ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex,
                borderColor: 'transparent',
                transition: 'border-color 0.3s',
              }"
              @mouseover="
                (e) => {
                  e.currentTarget.style.borderColor =
                    colorMode.value === 'dark' ? color.accentcolor_dark_hex : color.accentcolor_light_hex;
                  e.currentTarget.style.borderWidth = '1px';
                }
              "
              @mouseleave="
                (e) => {
                  e.currentTarget.style.borderColor = 'transparent';
                  e.currentTarget.style.borderWidth = '1px';
                }
              "
            >
              &nbsp;&nbsp;&nbsp;&nbsp;
            </UButton>
          </UTooltip>
        </UContainer>
      </template>
    </UPopover>
  </UTooltip>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsColorSchemeStore } from "@/stores/color";
const colorMode = useColorMode();
const checkListColorSchemeStore = useCheckListsColorSchemeStore();
const checkListsStore = useCheckListsStore();
const allColors = checkListColorSchemeStore.colors;

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});
function setCheckListColor(colorId: string | null) {
  (async () => {
    await checkListColorSchemeStore.updateChecklistColor(props.checkListId, colorId);
  })();
}
</script>

<style scoped></style>
