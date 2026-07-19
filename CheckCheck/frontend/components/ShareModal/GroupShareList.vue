<template>
  <ul
    v-if="groupShares.length > 0"
    class="rounded-md border border-default divide-y divide-default"
  >
    <li
      v-for="share in groupShares"
      :key="share.group"
      class="flex items-center justify-between px-3 py-2 gap-2"
      data-testid="share-group-row"
    >
      <div class="flex items-center gap-2 min-w-0">
        <UIcon name="i-lucide-users" class="size-4 shrink-0 text-muted" />
        <span class="text-sm truncate">{{ share.group }}</span>
      </div>

      <div class="flex items-center gap-1 shrink-0">
        <USelect
          :model-value="share.permission"
          :items="LEVEL_OPTIONS"
          size="xs"
          class="w-24"
          :disabled="busyGroup === share.group"
          data-testid="share-group-row-level"
          @update:model-value="(v: SharePermission) => changeLevel(share, v)"
        />
        <UButton
          icon="i-lucide-x"
          color="error"
          variant="ghost"
          size="xs"
          aria-label="Stop sharing with group"
          data-testid="share-group-remove"
          :loading="busyGroup === share.group"
          @click="revoke(share)"
        />
      </div>
    </li>
  </ul>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useShareStore } from "@/stores/share";

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

const LEVEL_OPTIONS = [
  { label: "View", value: "view" },
  { label: "Check", value: "check" },
  { label: "Edit", value: "edit" },
] as const;

const shareStore = useShareStore();
const toast = useToast();

const groupShares = computed(() => shareStore.groupSharesFor(props.checkListId));

const busyGroup = ref<string | null>(null);

async function changeLevel(share: GroupShareReadType, permission: SharePermission) {
  if (permission === share.permission) return;
  busyGroup.value = share.group;
  try {
    // Re-sharing the same group at a new level re-levels its members (living).
    await shareStore.upsertGroupShare(props.checkListId, share.group, permission);
  } catch {
    toast.add({ title: "Could not change group permission", color: "error" });
  } finally {
    busyGroup.value = null;
  }
}

async function revoke(share: GroupShareReadType) {
  busyGroup.value = share.group;
  try {
    await shareStore.revokeGroupShare(props.checkListId, share.group);
    toast.add({ title: `Stopped sharing with “${share.group}”`, color: "success" });
  } catch {
    toast.add({ title: "Could not stop sharing with this group", color: "error" });
  } finally {
    busyGroup.value = null;
  }
}
</script>

<style scoped></style>
