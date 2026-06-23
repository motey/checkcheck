import { createSharedComposable, useDebounceFn } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useShareStore } from "@/stores/share";
import { useNotificationStore } from "@/stores/notification";
import { useInviteStore } from "@/stores/invite";

export const useSync = createSharedComposable(() => {
  const checkListStore = useCheckListsStore();
  const checkListItemStore = useCheckListsItemStore();
  const shareStore = useShareStore();
  const notificationStore = useNotificationStore();
  const inviteStore = useInviteStore();

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
  // Track whether we've already had a successful connection. EventSource fires
  // onopen on the very first connect (board already freshly loaded → nothing to
  // do) and again on every automatic reconnect (events fired while we were
  // disconnected are lost → reconcile the store).
  let hasOpened = false;

  function connect() {
    if (es) return;
    hasOpened = false;
    es = new EventSource("/api/sync");
    es.onopen = () => {
      if (!hasOpened) {
        hasOpened = true;
        return;
      }
      console.info("[sync] SSE reconnected — resyncing store");
      checkListStore.resync();
    };
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
    hasOpened = false;
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

      // ── Sharing ────────────────────────────────────────────────────────

      case "share_added":
      case "share_removed":
        // The card's collaborator set changed, which may have changed *our*
        // effective permission. Re-read the card we already hold so
        // `my_permission` re-gates the UI immediately (the open ShareModal, once
        // it exists in F2, refreshes its own collaborator list off the same
        // event). A collaborator who was just added/removed gets a separate
        // `checklist_created` / `checklist_deleted` instead.
        if (checkListStore.get(clId)) checkListStore.refresh(clId);
        // If the ShareModal is open for this card, re-read its collaborator list.
        shareStore.refreshIfOpen(clId);
        break;

      case "share_invited":
        // A card was shared with this user in invite mode (it lands as a pending
        // invite they must accept/decline rather than appearing in their grid).
        // Re-read the inbox so the bell's Invites section updates live. NOTE:
        // authed board SSE only — the anonymous /p/<token> viewer never reaches
        // here (it uses usePublicCard's own EventSource).
        inviteStore.refresh();
        break;

      case "notification":
        // A new notification landed for this user. Always refresh the cheap
        // unread badge; if the dropdown is open, also re-list the visible feed so
        // the new row shows live. NOTE: this is the AUTHED board's SSE only — the
        // anonymous /p/<token> viewer uses usePublicCard's own EventSource and
        // never touches this store.
        notificationStore.refreshUnread();
        if (notificationStore.open) notificationStore.list({ limit: 30 });
        break;
    }
  }

  return { connect, disconnect };
});
