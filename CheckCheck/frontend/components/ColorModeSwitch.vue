<template>
  <div data-testid="theme-toggle" class="flex items-center gap-0.5">
    <UButton
      v-for="mode in modes"
      :key="mode.value"
      :icon="mode.icon"
      size="sm"
      square
      :color="colorMode.preference === mode.value ? 'primary' : 'neutral'"
      :variant="colorMode.preference === mode.value ? 'solid' : 'ghost'"
      :aria-label="mode.label"
      :data-active="colorMode.preference === mode.value ? 'true' : undefined"
      @click="setTheme(mode.value)"
    />
  </div>
</template>

<script setup lang="ts">
const colorMode = useColorMode();

const modes = [
  { value: "light", label: "Light", icon: "i-lucide-sun" },
  { value: "dark", label: "Dark", icon: "i-lucide-moon" },
  { value: "system", label: "System", icon: "i-lucide-monitor" },
];

function setTheme(mode: string) {
  colorMode.preference = mode;
}
</script>
