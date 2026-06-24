<template>
  <div class="flex items-center gap-2 w-full max-w-xl">
    <!-- Search: always inline on sm+, collapsible behind a button on mobile -->
    <div class="flex-1 min-w-0" :class="expanded ? 'flex' : 'hidden sm:flex'">
      <UInput
        v-model="searchQuery"
        data-testid="search-input"
        icon="i-lucide-search"
        size="lg"
        variant="outline"
        placeholder="Search..."
        class="w-full"
        @blur="collapseIfEmpty"
      >
        <template v-if="searchQuery" #trailing>
          <UButton variant="ghost" color="neutral" icon="i-lucide-x" size="xs" aria-label="Clear search" @click="searchQuery = ''" />
        </template>
      </UInput>
    </div>

    <!-- Mobile-only: tap to reveal the search input -->
    <UButton
      v-if="!expanded"
      data-testid="search-toggle"
      class="sm:hidden shrink-0"
      icon="i-lucide-search"
      size="lg"
      color="neutral"
      variant="ghost"
      aria-label="Search"
      @click="expandSearch"
    />

    <!-- New Check List: icon-only on mobile, labeled on sm+ -->
    <UButton
      data-testid="new-card-button"
      icon="i-lucide-list-plus"
      size="lg"
      color="primary"
      variant="solid"
      class="shrink-0"
      aria-label="New Check List"
      @click="createAndOpen()"
    >
      <span class="hidden sm:inline">New Check List</span>
    </UButton>
  </div>
</template>

<script setup lang="ts">
import { useAppRoute } from "~/composables/useAppRoute";
import { useCreateCheckList } from "~/composables/useCreateCheckList";

const { search, setSearch } = useAppRoute();
const { createAndOpen } = useCreateCheckList();

const searchQuery = ref(search.value ?? "");
// Mobile-only reveal state; sm+ always shows the input via CSS.
const expanded = ref(!!searchQuery.value);

watch(searchQuery, (val) => {
  setSearch(val || null);
});

watch(search, (val) => {
  searchQuery.value = val ?? "";
  if (val) expanded.value = true;
});

function expandSearch() {
  expanded.value = true;
  nextTick(() => {
    document.querySelector<HTMLInputElement>('input[data-testid="search-input"]')?.focus();
  });
}

function collapseIfEmpty() {
  if (!searchQuery.value) expanded.value = false;
}
</script>
