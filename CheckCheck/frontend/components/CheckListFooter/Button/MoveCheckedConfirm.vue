<template>
  <!-- Confirm for "Move checked items to new card": the items leave this card,
       so ask first (like "Delete checked items"). -->
  <UModal
    v-model:open="open"
    title="Move checked items?"
    :ui="{ content: 'max-w-sm w-[calc(100vw-1rem)] sm:w-full rounded-2xl ring ring-default' }"
  >
    <template #content>
      <div class="p-4 sm:p-6 flex flex-col gap-4">
        <div class="flex flex-col gap-1">
          <h2 class="text-lg font-semibold">Move checked items?</h2>
          <p class="text-sm text-muted">
            {{ checkedCount }} checked {{ checkedCount === 1 ? "item moves" : "items move" }} to a new card
            and {{ checkedCount === 1 ? "leaves" : "leave" }} this one.
          </p>
        </div>
        <div class="flex justify-end gap-2">
          <UButton color="neutral" variant="ghost" label="Cancel" @click.stop="open = false" />
          <UButton
            color="primary"
            label="Move checked"
            data-testid="confirm-move-checked"
            :loading="loading"
            @click.stop="emit('confirm')"
          />
        </div>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
defineProps<{ checkedCount: number; loading: boolean }>();
const open = defineModel<boolean>("open", { default: false });
const emit = defineEmits<{ confirm: [] }>();
</script>
