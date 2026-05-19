import { defineStore } from "pinia";

export type CheckListColorSchemeState = {
  colors: ChecklistColorSchemeType[];
};

export const useCheckListsColorSchemeStore = defineStore("checkListColorScheme", {
  state: () =>
    ({
      colors: [],
    } as CheckListColorSchemeState),
  getters: {
    getColor: (state) => {
      return (colorId: string) => state.colors.find((color) => color.id == colorId);
    },
  },
  actions: {
    async fetchColors() {
      const { $checkapi } = useNuxtApp();
      try {
        this.colors = await $checkapi("/api/color", { method: "get" });
      } catch (error) {
        console.error("Could not fetch color schemes 'GET /api/color'", error);
      }
      await this._sort();
    },
    async updateChecklistColor(checkListId: string, colorId: string | null) {
      const checkListStore = useCheckListsStore();
      await checkListStore.update(checkListId, { color_id: colorId });
    },
    async getChecklistColor(checkListId: string) {
      const checkListStore = useCheckListsStore();
      const checklist = await checkListStore.fetch(checkListId);
      return checklist.color;
    },
    async _sort() {
      // Placeholder: sort by hue once backend provides sort_order on color schemes.
      this.colors.sort((a, b) => a.backgroundcolor_dark_hex.localeCompare(b.backgroundcolor_dark_hex));
    },
  },
});
