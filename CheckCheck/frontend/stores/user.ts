import { defineStore } from "pinia";

export type UserState = {
  me: UserType | null;
};

export const useUserStore = defineStore("user", {
  state: () =>
    ({
      me: null,
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
  },
});
