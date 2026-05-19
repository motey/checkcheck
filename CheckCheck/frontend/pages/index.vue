<template>
  <NuxtLayout>
    <div class="flex flex-col h-screen">
      <Navbar @toggle-sidebar="mobileNavOpen = !mobileNavOpen" />

      <div class="flex flex-1 overflow-hidden">
        <SideMenu v-model:collapsed="sidebarCollapsed" />
        <main class="flex-1 h-full overflow-y-auto">
          <CheckListBoard />
        </main>
      </div>
    </div>

    <!-- Mobile nav drawer (portaled, lives outside the flex flow) -->
    <SideMenuDrawer v-model:open="mobileNavOpen" />
  </NuxtLayout>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { LabelManagerModal } from "#components";
import { useSync } from "~/composables/useSync";

const route = useRoute();
const router = useRouter();
const sidebarCollapsed = ref(false);
const mobileNavOpen = ref(false);

const { connect, disconnect } = useSync();
onMounted(connect);
onUnmounted(disconnect);

const labelEditorOverlay = useOverlay();
const labelEditorModal = labelEditorOverlay.create(LabelManagerModal);

// Guard so a pending open() can't be double-triggered
let modalOpening = false;

watch(
  () => route.query.editlabels,
  async (val) => {
    if (val === "true") {
      if (modalOpening) return;
      modalOpening = true;
      await labelEditorModal.open();
      modalOpening = false;
      // Modal was closed by the user — strip editlabels from URL without
      // adding a history entry (replace, not push).
      if (route.query.editlabels === "true") {
        const { editlabels: _, ...rest } = route.query;
        router.replace({ query: rest });
      }
    } else {
      // editlabels disappeared from URL (e.g. browser back while modal open)
      labelEditorModal.close();
    }
  },
  { immediate: true }
);
</script>

<style scoped></style>
