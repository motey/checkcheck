<template>
  <!-- Render nothing when there are no pending invites (the common case — invites
       only exist when the server runs in invite mode). Keeps the dropdown clean. -->
  <div
    v-if="store.pending.length > 0"
    class="border-b border-default"
    data-testid="invite-section"
  >
    <div class="px-3 py-2 bg-elevated">
      <span class="text-sm font-semibold">Invites</span>
    </div>

    <div
      v-for="inv in store.pending"
      :key="inv.checklist_id"
      class="flex items-start gap-3 px-3 py-2.5 border-t border-default first:border-t-0"
      data-testid="invite-row"
    >
      <UIcon name="i-lucide-user-plus" class="mt-0.5 size-4 shrink-0 text-muted" />
      <div class="flex flex-col min-w-0 flex-1 gap-1.5">
        <span class="text-sm">
          <span class="font-medium">{{ inviterOf(inv) }}</span>
          invited you to
          <span class="font-medium">{{ listNameOf(inv) }}</span>
        </span>
        <div class="flex items-center gap-2">
          <UBadge color="neutral" variant="subtle" size="sm">{{ inv.permission }}</UBadge>
          <span class="text-xs text-muted">{{ relativeTime(inv.created_at) }}</span>
        </div>
        <div class="flex items-center gap-2 mt-0.5">
          <UButton
            color="primary"
            size="xs"
            :loading="busy === inv.checklist_id"
            :disabled="busy !== null"
            data-testid="invite-accept"
            @click="onAccept(inv.checklist_id)"
          >
            Accept
          </UButton>
          <UButton
            color="neutral"
            variant="subtle"
            size="xs"
            :disabled="busy !== null"
            data-testid="invite-decline"
            @click="onDecline(inv.checklist_id)"
          >
            Decline
          </UButton>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useInviteStore } from "@/stores/invite";

const store = useInviteStore();

// The checklist id currently being accepted/declined (drives the row's loading
// state and disables all buttons so a double-click can't fire two calls).
const busy = ref<string | null>(null);

// Render the inviter defensively — display_name → user_name → "Someone" (the
// same fallback ladder the F5 notification rows use).
function inviterOf(inv: InviteReadType): string {
  return inv.inviter_display_name || inv.inviter_user_name || "Someone";
}

// The shared list's name, with a fallback when the backend sends null.
function listNameOf(inv: InviteReadType): string {
  return inv.checklist_name ? `"${inv.checklist_name}"` : "a list";
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

async function onAccept(clId: string) {
  if (busy.value) return;
  busy.value = clId;
  // accept() pushes the card into the grid + drops it from `pending` on success.
  await store.accept(clId).catch(() => {});
  busy.value = null;
}

async function onDecline(clId: string) {
  if (busy.value) return;
  busy.value = clId;
  await store.decline(clId).catch(() => {});
  busy.value = null;
}
</script>

<style scoped></style>
