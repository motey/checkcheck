<template>
  <div>
  <div class="checklist-item-row flex items-start gap-1.5 py-0.5" data-testid="item-row" @mouseover="hover = true" @mouseleave="hover = false">
    <span
      v-if="showHandle"
      :class="{ nonActive: !hover }"
      class="list-item-drag-handle flex-none self-center transition-opacity touch-none select-none"
      title="Drag to reorder"
      :id="item.id"
    >
      <UIcon name="i-lucide-grip-vertical" class="w-6 h-6 sm:w-5 sm:h-5 cursor-row-resize" />
    </span>
    <div class="flex-none flex items-center self-stretch min-h-5 sm:min-h-6" :title="canCheck ? undefined : 'View only'">
      <UCheckbox v-model="item.state.checked" :disabled="!canCheck" @click.stop="onToggle()" :size="isMobile ? 'sm' : 'md'" />
    </div>
    <!-- Rendered (Markdown) view. Shown on the board, and inside the open card
         whenever this item is not the one being edited — clicking or tabbing in
         swaps to the raw textarea below so the user edits the source. -->
    <div
      v-if="showRenderedText"
      class="md-inline min-w-0 flex-1 pt-0.5 break-words"
      :class="[
        item.state.checked ? 'strikethrough' : '',
        // Board preview: one-liners truncate at card width; items with an
        // authored newline get two lines. In the open card show the full text so
        // the rendered row matches the textarea it swaps with.
        editMode
          ? 'whitespace-pre-wrap'
          : previewHasNewline ? 'whitespace-pre-wrap line-clamp-2' : 'line-clamp-1',
        editMode && canEdit
          ? 'cursor-text rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary'
          : '',
      ]"
      :role="editMode && canEdit ? 'textbox' : undefined"
      :tabindex="editMode && canEdit ? 0 : undefined"
      data-testid="item-text-rendered"
      @click="onItemTextClick"
      @keydown.enter.prevent="enterEdit()"
      @keydown.space.prevent="enterEdit()"
    >
      <span v-if="localText" v-html="renderMarkdownInline(localText, { search: searchQuery })" />
      <span v-else-if="editMode" class="text-dimmed">Enter some text...</span>
    </div>

    <UTextarea
      ref="textareaComp"
      placeholder="Enter some text..."
      v-model="localText"
      v-if="editMode && editingText && canEdit"
      variant="none"
      autoresize
      :rows="1"
      :padded="false"
      :style="{ color: textColor }"
      class="min-w-0 flex-1 grow cursor-auto m-0 pt-0.5"
      :class="{ strikethrough: item.state.checked }"
      data-testid="item-text-editor"
      @focus="onTextFocus"
      @blur="onTextBlur"
      @keydown.enter="onEnter"
      @keydown.delete="onBackspace"
    />
    <button
      v-if="editMode && canEdit"
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
    <!-- Anything a surface wants to hang off this row (the authed card's
         uncheck-suggestion list) renders here, inside the row's own wrapper so it
         stays visually attached to the item it belongs to. -->
    <slot name="below" />
  </div>
</template>

<script setup lang="ts">
// The card's item row, shared by the authed card and the `/p/<token>` public
// viewer (issue #11). Purely presentational: props in, events out. It knows
// nothing about Pinia, the outbox or the editGuard: the *surface* that renders it
// owns those. Everything here is layout, Markdown rendering and keyboard
// behaviour, so both surfaces get an item row that looks and types the same.
//
// The text write is deliberately NOT debounced here: both adapters debounce at
// the same 500ms / 3000ms maxWait, but around their own write path (store +
// outbox vs. an anonymous PATCH), so the debounce belongs to neither and stays in
// each adapter. This component emits `update:text` on every keystroke.
import { ref } from "vue";
import { useMediaQuery } from "@vueuse/core";
import type { PropType } from "vue";
import { renderMarkdownInline } from "@/utils/markdown";

