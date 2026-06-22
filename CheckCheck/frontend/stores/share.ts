import { defineStore } from "pinia";

// Collaborator (per-user) share management, keyed by checklist id.
//
// Mirrors the backend Phase 3/4/10 endpoints. The card's own `my_permission`
// (P0.1) still gates *whether* the management UI is shown; this store only deals
// in the collaborator list + the share mutations the owner performs.
//
// `openForChecklistId` lets useSync refresh the list live: while the ShareModal
// is mounted it records its checklist id, and a `share_added`/`share_removed`
// SSE event for that id re-reads the list (see refreshIfOpen).

export type ShareState = {
  // Collaborator lists keyed by checklist id (owner row excluded — see backend).
  shares: Record<string, ShareReadType[]>;
  // The checklist id whose ShareModal is currently open, if any.
  openForChecklistId: string | null;
  // The current user's OIDC groups (empty for local users → hide group UI).
  // null = not fetched yet.
  myGroups: string[] | null;
};

export const useShareStore = defineStore("share", {
  state: () =>
    ({
      shares: {},
      openForChecklistId: null,
      myGroups: null,
    } as ShareState),
  getters: {
    // The cached collaborator list for a checklist (empty until listShares runs).
    sharesFor: (state) => {
      return (checkListId: string): ShareReadType[] => state.shares[checkListId] ?? [];
    },
  },
  actions: {
    async listShares(checkListId: string): Promise<ShareReadType[]> {
      const { $checkapi } = useNuxtApp();
      let res: ShareReadType[];
      try {
        res = await $checkapi("/api/checklist/{checklist_id}/shares", {
          path: { checklist_id: checkListId },
          method: "get",
        });
      } catch (error) {
        console.error("Could not list shares 'GET /checklist/" + checkListId + "/shares'", error);
        throw error;
      }
      this.shares[checkListId] = res;
      return res;
    },

    async upsertShare(
      checkListId: string,
      userId: string,
      permission: SharePermission
    ): Promise<ShareReadType> {
      const { $checkapi } = useNuxtApp();
      let res: ShareReadType;
      try {
        res = await $checkapi("/api/checklist/{checklist_id}/shares/{user_id}", {
          path: { checklist_id: checkListId, user_id: userId },
          method: "put",
          body: { permission },
        });
      } catch (error) {
        console.error("Could not upsert share 'PUT .../shares/" + userId + "'", error);
        throw error;
      }
      const list = (this.shares[checkListId] ??= []);
      const idx = list.findIndex((s) => s.user_id === userId);
      if (idx !== -1) list.splice(idx, 1, res);
      else list.push(res);
      return res;
    },

    // Revoke a collaborator. Passing the current user's id is "leave list".
    async revokeShare(checkListId: string, userId: string): Promise<void> {
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/checklist/{checklist_id}/shares/{user_id}", {
          path: { checklist_id: checkListId, user_id: userId },
          method: "delete",
        });
      } catch (error) {
        console.error("Could not revoke share 'DELETE .../shares/" + userId + "'", error);
        throw error;
      }
      const list = this.shares[checkListId];
      if (list) {
        const idx = list.findIndex((s) => s.user_id === userId);
        if (idx !== -1) list.splice(idx, 1);
      }
    },

    async transferOwnership(
      checkListId: string,
      newOwnerId: string
    ): Promise<TransferOwnershipResultType> {
      const { $checkapi } = useNuxtApp();
      try {
        return await $checkapi("/api/checklist/{checklist_id}/transfer-ownership", {
          path: { checklist_id: checkListId },
          method: "post",
          body: { new_owner_id: newOwnerId },
        });
      } catch (error) {
        console.error("Could not transfer ownership 'POST .../transfer-ownership'", error);
        throw error;
      }
    },

    // User search for the "Add people" picker. Caller debounces; we just enforce
    // the 2-char minimum the backend expects (returns [] below it).
    async searchUsers(q: string, limit = 10): Promise<UserSearchResult[]> {
      if (q.trim().length < 2) return [];
      const { $checkapi } = useNuxtApp();
      try {
        return await $checkapi("/api/user/search", {
          method: "get",
          query: { q, limit },
        });
      } catch (error) {
        console.error("Could not search users 'GET /api/user/search'", error);
        return [];
      }
    },

    async listMyGroups(): Promise<string[]> {
      if (this.myGroups !== null) return this.myGroups;
      const { $checkapi } = useNuxtApp();
      try {
        this.myGroups = await $checkapi("/api/user/me/groups", { method: "get" });
      } catch (error) {
        console.error("Could not list groups 'GET /api/user/me/groups'", error);
        this.myGroups = [];
      }
      return this.myGroups ?? [];
    },

    async shareWithGroup(
      checkListId: string,
      group: string,
      permission: SharePermission
    ): Promise<GroupShareResult> {
      const { $checkapi } = useNuxtApp();
      let res: GroupShareResult;
      try {
        res = await $checkapi("/api/checklist/{checklist_id}/shares/group/{group}", {
          path: { checklist_id: checkListId, group },
          method: "put",
          body: { permission },
        });
      } catch (error) {
        console.error("Could not share with group 'PUT .../shares/group/" + group + "'", error);
        throw error;
      }
      // The new/raised members are now collaborators — re-read the list so the
      // modal reflects them immediately.
      await this.listShares(checkListId).catch(() => {});
      return res;
    },

    // ── ShareModal open-state plumbing (for live SSE refresh) ───────────────
    setOpen(checkListId: string | null) {
      this.openForChecklistId = checkListId;
    },

    // Called from useSync on share_added/share_removed: if the modal is open for
    // this checklist, re-read its collaborator list. Guarded to owners — the
    // GET /shares endpoint is owner-only (403 otherwise, e.g. just after the
    // current user transferred ownership away).
    refreshIfOpen(checkListId: string) {
      if (this.openForChecklistId !== checkListId) return;
      const card = useCheckListsStore().get(checkListId);
      if (card?.my_permission !== "owner") return;
      this.listShares(checkListId).catch(() => {});
    },
  },
});
