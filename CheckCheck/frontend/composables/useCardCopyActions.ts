import type { DropdownMenuItem } from "@nuxt/ui";
import { computed, ref, type Ref } from "vue";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { usePermissions } from "@/composables/usePermissions";

// The two card actions that create a new card from this one, for the card's ⋮
// menu: "Duplicate card" (any access, the copy is the caller's own) and "Move
// checked items to a new card" (needs edit, like deleting checked items; asks
// for confirmation first, see MoveCheckedConfirm.vue). Both work offline: the
// store shows the new card at once and queues one outbox op.
export function useCardCopyActions(checkListId: Ref<string>) {
  const checkListsStore = useCheckListsStore();
  const checkListItemStore = useCheckListsItemStore();
  const { can } = usePermissions();
  const toast = useToast();

  const checkList = computed(() => checkListsStore.get(checkListId.value));
  const checkedCount = computed(() => checkListItemStore.getItemCount(checkListId.value, true));
  const canEdit = computed(() => can(checkList.value, "edit"));

  const confirmMoveOpen = ref(false);
  const moving = ref(false);

  async function duplicate() {
    try {
      const copy = await checkListsStore.duplicate(checkListId.value);
      toast.add({
        title: "Card duplicated",
        description: `“${copy.name}” is now on your board.`,
        icon: "i-lucide-copy",
        color: "primary",
      });
    } catch {
      toast.add({ title: "Could not duplicate the card", color: "error" });
    }
  }

  async function moveChecked() {
    moving.value = true;
    try {
      const done = await checkListsStore.moveCheckedToNewCard(checkListId.value);
      confirmMoveOpen.value = false;
      toast.add({
        title: "Checked items moved",
        description: `They are now in “${done.name}”.`,
        icon: "i-lucide-list-end",
        color: "primary",
      });
    } catch {
      toast.add({ title: "Could not move the checked items", color: "error" });
    } finally {
      moving.value = false;
    }
  }

  const menuItems = computed(
    () =>
      [
        {
          label: "Duplicate card",
          icon: "i-lucide-copy",
          "data-testid": "card-duplicate",
          onSelect() {
            void duplicate();
          },
        },
        {
          label: "Move checked items to new card",
          icon: "i-lucide-list-end",
          disabled: !canEdit.value || checkedCount.value === 0,
          "data-testid": "card-move-checked",
          onSelect() {
            confirmMoveOpen.value = true;
          },
        },
      ] satisfies DropdownMenuItem[]
  );

  return { menuItems, confirmMoveOpen, moving, checkedCount, moveChecked };
}
