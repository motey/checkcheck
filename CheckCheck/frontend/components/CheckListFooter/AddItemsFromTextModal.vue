<template>
  <UModal
    v-model:open="open"
    title="Add items from text"
    description="Each line becomes one item."
    :fullscreen="isMobile"
    :ui="{ content: isMobile ? MOBILE_CONTENT_CLASS : DESKTOP_CONTENT_CLASS }"
  >
    <template #content>
      <form class="flex h-full min-h-0 flex-col gap-3 p-4 sm:p-6" data-testid="add-items-from-text" @submit.prevent="add()">
        <div class="flex items-start justify-between gap-2">
          <div class="flex flex-col gap-1">
            <h2 class="text-lg font-semibold">Add items from text</h2>
            <p class="text-sm text-muted">
              Each line becomes one item. List markers like <code>-</code>, <code>*</code> or <code>1.</code> are
              removed, and <code>- [x]</code> adds a checked item.
            </p>
          </div>
          <UButton
            color="neutral"
            variant="ghost"
            icon="i-lucide-x"
            aria-label="Close"
            class="-mr-2 -mt-1 shrink-0 justify-center min-h-11 min-w-11 sm:min-h-0 sm:min-w-0"
            @click.stop="open = false"
          />
        </div>

        <UTextarea
          v-model="text"
          :rows="8"
          autofocus
          placeholder="milk&#10;eggs&#10;- [x] bread"
          class="w-full min-h-0 flex-1"
          :ui="{ base: 'h-full resize-none' }"
          aria-label="Items, one per line"
          data-testid="add-items-text"
          @keydown.ctrl.enter.prevent="add()"
          @keydown.meta.enter.prevent="add()"
        />

        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-sm text-muted" data-testid="add-items-count" aria-live="polite">{{ countLabel }}</p>
          <div class="ml-auto flex gap-2">
            <UButton color="neutral" variant="ghost" label="Cancel" @click.stop="open = false" />
            <UButton
              type="submit"
              :label="items.length > 1 ? `Add ${items.length} items` : 'Add item'"
              :disabled="!items.length"
              :loading="adding"
              data-testid="confirm-add-items"
            />
          </div>
        </div>
      </form>
    </template>
  </UModal>
</template>

<script setup lang="ts">
// "Add items from text" (issue #24): a multi-line textarea whose lines become
// items, appended in order where new items normally go. Each item is a normal
// optimistic create (one outbox op each), so this works offline too. The parser
// reads back what "Copy list as text" writes.
import { computed, ref, watch } from "vue";
import { useMediaQuery } from "@vueuse/core";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { parseItemsFromText } from "@/utils/parseItemsFromText";

const props = defineProps({
  checkListId: { type: String, required: true },
});
const open = defineModel<boolean>("open", { default: false });

const itemStore = useCheckListsItemStore();
const toast = useToast();

const text = ref("");
const adding = ref(false);
const items = computed(() => parseItemsFromText(text.value));
const countLabel = computed(() => {
  const n = items.value.length;
  if (!n) return "Paste or type a list, one item per line.";
  const checked = items.value.filter((i) => i.checked).length;
  return `${n} ${n === 1 ? "item" : "items"} will be added${checked ? `, ${checked} of them checked` : ""}.`;
});

// Start empty every time; a cancelled draft is discarded.
watch(open, (isOpen) => {
  if (isOpen) text.value = "";
});

// Below `sm` the dialog is a full-screen page pinned to the visual viewport (as
// the card editor is), so the buttons stay above the on-screen keyboard and the
// textarea takes whatever height is left.
useVisualViewport(() => open.value);
const isMobile = useMediaQuery("(max-width: 639px)");
const DESKTOP_CONTENT_CLASS = "max-w-lg w-[calc(100vw-1rem)] sm:w-full max-h-[92dvh] rounded-2xl ring ring-default";
const MOBILE_CONTENT_CLASS =
  "top-[var(--vv-top,0px)] bottom-auto h-[var(--vv-height,100dvh)] w-full max-w-none rounded-none overflow-hidden";

async function add() {
  if (!items.value.length || adding.value) return;
  adding.value = true;
  const count = items.value.length;
  try {
    await itemStore.createMany(props.checkListId, items.value);
    open.value = false;
    toast.add({ title: `${count} ${count === 1 ? "item" : "items"} added`, color: "success" });
  } catch {
    toast.add({ title: "Could not add all items", color: "error" });
  } finally {
    adding.value = false;
  }
}
</script>
