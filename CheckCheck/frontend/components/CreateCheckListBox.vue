<template>
  <div class="flex space-x-2">
    <UInput
      v-model="searchQuery"
      icon="i-lucide-search"
      size="xl"
      variant="outline"
      placeholder="Search..."
    >
      <template v-if="searchQuery" #trailing>
        <UButton variant="ghost" color="neutral" icon="i-lucide-x" size="xs" @click="searchQuery = ''" />
      </template>
    </UInput>
    <UButton icon="i-lucide-list-plus" size="xl" color="primary" @click="openCheckListEditor()" variant="solid"
      >New Check List</UButton
    >
  </div>
</template>

<script setup lang="ts">
import { useAppRoute } from "~/composables/useAppRoute";

const checkListsStore = useCheckListsStore();
const { search, setSearch, openCard } = useAppRoute();

const searchQuery = ref(search.value ?? "");

watch(searchQuery, (val) => {
  setSearch(val || null);
});

watch(search, (val) => {
  searchQuery.value = val ?? "";
});

function openCheckListEditor() {
  (async () => {
    const checkList = await checkListsStore.create({} as CheckListCreateType);
    // Open the freshly created card via the URL so it's shareable too.
    openCard(checkList.id);
  })();
}
</script>
