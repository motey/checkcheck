import { createSharedComposable } from "@vueuse/core";
import { onSyncNotice, type SyncNotice } from "@/utils/syncNotices";
import { useOutbox } from "@/composables/useOutbox";
import { useCheckListsStore } from "@/stores/checklist";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { applyDelta } from "@/utils/localSnapshot";
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
  // Captured at setup so async event handlers can drive a delta pull without a
  // live component context (Chunk A3).
  const pinia = useNuxtApp().$pinia as any;

  const recent = new Map<string, number>();
  // Sidebar counts count *cards* and are moved by the actor's own card
  // create/delete/archive and label attach/detach — but the confirming delta
  // pull is blind to those optimistic changes, so the badges never refresh on
  // their own (finding B1). Only *archive* adjusts them inline
  // (`_adjustCountsForArchive`); everything else relied on an unrelated refetch.
  // The outbox reports `countsDirty` when such an op drained, so refetch once per
  // drain (debounced to coalesce a burst of creates into one request). This also
  // reconciles `shared_by_me` on archive, closing the docs/ISSUES.md entry.
  let countsTimer: ReturnType<typeof setTimeout> | null = null;
  function scheduleCountsRefresh(): void {
    if (countsTimer) clearTimeout(countsTimer);
    countsTimer = setTimeout(() => {
      countsTimer = null;
      void checkListStore.fetchCounts();
    }, 500);
  }
  // A durable-storage failure is a persistent environment condition, not a
  // transient event — toast it once per session rather than on every failed
  // persist (which would fire on every enqueue/drain).
  let storageWarned = false;
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
      case "storage-failed": {
        if (storageWarned) return;
        storageWarned = true;
        toast.add({
          title: "Couldn’t save changes on this device",
          description:
            "Your browser blocked local storage. Offline changes may be lost if you reload — stay online so they sync.",
          icon: "i-lucide-database-backup",
          color: "error",
        });
        break;
      }
    }
  }

  function handleOutboxEvent(e: OutboxEvent): void {
    if (e.type === "idle") {
      if (e.countsDirty) scheduleCountsRefresh();
      return;
    }
    if (e.type !== "op-dropped") return;
    droppedOp(e.op, e.status);
  }

  // A 403 is NOT proof the card is gone: a permission *downgrade* (edit→view)
  // also 403s a queued write, but the user keeps view access and the card must
  // stay (Chunk A3). Only 403/404/410 (and 409) mean the row/access is truly
  // gone; a terminal 400/422 is a malformed op, not a deletion (finding B5).
  const impliesRowGone = (status: number | undefined): boolean =>
    status === 403 || status === 404 || status === 409 || status === 410;

  function droppedOp(op: OutboxOp, status: number | undefined): void {
    if (op.entityType === "checklist") {
      const key = `dropped:checklist:${op.entityId}`;
      const name = cardName(op.entityId);
      // Don't remove the card locally on a drop. Let the server decide: pull a
      // delta — the card id is still in `known=`, so a genuine revocation comes
      // back in `removed_checklist_ids` and the normal removal path takes it out;
      // a downgrade re-emits the card (view access) and it stays put (Chunk A3).
      if (isLocalFirstEnabled()) applyDelta(pinia).catch(() => {});
      if (!firstInWindow(key)) return;
      toast.add({
        title: `Offline changes to “${name}” couldn’t be saved`,
        description: impliesRowGone(status)
          ? "They were discarded — you may no longer have edit access to it."
          : "They were discarded.",
        icon: "i-lucide-ban",
        color: impliesRowGone(status) ? "error" : "neutral",
      });
    } else if (op.entityType === "item") {
      const clId = op.request.pathParams?.checklist_id;
      if (!firstInWindow(`dropped:item:${op.entityId}`)) return;
      toast.add({
        title: "An offline change couldn’t be applied",
        description: `A change to an item in “${clId ? cardName(clId) : "a list"}” was discarded.`,
        icon: "i-lucide-ban",
        color: "neutral",
      });
    }
    // label association drops (a card/label vanished) are benign — the card-level
    // toast above already covers the meaningful case; stay quiet to avoid noise.
  }

  onSyncNotice(handleNotice);
  useOutbox().onEvent(handleOutboxEvent);

  return {};
});
