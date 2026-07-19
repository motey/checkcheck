<template>
  <div>
  <div class="checklist-item-row flex items-start gap-1.5 py-0.5" data-testid="item-row" @mouseover="hover = true" @mouseleave="hover = false">
    <span
      v-if="parentEditMode && canEdit"
      :class="{ nonActive: !hover }"
      class="list-item-drag-handle flex-none self-center transition-opacity touch-none select-none"
      title="Drag to reorder"
      :id="checkListItem!.id"
    >
      <UIcon name="i-lucide-grip-vertical" class="w-6 h-6 sm:w-5 sm:h-5 cursor-row-resize" />
    </span>
    <div class="flex-none flex items-center self-stretch min-h-5 sm:min-h-6" :title="canCheck ? undefined : 'View only'">
      <UCheckbox v-model="checkListItem!.state.checked" :disabled="!canCheck" @click.stop="toggleCheck()" :size="isMobile ? 'sm' : 'md'" />
    </div>
    <!-- Rendered (Markdown) view. Shown on the board, and inside the open card
         whenever this item is not the one being edited — clicking or tabbing in
         swaps to the raw textarea below so the user edits the source. -->
    <div
      v-if="showRenderedText"
      class="md-inline min-w-0 flex-1 pt-0.5 break-words"
      :class="[
        checkListItem?.state.checked ? 'strikethrough' : '',
        // Board preview: one-liners truncate at card width; items with an
        // authored newline get two lines. In the open card show the full text so
        // the rendered row matches the textarea it swaps with.
        parentEditMode
          ? 'whitespace-pre-wrap'
          : previewHasNewline ? 'whitespace-pre-wrap line-clamp-2' : 'line-clamp-1',
        parentEditMode && canEdit
          ? 'cursor-text rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary'
          : '',
      ]"
      :role="parentEditMode && canEdit ? 'textbox' : undefined"
      :tabindex="parentEditMode && canEdit ? 0 : undefined"
      data-testid="item-text-rendered"
      @click="onItemTextClick"
      @keydown.enter.prevent="enterEdit()"
      @keydown.space.prevent="enterEdit()"
    >
      <span v-if="localText" v-html="renderMarkdownInline(localText, { search: searchQuery })" />
      <span v-else-if="parentEditMode" class="text-dimmed">Enter some text...</span>
    </div>

    <UTextarea
      ref="textareaComp"
      placeholder="Enter some text..."
      v-model="localText"
      v-if="parentEditMode && editingText && canEdit"
      variant="none"
      autoresize
      :rows="1"
      :padded="false"
      :style="{ color: textColor }"
      class="min-w-0 flex-1 grow cursor-auto m-0 pt-0.5"
      :class="{ strikethrough: checkListItem?.state.checked }"
      data-testid="item-text-editor"
      @focus="onTextFocus"
      @blur="onTextBlur"
      @keydown.enter="onEnter"
      @keydown.delete="onBackspace"
    />
    <button
      v-if="parentEditMode && canEdit"
      type="button"
      data-testid="delete-item"
      :class="{ nonActive: !hover }"
      class="list-item-delete flex-none self-center transition-opacity text-gray-400 hover:text-red-500"
      title="Delete item"
      @click.stop="onDelete(false)"
    >
      <UIcon name="i-lucide-x" class="w-5 h-5" />
    </button>
  </div>
    <!-- Keep-style autocomplete: while typing a new item, list existing *checked*
         items whose text starts with what's been typed so far, so the user can
         uncheck one instead of creating a duplicate. Detection is fully
         client-side (reads the in-memory item store); accepting is the only
         write. Gated per-card via suggest_existing_items; mousedown.prevent keeps
         the textarea focused so clicking a row doesn't dismiss the list mid-click. -->
    <ul
      v-if="parentEditMode && suggestions.length"
      data-testid="uncheck-suggestions"
      class="ml-8 mb-1 flex flex-col rounded-md border border-current/10 bg-current/5 overflow-hidden"
    >
      <li v-for="s in suggestions" :key="s.id">
        <button
          type="button"
          data-testid="uncheck-suggestion"
          class="flex w-full items-center gap-1.5 px-2 py-1 text-xs text-left opacity-80 hover:opacity-100 hover:bg-current/10 transition cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"
          @mousedown.prevent.stop="onAcceptSuggestion(s)"
        >
          <UIcon name="i-lucide-corner-down-left" class="flex-none size-4" />
          <span class="min-w-0 truncate">Uncheck &ldquo;{{ s.text }}&rdquo;</span>
        </button>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useDebounceFn, useMediaQuery } from "@vueuse/core";
