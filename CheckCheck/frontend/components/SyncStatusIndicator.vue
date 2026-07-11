<template>
  <!-- Global sync status (WI-14). Only meaningful under local-first, where the
       outbox + delta layer exist; flag-off there is nothing to show. -->
  <UPopover v-if="localFirst" v-model:open="isOpen">
    <div data-testid="sync-status-chip">
      <UChip
        :show="pendingCount > 0"
        :text="pendingBadge"
        size="2xl"
        color="primary"
      >
        <UButton
          variant="ghost"
          color="neutral"
          size="sm"
          :icon="icon"
          :ui="syncing ? { leadingIcon: 'animate-spin' } : {}"
          :aria-label="`Sync status: ${statusLabel}`"
          data-testid="sync-status-button"
        />
      </UChip>
    </div>

    <template #content>
      <div class="w-72 max-w-[90vw] flex flex-col p-3 gap-3" data-testid="sync-status-panel">
        <!-- Headline state -->
        <div class="flex items-start gap-2">
          <UIcon :name="icon" :class="['size-5 shrink-0 mt-0.5 text-muted', syncing ? 'animate-spin' : '']" />
          <div class="min-w-0">
            <p class="text-sm font-semibold text-highlighted" data-testid="sync-status-headline">
              {{ statusLabel }}
            </p>
            <p class="text-xs text-muted" data-testid="sync-status-detail">{{ statusDetail }}</p>
          </div>
        </div>

        <!-- Last synced -->
        <div class="flex items-center justify-between text-xs">
          <span class="text-muted">Last synced</span>
          <span class="text-default" data-testid="sync-status-last-synced">{{ lastSyncedLabel }}</span>
        </div>

        <!-- Manual sync -->
        <UButton
          block
          size="sm"
          color="neutral"
          variant="soft"
          icon="i-lucide-refresh-cw"
          :loading="syncing"
          :disabled="!online || syncing"
          data-testid="sync-now-button"
          @click="onSyncNow"
        >
          {{ online ? "Sync now" : "Offline" }}
        </UButton>
      </div>
    </template>
  </UPopover>
</template>

<script setup lang="ts">
import { useNow } from "@vueuse/core";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { useSyncStatus } from "@/composables/useSyncStatus";

// Flag-off there is no outbox/delta layer — never mount the composable.
const localFirst = isLocalFirstEnabled();

const isOpen = ref(false);

// Guard the composable behind the flag; give inert defaults when off so the
// template's computeds stay valid even though the popover never renders.
const status = localFirst ? useSyncStatus() : null;
const online = computed(() => status?.online.value ?? true);
const pendingCount = computed(() => status?.pendingCount.value ?? 0);
const syncing = computed(() => status?.syncing.value ?? false);
const lastSyncedAt = computed(() => status?.lastSyncedAt.value ?? null);

// Re-render relative time on a coarse tick so "3 min ago" stays honest while the
// popover is open (cheap; a single shared timer).
const now = useNow({ interval: 30_000 });

const pendingBadge = computed(() => (pendingCount.value > 99 ? "99+" : String(pendingCount.value)));

// Icon precedence: offline > syncing > pending > all-clear. Toast palette is
// primary/error/neutral only (no green/amber), so distinguish states by icon.
const icon = computed(() => {
  if (!online.value) return "i-lucide-cloud-off";
  if (syncing.value) return "i-lucide-refresh-cw";
  if (pendingCount.value > 0) return "i-lucide-cloud-upload";
  return "i-lucide-cloud-check";
});

const statusLabel = computed(() => {
  if (!online.value) return "Offline";
  if (syncing.value) return "Syncing…";
  if (pendingCount.value > 0) return "Changes pending";
  return "All changes synced";
});

const statusDetail = computed(() => {
  const n = pendingCount.value;
  const changes = `${n} ${n === 1 ? "change" : "changes"}`;
  if (!online.value) {
    return n > 0
      ? `${changes} saved on this device — will sync when you reconnect.`
      : "Working offline — changes are saved on this device.";
  }
  if (syncing.value) return "Bringing your data up to date…";
  if (n > 0) return `${changes} waiting to reach the server.`;
  return "Everything on this device matches the server.";
});

const lastSyncedLabel = computed(() => {
  const at = lastSyncedAt.value;
  if (at == null) return "never";
  // Reference `now` so this recomputes on the tick.
  const diffSec = Math.round((now.value.getTime() - at) / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr} h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 7) return `${diffDay} d ago`;
  return new Date(at).toLocaleDateString();
});

async function onSyncNow() {
  if (!status || !online.value || syncing.value) return;
  await status.syncNow();
}
</script>
