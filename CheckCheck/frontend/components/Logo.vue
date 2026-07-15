<template>
  <div class="flex items-center gap-2">
    <!-- Colored tile with the check icon -->
    <div
      class="relative bg-amber-400 rounded-lg flex items-center justify-center shrink-0"
      :class="size === 'full' ? 'size-12' : 'size-8'"
    >
      <UIcon name="i-lucide-list-checks" :class="[size === 'full' ? 'size-8' : 'size-5', 'text-amber-900']" />
      <!-- Folded corner -->
      <div class="absolute top-0 right-0 overflow-hidden" :class="size === 'full' ? 'w-4 h-4' : 'w-3 h-3'">
        <div class="w-full h-full bg-default clip-path-folded-corner"></div>
      </div>
    </div>
    <!-- Wordmark: hidden on xs in the bar, always shown when full -->
    <h1
      class="font-bold font-sans tracking-tight"
      :class="size === 'full' ? 'text-2xl' : 'hidden sm:block text-lg sm:text-xl'"
    >
      CheckCheck
    </h1>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ size?: "bar" | "full" }>(), {
  size: "bar",
});
</script>

<style scoped>
.clip-path-folded-corner {
  clip-path: polygon(100% 0, 100% 100%, 0 0);
}
</style>
