import { createSharedComposable, useDebounceFn } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";

export const useSync = createSharedComposable(() => {
  const checkListStore = useCheckListsStore();
  const checkListItemStore = useCheckListsItemStore();

  // Collapse bursts of item-level notifications (e.g. rapid moves) into a
  // single refresh per checklist.  One debouncer is created per checklist id
  // and torn down after it fires.
  const pendingItemRefresh = new Map<string, () => void>();
  function scheduleItemRefresh(clId: string) {
    if (!pendingItemRefresh.has(clId)) {
      pendingItemRefresh.set(
        clId,
        useDebounceFn(() => {
          if (checkListItemStore.checkListsItems[clId]) {
            checkListItemStore.refreshAllCheckListItems(clId);
          }
          pendingItemRefresh.delete(clId);
        }, 400)
      );
    }
    pendingItemRefresh.get(clId)!();
  }

  let es: EventSource | null = null;

  function connect() {
    if (es) return;
    es = new EventSource("/api/sync");
    es.onmessage = (event: MessageEvent) => {
      try {
        handle(JSON.parse(event.data) as SyncNotificationType);
      } catch (e) {
        console.warn("[sync] failed to parse SSE event", e);
      }
    };
    es.onerror = () => {
      // The browser retries automatically; log for visibility only.
      console.warn("[sync] SSE connection error — browser will retry");
    };
  }

  function disconnect() {
    es?.close();
    es = null;
  }

  function handle(noti: SyncNotificationType) {
    const { cl_id: clId, cli_id: cliId, upd_prop } = noti;

    switch (upd_prop) {

      // ── Item-level ─────────────────────────────────────────────────────

      case "item_state":
        // Only refresh state if we already have this checklist's items loaded.
        if (cliId && checkListItemStore.checkListsItems[clId]) {
          checkListItemStore.refreshState(clId, cliId);
        }
        break;

      case "item_text":
        // Only refresh text if we already have this checklist's items loaded.
        // Components protect focused text fields with local refs so this
        // won't wipe in-progress edits.
        if (cliId && checkListItemStore.checkListsItems[clId]) {
          checkListItemStore.refresh(clId, cliId);
        }
        break;

      case "item_position":
      case "item_created":
        // High-frequency events (rapid reorder, bulk create) are collapsed
        // into one refresh per checklist via the debouncer.
        scheduleItemRefresh(clId);
        break;

      case "item_deleted":
        if (cliId) {
          const items = checkListItemStore.checkListsItems[clId];
          if (items) {
            const idx = items.findIndex((i) => i.id === cliId);
            if (idx !== -1) items.splice(idx, 1);
          }
        }
        break;

      // ── Checklist-level ────────────────────────────────────────────────

      case "checklist_created": {
        const alreadyPresent = checkListStore.checkLists.some((c) => c.id === clId);
        if (alreadyPresent) {
          // The creator's tab already added it via create() — just keep the
          // total count in sync without a redundant GET.
          checkListStore.total_backend_count++;
        } else {
          // Another tab or user created this checklist — fetch it.
          checkListStore.refresh(clId).then(() => {
            checkListItemStore.fetchMultipleChecklistsItemsPreview([clId]);
            checkListStore.total_backend_count++;
          });
        }
        break;
      }

      case "checklist_deleted": {
        const idx = checkListStore.checkLists.findIndex((c) => c.id === clId);
        if (idx !== -1) {
          checkListStore.checkLists.splice(idx, 1);
          checkListStore.total_backend_count = Math.max(0, checkListStore.total_backend_count - 1);
        }
        break;
      }

      case "checklist":
      case "checklist_label":
      case "checklist_position":
        checkListStore.refresh(clId);
        break;
    }
  }

  return { connect, disconnect };
});
