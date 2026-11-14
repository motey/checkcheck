import { computed, ref } from "vue";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { usePermissions } from "@/composables/usePermissions";
import { duplicateItemIdsToRemove, itemsToMarkdown } from "@/utils/itemBulk";
import { copyTextToClipboard } from "@/utils/clipboard";

/**
 * The card kebab menu's item bulk actions from issue #20: check all, sort by
 * name, remove duplicates (with a confirm step) and copy as text. Kept out of
 * MoreOptionsMenu.vue so the menu only wires entries and the confirm modal.
 *
 * Sort, check all and remove duplicates are server-side bulk endpoints replayed
 * as one outbox op each (offline capable); copy is client only.
 */
export function useItemBulkActions(checkListId: () => string) {
  const checkListsStore = useCheckListsStore();
  const itemStore = useCheckListsItemStore();
  const { can } = usePermissions();
  const toast = useToast();

  const checkList = computed(() => checkListsStore.get(checkListId()));
  const canCheck = computed(() => can(checkList.value, "check"));
  const canEdit = computed(() => can(checkList.value, "edit"));
  const totalCount = computed(() => itemStore.getItemCount(checkListId()));
  const uncheckedCount = computed(() => itemStore.getItemCount(checkListId(), false));

  /** Items in the order the card shows them (unchecked first when separated). */
  function itemsInDisplayOrder() {
    const items = itemStore.getCheckListItems(checkListId());
    if (!checkList.value?.checked_items_seperated) return items;
    return [...items.filter((i) => !i.state.checked), ...items.filter((i) => i.state.checked)];
  }

  function checkAll() {
    itemStore.checkAllItems(checkListId()).catch(() => {
      toast.add({ title: "Could not check all items", color: "error" });
    });
  }

  async function sortByName() {
    try {
      // With the whole card present the optimistic order is exact; otherwise
      // the server sorts everything and the delta pull brings the rest.
      await itemStore.ensureAllItemsLoaded(checkListId());
      await itemStore.sortItemsByName(checkListId());
    } catch {
      toast.add({ title: "Could not sort the items", color: "error" });
    }
  }

  // ── Remove duplicates: count first, then confirm ────────────────────────
  const confirmDedupOpen = ref(false);
  /** How many items will go, or null when only a partial list is available offline. */
  const dedupCount = ref<number | null>(null);
  const deduping = ref(false);

  async function askRemoveDuplicates() {
    const complete = await itemStore.ensureAllItemsLoaded(checkListId());
    if (complete) {
      dedupCount.value = duplicateItemIdsToRemove(itemStore.getCheckListItems(checkListId())).length;
      if (dedupCount.value === 0) {
        toast.add({ title: "No duplicate items found", color: "neutral" });
        return;
      }
    } else {
      dedupCount.value = null;
    }
    confirmDedupOpen.value = true;
  }

  async function removeDuplicates() {
    deduping.value = true;
    try {
      await itemStore.removeDuplicateItems(checkListId());
      confirmDedupOpen.value = false;
    } catch {
      toast.add({ title: "Could not remove duplicate items", color: "error" });
    } finally {
      deduping.value = false;
    }
  }

  // ── Copy as text ────────────────────────────────────────────────────────
  async function copyAsText() {
    let complete = true;
    // Start the clipboard write inside the click (see copyTextToClipboard) and
    // let it wait for the full list.
    const text = itemStore.ensureAllItemsLoaded(checkListId()).then((ok) => {
      complete = ok;
      return itemsToMarkdown(itemsInDisplayOrder());
    });
    try {
      await copyTextToClipboard(text);
    } catch {
      toast.add({ title: "Could not copy the list", description: "The clipboard is not available here.", color: "error" });
      return;
    }
    const copied = (await text).split("\n").filter(Boolean).length;
    const label = `${copied} ${copied === 1 ? "item" : "items"}`;
    if (complete) {
      toast.add({ title: "List copied", description: `${label} copied as a Markdown checklist.`, color: "success" });
    } else {
      toast.add({
        title: "List partly copied",
        description: `${label} copied. The rest of this list is not available offline.`,
        color: "warning",
      });
    }
  }

  return {
    canCheck,
    canEdit,
    totalCount,
    uncheckedCount,
    checkAll,
    sortByName,
    confirmDedupOpen,
    dedupCount,
    deduping,
    askRemoveDuplicates,
    removeDuplicates,
    copyAsText,
  };
}
