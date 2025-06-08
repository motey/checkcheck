<template>
  <div class="flex items-center space-x-4">
    <!-- Text on the left -->

    <!-- Colored square with rounded corners and icon inside -->
    <div class="relative bg-amber-400 rounded-lg p-0 w-16 h-16 flex items-center justify-center">
      <UIcon name="i-lucide-list-checks" class="size-12 text-white" />
      <!-- Folded corner -->
      <div class="absolute top-0 right-0 w-6 h-6 overflow-hidden">
        <div class="w-full h-full bg-white/100 dark:bg-gray-900/100 clip-path-folded-corner"></div>
      </div>
    </div>
    <h1 class="text-5xl font-bold font-sans tracking-tight">CheckCheck</h1>
  </div>
</template>
<script setup></script>

<style scoped>
.clip-path-folded-corner {
  clip-path: polygon(100% 0, 100% 100%, 0 0);
}
</style>
