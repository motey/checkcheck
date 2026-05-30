import { createSharedComposable, useDebounceFn } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";

export const useSync = createSharedComposable(() => {
  const checkListStore = useCheckListsStore();
  const checkListItemStore = useCheckListsItemStore();

  // One debounced refresher per checklist — collapses bursts of item_position /
  // item_created events (e.g. a notification backlog) into a single fetch.
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
        }, 1200)
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
      } catch {}
    };
  }

  function disconnect() {
    es?.close();
    es = null;
  }

  function handle(noti: SyncNotificationType) {
    const clId = noti.cl_id;
    const cliId = noti.cli_id;

    switch (noti.upd_prop) {
      case "item_state":
        if (cliId && checkListItemStore.checkListsItems[clId]) {
          checkListItemStore.refreshState(clId, cliId);
        }
        break;

      case "item_text":
        if (cliId && checkListItemStore.checkListsItems[clId]) {
          checkListItemStore.refresh(clId, cliId);
        }
        break;

      case "item_position":
      case "item_created":
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

      case "checklist":
      case "checklist_label":
      case "checklist_position":
        checkListStore.refresh(clId);
        break;

      case "checklist_created":
        checkListStore.refresh(clId).then(() => {
          checkListItemStore.fetchMultipleChecklistsItemsPreview([clId]);
        });
        checkListStore.total_backend_count++;
        break;

      case "checklist_deleted": {
        const idx = checkListStore.checkLists.findIndex((c) => c.id === clId);
        if (idx !== -1) checkListStore.checkLists.splice(idx, 1);
        checkListStore.total_backend_count = Math.max(0, checkListStore.total_backend_count - 1);
        break;
      }
    }
  }

  return { connect, disconnect };
});
