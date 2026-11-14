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

  <!-- Destructive "delete checked items" confirm. Uncheck-all is non-destructive
       and fires straight from the menu; deleting checked items goes through this
       confirm because it tombstones rows. -->
  <UModal
    v-model:open="confirmDeleteOpen"
    title="Delete checked items?"
    :ui="{ content: 'max-w-sm w-[calc(100vw-1rem)] sm:w-full rounded-2xl ring ring-default' }"
  >
    <template #content>
      <div class="p-4 sm:p-6 flex flex-col gap-4">
        <div class="flex flex-col gap-1">
          <h2 class="text-lg font-semibold">Delete checked items?</h2>
          <p class="text-sm text-muted">
            {{ checkedCount }} checked {{ checkedCount === 1 ? "item" : "items" }} will be deleted. This can't be undone.
          </p>
        </div>
        <div class="flex justify-end gap-2">
          <UButton color="neutral" variant="ghost" label="Cancel" @click.stop="confirmDeleteOpen = false" />
          <UButton
            color="error"
            label="Delete checked"
            data-testid="confirm-delete-checked"
            :loading="deleting"
            @click.stop="deleteChecked()"
          />
        </div>
      </div>
    </template>
  </UModal>

  <CheckListFooterButtonMoveCheckedConfirm
    v-model:open="confirmMoveOpen"
    :checked-count="checkedCount"
    :loading="moving"
    @confirm="moveChecked()"
  />

  <!-- "Remove duplicates" confirm: it tombstones rows, like delete checked. -->
  <UModal
    v-model:open="bulk.confirmDedupOpen.value"
    title="Remove duplicate items?"
    :ui="{ content: 'max-w-sm w-[calc(100vw-1rem)] sm:w-full rounded-2xl ring ring-default' }"
  >
    <template #content>
      <div class="p-4 sm:p-6 flex flex-col gap-4">
        <div class="flex flex-col gap-1">
          <h2 class="text-lg font-semibold">Remove duplicate items?</h2>
          <p class="text-sm text-muted" data-testid="remove-duplicates-summary">
            <template v-if="bulk.dedupCount.value !== null">
              {{ bulk.dedupCount.value }} duplicate {{ bulk.dedupCount.value === 1 ? "item" : "items" }} will be deleted.
            </template>
            Items with the same text (ignoring case and spaces) are merged into one. An unchecked copy is kept if
            there is one, otherwise the topmost. This can't be undone.
          </p>
        </div>
        <div class="flex justify-end gap-2">
          <UButton color="neutral" variant="ghost" label="Cancel" @click.stop="bulk.confirmDedupOpen.value = false" />
          <UButton
            color="error"
            label="Remove duplicates"
            data-testid="confirm-remove-duplicates"
            :loading="bulk.deduping.value"
            @click.stop="bulk.removeDuplicates()"
          />
        </div>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import { computed, ref, toRef } from "vue";
import { useCardCopyActions } from "@/composables/useCardCopyActions";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { usePermissions } from "@/composables/usePermissions";
import { useReminderPanel } from "@/composables/useReminderPanel";
import { useItemBulkActions } from "@/composables/useItemBulkActions";

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

// Check all, sort by name, remove duplicates, copy as text.
const bulk = useItemBulkActions(() => props.checkListId);

// Only the open card carries a reminder panel (R4), so this is null on the
// board preview's hover toolbar and the entry is left out there: it would have
// nowhere to open. Any collaborator may set one, including a view-only one — a
// reminder notifies nobody but the person who set it.
const reminderPanel = useReminderPanel();

// "Duplicate card" and "Move checked items to new card" (own composable + confirm).
const {
  menuItems: cardCopyMenuItems,
  confirmMoveOpen,
  moving,
  moveChecked,
} = useCardCopyActions(toRef(props, "checkListId"));

async function deleteChecked() {
  deleting.value = true;
  try {
    await checkListItemStore.deleteCheckedItems(props.checkListId);
    confirmDeleteOpen.value = false;
  } catch {
    toast.add({ title: "Could not delete checked items", color: "error" });
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
      ...cardCopyMenuItems.value,
      { type: "separator" as const },
      ...(reminderPanel
        ? [
            {
              label: "Set a reminder",
              icon: "i-lucide-alarm-clock",
              "data-testid": "card-set-reminder",
              onSelect() {
                reminderPanel.open();
              },
            },
            { type: "separator" as const },
          ]
        : []),
      {
        label: "Check all items",
        icon: "i-lucide-check-check",
        disabled: !canCheck.value || bulk.uncheckedCount.value === 0,
        "data-testid": "card-check-all",
        onSelect() {
          bulk.checkAll();
        },
      },
      {
        label: "Uncheck all items",
        icon: "i-lucide-list-x",
        disabled: !canCheck.value || checkedCount.value === 0,
        "data-testid": "card-uncheck-all",
        onSelect() {
          checkListItemStore.uncheckAllItems(props.checkListId);
        },
      },
      {
        label: "Delete checked items",
        icon: "i-lucide-trash-2",
        color: "error" as const,
        disabled: !canEdit.value || checkedCount.value === 0,
        "data-testid": "card-delete-checked",
        onSelect() {
          confirmDeleteOpen.value = true;
        },
      },
      { type: "separator" as const },
      {
        label: "Sort items by name",
        icon: "i-lucide-arrow-down-a-z",
        disabled: !canEdit.value || bulk.totalCount.value < 2,
        "data-testid": "card-sort-by-name",
        onSelect() {
          bulk.sortByName();
        },
      },
      {
        label: "Remove duplicates",
        icon: "i-lucide-copy-minus",
        disabled: !canEdit.value || bulk.totalCount.value < 2,
        "data-testid": "card-remove-duplicates",
        onSelect() {
          bulk.askRemoveDuplicates();
        },
      },
      {
        label: "Copy list as text",
        icon: "i-lucide-clipboard-copy",
        disabled: bulk.totalCount.value === 0,
        "data-testid": "card-copy-as-text",
        onSelect() {
          bulk.copyAsText();
        },
      },
    ] satisfies DropdownMenuItem[]
);
</script>
