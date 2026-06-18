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
import { CheckListEditModal, LabelManagerModal } from "#components";
import { useSync } from "~/composables/useSync";
import { useAppRoute } from "~/composables/useAppRoute";
import { useCheckListsStore } from "@/stores/checklist";

// This page also responds to `/card/<cardId>` (see alias below). The board and
// the modals stay mounted across that path change, so an opened card is just a
// URL-reflected overlay on top of the board — shareable and back-button aware.
definePageMeta({
  alias: ["/card/:cardId"],
});

const sidebarCollapsed = ref(false);
const mobileNavOpen = ref(false);

const { connect, disconnect } = useSync();
onMounted(connect);
onUnmounted(disconnect);

const { cardId, editLabels, closeCard, closeLabelEditor } = useAppRoute();
const checkListStore = useCheckListsStore();

// --- Label editor modal, driven by ?editlabels=true ------------------------
const labelEditorOverlay = useOverlay();
const labelEditorModal = labelEditorOverlay.create(LabelManagerModal);
let labelEditorOpening = false;

watch(
  editLabels,
  async (open) => {
    if (open) {
      if (labelEditorOpening) return;
      labelEditorOpening = true;
      await labelEditorModal.open();
      labelEditorOpening = false;
      // Resolved => the user closed the modal. Strip editlabels from the URL.
      closeLabelEditor();
    } else {
      // editlabels disappeared from the URL (e.g. browser back while open).
      labelEditorModal.close();
    }
  },
  { immediate: true }
);

// --- Card editor modal, driven by the /card/<cardId> path ------------------
const cardEditorOverlay = useOverlay();
const cardEditorModal = cardEditorOverlay.create(CheckListEditModal);
let cardOpening = false;

watch(
  cardId,
  async (id) => {
    if (id) {
      if (cardOpening) return;
      cardOpening = true;
      // Make sure the card exists in the store before showing it — supports
      // deep-linking / sharing a card that isn't on the current page yet.
      await checkListStore.fetch(id).catch(() => {});
      await cardEditorModal.open({ checkListId: id });
      cardOpening = false;
      // Resolved => the user closed the modal. Return to the board.
      closeCard();
    } else {
      // Card path disappeared (e.g. browser back while open).
      cardEditorModal.close();
    }
  },
  { immediate: true }
);
</script>

<style scoped></style>
