import { defineStore } from "pinia";

export type CheckListLabelState = {
  labels: LabelType[];
};

export const useCheckListsLabelStore = defineStore("checkListLabelStore", {
  state: () =>
    ({
      labels: [],
    } as CheckListLabelState),
  getters: {
    getLabel: (state) => {
      return (labelId: string) => state.labels.find((label) => label.id == labelId);
    },
  },
  actions: {
    async fetchLabels() {
      const { $checkapi } = useNuxtApp();
      try {
        this.labels = await $checkapi("/api/label", { method: "get" });
      } catch (error) {
        console.error("Could not fetch labels 'GET /api/label'", error);
      }
      await this._sort();
    },
    async getChecklistLabels(checkListId: string, refresh: boolean = true) {
      const checkListStore = useCheckListsStore();
      const checklist = await checkListStore.fetch(checkListId);
      if (refresh) {
        const { $checkapi } = useNuxtApp();
        await this.fetchLabels();
        const labels = await $checkapi("/api/checklist/{checklist_id}/label", {
          method: "get",
          path: { checklist_id: checkListId },
        });
        checklist.labels = labels;
      }
      return checklist.labels;
    },
    async addCheckListLabel(checkListId: string, labelId: string) {
      const { $checkapi } = useNuxtApp();
      const checkListStore = useCheckListsStore();
      const checklist = await checkListStore.fetch(checkListId);
      try {
        const fresh_label = await $checkapi("/api/checklist/{checklist_id}/label/{label_id}", {
          method: "put",
          path: { checklist_id: checkListId, label_id: labelId },
        });
        const index = checklist.labels.findIndex((label) => label.id == labelId);
        if (index !== -1) {
          checklist.labels.splice(index, 1, fresh_label);
        } else {
          checklist.labels.push(fresh_label);
        }
      } catch (error) {
        console.error("Could not add label to checklist", error);
        throw error;
      }
      this._sort();
    },
    async removeCheckListLabel(checkListId: string, labelId: string) {
      const { $checkapi } = useNuxtApp();
      const checkListStore = useCheckListsStore();
      const checklist = await checkListStore.fetch(checkListId);
      try {
        await $checkapi("/api/checklist/{checklist_id}/label/{label_id}", {
          method: "delete",
          path: { checklist_id: checkListId, label_id: labelId },
        });
        const index = checklist.labels.findIndex((label) => label.id == labelId);
        if (index !== -1) checklist.labels.splice(index, 1);
      } catch (error) {
        console.error("Could not remove label from checklist", error);
        throw error;
      }
    },
    async createLabel(label: LabelCreateType) {
      const { $checkapi } = useNuxtApp();
      try {
        const fresh_label = await $checkapi("/api/label", { method: "post", body: label });
        this.labels.push(fresh_label);
        this._sort();
        return fresh_label;
      } catch (error) {
        console.error("Could not create label 'POST /api/label'", error);
        throw error;
      }
    },
    async updateLabel(labelId: string, label: LabelUpdateType) {
      const { $checkapi } = useNuxtApp();
      try {
        const fresh_label = await $checkapi("/api/label/{label_id}", {
          method: "patch",
          path: { label_id: labelId },
          body: label,
        });
        const index = this.labels.findIndex((l) => l.id == labelId);
        if (index !== -1) this.labels.splice(index, 1, fresh_label);
        this._sort();
      } catch (error) {
        console.error("Could not update label 'PATCH /api/label/" + labelId + "'", error);
        throw error;
      }
    },
    async deleteLabel(labelId: string) {
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/label/{label_id}", { method: "delete", path: { label_id: labelId } });
        const index = this.labels.findIndex((label) => label.id === labelId);
        if (index !== -1) this.labels.splice(index, 1);
      } catch (error) {
        console.error("Could not delete label 'DELETE /api/label/" + labelId + "'", error);
        throw error;
      }
    },
    async sortLabels(orderedLabelIds: string[]) {
      // `orderedLabelIds` is the desired display order (top -> bottom). Labels
      // are displayed by descending sort_order (see _sort), while the backend
      // assigns ascending sort_order (10, 20, …) to the ids in the order it is
      // given. Reverse so the top-most label ends up with the highest
      // sort_order and the persisted order matches the visual order.
      const { $checkapi } = useNuxtApp();
      try {
        const fresh_labels = await $checkapi("/api/label/sort", {
          method: "put",
          body: [...orderedLabelIds].reverse(),
        });
        this.labels = fresh_labels;
        this._sort();
      } catch (error) {
        console.error("Could not sort labels 'PUT /api/label/sort'", error);
        throw error;
      }
    },
    async _sort() {
      this.labels.sort((a, b) => b.sort_order! - a.sort_order!);
    },
  },
});
