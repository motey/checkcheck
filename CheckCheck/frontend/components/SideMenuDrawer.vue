<template>
  <UDrawer v-model:open="open" direction="left" :handle="false" :ui="{ content: 'w-56' }">
    <template #content>
      <div class="h-full flex flex-col">
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
