<template>
  <section class="flex flex-col gap-2 border-t border-[var(--ui-border)] pt-4">
    <h3 class="text-sm font-semibold">Share with a group</h3>
    <p class="text-xs text-[var(--ui-text-muted)]">
      Grant every member of one of your groups access at once.
    </p>

    <div class="flex gap-2">
      <USelect
        v-model="group"
        :items="groupOptions"
        placeholder="Select a group"
        class="flex-1"
        data-testid="share-group-select"
      />
      <USelect v-model="level" :items="LEVEL_OPTIONS" class="w-28" />
      <UButton
        icon="i-lucide-users"
        label="Share"
        :loading="sharing"
        :disabled="!group"
        @click="share"
      />
    </div>
  </section>
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

const groupOptions = computed(() =>
  (shareStore.myGroups ?? []).map((g) => ({ label: g, value: g }))
);

const group = ref<string | undefined>(undefined);
const level = ref<SharePermission>("edit");
const sharing = ref(false);

async function share() {
  if (!group.value) return;
  sharing.value = true;
  try {
    const res = await shareStore.shareWithGroup(props.checkListId, group.value, level.value);
    toast.add({
      title: `Shared with “${res.group}”`,
      description: `${res.added} added, ${res.skipped} already had access (of ${res.total_members}).`,
      color: "success",
    });
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
