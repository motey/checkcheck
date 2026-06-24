import { useAppRoute } from "~/composables/useAppRoute";
import { useCheckListsStore } from "@/stores/checklist";

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
    openCard(checkList.id);
  }

  return { createAndOpen };
}
