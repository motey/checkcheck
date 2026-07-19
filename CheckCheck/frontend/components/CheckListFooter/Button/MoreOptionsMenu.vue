<template>
  <UDropdownMenu :items="items" :content="{ align: 'end' }" :ui="{ content: 'min-w-56' }">
    <UTooltip text="Options" @click.stop>
      <CheckListColoredButton
        variant="ghost"
        icon="i-lucide-ellipsis-vertical"
        :checkListId="checkListId"
        data-testid="card-options-menu"
      />
    </UTooltip>
  </UDropdownMenu>

  <!-- Destructive "delete ticked items" confirm. Untick-all is non-destructive
       and fires straight from the menu; deleting ticked items goes through this
       confirm because it tombstones rows. -->
  <UModal
    v-model:open="confirmDeleteOpen"
    title="Delete ticked items?"
    :ui="{ content: 'max-w-sm w-[calc(100vw-1rem)] sm:w-full rounded-2xl ring ring-default' }"
  >
    <template #content>
      <div class="p-4 sm:p-6 flex flex-col gap-4">
        <div class="flex flex-col gap-1">
          <h2 class="text-lg font-semibold">Delete ticked items?</h2>
          <p class="text-sm text-muted">
            {{ checkedCount }} ticked {{ checkedCount === 1 ? "item" : "items" }} will be deleted. This can't be undone.
          </p>
        </div>
        <div class="flex justify-end gap-2">
          <UButton color="neutral" variant="ghost" label="Cancel" @click.stop="confirmDeleteOpen = false" />
          <UButton
            color="error"
            label="Delete ticked"
            data-testid="confirm-delete-ticked"
            :loading="deleting"
            @click.stop="deleteTicked()"
          />
        </div>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import { computed, ref } from "vue";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { usePermissions } from "@/composables/usePermissions";

const checkListsStore = useCheckListsStore();
const checkListItemStore = useCheckListsItemStore();
const { can } = usePermissions();
const toast = useToast();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});
const checkList = computed(() => checkListsStore.get(props.checkListId));

// Reactive gates: number of checked items (bulk ops are no-ops / disabled when
// zero) and the caller's permission on the card.
const checkedCount = computed(() => checkListItemStore.getItemCount(props.checkListId, true));
const canCheck = computed(() => can(checkList.value, "check"));
const canEdit = computed(() => can(checkList.value, "edit"));

const confirmDeleteOpen = ref(false);
const deleting = ref(false);

async function deleteTicked() {
  deleting.value = true;
  try {
    await checkListItemStore.deleteCheckedItems(props.checkListId);
    confirmDeleteOpen.value = false;
  } catch {
    toast.add({ title: "Could not delete ticked items", color: "error" });
  } finally {
    deleting.value = false;
  }
}

const items = computed(
  () =>
    [
      {
        label: "Separate checked items",
        icon: "i-lucide-list-todo",
        type: "checkbox" as const,
        checked: checkList.value?.checked_items_seperated ?? false,
        onUpdateChecked(checked: boolean) {
          checkListsStore.update(props.checkListId, { checked_items_seperated: checked } as CheckListUpdateType);
        },
        onSelect(e: Event) {
          e.preventDefault();
        },
      },
      {
        label: "Suggest existing items",
        icon: "i-lucide-list-checks",
        type: "checkbox" as const,
        checked: checkList.value?.suggest_existing_items ?? true,
        onUpdateChecked(checked: boolean) {
          checkListsStore.update(props.checkListId, { suggest_existing_items: checked } as CheckListUpdateType);
        },
        onSelect(e: Event) {
          e.preventDefault();
        },
      },
      { type: "separator" as const },
      {
        label: "Untick all items",
        icon: "i-lucide-list-x",
        disabled: !canCheck.value || checkedCount.value === 0,
        "data-testid": "card-untick-all",
        onSelect() {
          checkListItemStore.uncheckAllItems(props.checkListId);
        },
      },
      {
        label: "Delete ticked items",
        icon: "i-lucide-trash-2",
        color: "error" as const,
        disabled: !canEdit.value || checkedCount.value === 0,
        "data-testid": "card-delete-ticked",
        onSelect() {
          confirmDeleteOpen.value = true;
        },
      },
    ] satisfies DropdownMenuItem[]
);
</script>
