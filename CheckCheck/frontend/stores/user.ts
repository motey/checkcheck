import { defineStore } from "pinia";

export type UserState = {
  me: UserType | null;
  // The caller's own API keys (redacted read models — never a plaintext token).
  apiKeys: ApiKeyType[];
};

export const useUserStore = defineStore("user", {
  state: () =>
    ({
      me: null,
      apiKeys: [],
    } as UserState),
  getters: {
    // The current user's id, or null before fetchMe() resolves.
    myId(state): string | null {
      return state.me?.id ?? null;
    },
  },
  actions: {
    async fetchMe(): Promise<UserType | null> {
      const { $checkapi } = useNuxtApp();
      try {
        this.me = await $checkapi("/api/user/me", { method: "get" });
      } catch (error) {
        console.error("Could not fetch current user 'GET /api/user/me'", error);
      }
      return this.me;
    },

    // ── API keys ──────────────────────────────────────────────────────────
    // These actions own their own error UX (the ApiKeysModal shows toasts), so
    // they pass `skipErrorToast: true` and re-throw for the caller to handle.

    async listApiKeys(): Promise<ApiKeyType[]> {
      const { $checkapi } = useNuxtApp();
      try {
        this.apiKeys = await $checkapi("/api/user/me/api-keys", {
          method: "get",
          skipErrorToast: true,
        });
      } catch (error) {
        console.error("Could not list API keys 'GET /api/user/me/api-keys'", error);
        throw error;
      }
      return this.apiKeys;
    },

    // Create a key. The plaintext `token` on the response is returned exactly
    // once by the server — we hand it straight back to the caller and never
    // persist it in the store (the stored list only ever holds redacted keys).
    async createApiKey(body: ApiKeyCreateReq): Promise<ApiKeyCreatedType> {
      const { $checkapi } = useNuxtApp();
      let res: ApiKeyCreatedType;
      try {
        res = await $checkapi("/api/user/me/api-keys", {
          method: "post",
          body,
          skipErrorToast: true,
        });
      } catch (error) {
        console.error("Could not create API key 'POST /api/user/me/api-keys'", error);
        throw error;
      }
      // Store only the redacted view (drop the plaintext token before it lands
      // in reactive state).
      const { token, ...redacted } = res;
      this.apiKeys.unshift(redacted);
      return res;
    },

    // Revoke by the key's `api_token_id` (the identifier prefix the delete
    // endpoint keys on — NOT the row `id`).
    async revokeApiKey(apiTokenId: string): Promise<void> {
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/user/me/api-keys/{api_token_id}", {
          path: { api_token_id: apiTokenId },
          method: "delete",
          skipErrorToast: true,
        });
      } catch (error) {
        console.error(
          "Could not revoke API key 'DELETE /api/user/me/api-keys/{id}'",
          error
        );
        throw error;
      }
      this.apiKeys = this.apiKeys.filter((k) => k.api_token_id !== apiTokenId);
    },
  },
});
