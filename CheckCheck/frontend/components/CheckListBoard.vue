<template>
  <div>
    <!-- Pinned collection — shown above the normal list when non-empty -->
    <div v-show="dragPinned.length" data-testid="pinned-section">
      <h2 class="px-3 sm:px-5 pt-3 text-xs font-semibold uppercase tracking-wide opacity-60">Pinned</h2>
      <ul ref="pinnedBoard" data-testid="pinned-board" class="grid gap-3 sm:gap-4 grid-cols-[repeat(auto-fill,minmax(15rem,1fr))]">
        <li v-for="checkList in dragPinned" :key="checkList.id" class="w-full checklist-preview">
          <CheckList :checkListId="checkList.id" @click="openCheckListEditor(checkList.id)" :previewModeActive="true" />
        </li>
      </ul>
    </div>

    <h2 v-show="dragPinned.length" class="px-3 sm:px-5 pt-3 text-xs font-semibold uppercase tracking-wide opacity-60">Others</h2>
    <ul ref="normalBoard" data-testid="checklist-board" class="grid gap-3 sm:gap-4 grid-cols-[repeat(auto-fill,minmax(15rem,1fr))]">
      <li v-for="checkList in dragNormal" :key="checkList.id" class="w-full checklist-preview">
        <CheckList :checkListId="checkList.id" @click="openCheckListEditor(checkList.id)" :previewModeActive="true" />
      </li>
      <li
        class="no-drag text-center py-4"
        ref="loadMoreTrigger"
        v-element-visibility="onLoadingTriggerVisibility"
        v-if="hasMoreToLoad"
      >
        <UButton icon="i-heroicons-arrow-path" :loading="loadingInProcess" variant="ghost"> Load more... </UButton>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { useRoute } from "vue-router";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsColorSchemeStore } from "@/stores/color";
import { useCheckListsLabelStore } from "@/stores/label";
import { useDragAndDrop } from "@formkit/drag-and-drop/vue";
import { animations } from "@formkit/drag-and-drop";
import type { DragendEventData } from "@formkit/drag-and-drop";
import { vElementVisibility } from "@vueuse/components";
import { useDebounceFn } from "@vueuse/core";
import { useAppRoute } from "~/composables/useAppRoute";

const route = useRoute();
const { openCard } = useAppRoute();
const checkListStore = useCheckListsStore();
const checkListsColorSchemeStore = useCheckListsColorSchemeStore();
const checkListsLabelStore = useCheckListsLabelStore();
const { checkLists, total_backend_count, searchResults, searchTotalCount } = storeToRefs(checkListStore);

// "Load more" is visible when there is another page to fetch — in the filtered
// view (search / shared) that's searchTotalCount, otherwise the full feed.
const hasMoreToLoad = computed(() =>
  searchResults.value !== null
    ? searchResults.value.length < searchTotalCount.value
    : checkLists.value.length < total_backend_count.value
);

const loadingTriggerIsVisible = ref(false);
const loadingInProcess = ref(false);

onMounted(async () => {
  await checkListStore.fetchNextPage();
  await checkListsColorSchemeStore.fetchColors();
  await checkListsLabelStore.fetchLabels();
});

let checklistDragInProgress = false;
// One drag can fire onDragend on both parents (source + target) when an item
// crosses lists; this token ensures we process a given drag only once.
let pendingDragToken = 0;

function onDragend(event: DragendEventData<CheckListType>) {
  checklistDragInProgress = false;
  if (pendingDragToken === 0) return;
  pendingDragToken = 0;
  const draggedItem = event.draggedNode.data.value as CheckListType;
  // Final group = whichever list now holds the item after the (cross-)list drop.
  const targetPinned = dragPinned.value.some((c) => c.id === draggedItem.id);
  const targetList = (targetPinned ? dragPinned.value : dragNormal.value) as CheckListType[];
  checkListStore.reorderCheckLists(targetList, draggedItem, targetPinned);
}

// `group` lets items be dragged between the two parents; a fresh animations()
// plugin instance is created per parent.
const dragOptions = () => ({
  group: "checklists",
  onDragstart: () => { checklistDragInProgress = true; pendingDragToken = 1; },
  onDragend,
  draggable: (el: HTMLElement) => !(el && el.classList.contains("no-drag")),
  plugins: [animations()],
});

const [pinnedBoard, dragPinned] = useDragAndDrop<CheckListType>([], dragOptions());
const [normalBoard, dragNormal] = useDragAndDrop<CheckListType>([], dragOptions());

// Debounced server-side filtered view — fires 300 ms after the last change.
// Active whenever there is text search and/or a shared filter; a label narrows
// it. A label on its own stays client-side (see watchEffect below), so it does
// not open the filtered view.
const runFilter = useDebounceFn(
  async (query: string | null, labelId: string | null, shared: "with_me" | "by_me" | null) => {
    if (query || shared) {
      await checkListStore.searchChecklists(query, labelId, shared);
    } else {
      checkListStore.clearSearch();
    }
  },
  300
);

// Re-run whenever search text, label, or shared filter changes
watch(
  () => ({
    search: route.query.search as string,
    label: route.query.label as string,
    shared: route.query.shared as string,
  }),
  ({ search, label, shared }) =>
    runFilter(search || null, label || null, (shared as "with_me" | "by_me") || null),
  { immediate: true }
);

// Sync drag list — use search results when active, else normal paginated list.
// Guard: never splice while a drag is in progress — mid-drag store updates
// (SSE, prev-drag async) reset FormKit DnD's state and corrupt event.values.
watchEffect(() => {
  if (checklistDragInProgress) return;
  const label = (route.query.label as string) || null;
  const source = searchResults.value !== null
    ? searchResults.value
    : checkListStore.getCheckLists({ archived: false, label_id: label });
  const pinned = source.filter((c) => c.position.pinned ?? false);
  const normal = source.filter((c) => !(c.position.pinned ?? false));
  dragPinned.value.splice(0, dragPinned.value.length, ...pinned);
  dragNormal.value.splice(0, dragNormal.value.length, ...normal);
});

async function onLoadingTriggerVisibility(state: boolean) {
  loadingTriggerIsVisible.value = state;
  if (state && !loadingInProcess.value) {
    loadingInProcess.value = true;
    // Page the active view: filtered (search / shared) vs. the full feed.
    while (loadingTriggerIsVisible.value && hasMoreToLoad.value) {
      if (searchResults.value !== null) {
        await checkListStore.fetchMoreFiltered();
      } else {
        await checkListStore.fetchNextPage();
      }
    }
    loadingInProcess.value = false;
  }
}

function openCheckListEditor(checkListId: string) {
  openCard(checkListId);
}
</script>
<style scoped>
ul {
  margin: 0 auto;
  width: 100%;
  padding: 12px;
}
@media (min-width: 640px) {
  ul {
    padding: 20px;
  }
}
.checklist-preview {
  transition: transform 0.15s ease;
}
.checklist-preview:hover {
  transform: translateY(-2px);
}
</style>
