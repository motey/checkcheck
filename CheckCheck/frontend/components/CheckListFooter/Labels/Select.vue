<template>
  <UDropdownMenu searchable :items="items" :content="{ align: 'start' }" :ui="{ content: 'w-48' }">
    <CheckListColoredButton variant="ghost" icon="i-lucide-tags" :checkListId="checkListId" @click.stop />
    <template #search-label>
      <UInput v-model="query" />
    </template>
    <!--<UButton @click.stop color="neutral" variant="outline" icon="i-lucide-menu" />-->
  </UDropdownMenu>
</template>

<script setup lang="ts">
const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

const items = ref([
  {
    icon: "i-lucide-search",
    slot: "search" as const,
    onSelect(e: Event) {
      e.preventDefault();
    },
  },
  { label: "Apple 123erd23fr23wefwefwe", value: "apple" },
  { label: "Banana", value: "banana" },
  { label: "Cherry", value: "cherry" },
]);

const selected = ref(null);
const query = ref(null);
</script>
