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
    // The server's default API-key validity in whole days (null → the default is
    // no expiry). The token manager pre-selects the matching option.
    apiTokenDefaultExpiryDays: (state): number | null =>
      state.config?.api_token_default_expiry_days ?? null,
    // Whether the token manager may offer a never-expiring key.
    apiTokenAllowNeverExpire: (state): boolean =>
      state.config?.api_token_allow_never_expire ?? false,
    // The running server's version string, surfaced in the UI. Null until the
    // config resolves.
    serverVersion: (state): string | null => state.config?.server_version ?? null,
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
