<template>
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
  <USeparator v-if="editModeActive" color="neutral" type="dashed" />
  <div v-if="editModeActive" class="flex items-center" @click="switchCollapseCheckedItems()">
    <UIcon v-if="!checkList?.checked_items_collapsed" name="i-lucide-chevrons-down" class="w-5 h-8" />
    <UIcon v-if="checkList?.checked_items_collapsed" name="i-lucide-chevrons-right" class="w-5 h-8" />
    <span class="ml-2 text-base">{{ String(checkedItemCount) }} checked items</span>
  </div>
  <USeparator color="neutral" type="dashed"
    v-if="!editModeActive && checkList?.checked_items_seperated && checkedItemCount! > 0"
    :label="`+ ${String(checkedItemCount)} checked items`"
    class="opacity-90"  :ui="{
      label: 'text-primary-500 dark:text-primary-400',
      container: { base: 'flex' },
    }"
  />
  <Collapse :when="!checkList.checked_items_collapsed || false">
    <CheckListItemCollection
      v-if="editModeActive"
      :parentCheckList="checkList"
      
      :filterCheckedItems="true"
    />
    
    <!-- On a phone preview keep the card short: don't expand the checked
         section (the "+N checked items" hint above still surfaces it; the full
         list is one tap away in the editor). -->
    <CheckListItemCollectionPreview
      v-else-if="!isMobile"
      :parentCheckList="checkList"
      :showMaxItems="showCheckedItemCount"
      :filterCheckedItems="true"
    />
  </Collapse>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useMediaQuery } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { Collapse } from "vue-collapsed";

const isMobile = useMediaQuery("(max-width: 639px)");

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

const showCheckedItemCount: ComputedRef<number | undefined> = computed(() => {
  if (props.showMaxItems) {
    if (props.showMaxItems - unCheckedItemCount.value > 0 && !checkList.value.checked_items_collapsed) {
      return props.showMaxItems - unCheckedItemCount.value;
    } else {
      return 0;
    }
  }
  return undefined;
});

const switchCollapseCheckedItems = () => {
  checkList.value.checked_items_collapsed = !checkList.value.checked_items_collapsed;
  (async () => {
    await checkListStore.update(checkList.value.id, {checked_items_collapsed:checkList.value.checked_items_collapsed} as CheckListUpdateType);
  })();

};
</script>

<style scoped></style>
