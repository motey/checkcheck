<template>
  <UModal :title="title" :description="description">
    <template #content>
      <div class="max-h-[85vh] overflow-y-auto overflow-x-hidden p-4 sm:p-6 flex flex-col gap-6">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-lg font-semibold">{{ title }}</h2>
            <p class="text-sm text-muted">{{ description }}</p>
          </div>
          <UButton
            icon="i-lucide-x"
            color="neutral"
            variant="ghost"
            aria-label="Close"
            @click="emit('close')"
          />
        </div>

        <!-- Offline notice (WI-12): sharing stays server-authoritative, so all
             the management controls below are inert until reconnected. -->
        <UAlert
          v-if="!online"
          color="neutral"
          variant="subtle"
          icon="i-lucide-wifi-off"
          title="You're offline"
          description="Sharing changes need a connection. Reconnect to invite people, manage access, or create links."
          data-testid="share-offline-notice"
        />

        <!-- Owner: full management ------------------------------------------
             Each card below is a *separate, independent* way to share — the
             owner can use any one of them on its own; none requires the others.
             Keeping them in distinct bordered cards (rather than one long form)
             makes that "or" relationship obvious.

             Offline (WI-12): the whole management area goes `inert` so no share
             mutation can be triggered; the banner above says why. -->
        <div
          v-if="isOwner"
          :inert="!online"
          :class="['flex flex-col gap-6', { 'opacity-50 pointer-events-none': !online }]"
        >
          <!-- Invite specific people -->
          <div class="flex flex-col gap-3 rounded-lg border border-default p-4">
            <div class="flex items-start gap-3">
              <UIcon
                name="i-lucide-user-plus"
                class="mt-0.5 size-5 shrink-0 text-muted"
              />
              <div class="flex flex-col">
                <h3 class="text-sm font-semibold">Invite specific people</h3>
                <p class="text-xs text-muted">
                  Share with individual people and choose what each one can do.
                </p>
              </div>
            </div>
            <ShareModalAddPeople
              v-if="publicConfig.userSearchEnabled"
              :check-list-id="checkListId"
            />
            <ShareModalPeopleList :check-list-id="checkListId" :editable="true" />
          </div>

          <!-- Share with a group -->
          <div
            v-if="hasGroups"
            class="flex flex-col gap-3 rounded-lg border border-default p-4"
          >
            <div class="flex items-start gap-3">
              <UIcon
                name="i-lucide-users"
                class="mt-0.5 size-5 shrink-0 text-muted"
              />
              <div class="flex flex-col">
                <h3 class="text-sm font-semibold">Share with a group</h3>
                <p class="text-xs text-muted">
                  Grant every member of one of your groups access at once.
                </p>
              </div>
            </div>
            <ShareModalShareWithGroup :check-list-id="checkListId" />
          </div>

          <!-- Create a public link -->
          <div
            v-if="publicConfig.publicLinksEnabled"
            class="flex flex-col gap-3 rounded-lg border border-default p-4"
          >
            <div class="flex items-start gap-3">
              <UIcon
                name="i-lucide-link"
                class="mt-0.5 size-5 shrink-0 text-muted"
              />
              <div class="flex flex-col">
                <h3 class="text-sm font-semibold">Create a public link</h3>
                <p class="text-xs text-muted">
                  Anyone with the link can open this list anonymously — no account needed.
                </p>
              </div>
            </div>
            <ShareModalPublicLinks :check-list-id="checkListId" />
          </div>

          <!-- Advanced: transfer ownership (not a "way to share" — set apart) -->
          <div class="flex flex-col gap-3 rounded-lg border border-default p-4">
            <div class="flex items-start gap-3">
              <UIcon
                name="i-lucide-crown"
                class="mt-0.5 size-5 shrink-0 text-muted"
              />
              <div class="flex flex-col">
                <h3 class="text-sm font-semibold">Transfer ownership</h3>
                <p class="text-xs text-muted">
                  Hand this list to another collaborator. You'll be demoted to an
                  <strong>edit</strong> collaborator and lose owner controls.
                </p>
              </div>
            </div>
            <ShareModalTransferOwnership :check-list-id="checkListId" />
          </div>
        </div>

        <!-- Non-owner: collaborator notice + leave --------------------------
             The collaborator list endpoint (GET /shares) is owner-only, so a
             non-owner can't be shown "People with access". We just confirm
             access and offer to leave. -->
        <template v-else>
          <p class="text-sm text-muted">
            You're a collaborator on this list with
            <strong>{{ card?.my_permission ?? "view" }}</strong> access. Only the
            owner can manage who it's shared with.
          </p>

          <div class="border-t border-default pt-4">
            <UButton
              color="error"
              variant="soft"
              icon="i-lucide-log-out"
              :loading="leaving"
              :disabled="!online"
              block
              @click="leaveList"
            >
              Leave list
            </UButton>
            <p class="mt-2 text-xs text-muted">
              {{
                online
                  ? "You'll lose access to this list. The owner can re-share it with you later."
                  : "You're offline — reconnect to leave this list."
              }}
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
const { online } = useConnectivity();

const card = computed(() => checkListsStore.get(props.checkListId));
const isOwner = computed(() => isOwnerOf(card.value));

const title = computed(() =>
  isOwner.value ? "Share this list" : "List collaborators"
);
const description = computed(() =>
  isOwner.value
    ? "Invite people, share with a group, or create a public link — use whichever you need."
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
