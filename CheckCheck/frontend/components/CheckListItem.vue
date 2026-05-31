<template>
  <UContainer class="flex mb-0 grow break-after-column" @mouseover="hover = true" @mouseleave="hover = false">
    <div class="flex-none">
      <span :class="{ nonActive: !hover }" class="list-item-drag-handle" title="Drag to reorder" :id="checkListItem!.id" v-if="parentEditMode">
        <UIcon  name="i-mdi-drag" class="w-6 h-6 cursor-row-resize" />
      </span>
    </div>
    <div class="flex-none w-4">
      <UCheckbox v-model="checkListItem!.state.checked" @click.stop="toggleCheck()" size="xl"  />
    </div>
    <div v-if="!parentEditMode" :class="[' pl-2', { 'line-clamp-3': !parentEditMode, 'strikethrough': checkListItem?.state.checked }]" class=""
      v-html="highlightText(checkListItem!.text, searchQuery)" />

    <UTextarea
      placeholder="Enter some text..."
      v-model="localText"
      v-if="parentEditMode"
      variant="none"
      autoresize
      :rows="1"
      :padded="false"
      :style="{ color: textColor }"
      class="w-full grow pl-2 cursor-auto m-0"
      :class="{ strikethrough: checkListItem?.state.checked }"
      @focus="textFocused = true"
      @blur="textFocused = false"
    />
  </UContainer>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useDebounceFn } from "@vueuse/core";
import type { PropType } from "vue";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useTextareaAutosize } from '@vueuse/core'
import { highlightText } from "@/utils/highlight";

const props = defineProps({
  checkListItem: { type: Object as PropType<CheckListItemType>, required: false },
  parentCheckList: { type: Object as PropType<CheckListType>, required: true },
  parentEditMode: { type: Boolean, watch: true },
});
/* 
//TODO: This does not work to give texteares with long single rows the correct height. 
// we need to investigate deeper
const textarea = ref<HTMLTextAreaElement | null>(null) //add `ref="textarea"` to the textaeare component
const text = computed(() => props.checkListItem?.text ?? '')
onMounted(() => {
  if (textarea.value) {
    useTextareaAutosize({element:textarea, watch: text })
  }
})
*/
const hover = ref(false);
const textFocused = ref(false);
const checkListsItemStore = useCheckListsItemStore();
const route = useRoute();
const searchQuery = computed(() => (route.query.search as string) || null);
const emit = defineEmits(["checkedItem"]);
function toggleCheck() {
  (async () => {
    await checkListsItemStore.updateState(props.parentCheckList.id, props.checkListItem!.id, {
      checked: !props.checkListItem!.state.checked,
    } as CheckListItemStateUpdateType);
  })();
  emit("checkedItem");
}

let textColor = props.parentCheckList!.color?.dark_text ? "#fff" : "#000";

emit("checkedItem");

// Local copy decoupled from the store so SSE/update patches don't wipe
// text the user is currently typing.
const localText = ref(props.checkListItem!.text ?? '');

// Sync FROM store only when the field is not focused.
watch(
  () => props.checkListItem!.text,
  (serverText) => {
    if (!textFocused.value) localText.value = serverText ?? '';
  }
);

const debouncedUpdateCheckListItemText = useDebounceFn(
  (val: string) => {
    if (!props.checkListItem) return;
    (async () => {
      await checkListsItemStore.update(props.parentCheckList.id, props.checkListItem!.id, { text: val } as CheckListUpdateType);
    })();
  },
  500,
  { maxWait: 3000 }
);

watch(localText, (t) => debouncedUpdateCheckListItemText(t));


</script>

<style scoped>
.nonActive {
    opacity: 0.3;
  /*visibility: hidden;*/
}
.strikethrough {
  text-decoration: line-through;
}
::v-deep(.strikethrough textarea) {
  text-decoration: line-through;
}
textarea {
  max-height: none !important;
  height: auto !important;
  overflow-wrap: break-word;
  word-break: break-word; /* For older browsers */
  white-space: pre-wrap;  /* Preserves line breaks + allows wrapping */
}
</style>
