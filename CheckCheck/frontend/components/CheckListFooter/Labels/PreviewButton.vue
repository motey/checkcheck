<template>
    <div v-if="colorsReady" class="flex flex-wrap gap-2">
      <UButton
        v-for="label in labels"
        :key="label.id"
        size="xs"
        class="font-bold rounded-full"
        :style="getLabelStyle(label.color_id)"
      >
        {{ label.display_name }}
      </UButton>
    </div>
  </template>
  
  <script setup lang="ts">
  import { ref, computed, onMounted } from 'vue';
  import { useColorMode } from '#imports';
  import { useCheckListsLabelStore } from '@/stores/label';

  
  const colorMode = useColorMode();
  const labelStore = useCheckListsLabelStore();
  const checkListColorSchemeStore = useCheckListsColorSchemeStore();
  
  const props = defineProps({
    checkListId: {
      type: String,
      required: true,
    },
  });
  
  // Wait for labels to load immediately
  const labels = ref(await labelStore.getChecklistLabels(props.checkListId));
  
  // Check if colors are loaded
  const colorsReady = computed(() => checkListColorSchemeStore.colors.length > 0);
  
  // Style helper
  const getLabelStyle = (colorId: string | null) => {
    const color = checkListColorSchemeStore.colors.find(c => c.id === colorId);
    return {
      backgroundColor: color
        ? (colorMode.value === 'dark'
          ? color.backgroundcolor_dark_hex
          : color.backgroundcolor_light_hex)
        : 'transparent',
      borderColor: 'transparent',
      transition: 'border-color 0.3s',
    };
  };
  </script>
  