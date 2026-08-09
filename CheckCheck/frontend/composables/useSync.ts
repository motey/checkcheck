import type { Pinia } from "pinia";
import { createSharedComposable, useDebounceFn } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useShareStore } from "@/stores/share";
import { useNotificationStore } from "@/stores/notification";
import { useInviteStore } from "@/stores/invite";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { setConnectivity, probe, onConnectivityChange } from "@/utils/connectivity";
import { applyDelta } from "@/utils/localSnapshot";
import { setStreamLive } from "@/utils/syncStatus";

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

  // Self-managed reconnect on a capped backoff. The browser's own EventSource
  // retry only covers network-level drops and clean stream ends; when the stream
  // fails on an HTTP error status — exactly what a down backend behind Traefik
  // returns (502/503) — the spec requires the browser to fail *permanently*:
  // `onerror` fires once, `readyState` goes to CLOSED, and it never retries. Even
  // in the cases where the browser *does* retry, that retry can stall. Without an
  // explicit reconnect the client stays stuck "Offline" after a server-only
  // outage until a reload (see docs/ISSUES.md). So on any `onerror` we schedule a
  // rebuild ourselves; a successful `onopen` (ours or the browser's own retry)
  // cancels the pending timer, and the stream's `ready` message restores the
  // `setConnectivity(true)` path.
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

  /**
   * Tear the current stream down and build a fresh one.
   *
   * `connect()` no-ops if a stream already exists, so `disconnect()` first
   * guarantees a new EventSource. `hasOpened` is preserved across the rebuild:
   * we *had* a live connection before the gap, so the next `ready` must run the
   * reconcile delta-pull (catch up on everything that changed while we were
   * dark), not treat this as a fresh initial load.
   */
  function rebuildStream(reason: string) {
    console.info(`[sync] ${reason}: rebuilding SSE stream`);
    const wasOpened = hasOpened;
    disconnect();
    connect();
    hasOpened = wasOpened;
  }

  function scheduleReconnect() {
    if (reconnectTimer !== null) return; // already pending
    const delay = reconnectDelay;
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    console.warn(`[sync] SSE error — reconnecting in ${delay}ms`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      rebuildStream("reconnect timer fired");
    }, delay);
  }

  // ── Recovery that does not depend on `onerror` (bug B3) ─────────────────────
  //
  // Everything above hangs off the stream reporting its own failure. A stream can
  // also just go *quiet*: a sleeping laptop, a dropped Wi-Fi link, a silently
  // expired NAT mapping, and `context.setOffline` in the E2E suite, all kill the
  // connection without ever firing `onerror`. No error means no `scheduleReconnect`,
  // no reconnect means no `ready`, and since pokes only arrive over SSE the tab
  // then sits permanently stale with nothing scheduled to fix it.
  //
  // So we also watch the connectivity signal directly and force a rebuild when it
  // comes back up while the stream is not live.
  //
  // `streamLive` is the guard that keeps this from chasing its own tail: the
  // `ready` handler itself calls `setConnectivity(true)`, which re-enters this
  // listener. It marks the stream live BEFORE that call, so this sees a live
  // stream and does nothing.
  let streamLive = false;
  let stopConnectivityWatch: (() => void) | null = null;
  // True while a stream is wanted (between connect() and an explicit disconnect()).
  // Keeps a connectivity flip from resurrecting a stream we deliberately closed
  // (logout, unmount).
  let wantStream = false;

  function markStreamLive(live: boolean) {
    streamLive = live;
    setStreamLive(live);
  }

  // Subscribed once for the composable's lifetime rather than per connect: the
  // listener rebuilds the stream, and adding/removing listeners from inside a
  // listener would re-enter the notification loop.
  function watchConnectivity() {
    if (stopConnectivityWatch) return;
    stopConnectivityWatch = onConnectivityChange((online) => {
      if (!online) {
        // Whatever the stream believes, it cannot be delivering pokes now.
        markStreamLive(false);
        return;
      }
      if (!wantStream || streamLive) return;
      clearReconnect();
      reconnectDelay = RECONNECT_MIN_MS;
      rebuildStream("connectivity restored");
    });
  }

  // ── Delta-pull retry ────────────────────────────────────────────────────────
  //
  // A recovery pull that never reaches the server leaves the board stale with
  // nothing scheduled (`applyDelta` is best-effort and leaves the cursor
  // untouched). It reports whether it got through, so retry on the same capped
  // backoff the reconnect uses until one lands.
  let deltaRetryDelay = RECONNECT_MIN_MS;
  let deltaRetryTimer: ReturnType<typeof setTimeout> | null = null;

  function clearDeltaRetry() {
    if (deltaRetryTimer !== null) {
      clearTimeout(deltaRetryTimer);
      deltaRetryTimer = null;
    }
  }

  async function pullDeltaWithRetry(): Promise<void> {
    clearDeltaRetry();
    const reached = await applyDelta(pinia);
    if (reached) {
      deltaRetryDelay = RECONNECT_MIN_MS;
      return;
    }
    const delay = deltaRetryDelay;
    deltaRetryDelay = Math.min(deltaRetryDelay * 2, RECONNECT_MAX_MS);
    console.warn(`[sync] delta pull did not reach the server, retrying in ${delay}ms`);
    deltaRetryTimer = setTimeout(() => {
      deltaRetryTimer = null;
      void pullDeltaWithRetry();
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
      if (await probe()) void pullDeltaWithRetry();
      return;
    }
    checkListStore.resync();
    checkListStore.fetchCounts();
  }

  function connect() {
    wantStream = true;
    watchConnectivity();
    if (es) return;
    hasOpened = false;
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisible);
    }
    es = new EventSource("/api/sync");
    es.onopen = () => {
      // Response headers arrived. That proves the request was answered, NOT that
      // the server has subscribed us to the fan-out (it appends us to its client
      // list only once it starts iterating the stream body). Pokes are never
      // redelivered, so acting on `onopen` leaves a window in which a change
      // elsewhere is lost for good (bug B4). Everything that depends on "pokes
      // will reach me" therefore waits for the server's `ready` message below;
      // here we only reset the reconnect backoff, which is purely about the
      // request having succeeded.
      clearReconnect();
      reconnectDelay = RECONNECT_MIN_MS;
    };
    // The server's readiness handshake: emitted as the first message of the
    // stream, immediately after it has added us to its subscriber set.
    es.addEventListener("ready", () => {
      // A live sync socket proves real server reachability — feed the outbox's
      // connectivity signal (WI-7) so a reconnect resumes draining queued writes.
      // Harmless flag-off (no outbox listens); gated to avoid confusing the
      // legacy path. Mark the stream live FIRST: `setConnectivity` re-enters the
      // connectivity watcher, which must see a live stream and stand down.
      markStreamLive(true);
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
        void pullDeltaWithRetry();
        return;
      }
      console.info("[sync] SSE reconnected — resyncing store");
      checkListStore.resync();
      checkListStore.fetchCounts();
    });
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
      markStreamLive(false);
      if (isLocalFirstEnabled()) setConnectivity(false);
      // Always self-manage the reconnect rather than trusting the browser's own
      // retry. `onerror` fires in two situations and we can't reliably tell them
      // apart across browsers:
      //   • readyState === CONNECTING → the browser *intends* to auto-retry (a
      //     network-level drop / clean stream end), but that retry can itself get
      //     stuck.
      //   • readyState === CLOSED    → a *permanent* failure — an HTTP error close
      //     (a 502/503 from a bounced backend behind Traefik) — which the browser
      //     will NEVER retry. This is the reported "stuck Offline" case.
      // So we schedule our own capped-backoff reconnect on every error. If the
      // browser's native retry beats us to it, its `onopen` calls `clearReconnect`
      // and cancels our pending timer — cooperative, no double-connect.
      scheduleReconnect();
    };
  }

  function disconnect() {
    wantStream = false;
    clearReconnect();
    clearDeltaRetry();
    markStreamLive(false);
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
        if (!cliId) {
          // Bulk "untick all" (cli_id=null): a single item refresh can't cover it,
          // so refetch the whole card's items (debounced, and only if loaded).
          scheduleItemRefresh(clId);
        } else if (checkListItemStore.checkListsItems[clId]) {
          // Only refresh state if we already have this checklist's items loaded.
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
        if (!cliId) {
          // Bulk "delete ticked" (cli_id=null): refetch the whole card's items
          // (debounced) rather than splicing a single known id.
          scheduleItemRefresh(clId);
        } else {
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
