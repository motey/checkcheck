<template>
  <!-- FEATURE-GATE: no notifications are ever produced server-side when sharing
       is off, so render nothing at all. -->
  <UPopover v-if="publicConfig.sharingEnabled" v-model:open="isOpen">
    <div data-testid="notification-bell-chip">
      <UChip :show="store.unreadCount > 0" :text="badgeText" size="2xl" color="error">
        <UButton
          variant="ghost"
          color="neutral"
          size="sm"
          icon="i-lucide-bell"
          aria-label="Notifications"
          data-testid="notification-bell"
        />
      </UChip>
    </div>

    <template #content>
      <div class="w-80 max-w-[90vw] flex flex-col" data-testid="notification-panel">
        <!-- Header: title + mark-all-read -->
        <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-default">
          <span class="text-sm font-semibold">Notifications</span>
          <UButton
            v-if="store.unreadCount > 0"
            variant="link"
            color="primary"
            size="xs"
            data-testid="notification-mark-all-read"
            @click="onMarkAllRead"
          >
            Mark all read
          </UButton>
        </div>

        <!-- Invite inbox (F6): a distinct "Invites" section above the feed,
             accept/decline inline. Renders nothing when there are no pending
             invites (the common case — invites only exist in invite mode). -->
        <InviteInbox />

        <!-- Feed (newest-first as the backend returns it) -->
        <div class="max-h-[60vh] overflow-y-auto">
          <p
            v-if="store.items.length === 0"
            class="px-3 py-6 text-center text-sm text-muted"
          >
            No notifications yet.
          </p>

          <button
            v-for="n in store.items"
            :key="n.id"
            type="button"
            data-testid="notification-row"
            class="w-full text-left flex items-start gap-3 px-3 py-2.5 border-b border-default last:border-b-0 hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
            :class="!n.read_at ? 'bg-elevated' : ''"
            @click="onRowClick(n)"
          >
            <UIcon :name="iconFor(n.type)" class="mt-0.5 size-4 shrink-0 text-muted" />
            <div class="flex flex-col min-w-0 flex-1">
              <span class="text-sm" :class="!n.read_at ? 'font-medium' : 'text-muted'">
                {{ messageFor(n) }}
              </span>
              <span class="text-xs text-muted">{{ relativeTime(n.created_at) }}</span>
            </div>
            <!-- Unread dot -->
            <span
              v-if="!n.read_at"
              class="mt-1.5 size-2 shrink-0 rounded-full bg-primary"
              aria-hidden="true"
            />
          </button>
        </div>
      </div>
    </template>
  </UPopover>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useNotificationStore } from "@/stores/notification";
import { useInviteStore } from "@/stores/invite";
import { usePublicConfigStore } from "@/stores/publicConfig";
import { useAppRoute } from "~/composables/useAppRoute";

const store = useNotificationStore();
const inviteStore = useInviteStore();
const publicConfig = usePublicConfigStore();
const { openCard } = useAppRoute();

const isOpen = ref(false);

// Cap the badge so a large backlog doesn't blow out the chip.
const badgeText = computed(() => (store.unreadCount > 99 ? "99+" : String(store.unreadCount)));

onMounted(() => {
  // Only meaningful when sharing is on, but the unread-count call is cheap and
  // harmless either way; gate to avoid a needless request when off.
  if (publicConfig.sharingEnabled) {
    store.refreshUnread();
    // Load the invite inbox up front so the Invites section is ready the moment
    // the dropdown opens (the #content slot is rendered lazily, so InviteInbox's
    // own mount can't be relied on to fetch). An empty list is correct + cheap
    // when the server isn't in invite mode.
    inviteStore.refresh();
  }
});

// Drive the useSync live re-list off the open flag, and load the feed when the
// dropdown opens.
watch(isOpen, (open) => {
  store.setOpen(open);
  if (open) store.list({ limit: 30 });
});

function iconFor(type: NotificationType): string {
  switch (type) {
    case "card_shared":
      return "i-lucide-share-2";
    case "card_invited":
      return "i-lucide-user-plus";
    case "public_link_opened":
      return "i-lucide-eye";
    default:
      return "i-lucide-bell";
  }
}

// Render from the open `payload` defensively — it may be null and individual keys
// may be absent; fall back to a sensible per-type string.
function messageFor(n: NotificationReadType): string {
  const payload = (n.payload ?? {}) as Record<string, unknown>;
  const actor = typeof payload.actor_display_name === "string" ? payload.actor_display_name : null;
  const listName = typeof payload.checklist_name === "string" ? payload.checklist_name : null;
  const who = actor ?? "Someone";
  const list = listName ? `"${listName}"` : "a list";

  switch (n.type) {
    case "card_shared":
      return `${who} shared ${list} with you`;
    case "card_invited":
      return `${who} invited you to ${list}`;
    case "public_link_opened":
      return `${who} opened the public link for ${list}`;
    default:
      return `Update on ${list}`;
  }
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return new Date(then).toLocaleDateString();
}

async function onRowClick(n: NotificationReadType) {
  await store.markRead(n.id).catch(() => {});
  // Card-related rows navigate to the card overlay; rows without a cl_id just
  // mark read.
  if (n.cl_id) {
    isOpen.value = false;
    openCard(n.cl_id);
  }
}

async function onMarkAllRead() {
  await store.markAllRead().catch(() => {});
}
</script>

<style scoped></style>
