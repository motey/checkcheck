<template>
  <div class="flex items-start gap-2 py-0.5" @mouseover="hover = true" @mouseleave="hover = false">
    <div class="flex-none pt-1" :title="canCheck ? undefined : 'View only'">
      <UCheckbox
        :model-value="item.state.checked"
        :disabled="!canCheck"
        size="xl"
        data-testid="public-item-checkbox"
        @update:model-value="emit('toggle')"
      />
    </div>

    <div v-if="!canEdit" class="flex-1 pl-1 break-words" :class="{ strikethrough: item.state.checked }">
      {{ item.text }}
    </div>

    <UTextarea
      v-else
      v-model="localText"
      variant="none"
      autoresize
      :rows="1"
      :padded="false"
      placeholder="Enter some text..."
      class="flex-1 pl-1"
      :class="{ strikethrough: item.state.checked }"
      data-testid="public-item-text"
      @focus="textFocused = true"
      @blur="textFocused = false"
    />

    <div class="flex-none">
      <UButton
        v-if="canEdit"
        :class="{ 'opacity-0': !hover }"
        icon="i-lucide-x"
        color="neutral"
        variant="ghost"
        size="xs"
        title="Delete item"
        data-testid="public-item-delete"
        @click="emit('delete')"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import type { PropType } from "vue";
import { useDebounceFn } from "@vueuse/core";

const props = defineProps({
  item: { type: Object as PropType<CheckListItemType>, required: true },
  canCheck: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
});

const emit = defineEmits<{
  toggle: [];
  delete: [];
  "update-text": [string];
}>();

const hover = ref(false);
const textFocused = ref(false);

// Local copy decoupled from the store so SSE patches don't wipe in-progress edits.
const localText = ref(props.item.text ?? "");
watch(
  () => props.item.text,
  (serverText) => {
    if (!textFocused.value) localText.value = serverText ?? "";
  }
);

const debouncedUpdate = useDebounceFn((val: string) => {
  if (!props.canEdit) return;
  emit("update-text", val);
}, 500, { maxWait: 3000 });

watch(localText, (t) => {
  if (t !== (props.item.text ?? "")) debouncedUpdate(t);
});
</script>

<style scoped>
.strikethrough {
  text-decoration: line-through;
}
:deep(.strikethrough textarea) {
  text-decoration: line-through;
}
</style>
