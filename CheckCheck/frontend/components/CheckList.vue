<template>
  <UContainer
    v-if="checkListId"
    :style="{ color: textColor, backgroundColor: backgroundColor, borderColor: accentColor }"
    :class="backgroundColor"
    class="checklist list-drag-handle shadow rounded gap-0 textareas-inherit-color min-h-48  flex flex-col border-1 border-solid px-4 sm:p-4 lg:p-6 lg:pb-1 sm:pb-1"
  >

    <div v-if="!editModeActive" class="flex-none text-lg font-semibold min-h-8">
      {{ checkList!.name }}
    </div>
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
    <p v-if="!editModeActive" class="flex-none line-clamp-3">{{ checkList!.text }}</p>
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
      class="w-full flex-none pl-1"
    />
    <div class="checklist-items-collection max-h-[90vm] overflow-y-scroll">
      <CheckListItemCollectionSeperated
        v-if="checkList?.position?.checked_items_seperated"
        :parentCheckList="checkList"
        :showMaxItems="showMaxItems"
        :editModeActive="editModeActive"
      />
      <CheckListItemCollection
        v-else-if="editModeActive"
        :parentCheckList="checkList!"
        :showMaxItems="showMaxItems"
        :filterCheckedItems="undefined"
      />
      <CheckListItemCollectionPreview
        v-else
        :parentCheckList="checkList!"
        :showMaxItems="showMaxItems"
        :filterCheckedItems="undefined"
      />
    </div>

    <div class="checklist-footer flex-none">
      <CheckListFooter :checkListId="checkListId" />
    </div>
  </UContainer>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useDebounceFn } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
const colorMode = useColorMode();

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

const textColor = computed(() => {
  const { color } = checkList.value!;
  const isDarkModeEnabled = colorMode.value === "dark";

  if (color) {
    return isDarkModeEnabled ? color.textcolor_dark_hex : color.textcolor_light_hex;
  }
  // Checklist has not color theme applied. lets just return a contrast color the background
  return isDarkModeEnabled ? "#fff" : "#000";
});
const accentColor = computed(() => {
  const { color } = checkList.value!;
  const isDarkModeEnabled = colorMode.value === "dark";

  if (color) {
    return isDarkModeEnabled ? color.accentcolor_dark_hex : color.accentcolor_light_hex;
  }
  // Checklist has not color theme applied. lets just return a contrast color the background
  return isDarkModeEnabled ? "#fff" : "#000";
});
const backgroundColor = computed(() => {
  const { color } = checkList.value!;
  const isDarkModeEnabled = colorMode.value === "dark";

  return color ? (isDarkModeEnabled ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex) : "";
});

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
