<template>
  <!-- `public-item-text` marks an *editable* row, which is what the viewer specs
       assert on (count 0 on a view link, one per row on an edit link). The shared
       row is a focus-swap surface, so the raw textarea only exists while a row is
       actually being edited, so the marker sits on the wrapper instead. -->
  <div :data-testid="canEdit ? 'public-item-text' : 'public-item'">
    <CardPartsItemRow
      ref="rowRef"
      :item="item"
      :can-check="canCheck"
      :can-edit="canEdit"
      edit-mode
      :show-drag-handle="canEdit"
      @toggle="emit('toggle')"
      @update:text="onTextInput"
      @delete="(focusPrev: boolean) => emit('delete', focusPrev)"
      @add-after="emit('add-after')"
    />
  </div>
</template>

<script setup lang="ts">
// The `/p/<token>` viewer's item row: a thin adapter over the shared, store-free
// CardPartsItemRow (issue #11), mirroring what CheckListItem.vue is on the authed
// side. All it owns is the debounced write (the public surface PATCHes directly:
// no store, no outbox) and the public test markers; the layout, the Markdown
// rendering and the keyboard behaviour are the shared row's, so a public link now
// renders items exactly like the open card.
import { ref } from "vue";
import type { PropType } from "vue";
import { useDebounceFn } from "@vueuse/core";

const props = defineProps({
  item: { type: Object as PropType<CheckListItemType>, required: true },
  canCheck: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
});

const emit = defineEmits<{
  toggle: [];
  delete: [focusPrev: boolean];
  "add-after": [];
  "update-text": [string];
}>();

const rowRef = ref();
function focusTextarea() {
  rowRef.value?.focusTextarea?.();
}
defineExpose({ focusTextarea });

// The shared row is deliberately undebounced (both surfaces debounce at the same
// numbers around different write paths), so the PATCH is debounced here, with the
// same 500ms / 3000ms maxWait the authed card uses.
const debouncedUpdate = useDebounceFn((val: string) => {
  if (!props.canEdit) return;
  emit("update-text", val);
}, 500, { maxWait: 3000 });

function onTextInput(val: string) {
  if (val !== (props.item.text ?? "")) debouncedUpdate(val);
}
</script>
