<template>
  <UModal
    v-model:open="open"
    title="Opened Checklist Editor"
    :ui="{
      content: 'max-w-2xl w-[calc(100vw-1rem)] sm:w-full max-h-[92dvh] rounded-2xl ring ring-default overflow-hidden',
    }"
  >
    <template #content>
      <div class="relative flex flex-col max-h-[92dvh] overflow-hidden overscroll-contain">
        <!-- Explicit close affordance (the editor otherwise fills the modal). -->
        <UButton
          icon="i-lucide-x"
          color="neutral"
          variant="ghost"
          size="sm"
          aria-label="Close"
          class="absolute top-2 right-2 z-10"
          @click="open = false"
        />
        <!-- CheckList has a top-level await (loads the card + its items on
             mount), so it needs its own Suspense boundary. Keyed by id so
             reopening a different card always remounts with fresh data. -->
        <Suspense>
          <CheckList :key="checkListId" :checkListId="checkListId" :editModeActive="true" />
          <template #fallback>
            <div class="flex items-center justify-center min-h-48 p-10 text-muted">
              <UIcon name="i-lucide-loader-circle" class="size-6 animate-spin" />
            </div>
          </template>
        </Suspense>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
const open = defineModel<boolean>("open", { default: false });

defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});
</script>
