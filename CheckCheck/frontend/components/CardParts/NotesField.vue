<template>
  <!-- Card notes: focus-swap edit surface. Rendered Markdown when the field is not
       being edited (and always, for a view-only visitor); clicking or tabbing in
       swaps to the raw textarea so the user edits the source. -->
  <div
    v-if="!editingNotes || !canEdit"
    :class="[
      'md-notes w-full flex-none text-sm opacity-90 break-words',
      canEdit ? 'md-notes-editable cursor-text rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary' : '',
    ]"
    :role="canEdit ? 'textbox' : undefined"
    :tabindex="canEdit ? 0 : undefined"
    :aria-label="canEdit ? 'Notes' : undefined"
    data-testid="card-notes-rendered"
    @click="canEdit && enterNotesEdit()"
    @keydown.enter.prevent="canEdit && enterNotesEdit()"
    @keydown.space.prevent="canEdit && enterNotesEdit()"
  >
    <div v-if="modelValue" v-html="renderMarkdown(modelValue, { search: searchQuery })" />
    <span v-else class="text-dimmed">{{ placeholder }}</span>
  </div>
  <UTextarea
    v-else
    ref="notesTextField"
    autoresize
    variant="none"
    :rows="0"
    :padded="false"
    :placeholder="placeholder"
    :model-value="modelValue"
    class="w-full flex-none text-sm opacity-90"
    data-testid="card-notes-textarea"
    @update:model-value="(v: string | number) => emit('update:modelValue', String(v))"
    @focus="emit('focus')"
    @blur="onNotesBlur"
  />
  <!-- Hint row: the Markdown hint + cheat-sheet popup (editable notes only) on
       the left, the copy buttons (any non-empty notes) on the right. A view-only
       reader gets the row with just the copy buttons. -->
  <div v-if="canEdit || hasNotes" class="flex-none mt-0.5 flex items-center gap-2 text-xs text-dimmed">
    <div v-if="canEdit" class="min-w-0">
      Markdown supported ·
      <UPopover mode="click" :content="{ side: 'top', align: 'start' }">
        <button type="button" class="underline hover:text-muted cursor-pointer" data-testid="markdown-help-trigger">
          Formatting help
        </button>
        <template #content>
          <MarkdownHelp />
        </template>
      </UPopover>
    </div>
    <!-- mousedown.prevent: a click here while the textarea is focused must not
         blur it. The blur would swap to the rendered view, whose height differs,
         and move the button away before the click lands. -->
    <div v-if="hasNotes" class="ml-auto flex items-center gap-0.5" data-testid="notes-copy-actions">
      <UTooltip text="Copy notes">
        <UButton
          size="xs"
          variant="ghost"
          color="neutral"
          icon="i-lucide-clipboard-copy"
          aria-label="Copy notes"
          class="p-0.5 -my-0.5 text-dimmed hover:text-muted"
          data-testid="notes-copy-text"
          @mousedown.prevent
          @click="copyNotes('text')"
        />
      </UTooltip>
      <UTooltip v-if="usesMarkdown" text="Copy as Markdown">
        <UButton
          size="xs"
          variant="ghost"
          color="neutral"
          icon="i-lucide-file-code"
          aria-label="Copy notes as Markdown"
          class="p-0.5 -my-0.5 text-dimmed hover:text-muted"
          data-testid="notes-copy-markdown"
          @mousedown.prevent
          @click="copyNotes('markdown')"
        />
      </UTooltip>
    </div>
  </div>
</template>

<script setup lang="ts">
// The card's notes field, shared by the authed open card and the `/p/<token>`
// public viewer (issue #11). Purely presentational: it renders Markdown, swaps to
// a raw textarea on focus, and emits. The surface owns the write: the authed card
// debounces into the store and holds the WI-10 editGuard marks in its focus/blur
// handlers; the public viewer debounces into an anonymous PATCH.
//
// Copy buttons (issue #31): "Copy notes" puts readable plain text plus the
// rendered HTML on the clipboard (a plain field pastes the text, a rich editor
// the formatting); "Copy as Markdown" puts the stored source, plain text only,
// and only shows when the notes use Markdown. Client only, works offline.
import { computed, ref } from "vue";
import type { PropType } from "vue";
import { markdownToPlainText, notesUseMarkdown, renderMarkdown } from "@/utils/markdown";
import { copyTextToClipboard } from "@/utils/clipboard";

const props = defineProps({
  modelValue: { type: String, default: "" },
  canEdit: { type: Boolean, default: false },
  placeholder: { type: String, default: "Enter some notes..." },
  // Highlight search hits in the rendered notes; null on surfaces with no search.
  searchQuery: { type: String as PropType<string | null>, default: null },
});

const emit = defineEmits<{
  "update:modelValue": [string];
  focus: [];
  blur: [];
}>();

const notesTextField = ref();

// `editingNotes` mounts the raw textarea; we focus it on the next tick so the
// caret lands there. Blur swaps back to the rendered view.
const editingNotes = ref(false);
function enterNotesEdit() {
  if (!props.canEdit) return;
  editingNotes.value = true;
  nextTick(() => notesTextField.value?.textareaRef?.focus?.());
}
function onNotesBlur() {
  emit("blur");
  editingNotes.value = false;
}

// The live value, so text typed but not yet saved is copied too.
const hasNotes = computed(() => !!props.modelValue?.trim());
const usesMarkdown = computed(() => notesUseMarkdown(props.modelValue));

const toast = useToast();
async function copyNotes(as: "text" | "markdown") {
  const source = props.modelValue ?? "";
  try {
    if (as === "markdown") await copyTextToClipboard(source);
    else await copyTextToClipboard(markdownToPlainText(source), { html: renderMarkdown(source) });
  } catch {
    toast.add({ title: "Could not copy the notes", description: "The clipboard is not available here.", color: "error" });
    return;
  }
  toast.add({ title: as === "markdown" ? "Notes copied as Markdown" : "Notes copied", color: "primary" });
}
</script>

<style scoped></style>
