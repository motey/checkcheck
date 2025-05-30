<template>
  <UModal title="Opened Checklist Editor" :ui="{
    content: 'p-0',       // Remove padding from content
    body: 'p-0',          // Remove padding from body
  }">
    <template #content>
        <div class="max-h-[100vh] overflow-y-auto overflow-x-hidden">
      <CheckList  :checkListId="checkListId" :editModeActive="true" />
    </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
console.log("MODAL OPEN");
// Props
const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});


</script>

<style scoped>

</style>
