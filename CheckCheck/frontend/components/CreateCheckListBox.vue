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
import { CheckListEditModal } from "#components";

const checkListsStore = useCheckListsStore();
const route = useRoute();
const router = useRouter();

const searchQuery = ref((route.query.search as string) || "");

watch(searchQuery, (val) => {
  router.replace({ query: { ...route.query, search: val || undefined } });
});

watch(() => route.query.search, (val) => {
  searchQuery.value = (val as string) || "";
});

const overlayCheckListCreate = useOverlay();
const modalCheckListCreate = overlayCheckListCreate.create(CheckListEditModal);

function openCheckListEditor() {
  (async () => {
    const checkList = ref(await checkListsStore.create({} as CheckListCreateType));
    modalCheckListCreate.open({
      checkListId: checkList.value.id,
    });
  })();
}
</script>