import type { PropType } from "vue";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useTextareaAutosize } from '@vueuse/core'
import { renderMarkdownInline } from "@/utils/markdown";
import { markEditing, clearEditing } from "@/utils/editGuard";
import { findMatchingCheckedItems } from "@/utils/normalizeItemText";

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
const textareaComp = ref();

// Focus-swap: inside the open card an item shows rendered Markdown until it is
// actually being edited, mirroring the card-notes field. `editingText` mounts the
// raw textarea; blur swaps back. View-only collaborators never enter edit state.
const editingText = ref(false);
const showRenderedText = computed(
  () => !props.parentEditMode || !editingText.value || !canEdit.value
);

function focusTextareaEl() {
  const el = textareaComp.value?.$el?.querySelector?.("textarea") as HTMLTextAreaElement | null;
  if (!el) return;
  el.focus();
  const end = el.value.length;
  el.setSelectionRange(end, end);
}

// Enter edit mode and put the caret in the textarea once it has mounted.
function enterEdit() {
  if (!props.parentEditMode || !canEdit.value) return;
  editingText.value = true;
  nextTick(focusTextareaEl);
}

// A URL in item text renders as inert text plus a boxed-arrow icon link (see
// utils/markdown.ts). The icon opens the link in a new tab on its own; we only
// stop the click from bubbling — so it never opens the card (board) or drops the
// row into edit mode (open card). Any other click enters edit inside the card,
// and on the board falls through to the card's open-editor handler as before.
function onItemTextClick(e: MouseEvent) {
  if ((e.target as HTMLElement | null)?.closest?.("a.ext-link")) {
    e.stopPropagation();
    return;
  }
  enterEdit();
}

// Keep the local textarea guard AND the WI-10 store-apply guard in sync so an
// incoming delta never clobbers the item text mid-edit (SYNC §4).
function onTextFocus() {
  textFocused.value = true;
  if (props.checkListItem) markEditing("item", props.checkListItem.id, "text");
}
function onTextBlur() {
  textFocused.value = false;
  // Swap back to the rendered Markdown view.
  editingText.value = false;
  if (props.checkListItem) clearEditing("item", props.checkListItem.id, "text");
}
onBeforeUnmount(() => {
  if (props.checkListItem) clearEditing("item", props.checkListItem.id, "text");
});
// Phones get a smaller, lighter checkbox (Keep-like density) without shrinking
// the item text; desktop keeps the larger md checkbox.
const isMobile = useMediaQuery("(max-width: 639px)");
const checkListsItemStore = useCheckListsItemStore();
const route = useRoute();
const searchQuery = computed(() => (route.query.search as string) || null);
// Board preview clamps differently for one-liners vs multi-line items (see the
// display node): only items with an authored newline get the two-line treatment.
const previewHasNewline = computed(() => (props.checkListItem?.text ?? "").includes("\n"));
const emit = defineEmits(["checkedItem", "addItemAfter", "deleteItem", "acceptSuggestion"]);

