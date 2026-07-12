<template>
  <div class="flex flex-col">
    <!-- Header -->
    <div class="flex items-center gap-3 px-4 sm:px-5 py-3 border-b border-default sticky top-0 bg-default/90 backdrop-blur z-10">
      <UIcon name="i-lucide-tags" class="size-5 text-primary shrink-0" />
      <h2 class="text-base font-semibold flex-1">Labels</h2>
      <UButton
        icon="i-lucide-x"
        color="neutral"
        variant="ghost"
        size="sm"
        aria-label="Close"
        @click="emit('close')"
      />
    </div>

    <!-- Offline notice: label CRUD stays online-only (WI-12), so the controls
         below are inert until reconnected. The banner says why rather than
         leaving a dead click. -->
    <UAlert
      v-if="!online"
      color="neutral"
      variant="subtle"
      icon="i-lucide-wifi-off"
      title="You're offline"
      description="Labels need a connection to create, rename, delete, or reorder. Reconnect to manage them."
      class="mx-4 sm:mx-5 my-3"
      data-testid="label-offline-notice"
    />

    <!-- Create new label -->
    <form class="flex items-center gap-2 px-4 sm:px-5 py-3 border-b border-default" @submit.prevent="createLabel">
      <UInput
        v-model="newLabelName"
        size="md"
        class="flex-1 min-w-0"
        placeholder="New label name…"
        icon="i-lucide-plus"
        :disabled="!online"
      />
      <UButton
        type="submit"
        color="primary"
        size="md"
        :disabled="!online || !newLabelName.trim()"
        :loading="creating"
        label="Add"
      />
    </form>

    <!-- List -->
    <div class="px-2 sm:px-3 py-2">
      <ul v-if="draggableItems.length" ref="ItemsView" class="flex flex-col">
        <li v-for="item in draggableItems" :key="item.id">
          <LabelManagerEditItem :label="item" />
        </li>
      </ul>
      <div v-else class="flex flex-col items-center justify-center gap-2 py-10 text-center text-muted">
        <UIcon name="i-lucide-tag" class="size-8 opacity-50" />
        <p class="text-sm">No labels yet. Create your first one above.</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useDragAndDrop } from "@formkit/drag-and-drop/vue";
import { animations } from "@formkit/drag-and-drop";
import { watch } from "vue";
import { useCheckListsLabelStore } from "@/stores/label";
import { useConnectivity } from "@/composables/useConnectivity";

const emit = defineEmits<{ close: [] }>();

const checkListsLabelStore = useCheckListsLabelStore();
const { online } = useConnectivity();

const newLabelName = ref("");
const creating = ref(false);

async function createLabel() {
  const name = newLabelName.value.trim();
  if (!name || creating.value || !online.value) return;
  creating.value = true;
  try {
    await checkListsLabelStore.createLabel({ display_name: name } as LabelCreateType);
    newLabelName.value = "";
  } finally {
    creating.value = false;
  }
}

const [ItemsView, draggableItems] = useDragAndDrop([...checkListsLabelStore.labels], {
  dragHandle: ".label-item-drag-handle",
  draggable: (el) => !(el && el.classList.contains("no-drag")),
  plugins: [animations()],
  onDragend: persistOrder,
});

// After a drag finishes, draggableItems holds the new top -> bottom order.
// Persist it so the reordering survives a reload (otherwise it is purely
// client-side and has no effect on the actual label list).
async function persistOrder() {
  // Reordering is online-only (WI-12). Offline, undo the client-side drag by
  // restoring the store's order rather than firing a call that would throw.
  if (!online.value) {
    draggableItems.value = [...checkListsLabelStore.labels];
    return;
  }
  await checkListsLabelStore.sortLabels(draggableItems.value.map((label) => label.id));
}

watch(
  () => checkListsLabelStore.labels,
  (newLabels) => {
    draggableItems.value = [...newLabels];
  },
  { deep: true }
);
</script>
