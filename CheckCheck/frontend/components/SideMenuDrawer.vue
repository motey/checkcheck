<template>
  <UDrawer v-model:open="open" direction="left" :handle="false" :ui="{ content: 'w-56' }">
    <template #content>
      <!-- w-full: DrawerContent is flex-row-reverse, so without an explicit
           width this column shrinks to its content and gets packed to the right
           edge, leaving an empty gutter down the left of the drawer. -->
      <div class="h-full w-full flex flex-col">
        <!-- Panel header: logo + close, so the drawer reads as a first-class surface -->
        <div class="flex items-center justify-between gap-2 p-3 border-b border-default">
          <NuxtLink to="/" class="flex items-center gap-2 min-w-0" @click="open = false">
            <Logo />
          </NuxtLink>
          <UButton
            variant="ghost"
            color="neutral"
            size="sm"
            icon="i-lucide-x"
            aria-label="Close menu"
            data-testid="drawer-close"
            @click="open = false"
          />
        </div>
        <SideMenuNav :collapsed="false" class="flex-1 min-h-0" />
      </div>
    </template>
  </UDrawer>
</template>

<script setup lang="ts">
import { watch } from "vue";
import { useRoute } from "vue-router";

const open = defineModel<boolean>("open", { default: false });
const route = useRoute();

watch(() => route.fullPath, () => {
  open.value = false;
});
</script>