// Keep-style autocomplete: the *checked* items in this card whose normalized
// text starts with what the user is currently typing into this (unchecked)
// item, so the list narrows live as they type. Fully client-side — reads the
// already-loaded item store, no network. Only shown while the field is focused,
// the per-card toggle is on, and this item is itself unchecked (we never offer
// to uncheck a match while editing a checked item).
const suggestions = computed<CheckListItemType[]>(() => {
  if (!props.parentEditMode) return [];
  if (props.parentCheckList.suggest_existing_items === false) return [];
  if (!canEdit.value) return [];
  if (!textFocused.value) return [];
  if (props.checkListItem?.state.checked) return [];
  const checkedItems = checkListsItemStore.getCheckListItems(props.parentCheckList.id, true);
  return findMatchingCheckedItems(checkedItems, props.checkListItem?.id, localText.value);
});

// Accept a suggestion: hand the pair up to the collection, which unchecks the
// chosen item, removes this just-typed duplicate, and refocuses the match.
function onAcceptSuggestion(match: CheckListItemType) {
  if (!match || !props.checkListItem) return;
  emit("acceptSuggestion", { currentItemId: props.checkListItem.id, matchedItemId: match.id });
}

// Enter adds a new item below; Shift+Enter (and IME confirm) inserts a newline.
function onEnter(e: KeyboardEvent) {
  if (e.shiftKey || e.isComposing) return;
  e.preventDefault();
  if (!canEdit.value) return;
  emit("addItemAfter", props.checkListItem!.id);
}

// Backspace on an already-empty item deletes it (Keep-style) and moves focus
// up to the previous item. Fires on keydown before the value changes, so an
// item with text just loses its last char; the *next* backspace removes it.
function onBackspace(e: KeyboardEvent) {
  if (!canEdit.value || e.isComposing) return;
  if (localText.value.length > 0) return;
  e.preventDefault();
  emit("deleteItem", props.checkListItem!.id, true);
}

// The × button deletes without hijacking focus (mouse users stay where they are).
function onDelete(focusPrev: boolean) {
  if (!canEdit.value) return;
  emit("deleteItem", props.checkListItem!.id, focusPrev);
}

// Called by the parent collection after it inserts the freshly created item (and
// on add-after / accept-suggestion / backspace-merge). Must open the editor
// first: with focus-swap the textarea only exists once `editingText` is set.
function focusTextarea() {
  editingText.value = true;
  nextTick(focusTextareaEl);
}
defineExpose({ focusTextarea });

// Permission gating — driven by the parent card's my_permission (P0.1).
const { can } = usePermissions();
const canCheck = computed(() => can(props.parentCheckList, "check"));
const canEdit = computed(() => can(props.parentCheckList, "edit"));

function toggleCheck() {
  if (!canCheck.value) return;
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
    if (!props.checkListItem || !canEdit.value) return;
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
/* On touch there is no hover: keep drag handles fully visible and give the
   checkbox/text row a thumb-friendly hit area (>=40px). */
@media (hover: none) {
  .nonActive {
    opacity: 1;
  }
  .checklist-item-row {
    padding-top: 0.125rem;
    padding-bottom: 0.125rem;
    min-height: 26px;
  }
  /* Bigger grab target on touch: pad the handle so the whole ~34px box is
     draggable, and pull it back with a matching negative margin so the icon
     stays visually aligned and neighbours (checkbox/text) don't shift. */
  .list-item-drag-handle {
    padding: 0.375rem 0.25rem;
    margin: -0.375rem -0.125rem;
  }
}
.strikethrough {
  text-decoration: line-through;
}
::v-deep(.strikethrough textarea) {
  text-decoration: line-through;
}
:deep(textarea) {
  max-height: none !important;
  height: auto !important;
  /* Size to content natively so a wrapped item grows instead of showing a
     scrollbar — Nuxt UI's JS autoresize mismeasures rows before the textarea
     has its final wrapped width inside the modal. overflow:hidden guarantees
     no scrollbar ever appears on an item. */
  field-sizing: content;
  overflow: hidden !important;
  /* Keep-style wrapping: only break words that overflow the line; never break
     inside short words (dropped the aggressive non-standard word-break). */
  overflow-wrap: break-word;
  white-space: pre-wrap;  /* Preserves line breaks + allows wrapping */
}
</style>
