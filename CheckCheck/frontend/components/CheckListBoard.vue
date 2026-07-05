<template>
  <div>
    <!-- First-load skeleton — shown until the initial fetch resolves so the
         board doesn't pop in from blank. Transient/visual; not tested. -->
    <ul
      v-if="!initialLoadDone"
      class="grid gap-2.5 sm:gap-4 grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(15rem,1fr))]"
    >
      <li v-for="n in 8" :key="n" class="w-full">
        <USkeleton class="h-32 w-full rounded-xl" />
      </li>
    </ul>

    <!-- Empty states — gated behind the initial load so they don't flash on the
         brief blank first paint before data arrives. -->
    <template v-else>
      <div
        v-if="isEmpty && (searchQuery || labelFilter)"
        data-testid="board-empty-search"
        class="flex flex-col items-center justify-center text-center gap-3 py-20 px-6"
      >
        <UIcon name="i-lucide-search-x" class="size-12 text-dimmed" />
        <p class="text-muted">
          No results<template v-if="searchQuery"> for &ldquo;{{ searchQuery }}&rdquo;</template>.
        </p>
      </div>

      <div
        v-else-if="isEmpty && sharedFilter"
        data-testid="board-empty-shared"
        class="flex flex-col items-center justify-center text-center gap-3 py-20 px-6"
      >
        <UIcon name="i-lucide-users" class="size-12 text-dimmed" />
        <p class="text-muted">
          Nothing shared {{ sharedFilter === "by_me" ? "by you" : "with you" }} yet.
        </p>
      </div>

      <div
        v-else-if="isEmpty && archivedFilter"
        data-testid="board-empty-archive"
        class="flex flex-col items-center justify-center text-center gap-3 py-20 px-6"
      >
        <UIcon name="i-lucide-archive" class="size-12 text-dimmed" />
        <p class="text-muted">Your archive is empty.</p>
      </div>

      <div
        v-else-if="isEmpty"
        data-testid="board-empty"
        class="flex flex-col items-center justify-center text-center gap-3 py-20 px-6"
      >
        <UIcon name="i-lucide-clipboard-list" class="size-12 text-dimmed" />
        <p class="text-muted">You don&rsquo;t have any lists yet.</p>
        <UButton
          data-testid="board-empty-cta"
          icon="i-lucide-plus"
          color="primary"
          @click="createAndOpen"
        >
          Create your first list
        </UButton>
      </div>
    </template>

    <!-- Boards stay mounted (v-show) so the FormKit DnD refs persist and the
         board testids never leave the DOM; hidden only during the first-load
         skeleton. When empty after load they render as an (empty) grid that
         sits under the empty-state message — the testids stay visible so specs
         that wait on them don't hang on an empty board. -->
    <div v-show="initialLoadDone">
      <!-- Pinned collection — shown above the normal list when non-empty -->
      <div v-show="dragPinned.length" data-testid="pinned-section">
        <h2 class="px-3 sm:px-5 pt-3 text-xs font-semibold uppercase tracking-wide opacity-60">Pinned</h2>
        <ul
          ref="pinnedBoard"
          data-testid="pinned-board"
          class="grid gap-2.5 sm:gap-4 grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(15rem,1fr))]"
        >
          <li
            v-for="checkList in dragPinned"
            :key="checkList.id"
            class="w-full checklist-preview rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
            role="button"
            tabindex="0"
            :aria-label="`Open ${checkList.name || 'checklist'}`"
            @keydown.enter.self.prevent="openCheckListEditor(checkList.id)"
            @keydown.space.self.prevent="openCheckListEditor(checkList.id)"
          >
            <CheckList :checkListId="checkList.id" @click="openCheckListEditor(checkList.id)" :previewModeActive="true" />
          </li>
        </ul>
      </div>

      <h2 v-show="dragPinned.length" class="px-3 sm:px-5 pt-3 text-xs font-semibold uppercase tracking-wide opacity-60">Others</h2>
      <ul
        ref="normalBoard"
        data-testid="checklist-board"
        class="grid gap-2.5 sm:gap-4 grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(15rem,1fr))]"
      >
        <li
          v-for="checkList in dragNormal"
          :key="checkList.id"
          class="w-full checklist-preview rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
          role="button"
          tabindex="0"
          :aria-label="`Open ${checkList.name || 'checklist'}`"
          @keydown.enter.self.prevent="openCheckListEditor(checkList.id)"
          @keydown.space.self.prevent="openCheckListEditor(checkList.id)"
        >
          <CheckList :checkListId="checkList.id" @click="openCheckListEditor(checkList.id)" :previewModeActive="true" />
        </li>
        <!-- Pagination trigger. The IntersectionObserver (v-element-visibility)
             drives auto-paging; the visible affordance is just a subtle spinner
             while loading, with a fallback button only when no observer exists. -->
        <li
          class="no-drag text-center py-4"
          ref="loadMoreTrigger"
          v-element-visibility="onLoadingTriggerVisibility"
          v-if="hasMoreToLoad"
        >
          <span v-if="loadingInProcess" class="inline-flex items-center gap-2 text-sm text-muted">
            <UIcon name="i-lucide-loader-circle" class="size-4 animate-spin" />
            Loading more&hellip;
          </span>
          <UButton
            v-else-if="!hasObserver"
            icon="i-lucide-refresh-cw"
            color="neutral"
            variant="ghost"
            size="sm"
            @click="loadMore"
          >
            Load more
          </UButton>
        </li>
      </ul>
    </div>

    <!-- Mobile FAB: new list. The navbar keeps the canonical new-card-button
         (Phase 2); this is the prominent bottom-right affordance on phones. -->
    <UButton
      data-testid="board-fab-new"
      class="sm:hidden fixed right-4 z-40 rounded-full shadow-lg"
      style="bottom: calc(1rem + env(safe-area-inset-bottom))"
      icon="i-lucide-plus"
      size="xl"
      color="primary"
      aria-label="New Check List"
      @click="createAndOpen"
    />
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
import { useCreateCheckList } from "~/composables/useCreateCheckList";

