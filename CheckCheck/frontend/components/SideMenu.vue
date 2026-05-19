<template>
  <aside
    class="hidden md:flex flex-col h-full border-r overflow-hidden transition-all duration-200 shrink-0"
    :class="collapsed ? 'w-14' : 'w-56'"
  >
    <SideMenuNav :collapsed="collapsed" class="flex-1 min-h-0" />

    <!-- Collapse toggle -->
    <div class="p-2 border-t flex" :class="collapsed ? 'justify-center' : 'justify-end'">
      <UButton
        variant="ghost"
        color="neutral"
        size="sm"
        :icon="collapsed ? 'i-lucide-chevrons-right' : 'i-lucide-chevrons-left'"
        @click="collapsed = !collapsed"
      />
    </div>
  </aside>
</template>

<script setup lang="ts">
const collapsed = defineModel<boolean>("collapsed", { default: false });
</script>
