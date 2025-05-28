<template>
  <UContainer
    v-if="checkListId"
    :style="{ backgroundColor: checkList!.color?.backgroundcolor_hex, color: textColor }"
    class="checklist list-drag-handle shadow rounded gap-0 textareas-inherit-color min-h-48"
  >
    <div class="checklist-content flex-1 overflow-y-scroll">
      <UCheckbox
        v-model="checkList!.position!.checked_items_seperated!"
        @click.stop="toggleCheckedItemsSeperated()"
      />
      
      <div v-if="!editModeActive" class="flex-initial text-lg font-semibold min-h-8">{{ checkList!.name }}</div>
      <UTextarea
        v-if="editModeActive"
        autoresize
        variant="none"
        :rows="0"
        :padded="false"
        placeholder="Enter a checklist title..."
        v-model="checkList!.name!"
        class="flex-initial w-full grow pl-1 text-2xl font-semibold"
      />
      <p v-if="!editModeActive" class="line-clamp-3">{{ checkList!.text }}</p>
      <UTextarea
        v-if="editModeActive"
        ref="notesTextField"
        :autofocus="true"
        autoresize
        variant="none"
        :rows="0"
        :padded="false"
        placeholder="Enter some notes..."
        v-model="checkList!.text!"
        class="w-full grow pl-1"
      />

      <CheckListItemCollectionSeperated
        v-if="checkList?.position?.checked_items_seperated"
        :parentCheckList="checkList"
        :showMaxItems="showMaxItems"
        :editModeActive="editModeActive"
      />
      <CheckListItemCollection
        v-else-if="editModeActive"
        :parentCheckList="checkList"
        :showMaxItems="showMaxItems"
        :filterCheckedItems="undefined"
      />
      <CheckListItemCollectionPreview
        v-else
        :parentCheckList="checkList"
        :showMaxItems="showMaxItems"
        :filterCheckedItems="undefined"
      />
    </div>
    <div class="checklist-footer">
      
      <UContainer><CheckListFooter :checkListId="checkListId" /></UContainer>
    </div>
  </UContainer>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useDebounceFn } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";

const checkListsStore = useCheckListsStore();
const checkListsItemStore = useCheckListsItemStore();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
  editModeActive: { type: Boolean, default: false },
  previewModeActive: { type: Boolean, default: false },

});

var showMaxItems: Ref<number | undefined> = ref(undefined);
if (props.previewModeActive) {
  showMaxItems.value = appConfig.previewItemCount;
}




const checkList = ref(await checkListsStore.get(props.checkListId));
const textColor = checkList!.value?.color?.dark_text ? "#fff" : "#000";


const notesTextField = ref();

if (!props.previewModeActive && props.checkListId) {
  await checkListsItemStore.refreshAllCheckListItems(props.checkListId);
}

const debouncedUpdateCheckListText = useDebounceFn(
  (updatedAttrName, updatedAttrVal) => {
    if (!checkList.value) {
      return;
    }
    (async () => {
      const patchBody = {
        [updatedAttrName]: updatedAttrVal,
      };
      const res = await checkListsStore.update(checkList.value!.id, patchBody);
    })();
  },
  500,
  { maxWait: 3000 }
);

watch(
  () => checkList.value?.text,
  (t) => debouncedUpdateCheckListText("text", t!)
);
watch(
  () => checkList.value?.name,
  (n) => debouncedUpdateCheckListText("name", n!)
);

const toggleCheckedItemsSeperated = () => {
  console.log("Change seperated");
};
</script>

<style scoped>
.checklist {
  text-align: left;
}


.viewport95 {
  max-height: 95vh;
}
.textareas-inherit-color :is(textarea) {
  color: inherit !important;
}

:deep(.ring-accented) {
  --tw-ring-color: currentColor !important;
}
:deep(.border-dashed) {
  border-color: currentColor !important;
}


</style>
