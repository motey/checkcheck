<template>
  <div class="flex space-x-2">
    <UButton
      v-for="mode in modes"
      :key="mode.value"
      :label="`${mode.emoji} ${mode.label}`"
      size="sm"
      :color="colorMode.preference === mode.value ? 'primary' : 'gray'"
      variant="solid"
      @click="setTheme(mode.value)"
    />
  </div>
</template>

<script setup lang="ts">
const colorMode = useColorMode();

const modes = [
  { value: "light", label: "Light", emoji: "🌞" },
  { value: "dark", label: "Dark", emoji: "🌙" },
  { value: "system", label: "System", emoji: "🖥️" },
];

function setTheme(mode: string) {
  colorMode.preference = mode;
}
</script>
