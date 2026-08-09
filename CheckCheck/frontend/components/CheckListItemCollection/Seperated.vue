<template>
  <CardPartsItemsSection
    separated
    :collapsed="!!checkList.checked_items_collapsed"
    :edit-mode="editModeActive"
    :checked-count="checkedItemCount"
    :unchecked-count="unCheckedItemCount"
    :show-max-items="showMaxItems"
    @toggle-collapsed="switchCollapseCheckedItems()"
  >
    <template #unchecked>
      <CheckListItemCollection
        v-if="editModeActive"
        :parentCheckList="checkList"
        :showMaxItems="showMaxItems"
        :filterCheckedItems="false"
      />
      <CheckListItemCollectionPreview
        v-else
        :parentCheckList="checkList"
        :showMaxItems="showMaxItems"
        :filterCheckedItems="false"
      />
    </template>
    <template #checked="{ showMaxItems: checkedMaxItems }">
      <CheckListItemCollection
        v-if="editModeActive"
        :parentCheckList="checkList"
        :filterCheckedItems="true"
      />
      <CheckListItemCollectionPreview
        v-else
        :parentCheckList="checkList"
        :showMaxItems="checkedMaxItems"
        :filterCheckedItems="true"
      />
    </template>
  </CardPartsItemsSection>
</template>

<script setup lang="ts">
// The authed card's "separate checked items" layout: a store adapter over the
// shared, store-free CardPartsItemsSection (issue #11). The separator, the
// collapse header and the transition are shared with the `/p/<token>` viewer;
// the counts and the "persist the collapse flag on the card" write stay here.
import { computed } from "vue";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";

const checkListStore = useCheckListsStore();
const checkListItemStore = useCheckListsItemStore();
const props = defineProps({
  parentCheckList: { type: Object as PropType<CheckListType>, required: true },
  editModeActive: { type: Boolean, default: false },
  showMaxItems: { type: Number, required: false },
});

const checkList = computed(() => checkListStore.get(props.parentCheckList.id) ?? props.parentCheckList);
// Reactive: `getItemCount` returns a plain number, so these must be computed —
// captured as plain consts they freeze at mount time and never reflect items
// checked/added afterwards (both the editor label and the board-card separator
// then stick at their mount-time value, e.g. 0 on a freshly-created card).
const checkedItemCount = computed(() => checkListItemStore.getItemCount(checkList.value.id, true));
const unCheckedItemCount = computed(() => checkListItemStore.getItemCount(checkList.value.id, false));

const switchCollapseCheckedItems = () => {
  checkList.value.checked_items_collapsed = !checkList.value.checked_items_collapsed;
  (async () => {
    await checkListStore.update(checkList.value.id, {checked_items_collapsed:checkList.value.checked_items_collapsed} as CheckListUpdateType);
  })();

};
</script>

<style scoped></style>
