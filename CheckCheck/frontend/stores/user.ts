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
    // Belt-and-suspenders ownership check (next to card.my_permission === "owner").
    // Used by the share UI so we never offer to share a card with its owner/self.
    isOwnerOf: (state) => {
      return (card?: CheckListType | null): boolean =>
        !!card && !!state.me && card.owner_id === state.me.id;
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
