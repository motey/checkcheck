<template>
  <!-- The unchecked items (or, when the card does not separate them, all of them).
       The surface fills the slot with its own list. -->
  <slot name="unchecked" />

  <template v-if="separated">
    <USeparator v-if="editMode" color="neutral" type="dashed" />
    <div
      v-if="editMode"
      data-testid="editor-checked-section"
      class="flex items-center"
      @click="onToggleCollapsed()"
    >
      <UIcon v-if="!collapsed" name="i-lucide-chevrons-down" class="w-5 h-8" />
      <UIcon v-if="collapsed" name="i-lucide-chevrons-right" class="w-5 h-8" />
      <span class="ml-2 text-base">{{ String(checkedCount) }} checked items</span>
    </div>
    <USeparator color="neutral" type="dashed"
      v-if="!editMode && checkedCount > 0"
      :label="`+ ${String(checkedCount)} checked items`"
      class="opacity-90"  :ui="{
        label: 'text-primary-500 dark:text-primary-400',
        container: { base: 'flex' },
      }"
    />
    <Collapse :when="!collapsed">
      <!-- On a phone preview keep the card short: don't expand the checked
           section (the "+N checked items" hint above still surfaces it; the full
           list is one tap away in the editor). -->
      <slot
        v-if="editMode || !isMobile"
        name="checked"
        :show-max-items="showCheckedItemCount"
      />
    </Collapse>
  </template>
</template>

<script setup lang="ts">
// The "separate checked items" layout, shared by the authed card and the
// `/p/<token>` public viewer (issue #11, where the public link never had it). Owns the
// separator, the collapse header with its count, and the vue-collapsed transition;
// it reads no store and writes nothing. The surface decides what a collapse toggle
// *means*: the authed card persists it on the card, a public check-or-better link
// persists it too (it is a card column, not per-user), and a public view-level
// link keeps it in the visitor's own session only.
import { computed } from "vue";
import { useMediaQuery } from "@vueuse/core";
import { Collapse } from "vue-collapsed";

const isMobile = useMediaQuery("(max-width: 639px)");

const props = defineProps({
  // The card's `checked_items_seperated`. False renders the unchecked slot alone.
  separated: { type: Boolean, default: true },
  collapsed: { type: Boolean, default: false },
  // The open card (collapse header, chevron) vs. a board preview ("+N checked
  // items" label, no toggle).
  editMode: { type: Boolean, default: false },
  checkedCount: { type: Number, default: 0 },
  uncheckedCount: { type: Number, default: 0 },
  // Board preview only: the total row budget for the card.
  showMaxItems: { type: Number, required: false },
  // False on a public view-level link that has no session storage to fall back
  // to; the header then renders as a plain label.
  canToggle: { type: Boolean, default: true },
});

const emit = defineEmits<{ "toggle-collapsed": [] }>();

// Board preview: the checked list only gets whatever row budget the unchecked
// items left over, and nothing at all while the section is collapsed.
const showCheckedItemCount = computed<number | undefined>(() => {
  if (props.showMaxItems) {
    if (props.showMaxItems - props.uncheckedCount > 0 && !props.collapsed) {
      return props.showMaxItems - props.uncheckedCount;
    } else {
      return 0;
    }
  }
  return undefined;
});

function onToggleCollapsed() {
  if (!props.canToggle) return;
  emit("toggle-collapsed");
}
</script>

<style scoped></style>
