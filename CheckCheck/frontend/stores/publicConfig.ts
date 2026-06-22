import { defineStore } from "pinia";

// Server feature flags the web client needs to decide which sharing UI to
// render (P0.2). Fetched once from the unauthenticated `GET /api/public-config`
// on app mount (see pages/index.vue). Until it resolves every flag reads as
// `false`, so we never flash a button that would 404 server-side.

export type PublicConfigState = {
  config: PublicConfigType | null;
};

export const usePublicConfigStore = defineStore("publicConfig", {
  state: () =>
    ({
      config: null,
    } as PublicConfigState),
  getters: {
    // Master switch — hide the whole share UI (incl. the footer button) when off.
    sharingEnabled: (state): boolean => state.config?.sharing_enabled ?? false,
    // Whether owners may create anonymous public links (F3 section).
    publicLinksEnabled: (state): boolean => state.config?.sharing_public_links_enabled ?? false,
    // Whether the user-search "Add people" field is allowed.
    userSearchEnabled: (state): boolean => state.config?.sharing_user_search_enabled ?? false,
    // Whether shares start as pending invites the target must accept (invite mode).
    requireInviteAccept: (state): boolean => state.config?.sharing_require_invite_accept ?? false,
  },
  actions: {
    async fetch(): Promise<PublicConfigType | null> {
      // Already loaded — the flags don't change within a session.
      if (this.config) return this.config;
      const { $checkapi } = useNuxtApp();
      try {
        this.config = await $checkapi("/api/public-config", { method: "get" });
      } catch (error) {
        console.error("Could not fetch feature flags 'GET /api/public-config'", error);
      }
      return this.config;
    },
  },
});
