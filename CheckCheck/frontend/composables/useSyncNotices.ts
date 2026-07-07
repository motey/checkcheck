import { createSharedComposable } from "@vueuse/core";
import { onSyncNotice, type SyncNotice } from "@/utils/syncNotices";
import { useOutbox } from "@/composables/useOutbox";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import type { OutboxEvent, OutboxOp } from "@/utils/outbox";

// ── Sync-notice UI (WI-11) ───────────────────────────────────────────────────
//
// The single consumer that turns offline-sync signals into user-visible toasts
// (and cleans up local state for a terminally-dropped write). Two sources:
//
//   • `utils/syncNotices` — concurrent-edit collisions (the local value was kept)
//     and `full_resync` drops, raised from the framework-free delta path.
//   • `useOutbox().onEvent` — `op-dropped`: a queued write that replayed to a
//     terminal 403/404/410 (access revoked / row deleted while offline-edited).
//     WI-11 discards the orphaned local state and shows a one-time message.
//
// Started once from `plugins/outbox.client.ts` (flag-on only). A singleton via
// `createSharedComposable`; the subscriptions live for the app's lifetime.

/** Suppress repeat toasts for the same subject inside this window (bursty deltas / coalesced drops). */
const DEDUPE_WINDOW_MS = 4000;

export const useSyncNotices = createSharedComposable(() => {
  const toast = useToast();
  const checkListStore = useCheckListsStore();
  const itemStore = useCheckListsItemStore();

  const recent = new Map<string, number>();
  /** True the first time a subject is seen in the window (and starts/refreshes its timer). */
  function firstInWindow(key: string): boolean {
    const now = Date.now();
    const last = recent.get(key) ?? 0;
    recent.set(key, now);
    return last <= now - DEDUPE_WINDOW_MS;
  }

  const cardName = (id: string): string => checkListStore.get(id)?.name?.trim() || "a list";

  function handleNotice(n: SyncNotice): void {
    switch (n.type) {
      case "conflict": {
        if (!firstInWindow(`conflict:${n.entity}:${n.id}`)) return;
        const subject = n.entity === "checklist" ? `“${cardName(n.id)}”` : "An item";
        toast.add({
          title: `${subject} was also edited elsewhere`,
          description: "Your change was kept and will sync.",
          icon: "i-lucide-users",
          color: "primary",
        });
        break;
      }
      case "resync-dropped": {
        const plural = n.count === 1 ? "change" : "changes";
        toast.add({
          title: "The server was reset",
          description: `${n.count} pending ${plural} couldn’t be applied and were discarded.`,
          icon: "i-lucide-server-crash",
          color: "error",
        });
        break;
      }
    }
  }

  function handleOutboxEvent(e: OutboxEvent): void {
    if (e.type !== "op-dropped") return;
    droppedOp(e.op, e.status);
  }

  function droppedOp(op: OutboxOp, status: number | undefined): void {
    if (op.entityType === "checklist") {
      const key = `dropped:checklist:${op.entityId}`;
      const name = cardName(op.entityId); // resolve BEFORE removing
      removeChecklistLocally(op.entityId);
      if (!firstInWindow(key)) return;
      toast.add({
        title: `“${name}” is no longer available`,
        description:
          status === 403
            ? "Your access was removed — offline changes to it were discarded."
            : "It was deleted — your offline changes to it were discarded.",
        icon: "i-lucide-ban",
        color: "error",
      });
    } else if (op.entityType === "item") {
      const clId = op.request.pathParams?.checklist_id;
      if (!firstInWindow(`dropped:item:${op.entityId}`)) return;
      toast.add({
        title: "An offline change couldn’t be applied",
        description: `An item in “${clId ? cardName(clId) : "a list"}” was removed on the server — your change was discarded.`,
        icon: "i-lucide-ban",
        color: "neutral",
      });
    }
    // label association drops (a card/label vanished) are benign — the card-level
    // toast above already covers the meaningful case; stay quiet to avoid noise.
  }

  /** Cleanly drop a card the server no longer accepts writes for, plus its items. */
  function removeChecklistLocally(id: string): void {
    const idx = checkListStore.checkLists.findIndex((c) => c.id === id);
    if (idx !== -1) {
      checkListStore.checkLists.splice(idx, 1);
      checkListStore.total_backend_count = Math.max(0, checkListStore.total_backend_count - 1);
    }
    if (checkListStore.searchResults) {
      const sIdx = checkListStore.searchResults.findIndex((c) => c.id === id);
      if (sIdx !== -1) checkListStore.searchResults.splice(sIdx, 1);
    }
    delete itemStore.checkListsItems[id];
  }

  onSyncNotice(handleNotice);
  useOutbox().onEvent(handleOutboxEvent);

  return {};
});
