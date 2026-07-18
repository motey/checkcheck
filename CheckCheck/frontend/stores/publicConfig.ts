import { defineStore } from "pinia";

// Server feature flags the web client needs to decide which sharing UI to
// render (P0.2). Fetched once from the unauthenticated `GET /api/public-config`
// on app mount (see pages/index.vue). Until it resolves every flag reads as
// `false`, so we never flash a button that would 404 server-side.

export type PublicConfigState = {
  config: PublicConfigType | null;
};

// In-flight GET /api/public-config, module-scoped so it's reset on every page
// load and shared across concurrent fetch() callers (dedupe without memoizing a
// stale hydrated snapshot — see the note in the fetch action).
let inflight: Promise<PublicConfigType | null> | null = null;

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
      // Always refresh from the network on a fetch() — do NOT short-circuit on
      // `this.config` being set. `config` is snapshotted to IndexedDB and
      // *hydrated* on boot (localSnapshot.ts), so an "already loaded" check here
      // conflated a stale hydrated snapshot with a completed fetch: the request
      // never ran and `server_version` (plus the feature flags) stayed frozen at
      // whatever a past session persisted — the real cause of the "stuck version"
      // in the sidebar (no amount of reload / cache:no-store / SW-clearing helped
      // because the fetch simply didn't happen). The hydrated value still shows
      // instantly on first paint and remains as the offline fallback below.
      //
      // Dedupe only *concurrent* calls (the two boot branches are mutually
      // exclusive, but keep it robust); resets each page load, so every load
      // refreshes.
      if (inflight) return inflight;
      const { $checkapi } = useNuxtApp();
      inflight = (async () => {
        try {
          // `cache: "no-store"` also keeps the browser's own HTTP cache out of the
          // loop (server sends the same header) so the refresh is always live.
          this.config = await $checkapi("/api/public-config", { method: "get", cache: "no-store" });
        } catch (error) {
          // Offline / server error — keep the hydrated (or previous) config.
          console.error("Could not fetch feature flags 'GET /api/public-config'", error);
        } finally {
          inflight = null;
        }
        return this.config;
      })();
      return inflight;
    },
  },
});
