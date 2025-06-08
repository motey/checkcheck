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
    <UIcon v-if="!checkList?.checked_items_collapsed" name="i-heroicons-chevron-double-down" class="w-5 h-8" />
    <UIcon v-if="checkList?.checked_items_collapsed" name="i-heroicons-chevron-double-right" class="w-5 h-8" />
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
    
    <CheckListItemCollectionPreview
      v-else
      :parentCheckList="checkList"
      :showMaxItems="showCheckedItemCount"
      :filterCheckedItems="true"
    />
  </Collapse>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { Collapse } from "vue-collapsed";

const checkListStore = useCheckListsStore();
const checkListItemStore = useCheckListsItemStore();
const props = defineProps({
  parentCheckList: { type: Object as PropType<CheckListType>, required: true },
  editModeActive: { type: Boolean, default: false },
  showMaxItems: { type: Number, required: false },
});

const checkList = ref(await checkListStore.fetch(props.parentCheckList.id));
const checkedItemCount = checkListItemStore.getItemCount(checkList.value.id, true);
const unCheckedItemCount = checkListItemStore.getItemCount(checkList.value.id, false);

const showCheckedItemCount: ComputedRef<number | undefined> = computed(() => {
  if (props.showMaxItems) {
    if (props.showMaxItems - unCheckedItemCount > 0 && !checkList.value.checked_items_collapsed) {
      return props.showMaxItems - unCheckedItemCount;
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

  console.log("SWITCH COLLAPSE");
};
</script>

<style scoped></style>
