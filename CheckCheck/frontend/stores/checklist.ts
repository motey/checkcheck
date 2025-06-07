import { defineStore } from "pinia";
import type { components, operations } from "#open-fetch-schemas/checkapi";
import { useCheckListsItemStore } from "@/stores/checklist_item";

export type CheckListState = {
  checkLists: CheckListType[];
  total_backend_count: number;
  pending_checklist: CheckListCreateType | null;
  filterLabelId: String | null;
};
/*
checkList: {
    type: Object as PropType<CheckListType>,
        required: true,
    },
items: {
    type: Array as PropType<components["schemas"]["CheckListItemRead"][]>,
        required: false,
    },
        total_backend_count: number;
*/
export const useCheckListsStore = defineStore("checkList", {
  state: () =>
    ({
      checkLists: [],
      total_backend_count: -1,
      pending_checklist: null,
      filterLabelId: null,
    } as CheckListState),
  getters: {
    checklist_ids(state) {
      if (state.checkLists != undefined) {
        return state.checkLists.map((item) => item.id);
      }
      return [];
    },
    getCheckLists: (state) => {
      return (archived: boolean | null = null, limit: number | null = null) => {
        const filterLabelId = state.filterLabelId;
        if (archived === null && limit === null && state.filterLabelId === null) {
          return [...state.checkLists];
        }
    
        const filtered = state.checkLists.filter((item) => {
          if (archived !== null && item.position.archived !== archived) {
            return false;
          }
    
          if (filterLabelId !== null && !item.labels.some(label => label.id === filterLabelId)) {
            return false;
          }
    
          return true;
        });
    
        return (limit !== null && limit > 0) ? filtered.slice(0, limit) : filtered;
      };
    },
    get: (state) => {
      return (checkListId: string) => {
        return state.checkLists[state.checkLists.findIndex((checklist) => checklist.id == checkListId)];
      };
    },
  },
  actions: {
    setFilterLabel(labelId: String | null) {
      this.filterLabelId = labelId
    },
    async reorderCheckLists(newOrder: CheckListType[], movedItem: CheckListType) {
      const { $sortBySubset, $findNewPlacementForItem } = useNuxtApp(); // external helper, e.g., sorts items by subset order

      const placement = $findNewPlacementForItem(movedItem, newOrder);
      console.log(placement);
      if (placement.placement == "above") {
        const neighbor = placement.target_neighbor_item as CheckListType;
        await this.moveCheckListAboveOtherCheckList(movedItem, neighbor);
      } else if (placement.placement == "below") {
        const neighbor = placement.target_neighbor_item as CheckListType;
        await this.moveCheckListUnderOtherCheckList(movedItem, neighbor);
      }
      const new_order_with_new_index_from_server = $sortBySubset(this.checkLists, newOrder) as CheckListType[];
      this.checkLists = new_order_with_new_index_from_server;
    },
    async create(checklist: CheckListCreateType): Promise<CheckListType> {
      if (!checklist) throw new Error("Checklistid empty");
      const { $checkapi } = useNuxtApp();
      var resChecklist: CheckListType;
      try {
        resChecklist = await $checkapi("/api/checklist", { method: "post", body: checklist });
      } catch (error) {
        console.error(checklist);
        console.error(error);
        throw new Error("Could not store new checklist to backend 'PATCH /checklist'");
      }
      const checkListItemStore = useCheckListsItemStore();
      await checkListItemStore.refreshAllCheckListItems(resChecklist.id);
      this.checkLists.push(resChecklist);
      this._sort();

      return resChecklist;
    },
    async update(checkListId: string, checklist: CheckListUpdateType): Promise<CheckListType | undefined> {
      if (!checklist) return;
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resChecklist: CheckListType;
      try {
        resChecklist = await $checkapi("/api/checklist/{checklist_id}", {
          path: { checklist_id: checkListId },
          method: "patch",
          body: checklist,
        });
      } catch (error) {
        console.error(checklist);
        console.error("Could not store updated checklist to backend 'PATCH /checklist/" + checkListId + "'", error);
        return checklist as CheckListType;
      }
      // get index of existing checkList in store
      var index = this.checkLists.findIndex((checklist) => checklist.id == resChecklist.id);

      // replace-update the new checklist in state
      if (index !== -1) {
        $transferAttrs(resChecklist, this.checkLists[index]!);
        //this.checkLists.splice(index, 1, resChecklist); // Replace the item in the store
      } else {
        this.checkLists.push(resChecklist);
        await this._sort();
      }
      return resChecklist;
    },
    async refresh(checkListId: string): Promise<CheckListType> {
      if (!checkListId) throw new Error("Checklistid empty"); // do we need that?
      const { $checkapi } = useNuxtApp();
      var resChecklist: CheckListType;
      try {
        resChecklist = await $checkapi("/api/checklist/{checklist_id}", {
          path: { checklist_id: checkListId },
          method: "get",
        });
      } catch (error) {
        console.error("Could not refresh checklist from backend 'GET /checklist/" + checkListId + "'", error);
        throw new Error("Could not refresh checklist from backend");
      }
      var index = this.checkLists.findIndex((checklist) => checklist.id == resChecklist.id);
      if (index !== -1) {
        this.checkLists.splice(index, 1, resChecklist); // Replace the item in the store
      } else {
        this.checkLists.push(resChecklist);
      }
      return resChecklist;
    },
    async fetch(checkListId: string): Promise<CheckListType> {
      if (!checkListId) throw new Error("Checklistid empty"); // do we need that?
      var index = this.checkLists.findIndex((checklist) => checklist.id == checkListId);
      if (index == -1) {
        return await this.refresh(checkListId);
      }
      return this.checkLists[index]!;
    },
    async archive(checkListId: string, state: boolean = true) {
      if (!checkListId) throw new Error("Checklistid empty"); // do we need that?
      const checkList = await this.fetch(checkListId);
      checkList.position.archived = state;
      checkList.position = await this.updatePosition(checkListId, checkList.position);
    },
    async fetchNextPage() {
      const { $checkapi } = useNuxtApp();
      var resChecklistPage: CheckListsPageType;
      var fetched_count: number = this.checkLists.length;
      const checkListItemStore = useCheckListsItemStore();
      try {
        resChecklistPage = await $checkapi("/api/checklist", {
          method: "get",
          query: { offset: fetched_count, limit: 10 },
        });
      } catch (error) {
        console.error("Could not fetch next checklist page from backend 'GET /api/checklist'", error);
        return;
      }
      await checkListItemStore.fetchMultipleChecklistsItemsPreview(resChecklistPage.items.map((item) => item.id));

      this.checkLists = [...this.checkLists, ...resChecklistPage.items];
      this.total_backend_count = resChecklistPage.total_count;
      return resChecklistPage.items;
    },

    async moveCheckListUnderOtherCheckList(
      itemToMove: CheckListType,
      otherItem: CheckListType
    ): Promise<CheckListPositionType> {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resPos: CheckListItemPositionType;
      try {
        resPos = await $checkapi("/api/checklist/{checklist_id}/move/under/{other_checklist_id}", {
          path: { checklist_id: itemToMove.id, other_checklist_id: otherItem.id },
          method: "put",
        });
      } catch (error) {
        console.error(
          "Could not update checklist position to backend 'PATCH /checklist/" +
            itemToMove.id +
            "/item/" +
            itemToMove.id +
            "/move/under/" +
            otherItem.id +
            "'",
          error
        );
        throw new Error("Could not update checklist position to backend");
      }
      $transferAttrs(resPos, itemToMove.position);
      this._sort();
      return itemToMove.position;
    },
    async moveCheckListAboveOtherCheckList(
      itemToMove: CheckListType,
      otherItem: CheckListType
    ): Promise<CheckListPositionType> {
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resPos: CheckListItemPositionType;

      try {
        resPos = await $checkapi("/api/checklist/{checklist_id}/move/above/{other_checklist_id}", {
          path: { checklist_id: itemToMove.id, other_checklist_id: otherItem.id },
          method: "put",
        });
      } catch (error) {
        console.error(
          "Could not update checklist position to backend 'PATCH /checklist/" +
            itemToMove.id +
            "/item/" +
            itemToMove.id +
            "/move/above/" +
            otherItem.id +
            "'",
          error
        );
        throw new Error("Could not update checklist position to backend");
      }
      $transferAttrs(resPos, itemToMove.position);
      this._sort();
      return itemToMove.position;
    },
    async updatePosition(
      checkListId: string,
      checklistPosition: CheckListPositionUpdateType
    ): Promise<CheckListPositionType> {
      if (!checkListId) throw new Error("checkListId empty");
      const { $checkapi, $transferAttrs } = useNuxtApp();
      var resChecklistPosition: CheckListPositionType;
      try {
        resChecklistPosition = await $checkapi("/api/checklist/{checklist_id}/position", {
          path: { checklist_id: checkListId },
          method: "patch",
          body: checklistPosition,
        });
      } catch (error) {
        console.error(checklistPosition);
        console.error(
          "Could not store updated checklistPosition to backend 'PATCH /checklist/" + checkListId + "/position'",
          error
        );
        throw new Error("Could not store updated checklistPosition to backend");
      }
      // get index of existing checkList in store
      var index = this.checkLists.findIndex((checklist) => checklist.id == checkListId);
      var checkList: CheckListType;
      if (index !== -1) {
        checkList = this.checkLists[index]!;
      } else {
        checkList = await this.refresh(checkListId);
      }
      $transferAttrs(resChecklistPosition, checkList.position);
      return checkList.position;
    },
    async _sort() {
      this.checkLists.sort((a, b) => b.position.index - a.position.index);
    },
  },
});
