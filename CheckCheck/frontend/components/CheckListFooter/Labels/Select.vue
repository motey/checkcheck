<template>
  <USelectMenu v-model="value" multiple :items="items" class="w-48" />
</template>
<script setup lang="ts">
const items = ref(["Backlog", "Todo", "In Progress", "Done"]);
const value = ref(["Backlog", "Todo"]);
</script>
