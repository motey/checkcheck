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

    <!-- Card editor, driven by the /card/<cardId> path. Declarative (single
         instance) so it can't double-mount the way the old useOverlay wiring
         did — that double-mount was the "card reopens / needs multiple clicks
         to close" bug. -->
    <CheckListEditModal v-if="cardModalId" :checkListId="cardModalId" v-model:open="cardModalOpen" />

    <!-- Label editor, driven by ?editlabels=true -->
    <LabelManagerModal v-model:open="labelEditorOpen" />
  </NuxtLayout>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useSync } from "~/composables/useSync";
import { useAppRoute } from "~/composables/useAppRoute";
import { useCheckListsStore } from "@/stores/checklist";
import { useUserStore } from "@/stores/user";
import { usePublicConfigStore } from "@/stores/publicConfig";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { runBackgroundSync, reconcileAccount } from "@/utils/localSnapshot";

// This page also responds to `/card/<cardId>` (see alias below). The board and
// the modals stay mounted across that path change, so an opened card is just a
// URL-reflected overlay on top of the board — shareable and back-button aware.
definePageMeta({
  alias: ["/card/:cardId"],
  // Stable key so the board (and its FormKit drag instances) is NOT torn down
  // and rebuilt when toggling between "/" and the "/card/<id>" alias — the card
  // editor is an overlay on top of a persistent board, not a new page.
  key: () => "board",
});

const sidebarCollapsed = ref(false);
const mobileNavOpen = ref(false);

const { connect, disconnect } = useSync();
const userStore = useUserStore();
const publicConfigStore = usePublicConfigStore();
onMounted(() => {
  if (isLocalFirstEnabled()) {
    // Local-first boot needs `me` before the count fetch / delta pull so it can
    // invalidate a stale cache left by a previous user on this browser (A1).
    void bootLocalFirst(useNuxtApp().$pinia as any);
    return;
  }
  // Legacy (flag-off) boot — call order preserved exactly.
  // Load the current user once; needed by the permission/share/notification UI.
  userStore.fetchMe();
  // Load server feature flags once; gates the sharing UI (P0.2).
  publicConfigStore.fetch();
  // Load the sidebar count badges once; kept fresh thereafter by useSync.
  checkListStore.fetchCounts();
  connect();
});

// Local-first boot ordering (Chunk A1). Resolve the authenticated user first,
// then reconcile the hydrated cache against it: if a different user owns the
// local state, it is dropped (snapshot + cursor + outbox) BEFORE the sidebar
// counts fetch, the SSE connect, and the background delta pull run — so none of
// them operate on the previous user's cursor or queued writes.
async function bootLocalFirst(pinia: any): Promise<void> {
  // Independent of identity — gates the sharing UI (P0.2); fire it early.
  publicConfigStore.fetch();
  const me = await userStore.fetchMe();
  if (me?.id) {
    await reconcileAccount(pinia, me.id).catch(() => {});
  }
  // Load the sidebar count badges once; kept fresh thereafter by useSync.
  checkListStore.fetchCounts();
  connect();
  // Advance the sync cursor in the background (best-effort; no-op offline). The
  // board already rendered from the hydrated snapshot (WI-10).
  runBackgroundSync(pinia).catch(() => {});
}
onUnmounted(disconnect);

const { cardId, editLabels, closeCard, closeLabelEditor } = useAppRoute();
const checkListStore = useCheckListsStore();

// --- Card editor modal, driven by the /card/<cardId> path ------------------
// The URL is the single source of truth. `cardModalId` retains the last id so
// the modal content stays valid during the close animation (cardId goes null
// before the leave transition finishes).
const cardModalId = ref(cardId.value);
watch(cardId, async (id) => {
  if (!id) return;
  cardModalId.value = id;
  // Make sure the card exists in the store before showing it — supports
  // deep-linking / sharing a card that isn't on the current page yet.
  await checkListStore.fetch(id).catch(() => {});
});

const cardModalOpen = computed({
  get: () => !!cardId.value,
  set: (open) => {
    // Only the user closing the modal (open=false) drives a route change.
    if (!open) closeCard();
  },
});

// --- Label editor modal, driven by ?editlabels=true ------------------------
const labelEditorOpen = computed({
  get: () => editLabels.value,
  set: (open) => {
    if (!open) closeLabelEditor();
  },
});
</script>

<style scoped></style>