const route = useRoute();
const { openCard } = useAppRoute();
const { createAndOpen } = useCreateCheckList();
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

// Gates the empty states: fetchNextPage() is async, so the board is briefly
// empty on first paint. We only know whether the board is truly empty once the
// initial fetch has resolved.
const initialLoadDone = ref(false);
// Whether IntersectionObserver-driven auto-paging is available; if not, we fall
// back to a manual "Load more" button.
const hasObserver = ref(true);

// Current filter context (drives which empty state, if any, is shown).
const searchQuery = computed(() => (route.query.search as string) || null);
const labelFilter = computed(() => (route.query.label as string) || null);
const sharedFilter = computed(() => (route.query.shared as string) || null);
const archivedFilter = computed(() => route.query.archived === "true");

const isEmpty = computed(() => dragPinned.value.length === 0 && dragNormal.value.length === 0);

onMounted(async () => {
  hasObserver.value = typeof IntersectionObserver !== "undefined";
  try {
    await checkListStore.fetchNextPage();
  } finally {
    // Always reveal the board once the initial fetch settles — even if it
    // rejected. Gating on success would leave the board hidden permanently on a
    // transient error. Colours/labels are board chrome and don't block paint.
    initialLoadDone.value = true;
  }
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
  async (
    query: string | null,
    labelId: string | null,
    shared: "with_me" | "by_me" | null,
    archived: boolean
  ) => {
    if (query || shared || archived) {
      await checkListStore.searchChecklists(query, labelId, shared, archived);
    } else {
      checkListStore.clearSearch();
    }
  },
  300
);

// Re-run whenever search text, label, shared, or archived filter changes. The
// Archive view (?archived=true) pages archived cards through the same
// server-side filtered view as search/shared.
watch(
  () => ({
    search: route.query.search as string,
    label: route.query.label as string,
    shared: route.query.shared as string,
    archived: route.query.archived as string,
  }),
  ({ search, label, shared, archived }) =>
    runFilter(
      search || null,
      label || null,
      (shared as "with_me" | "by_me") || null,
      archived === "true"
    ),
  { immediate: true }
);

// Sync drag list — use search results when active, else normal paginated list.
// Guard: never splice while a drag is in progress — mid-drag store updates
// (SSE, prev-drag async) reset FormKit DnD's state and corrupt event.values.
watchEffect(() => {
  if (checklistDragInProgress) return;
  const label = (route.query.label as string) || null;
  const archivedView = route.query.archived === "true";
  // In a filtered view, resolve each result through the store's canonical
  // instance (get() prefers checkLists) so an archive/restore that flipped the
  // flag on the shared card object is reflected here, then keep only cards whose
  // archived state matches the current view — a restored card drops out of the
  // Archive board immediately, an archived one drops out of search/shared.
  const source = searchResults.value !== null
    ? searchResults.value
        .map((c) => checkListStore.get(c.id) ?? c)
        .filter((c) => (c.position.archived ?? false) === archivedView)
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

// Manual one-page fetch for the no-observer fallback button. (The observer path
// uses onLoadingTriggerVisibility, which keeps paging while the trigger stays
// in view.)
async function loadMore() {
  if (loadingInProcess.value || !hasMoreToLoad.value) return;
  loadingInProcess.value = true;
  if (searchResults.value !== null) {
    await checkListStore.fetchMoreFiltered();
  } else {
    await checkListStore.fetchNextPage();
  }
  loadingInProcess.value = false;
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
/* Lift on the same 150ms / cubic-bezier curve as the card's shadow+ring
   transition (Tailwind's transition-shadow) so they move together. */
.checklist-preview {
  transition: transform 0.15s cubic-bezier(0.4, 0, 0.2, 1);
}
.checklist-preview:hover {
  transform: translateY(-2px);
}
</style>
