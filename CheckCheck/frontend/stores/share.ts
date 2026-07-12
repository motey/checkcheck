import { defineStore } from "pinia";
import { assertOnline } from "@/utils/connectivity";

// Collaborator (per-user) share management, keyed by checklist id.
//
// Mirrors the backend Phase 3/4/10 endpoints. The card's own `my_permission`
// (P0.1) still gates *whether* the management UI is shown; this store only deals
// in the collaborator list + the share mutations the owner performs.
//
// `openForChecklistId` lets useSync refresh the list live: while the ShareModal
// is mounted it records its checklist id, and a `share_added`/`share_removed`
// SSE event for that id re-reads the list (see refreshIfOpen).
//
// Every call here passes `skipErrorToast: true` (F7): this store fully owns its
// error UX — the ShareModal call sites surface friendly, status-aware messages
// ("You can only share with groups you belong to", "This user already has
// access", …), and the read/search calls deliberately swallow. Without the
// opt-out the global handler in `plugins/api.ts` would stack a generic
// "Error <code>" toast on top of those (it runs before any per-call handler).

export type ShareState = {
  // Collaborator lists keyed by checklist id (owner row excluded — see backend).
  shares: Record<string, ShareReadType[]>;
  // Public (anonymous URL) links keyed by checklist id. Tokens are redacted by
  // the backend on list/update — they only ever exist in a createLink result.
  links: Record<string, PublicLinkReadType[]>;
  // Tokens captured at creation, keyed by link id. The server never returns a
  // token again, so this is purely an in-memory convenience: links created while
  // the app is open stay copyable from the list. Lost on reload (by design — the
  // token is a capability we don't persist).
  linkTokens: Record<string, string>;
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
      links: {},
      linkTokens: {},
      openForChecklistId: null,
      myGroups: null,
    } as ShareState),
  getters: {
    // The cached collaborator list for a checklist (empty until listShares runs).
    sharesFor: (state) => {
      return (checkListId: string): ShareReadType[] => state.shares[checkListId] ?? [];
    },
    // The cached public-link list for a checklist (empty until listLinks runs).
    linksFor: (state) => {
      return (checkListId: string): PublicLinkReadType[] => state.links[checkListId] ?? [];
    },
    // The token captured for a link at creation (this session only), or null.
    tokenFor: (state) => {
      return (linkId: string): string | null => state.linkTokens[linkId] ?? null;
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
          skipErrorToast: true,
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
      assertOnline("Sharing isn't available offline.");
      const { $checkapi } = useNuxtApp();
      let res: ShareReadType;
      try {
        res = await $checkapi("/api/checklist/{checklist_id}/shares/{user_id}", {
          path: { checklist_id: checkListId, user_id: userId },
          method: "put",
          body: { permission },
          skipErrorToast: true,
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
      assertOnline("Removing access isn't available offline.");
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/checklist/{checklist_id}/shares/{user_id}", {
          path: { checklist_id: checkListId, user_id: userId },
          method: "delete",
          skipErrorToast: true,
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
      assertOnline("Transferring ownership isn't available offline.");
      const { $checkapi } = useNuxtApp();
      try {
        return await $checkapi("/api/checklist/{checklist_id}/transfer-ownership", {
          path: { checklist_id: checkListId },
          method: "post",
          body: { new_owner_id: newOwnerId },
          skipErrorToast: true,
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
          skipErrorToast: true,
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
        this.myGroups = await $checkapi("/api/user/me/groups", {
          method: "get",
          skipErrorToast: true,
        });
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
      assertOnline("Sharing isn't available offline.");
      const { $checkapi } = useNuxtApp();
      let res: GroupShareResult;
      try {
        res = await $checkapi("/api/checklist/{checklist_id}/shares/group/{group}", {
          path: { checklist_id: checkListId, group },
          method: "put",
          body: { permission },
          skipErrorToast: true,
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

    // ── Public (anonymous URL) links — owner-only, F3 ───────────────────────
    // Tokens are redacted everywhere except the createLink result below. There
    // is no way to recover a token after creation; the owner must delete and
    // recreate a link to get a fresh URL.

    async listLinks(checkListId: string): Promise<PublicLinkReadType[]> {
      const { $checkapi } = useNuxtApp();
      let res: PublicLinkReadType[];
      try {
        res = await $checkapi("/api/checklist/{checklist_id}/public-links", {
          path: { checklist_id: checkListId },
          method: "get",
          skipErrorToast: true,
        });
      } catch (error) {
        console.error("Could not list public links 'GET .../public-links'", error);
        throw error;
      }
      this.links[checkListId] = res;
      return res;
    },

    // Create a link. The returned token is the ONE chance to capture the URL —
    // the caller must surface it immediately. We cache the link (without the
    // token) so the list reflects it right away.
    async createLink(
      checkListId: string,
      body: PublicLinkCreateReq
    ): Promise<PublicLinkCreateRes> {
      assertOnline("Creating a public link isn't available offline.");
      const { $checkapi } = useNuxtApp();
      let res: PublicLinkCreateRes;
      try {
        res = await $checkapi("/api/checklist/{checklist_id}/public-links", {
          path: { checklist_id: checkListId },
          method: "post",
          body,
          skipErrorToast: true,
        });
      } catch (error) {
        console.error("Could not create public link 'POST .../public-links'", error);
        throw error;
      }
      const { token, ...redacted } = res;
      const list = (this.links[checkListId] ??= []);
      list.unshift(redacted);
      // Retain the token in memory so the new link stays copyable from the list
      // (the server won't ever hand it back). Lost on reload — by design.
      this.linkTokens[res.id] = token;
      return res;
    },

    async updateLink(
      checkListId: string,
      linkId: string,
      patch: PublicLinkUpdateReq
    ): Promise<PublicLinkReadType> {
      assertOnline("Updating a public link isn't available offline.");
      const { $checkapi } = useNuxtApp();
      let res: PublicLinkReadType;
      try {
        res = await $checkapi("/api/checklist/{checklist_id}/public-links/{link_id}", {
          path: { checklist_id: checkListId, link_id: linkId },
          method: "patch",
          body: patch,
          skipErrorToast: true,
        });
      } catch (error) {
        console.error("Could not update public link 'PATCH .../public-links/" + linkId + "'", error);
        throw error;
      }
      const list = (this.links[checkListId] ??= []);
      const idx = list.findIndex((l) => l.id === linkId);
      if (idx !== -1) list.splice(idx, 1, res);
      else list.push(res);
      return res;
    },

    async deleteLink(checkListId: string, linkId: string): Promise<void> {
      assertOnline("Deleting a public link isn't available offline.");
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/checklist/{checklist_id}/public-links/{link_id}", {
          path: { checklist_id: checkListId, link_id: linkId },
          method: "delete",
          skipErrorToast: true,
        });
      } catch (error) {
        console.error("Could not delete public link 'DELETE .../public-links/" + linkId + "'", error);
        throw error;
      }
      const list = this.links[checkListId];
      if (list) {
        const idx = list.findIndex((l) => l.id === linkId);
        if (idx !== -1) list.splice(idx, 1);
      }
      delete this.linkTokens[linkId];
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