const props = defineProps({
  item: { type: Object as PropType<CheckListItemType>, required: true },
  canCheck: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
  // The open card (editable rows, drag handles, delete buttons) vs. a board
  // preview (rendered text only, clamped).
  editMode: { type: Boolean, default: false },
  showDragHandle: { type: Boolean, default: true },
  // Highlight search hits in the rendered text; null on surfaces with no search.
  searchQuery: { type: String as PropType<string | null>, default: null },
  // Forced textarea colour on a colour-themed card (the raw textarea does not
  // inherit through Nuxt UI's variant styling).
  textColor: { type: String as PropType<string | undefined>, default: undefined },
});

const emit = defineEmits<{
  toggle: [];
  "update:text": [string];
  delete: [focusPrev: boolean];
  "add-after": [];
  focus: [];
  blur: [];
}>();

const hover = ref(false);
const textFocused = ref(false);
const textareaComp = ref();

// Focus-swap: inside the open card an item shows rendered Markdown until it is
// actually being edited, mirroring the card-notes field. `editingText` mounts the
// raw textarea; blur swaps back. View-only visitors never enter edit state.
const editingText = ref(false);
const showRenderedText = computed(
  () => !props.editMode || !editingText.value || !props.canEdit
);
const showHandle = computed(() => props.editMode && props.canEdit && props.showDragHandle);

function focusTextareaEl() {
  const el = textareaComp.value?.$el?.querySelector?.("textarea") as HTMLTextAreaElement | null;
  if (!el) return;
  el.focus();
  const end = el.value.length;
  el.setSelectionRange(end, end);
}

// Enter edit mode and put the caret in the textarea once it has mounted.
function enterEdit() {
  if (!props.editMode || !props.canEdit) return;
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

// focus/blur are emitted so the surface can hold its own guards (the authed card
// marks the field in the WI-10 editGuard so an incoming delta cannot clobber text
// mid-edit; the public viewer has no delta feed and just tracks focus).
function onTextFocus() {
  textFocused.value = true;
  emit("focus");
}
function onTextBlur() {
  textFocused.value = false;
  // Swap back to the rendered Markdown view.
  editingText.value = false;
  emit("blur");
}
// Phones get a smaller, lighter checkbox (Keep-like density) without shrinking
// the item text; desktop keeps the larger md checkbox.
const isMobile = useMediaQuery("(max-width: 639px)");
// Board preview clamps differently for one-liners vs multi-line items (see the
// display node): only items with an authored newline get the two-line treatment.
const previewHasNewline = computed(() => (props.item.text ?? "").includes("\n"));

function onToggle() {
  if (!props.canCheck) return;
  emit("toggle");
}

// Enter adds a new item below; Shift+Enter (and IME confirm) inserts a newline.
function onEnter(e: KeyboardEvent) {
  if (e.shiftKey || e.isComposing) return;
  e.preventDefault();
  if (!props.canEdit) return;
  emit("add-after");
}

// Backspace on an already-empty item deletes it (Keep-style) and moves focus
// up to the previous item. Fires on keydown before the value changes, so an
// item with text just loses its last char; the *next* backspace removes it.
function onBackspace(e: KeyboardEvent) {
  if (!props.canEdit || e.isComposing) return;
  if (localText.value.length > 0) return;
  e.preventDefault();
  emit("delete", true);
}

// The × button deletes without hijacking focus (mouse users stay where they are).
function onDelete(focusPrev: boolean) {
  if (!props.canEdit) return;
  emit("delete", focusPrev);
}

// Called by the parent list after it inserts the freshly created item (and on
// add-after / accept-suggestion / backspace-merge). Must open the editor first:
// with focus-swap the textarea only exists once `editingText` is set.
function focusTextarea() {
  editingText.value = true;
  nextTick(focusTextareaEl);
}
defineExpose({ focusTextarea });

// Local copy decoupled from the source of truth so incoming patches (SSE, delta
// apply, a slow PATCH response) don't wipe text the user is currently typing.
const localText = ref(props.item.text ?? "");

// Sync FROM the source only when the field is not focused.
watch(
  () => props.item.text,
  (serverText) => {
    if (!textFocused.value) localText.value = serverText ?? "";
  }
);

watch(localText, (t) => emit("update:text", t));
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
