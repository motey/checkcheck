<template>
  <div class="flex flex-col gap-2">
    <div class="flex gap-2">
      <USelect
        v-model="group"
        :items="groupOptions"
        placeholder="Select a group"
        class="flex-1"
        :disabled="groupOptions.length === 0"
        data-testid="share-group-select"
      />
      <USelect
        v-model="level"
        :items="LEVEL_OPTIONS"
        class="w-28"
        data-testid="share-group-level"
      />
      <UButton
        icon="i-lucide-users"
        label="Share"
        :loading="sharing"
        :disabled="!group"
        data-testid="share-group-add"
        @click="share"
      />
    </div>
    <p v-if="groupOptions.length === 0" class="text-xs text-muted">
      All of your groups are already shared with this list.
    </p>
  </div>
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

// Offer only groups not already shared with this list (mirrors how the people
// picker hides existing collaborators) — the list below handles the rest.
const groupOptions = computed(() => {
  const already = new Set(
    shareStore.groupSharesFor(props.checkListId).map((g) => g.group)
  );
  return (shareStore.myGroups ?? [])
    .filter((g) => !already.has(g))
    .map((g) => ({ label: g, value: g }));
});

const group = ref<string | undefined>(undefined);
const level = ref<SharePermission>("edit");
const sharing = ref(false);

async function share() {
  if (!group.value) return;
  sharing.value = true;
  try {
    const res = await shareStore.upsertGroupShare(
      props.checkListId,
      group.value,
      level.value
    );
    toast.add({
      title: `Shared with “${res.group}”`,
      description: `${res.added} added, ${res.skipped} already had access (of ${res.total_members}).`,
      color: "success",
    });
    // Ready to add another group.
    group.value = undefined;
  } catch {
    toast.add({
      title: "Could not share with this group",
      description: "You can only share with groups you belong to.",
      color: "error",
    });
  } finally {
    sharing.value = false;
  }
}
</script>

<style scoped></style>
