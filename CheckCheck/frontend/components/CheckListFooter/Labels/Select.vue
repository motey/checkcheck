

<template>
  <USelectMenu
    @click.stop
    multiple
    :items="items"
    v-model="selected"
    searchable
    :ui="{ width: 'w-auto' }"
    :uiMenu="{ width: 'w-64' }"
    variant="none"
  >
    <!-- Custom Activator: Only Icon Button -->
    <template #default="{ open, toggle }">
      <UButton
        icon="i-lucide-tag"
        size="sm"
        color="gray"
        variant="ghost"
        @click="toggle"
        :aria-expanded="open"
        aria-haspopup="listbox"
      />
    </template>
  </USelectMenu>
</template>


<script setup>
const items = ref([
  { label: "Apple 123erd23fr23wefwefwe", value: "apple" },
  { label: "Banana", value: "banana" },
  { label: "Cherry", value: "cherry" },
]);

const selected = ref(null);
</script>