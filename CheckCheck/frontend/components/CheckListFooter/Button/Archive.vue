<template>
  <UTooltip :text="isArchived ? 'Delete forever' : 'Archive'" :popper="{ arrow: true }">
    <CheckListColoredButton
      variant="ghost"
      :padded="false"
      icon="i-lucide-trash-2"
      :checkListId="checkListId"
      :data-testid="isArchived ? 'card-delete-forever' : 'card-archive'"
      @click.stop="onTrash()"
    />
  </UTooltip>

  <!-- Permanent-delete confirm — only reachable from the Archive view, where the
       trash action deletes forever instead of soft-archiving. -->
  <UModal
    v-model:open="confirmOpen"
    title="Delete forever?"
    :ui="{ content: 'max-w-sm w-[calc(100vw-1rem)] sm:w-full rounded-2xl ring ring-default' }"
  >
    <template #content>
      <div class="p-4 sm:p-6 flex flex-col gap-4">
        <div class="flex flex-col gap-1">
          <h2 class="text-lg font-semibold">Delete forever?</h2>
          <p class="text-sm text-muted">This list will be permanently deleted. This can't be undone.</p>
        </div>
        <div class="flex justify-end gap-2">
          <UButton color="neutral" variant="ghost" label="Cancel" @click.stop="confirmOpen = false" />
          <UButton
            color="error"
            label="Delete forever"
            data-testid="confirm-delete"
            :loading="deleting"
            @click.stop="deleteForever()"
          />
        </div>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useCheckListsStore } from "@/stores/checklist";
import { useAppRoute } from "~/composables/useAppRoute";

const checkListsStore = useCheckListsStore();
const toast = useToast();
const route = useRoute();
const { closeCard } = useAppRoute();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

const checkList = computed(() => checkListsStore.get(props.checkListId));
// Drive behaviour off the card's own state, not the route: an archived card's
// trash button deletes forever, a live card's soft-archives. This is correct in
// both the board preview and the open editor.
const isArchived = computed(() => checkList.value?.position.archived ?? false);

const confirmOpen = ref(false);
const deleting = ref(false);

// If this card is currently open in the editor, close it (archiving/deleting
// removes it from the board underneath the modal).
function closeIfOpen() {
  if (route.params.cardId === props.checkListId) closeCard();
}

function onTrash() {
  if (isArchived.value) {
    confirmOpen.value = true;
  } else {
    archiveWithUndo();
  }
}

async function archiveWithUndo() {
  await checkListsStore.archive(props.checkListId, true);
  closeIfOpen();
  toast.add({
    title: "List archived",
    color: "neutral",
    actions: [
      {
        label: "Undo",
        color: "neutral",
        variant: "outline",
        "data-testid": "undo-archive",
        onClick: () => {
          checkListsStore.archive(props.checkListId, false);
        },
      },
    ],
  });
}

async function deleteForever() {
  deleting.value = true;
  try {
    await checkListsStore.delete(props.checkListId);
    confirmOpen.value = false;
    closeIfOpen();
    toast.add({ title: "List deleted", color: "neutral" });
  } catch {
    toast.add({ title: "Could not delete list", color: "error" });
  } finally {
    deleting.value = false;
  }
}
</script>

<style scoped></style>
