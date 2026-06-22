<template>
  <UModal :title="title" :description="description">
    <template #content>
      <div class="max-h-[85vh] overflow-y-auto overflow-x-hidden p-4 sm:p-6 flex flex-col gap-6">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-lg font-semibold">{{ title }}</h2>
            <p class="text-sm text-[var(--ui-text-muted)]">{{ description }}</p>
          </div>
          <UButton
            icon="i-lucide-x"
            color="neutral"
            variant="ghost"
            aria-label="Close"
            @click="emit('close')"
          />
        </div>

        <!-- Owner: full management ------------------------------------------ -->
        <template v-if="isOwner">
          <ShareModalAddPeople
            v-if="publicConfig.userSearchEnabled"
            :check-list-id="checkListId"
          />

          <ShareModalPeopleList :check-list-id="checkListId" :editable="true" />

          <ShareModalShareWithGroup
            v-if="hasGroups"
            :check-list-id="checkListId"
          />

          <ShareModalPublicLinks
            v-if="publicConfig.publicLinksEnabled"
            :check-list-id="checkListId"
          />

          <ShareModalTransferOwnership :check-list-id="checkListId" />
        </template>

        <!-- Non-owner: collaborator notice + leave --------------------------
             The collaborator list endpoint (GET /shares) is owner-only, so a
             non-owner can't be shown "People with access". We just confirm
             access and offer to leave. -->
        <template v-else>
          <p class="text-sm text-[var(--ui-text-muted)]">
            You're a collaborator on this list with
            <strong>{{ card?.my_permission ?? "view" }}</strong> access. Only the
            owner can manage who it's shared with.
          </p>

          <div class="border-t border-[var(--ui-border)] pt-4">
            <UButton
              color="error"
              variant="soft"
              icon="i-lucide-log-out"
              :loading="leaving"
              block
              @click="leaveList"
            >
              Leave list
            </UButton>
            <p class="mt-2 text-xs text-[var(--ui-text-muted)]">
              You'll lose access to this list. The owner can re-share it with you later.
            </p>
          </div>
        </template>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useCheckListsStore } from "@/stores/checklist";
import { useShareStore } from "@/stores/share";
import { useUserStore } from "@/stores/user";
import { usePublicConfigStore } from "@/stores/publicConfig";

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

// Overlay components close (and resolve the open() promise) by emitting `close`.
const emit = defineEmits<{ close: [] }>();

const checkListsStore = useCheckListsStore();
const shareStore = useShareStore();
const userStore = useUserStore();
const publicConfig = usePublicConfigStore();
const toast = useToast();
const { isOwner: isOwnerOf } = usePermissions();

const card = computed(() => checkListsStore.get(props.checkListId));
const isOwner = computed(() => isOwnerOf(card.value));

const title = computed(() =>
  isOwner.value ? "Share this list" : "List collaborators"
);
const description = computed(() =>
  isOwner.value
    ? "Manage who can see and edit this list."
    : "People with access to this list."
);

const hasGroups = computed(() => (shareStore.myGroups?.length ?? 0) > 0);

onMounted(() => {
  // Record the open checklist so useSync can refresh this list on share SSE.
  shareStore.setOpen(props.checkListId);
  // The collaborator list endpoint is owner-only (403 otherwise), so only the
  // owner fetches it / their shareable groups.
  if (isOwner.value) {
    shareStore.listShares(props.checkListId).catch(() => {});
    shareStore.listMyGroups().catch(() => {});
  }
});

onUnmounted(() => {
  shareStore.setOpen(null);
});

const leaving = ref(false);
async function leaveList() {
  const myId = userStore.myId;
  if (!myId) return;
  leaving.value = true;
  try {
    await shareStore.revokeShare(props.checkListId, myId);
    // The backend pins `checklist_deleted` to us, so useSync removes the card
    // from the grid — just close the modal.
    toast.add({ title: "You left the list", color: "success" });
    emit("close");
  } catch {
    toast.add({ title: "Could not leave the list", color: "error" });
  } finally {
    leaving.value = false;
  }
}
</script>

<style scoped></style>
