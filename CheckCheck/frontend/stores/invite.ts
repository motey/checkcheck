import { defineStore } from "pinia";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { assertOnline } from "@/utils/connectivity";

// Invite inbox (backend Phase 8). When the server runs in invite mode
// (SHARING_REQUIRE_INVITE_ACCEPT), a card shared with a user lands as a *pending*
// invite they must Accept or Decline rather than appearing instantly in their
// grid. When the flag is off no invite is ever created, so `pending` stays empty
// and the inbox renders nothing (harmless).
//
// Mirrors the established store idiom (stores/notification.ts / stores/share.ts):
// `const { $checkapi } = useNuxtApp()`, path/body, try/catch + console.error, and
// reconcile the local array in place rather than blindly refetching.

export type InviteState = {
  // Pending invites awaiting accept/decline, as the backend returns them.
  pending: InviteReadType[];
};

export const useInviteStore = defineStore("invite", {
  state: () =>
    ({
      pending: [],
    } as InviteState),
  actions: {
    async refresh(): Promise<InviteReadType[]> {
      const { $checkapi } = useNuxtApp();
      try {
        this.pending = await $checkapi("/api/user/me/invites", { method: "get" });
      } catch (error) {
        console.error("Could not list invites 'GET /api/user/me/invites'", error);
      }
      return this.pending;
    },

    // Accept an invite. The response is the full card (CheckListApiWithSubObj),
    // already carrying the caller's position + my_permission. Reconcile it into
    // the checklist store + fetch its item preview so it appears in the grid —
    // mirroring useSync's `checklist_created` path (refresh + preview + count
    // bump) but reusing the object we already have instead of a redundant GET.
    // The card's own `share_added` SSE that fires alongside accept just re-reads
    // the now-present card (harmless), so there's no double count.
    async accept(checkListId: string): Promise<void> {
      assertOnline("Accepting an invite isn't available offline.");
      const { $checkapi } = useNuxtApp();
      let card: CheckListType;
      try {
        card = await $checkapi("/api/checklist/{checklist_id}/invites/accept", {
          path: { checklist_id: checkListId },
          method: "post",
        });
      } catch (error) {
        console.error(
          "Could not accept invite 'POST /checklist/" + checkListId + "/invites/accept'",
          error
        );
        throw error;
      }

      const checkListStore = useCheckListsStore();
      const checkListItemStore = useCheckListsItemStore();
      const cards = checkListStore.checkLists;
      const idx = cards.findIndex((c) => c.id === card.id);
      if (idx === -1) {
        cards.push(card);
        checkListStore.total_backend_count++;
      } else {
        cards.splice(idx, 1, card);
      }
      await checkListItemStore.fetchMultipleChecklistsItemsPreview([card.id]);

      this.drop(checkListId);
    },

    // Decline an invite (204). No access is granted; the backend keeps the row as
    // 'declined' (the owner sees it in their share list and can re-invite).
    async decline(checkListId: string): Promise<void> {
      assertOnline("Declining an invite isn't available offline.");
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/checklist/{checklist_id}/invites/decline", {
          path: { checklist_id: checkListId },
          method: "post",
        });
      } catch (error) {
        console.error(
          "Could not decline invite 'POST /checklist/" + checkListId + "/invites/decline'",
          error
        );
        throw error;
      }
      this.drop(checkListId);
    },

    // Remove a pending invite from the local list (after accept/decline).
    drop(checkListId: string) {
      const idx = this.pending.findIndex((i) => i.checklist_id === checkListId);
      if (idx !== -1) this.pending.splice(idx, 1);
    },
  },
});
