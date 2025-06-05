import { defineStore } from "pinia";
import type { components, operations } from "#open-fetch-schemas/checkapi";
import { useCheckListsItemStore } from "@/stores/checklist_item";

export type CheckListLabelState = {
  labels: Label[];
};

export const useCheckListsLabelStore = defineStore("checkListLabelStore", {
  state: () =>
    ({
      labels: [],
    } as CheckListLabelState),
  getters: {
    getLabel: (state) => {
      return (labelId: string) => {
        return state.labels.find((label) => label.id == labelId);
      };
    },
  },
  actions: {
    async fetchLabels() {
      const { $checkapi } = useNuxtApp();
      try {
        const resLabels = await $checkapi("/api/label", {
          method: "get",
        });
        this.labels = resLabels;
      } catch (error) {
        console.error("Could not fetch checklist labels from backend 'GET /api/label'", error);
      }
      await this._sort();
      return;
    },

    async getChecklistLabels(checkListId: string) {
      const checkListStore = useCheckListsStore();
      const checklist = await checkListStore.fetch(checkListId);
      return checklist.labels;
    },
    async addCheckListLabel(checkListId: string, labelId: string) {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      const checkListStore = useCheckListsStore();
      const checklist = await checkListStore.fetch(checkListId);
      const fresh_label = await $checkapi("/api/checklist/{checklist_id}/label/{label_id}", {
        method: "put",
        path: { checklist_id: checkListId, label_id: labelId },
      });
      const old_label = checklist.labels.find((label) => label.id == labelId);
      if (old_label != undefined) {
        // label is allready attached but we refresh its data from the server for good measure
        $transferAttrs(fresh_label, old_label);
      } else {
        checklist.labels.push(fresh_label);
      }
        this._sort()
    },
    async removeCheckListLabel(checkListId: string, labelId: string) {
        const { $checkapi } = useNuxtApp();
        const checkListStore = useCheckListsStore();
        const checklist = await checkListStore.fetch(checkListId);
        const fresh_label = await $checkapi("/api/checklist/{checklist_id}/label/{label_id}", {
          method: "delete",
          path: { checklist_id: checkListId, label_id: labelId },
        });
        const old_label_index = checklist.labels.findIndex((label) => label.id == labelId);
        if (old_label_index !== -1) {
          checklist.labels.splice(old_label_index,1);
        }
    },
    async _sort() {
      // this is just a placeholder for now
        //this.labels.sort((a, b) => a.sort_order - b.sort_order);
        this.labels.sort((a, b) => a.display_name.localeCompare(b.display_name));
    },
  },
});
