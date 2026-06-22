<template>
  <UModal
    v-model:open="open"
    title="Edit Labels"
    :ui="{
      content: 'max-w-lg w-[calc(100vw-1rem)] sm:w-full max-h-[92dvh] rounded-2xl ring ring-default overflow-hidden',
      body: 'p-0',
      header: 'hidden',
    }"
  >
    <template #content>
      <div class="max-h-[92dvh] overflow-y-auto overflow-x-hidden overscroll-contain">
        <LabelManager @close="open = false" />
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
const open = defineModel<boolean>("open", { default: false });
</script>
