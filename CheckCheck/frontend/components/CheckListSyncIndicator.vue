<template>
  <!-- A subtle corner badge shown while this card has undrained offline writes
       (WI-11 per-card pending indicator; feeds the WI-14 global status UI). It
       floats over the top-left corner so it never overlaps the title text. -->
  <UTooltip v-if="pending" :text="online ? 'Syncing changes…' : 'Changes saved offline — will sync when online'">
    <span
      data-testid="card-sync-pending"
      class="absolute -top-2 -left-2 z-10 flex h-5 w-5 items-center justify-center rounded-full bg-elevated text-dimmed shadow ring-1 ring-default"
    >
      <UIcon
        :name="online ? 'i-lucide-refresh-cw' : 'i-lucide-cloud-off'"
        :class="['h-3 w-3', online ? 'animate-spin' : '']"
      />
    </span>
  </UTooltip>
</template>

<script setup lang="ts">
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { useOutbox } from "@/composables/useOutbox";

const props = defineProps<{ checkListId: string }>();

// Flag-off there is no outbox, so never mount the composable / indicator.
const localFirst = isLocalFirstEnabled();
const outbox = localFirst ? useOutbox() : null;

const pending = computed(() => !!outbox && outbox.pendingCardIds.value.has(props.checkListId));
const online = computed(() => outbox?.online.value ?? true);
</script>
