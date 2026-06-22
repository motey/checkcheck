<template>
  <div class="flex flex-col gap-2">
    <p
      v-if="candidates.length === 0"
      class="text-xs text-[var(--ui-text-muted)] italic"
    >
      Add an accepted collaborator first to transfer ownership.
    </p>

    <template v-else>
      <div v-if="!confirming" class="flex gap-2">
        <USelect
          v-model="targetId"
          :items="candidateOptions"
          placeholder="Select new owner"
          class="flex-1"
          data-testid="share-transfer-select"
        />
        <UButton
          color="warning"
          variant="soft"
          icon="i-lucide-crown"
          label="Transfer"
          :disabled="!targetId"
          @click="confirming = true"
        />
      </div>

      <div v-else class="flex flex-col gap-2 rounded-md border border-[var(--ui-border)] p-3">
        <p class="text-sm">
          Make <strong>{{ targetName }}</strong> the owner of this list? This can't be undone by you.
        </p>
        <div class="flex justify-end gap-2">
          <UButton color="neutral" variant="ghost" label="Cancel" @click="confirming = false" />
          <UButton
            color="warning"
            label="Transfer ownership"
            :loading="transferring"
            @click="transfer"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useShareStore } from "@/stores/share";
import { useCheckListsStore } from "@/stores/checklist";

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

const shareStore = useShareStore();
const checkListsStore = useCheckListsStore();
const toast = useToast();

// Only accepted collaborators can become owner (a pending invitee has no access).
const candidates = computed(() =>
  shareStore.sharesFor(props.checkListId).filter((s) => (s.status ?? "accepted") === "accepted")
);
const candidateOptions = computed(() =>
  candidates.value.map((s) => ({
    label: s.display_name || s.user_name || s.user_id,
    value: s.user_id,
  }))
);

const targetId = ref<string | undefined>(undefined);
const confirming = ref(false);
const transferring = ref(false);

const targetName = computed(() => {
  const t = candidates.value.find((s) => s.user_id === targetId.value);
  return t?.display_name || t?.user_name || "this user";
});

async function transfer() {
  if (!targetId.value) return;
  transferring.value = true;
  try {
    await shareStore.transferOwnership(props.checkListId, targetId.value);
    // Re-read the card so `my_permission` flips to "edit" and the modal swaps to
    // the non-owner (collaborator) view. Don't re-list shares — we're no longer
    // the owner, and GET /shares is owner-only (would 403).
    await checkListsStore.refresh(props.checkListId).catch(() => {});
    toast.add({ title: "Ownership transferred", color: "success" });
    confirming.value = false;
    targetId.value = undefined;
  } catch {
    toast.add({ title: "Could not transfer ownership", color: "error" });
  } finally {
    transferring.value = false;
  }
}
</script>

<style scoped></style>
