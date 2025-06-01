import { defineStore } from "pinia";
import type { components, operations } from "#open-fetch-schemas/checkapi";
import { useCheckListsItemStore } from "@/stores/checklist_item";

export type CheckListColorSchemeState = {
  colors: ChecklistColorSchemeType[];
};

export const useCheckListsColorSchemeStore = defineStore("checkListColorScheme",{
  state: () =>
    ({
      colors: [],
    } as CheckListColorSchemeState),
  getters: {
    getColor: (state) => {
      return (colorId: string) => {
        
        return state.colors.find((color) => color.id==colorId)
      };
    },
  },
  actions: {
    async fetchColors() {
      const { $checkapi } = useNuxtApp();
      try {
        const resColorSchemes = await $checkapi("/api/color", {
          method: "get",
          
        });
        this.colors = resColorSchemes
      } catch (error) {
        console.error("Could not fetch color schemes from backend 'GET /api/color'", error);
        
      }
      await this._sort()
      return;
    },
    async updateChecklistColor(
      checkListId: string,
      colorId: string | null
    ) {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      const checkListStore = useCheckListsStore()
      await checkListStore.update(checkListId,{color_id:colorId})
    },
    async getChecklistColor(
      checkListId: string,
    ) {
      const { $checkapi } = useNuxtApp();
      const checkListStore = useCheckListsStore()
      const checklist = await checkListStore.fetch(checkListId)
      return checklist.color
    },
    async _sort() {
      // this is just a placeholder for a real sorting that still has to be implemented. We want to sort by hue and generate a sort_order on the backend 
      this.colors.sort((a, b) => a.backgroundcolor_dark_hex.localeCompare(b.backgroundcolor_dark_hex));
    },
  },
});
