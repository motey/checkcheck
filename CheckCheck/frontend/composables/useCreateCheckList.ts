import { ref } from "vue";
import { useAppRoute } from "~/composables/useAppRoute";
import { useCheckListsStore } from "@/stores/checklist";

// Module-scoped so the id of a just-created card is visible to the card editor
// that opens next. The editor autofocuses the title only for this card (a new,
// empty list wants the cursor ready); reopening an existing card must NOT focus
// a field, otherwise mobile keyboards pop up unprompted. Consumed (cleared) by
// CheckList on mount.
const newlyCreatedCardId = ref<string | null>(null);

/**
 * Shared "new list" flow. Creates an empty checklist on the server and opens it
 * in the card editor via the URL (so the freshly created card is shareable and
 * back-button aware). Reused by the navbar "+", the board empty-state CTA, and
 * the mobile FAB so the create behaviour stays in exactly one place.
 */
export function useCreateCheckList() {
  const checkListsStore = useCheckListsStore();
  const { openCard } = useAppRoute();

  async function createAndOpen() {
    const checkList = await checkListsStore.create({} as CheckListCreateType);
    newlyCreatedCardId.value = checkList.id;
    openCard(checkList.id);
  }

  return { createAndOpen, newlyCreatedCardId };
}
