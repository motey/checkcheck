import type { Pinia } from "pinia";
import { createSharedComposable, useDebounceFn } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useShareStore } from "@/stores/share";
import { useNotificationStore } from "@/stores/notification";
import { useInviteStore } from "@/stores/invite";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { setConnectivity, probe } from "@/utils/connectivity";
import { applyDelta } from "@/utils/localSnapshot";

export const useSync = createSharedComposable(() => {
  const pinia = useNuxtApp().$pinia as Pinia;
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

  // Refetch the sidebar count badges after board-mutating events. Debounced so
  // a burst (bulk archive, rapid label toggles) costs one request, not one per
  // event. Fires on the trailing edge.
  const scheduleCountsRefresh = useDebounceFn(() => {
    checkListStore.fetchCounts();
  }, 500);

  let es: EventSource | null = null;
  // Track whether we've already had a successful connection. EventSource fires
  // onopen on the very first connect (board already freshly loaded → nothing to
  // do) and again on every automatic reconnect (events fired while we were
  // disconnected are lost → reconcile the store).
  let hasOpened = false;

  // Manual reconnect on a capped backoff. The browser's own EventSource retry
  // only covers network-level drops and clean stream ends; when the stream fails
  // on an HTTP error status — exactly what a down backend behind Traefik returns
  // (502/503) — the spec requires the browser to fail *permanently*: `onerror`
  // fires once, `readyState` goes to CLOSED, and it never retries. Without this
  // the client stays stuck "Offline" after a server-only outage until a reload
  // (see docs/ISSUES.md). We watch for the CLOSED state and rebuild the stream
  // ourselves; `onopen` then restores the `setConnectivity(true)` path.
  const RECONNECT_MIN_MS = 1_000;
  const RECONNECT_MAX_MS = 30_000;
  let reconnectDelay = RECONNECT_MIN_MS;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function clearReconnect() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer !== null) return; // already pending
    const delay = reconnectDelay;
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    console.warn(`[sync] SSE closed — reconnecting in ${delay}ms`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      // Tear down the dead stream and rebuild it. `connect()` no-ops if a stream
      // already exists, so `disconnect()` first guarantees a fresh EventSource.
      // Preserve `hasOpened` across the rebuild: we *had* a live connection before
      // the outage, so the next `onopen` must run the reconcile delta-pull (catch
      // up on everything that changed while the server was down), not treat this
      // as a fresh initial load.
      const wasOpened = hasOpened;
      disconnect();
      connect();
      hasOpened = wasOpened;
    }, delay);
  }

  // When a backgrounded tab returns to the foreground, catch up. A mobile PWA
  // (iOS standalone especially) gets its page — and its SSE stream — frozen by
  // the OS while backgrounded; on resume the browser's EventSource auto-reconnect
  // *usually* fires `onopen` → a delta pull, but that is not guaranteed and can
  // lag. A `visibilitychange → pull` is a cheap belt-and-suspenders that
  // converges the board the moment the tab is visible again. Only fires on
  // becoming visible (never on hide) so it costs nothing in the background.
  async function onVisible() {
    if (typeof document === "undefined" || document.visibilityState !== "visible") return;
    if (isLocalFirstEnabled()) {
      // The frozen tab may still believe it's online; confirm reachability first
      // (this also flips the connectivity signal back so the outbox resumes
      // draining and online-only surfaces re-enable), then pull the delta.
      if (await probe()) void applyDelta(pinia);
      return;
    }
    checkListStore.resync();
    checkListStore.fetchCounts();
  }

  function connect() {
    if (es) return;
    hasOpened = false;
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisible);
    }
    es = new EventSource("/api/sync");
    es.onopen = () => {
      // A live stream means our manual-reconnect backoff can reset to its floor.
      clearReconnect();
      reconnectDelay = RECONNECT_MIN_MS;
      // A live sync socket proves real server reachability — feed the outbox's
      // connectivity signal (WI-7) so a reconnect resumes draining queued writes.
      // Harmless flag-off (no outbox listens); gated to avoid confusing the
      // legacy path.
      if (isLocalFirstEnabled()) setConnectivity(true);
      if (!hasOpened) {
        hasOpened = true;
        return;
      }
      // Events fired while we were disconnected are gone; reconcile. Flag-on
      // (WI-10): a single delta pull catches up everything the poke would have
      // triggered — no full board refetch. Flag-off keeps the legacy resync.
      if (isLocalFirstEnabled()) {
        console.info("[sync] SSE reconnected — pulling delta");
        void applyDelta(pinia);
        return;
      }
      console.info("[sync] SSE reconnected — resyncing store");
      checkListStore.resync();
      checkListStore.fetchCounts();
    };
    es.onmessage = (event: MessageEvent) => {
      try {
        handle(JSON.parse(event.data) as SyncNotificationType);
      } catch (e) {
        console.warn("[sync] failed to parse SSE event", e);
      }
    };
    es.onerror = () => {
      // A dropped sync socket is our earliest proof of lost reachability (the
      // `offline` window event may lag, or the interface may be up but the
      // server unreachable). Feed the connectivity signal (finding #8) so the
      // outbox stops draining and online-only surfaces (WI-12) disable; `onopen`
      // flips it back true on reconnect. Gated flag-on like onopen so the legacy
      // path's behaviour is untouched.
      if (isLocalFirstEnabled()) setConnectivity(false);
      // Two cases behind onerror:
      //   • readyState === CONNECTING → a transient blip; the browser is already
      //     auto-retrying, so leave it be (log only).
      //   • readyState === CLOSED    → a *permanent* failure (HTTP error close,
      //     e.g. a 502/503 from a bounced backend behind Traefik). The browser
      //     will never retry on its own, so schedule a manual reconnect.
      if (es && es.readyState === EventSource.CLOSED) {
        scheduleReconnect();
      } else {
        console.warn("[sync] SSE connection error — browser will retry");
      }
    };
  }

  function disconnect() {
    clearReconnect();
    es?.close();
    es = null;
    hasOpened = false;
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisible);
    }
  }

  // Events that can change a sidebar count badge: create/delete (home), an
  // archive toggle (a position update — moves a card between home & archive), a
  // label add/remove, and share add/remove (shared-with/by-me). Any of these
  // triggers a debounced counts refetch below.
  const COUNT_AFFECTING: ReadonlySet<string> = new Set([
    "checklist_created",
    "checklist_deleted",
    "checklist_position",
    "checklist_label",
    "share_added",
    "share_removed",
  ]);

  // Store touches that are NOT part of the /api/changes delta feed (shares,
  // invites, notifications stay online-only — WI-12). Driven by their SSE events
  // on both paths; the flag-on path calls this instead of the legacy board
  // refetch, since board state comes from the delta pull.
  function handleSideChannel(noti: SyncNotificationType) {
    switch (noti.upd_prop) {
      case "share_added":
      case "share_removed":
        // Permission / card changes arrive via the delta; here we only refresh
        // the open ShareModal's collaborator list (not in the delta feed).
        shareStore.refreshIfOpen(noti.cl_id);
        break;
      case "share_invited":
        inviteStore.refresh();
        break;
      case "notification":
        notificationStore.refreshUnread();
        if (notificationStore.open) notificationStore.list({ limit: 30 });
        break;
    }
  }

  function handle(noti: SyncNotificationType) {
    const { cl_id: clId, cli_id: cliId, upd_prop } = noti;

    // ── Local-first (WI-10): the poke is the single read trigger ─────────────
    // Flag-on, the board reconciles ONLY via `changes_available` → delta pull
    // (§9b). The frozen per-entity events are ignored for board state; the
    // side-channel stores (shares/invites/notifications) still react to theirs.
    if (isLocalFirstEnabled()) {
      if (upd_prop === "changes_available") {
        void applyDelta(pinia, { sinceSeq: noti.server_seq });
      } else {
        handleSideChannel(noti);
      }
      return;
    }

    if (COUNT_AFFECTING.has(upd_prop)) scheduleCountsRefresh();

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
