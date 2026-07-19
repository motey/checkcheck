<template>
  <div class="flex flex-col gap-2">
    <ul class="rounded-md border border-default divide-y divide-default">
      <!-- Owner row (synthetic — the backend list excludes the owner). Only
           shown when the current user is the owner, since otherwise we don't
           have the owner's name to display. -->
      <li
        v-if="showSelfOwnerRow"
        class="flex items-center justify-between px-3 py-2 gap-2"
      >
        <span class="text-sm">
          {{ ownerName
          }}<span v-if="ownerHasName" class="ml-1 text-xs font-bold text-muted"
            >(you)</span
          >
        </span>
        <UBadge color="primary" variant="subtle" size="sm">Owner</UBadge>
      </li>

      <li
        v-if="collaborators.length === 0 && !showSelfOwnerRow"
        class="px-3 py-2 text-sm text-muted"
      >
        No collaborators yet.
      </li>

      <li
        v-for="share in collaborators"
        :key="share.user_id"
        class="flex items-center justify-between px-3 py-2 gap-2"
        data-testid="share-collaborator-row"
      >
        <div class="flex items-center gap-2 min-w-0">
          <span class="text-sm truncate">
            {{ share.display_name || share.user_name || share.user_id }}
          </span>
          <UBadge
            v-if="share.status && share.status !== 'accepted'"
            :color="share.status === 'declined' ? 'error' : 'warning'"
            variant="subtle"
            size="sm"
          >
            {{ share.status }}
          </UBadge>
        </div>

        <div class="flex items-center gap-1 shrink-0">
          <USelect
            v-if="editable"
            :model-value="share.permission"
            :items="LEVEL_OPTIONS"
            size="xs"
            class="w-24"
            :disabled="busyId === share.user_id"
            @update:model-value="(v: SharePermission) => changeLevel(share, v)"
          />
          <UBadge v-else color="neutral" variant="subtle" size="sm">
            {{ share.permission }}
          </UBadge>

          <UButton
            v-if="editable"
            icon="i-lucide-x"
            color="error"
            variant="ghost"
            size="xs"
            aria-label="Remove"
            :loading="busyId === share.user_id"
            @click="revoke(share)"
          />
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useShareStore } from "@/stores/share";
import { useUserStore } from "@/stores/user";
import { useCheckListsStore } from "@/stores/checklist";

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
  editable: {
    type: Boolean,
    default: false,
  },
});

const LEVEL_OPTIONS = [
  { label: "View", value: "view" },
  { label: "Check", value: "check" },
  { label: "Edit", value: "edit" },
] as const;

const shareStore = useShareStore();
const userStore = useUserStore();
const checkListsStore = useCheckListsStore();
const toast = useToast();
const { isOwner } = usePermissions();

// Only explicit individual shares are listed here. Collaborators materialized
// from a group share (via_group set) are represented by the group in the
// group-share list, not shown per-member — otherwise a large group floods this
// list, and removing such a row wouldn't stick (the reconciler re-adds them).
const collaborators = computed(() =>
  shareStore.sharesFor(props.checkListId).filter((s) => !s.via_group)
);

const card = computed(() => checkListsStore.get(props.checkListId));
// Editable view is owner-only, so when editable the current user is the owner.
const showSelfOwnerRow = computed(() => props.editable && isOwner(card.value));
// Split the name from the "(you)" marker so the template can style the marker
// (smaller + bold) distinctly, making clear it isn't part of the name.
const ownerName = computed(() => {
  const me = userStore.me;
  return me?.display_name || me?.user_name || "You";
});
const ownerHasName = computed(() => {
  const me = userStore.me;
  return Boolean(me?.display_name || me?.user_name);
});

const busyId = ref<string | null>(null);

async function changeLevel(share: ShareReadType, permission: SharePermission) {
  if (permission === share.permission) return;
  busyId.value = share.user_id;
  try {
    await shareStore.upsertShare(props.checkListId, share.user_id, permission);
  } catch {
    toast.add({ title: "Could not change permission", color: "error" });
  } finally {
    busyId.value = null;
  }
}

async function revoke(share: ShareReadType) {
  busyId.value = share.user_id;
  try {
    await shareStore.revokeShare(props.checkListId, share.user_id);
    toast.add({
      title: `Removed ${share.display_name || share.user_name || "user"}`,
      color: "success",
    });
  } catch {
    toast.add({ title: "Could not remove collaborator", color: "error" });
  } finally {
    busyId.value = null;
  }
}
</script>

<style scoped></style>
