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
  <!-- Unobtrusive hint + Markdown cheat-sheet popup (editable notes only). -->
  <div v-if="canEdit" class="flex-none mt-0.5 text-xs text-dimmed">
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
</template>

<script setup lang="ts">
// The card's notes field, shared by the authed open card and the `/p/<token>`
// public viewer (issue #11). Purely presentational: it renders Markdown, swaps to
// a raw textarea on focus, and emits. The surface owns the write: the authed card
// debounces into the store and holds the WI-10 editGuard marks in its focus/blur
// handlers; the public viewer debounces into an anonymous PATCH.
import { ref } from "vue";
import type { PropType } from "vue";
import { renderMarkdown } from "@/utils/markdown";

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
</script>

<style scoped></style>
