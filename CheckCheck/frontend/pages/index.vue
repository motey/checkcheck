<template>
  <NuxtLayout>
    <LabelManager></LabelManager>
    <div class="flex flex-col h-screen">
      <Navbar />
      
      <div class="flex flex-1 overflow-hidden">
        <div class="flex-none side-menu-frame h-full overflow-y-auto">
          
          <SideMenu v-model:collapsed="isSideBarcollapsed" v-model:miniMenu="isSideBarMinimized"/>
        </div>
        <div class="flex-1 checklist-content-frame h-full overflow-y-auto">
          <CheckListBoard />
        </div>
      </div>
      <div class="hidden">
        <USwitch v-model="isSideBarMinimized">Menu</USwitch>
      </div>
    </div>
  </NuxtLayout>
</template>
<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
import SideMenu from "~/components/SideMenu.vue";

const isSideBarcollapsed = ref(false)
const isSideBarMinimized = ref(false)

</script>

<style scoped></style>
