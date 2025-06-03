<template>
  <NuxtLayout>
    <div class="test max-h-[100vh] bg-amber-700">
      <Navbar />
      <div class="flex">
        <div class="flex-none"><SideMenu/></div>
        <div class="flex-initial"><CheckListBoard /></div>
      </div>
      <div>
        
        footer
      </div>
    </div>
  </NuxtLayout>
</template>
<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
import SideMenu from "~/components/SideMenu.vue";
</script>

<style scoped></style>
