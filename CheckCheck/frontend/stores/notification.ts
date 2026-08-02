import { defineStore } from "pinia";
import { assertOnline } from "@/utils/connectivity";

// In-app notification feed (backend Phase 9). Surfaces "card shared / invited /
// public link opened" events to the recipient via a navbar bell (see
// components/NotificationBell.vue).
//
// Mirrors the established store idiom (stores/share.ts / stores/publicConfig.ts):
// `const { $checkapi } = useNuxtApp()`, path/query/body, try/catch + console.error,
// and reconcile local arrays in place rather than blindly refetching (F7
// "optimistic vs refetch" note).
//
// `open` records whether the dropdown is currently shown, so useSync's
// `notification` SSE case can decide to re-`list()` (the visible feed) on top of
// the always-cheap `refreshUnread()`.

export type NotificationState = {
  // Unread badge count. Kept in sync optimistically on mark-read/mark-all-read
  // and authoritatively via refreshUnread() (SSE-driven).
  unreadCount: number;
  // The feed itself, newest-first as the backend returns it.
  items: NotificationReadType[];
  // Whether the dropdown is open (drives useSync's live re-list).
  open: boolean;
  // The effective preference matrix behind the settings dialog (E5). Null until
  // the dialog has opened once; never snapshotted to IndexedDB, since this is an
  // online-only surface that must always show what the server actually holds.
  settings: NotificationSettingsType | null;
};

export const useNotificationStore = defineStore("notification", {
  state: () =>
    ({
      unreadCount: 0,
      items: [],
      open: false,
      settings: null,
    } as NotificationState),
  actions: {
    async refreshUnread(): Promise<number> {
      const { $checkapi } = useNuxtApp();
      try {
        const res = await $checkapi("/api/user/me/notifications/unread-count", {
          method: "get",
        });
        this.unreadCount = res.unread_count;
      } catch (error) {
        console.error(
          "Could not fetch unread count 'GET /api/user/me/notifications/unread-count'",
          error
        );
      }
      return this.unreadCount;
    },

    async list(query: { unread_only?: boolean; limit?: number } = {}): Promise<NotificationReadType[]> {
      const { $checkapi } = useNuxtApp();
      try {
        this.items = await $checkapi("/api/user/me/notifications", {
          method: "get",
          query,
        });
      } catch (error) {
        console.error("Could not list notifications 'GET /api/user/me/notifications'", error);
      }
      return this.items;
    },

    // Mark one notification read. Reconcile locally (stamp read_at + decrement the
    // badge) rather than refetching — the SSE/refreshUnread will reconcile anyway.
    async markRead(id: string): Promise<void> {
      assertOnline("Notifications can't be updated offline.");
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/user/me/notifications/{notification_id}/read", {
          path: { notification_id: id },
          method: "post",
        });
      } catch (error) {
        console.error("Could not mark notification read 'POST .../" + id + "/read'", error);
        throw error;
      }
      const item = this.items.find((n) => n.id === id);
      // Only decrement if it was actually unread (idempotent re-marks shouldn't
      // drive the badge negative).
      if (item && !item.read_at) {
        item.read_at = new Date().toISOString();
        this.unreadCount = Math.max(0, this.unreadCount - 1);
      }
    },

    async markAllRead(): Promise<void> {
      assertOnline("Notifications can't be updated offline.");
      const { $checkapi } = useNuxtApp();
      try {
        await $checkapi("/api/user/me/notifications/read-all", { method: "post" });
      } catch (error) {
        console.error("Could not mark all read 'POST .../notifications/read-all'", error);
        throw error;
      }
      const now = new Date().toISOString();
      for (const n of this.items) if (!n.read_at) n.read_at = now;
      this.unreadCount = 0;
    },

    // Let useSync's `notification` case know whether the dropdown is open, so it
    // can re-list (the visible feed) in addition to refreshing the badge.
    setOpen(open: boolean) {
      this.open = open;
    },

    // ── Preferences (E5) ────────────────────────────────────────────────────
    //
    // Online-only, like every other notification mutation (WI-12): the matrix is
    // resolved server-side out of the user's choices *and* the instance
    // configuration, so a queued write would be replaying a decision taken
    // against a matrix nobody can see. `assertOnline` throws before any request
    // is made and nothing reaches the outbox; the dialog disables its controls
    // while offline, this is the backstop.
    //
    // These three pass `skipErrorToast` because the dialog owns their error
    // wording (a 409 on the test mail means something specific and useful).

    async fetchSettings(): Promise<NotificationSettingsType> {
      assertOnline("Notification settings can't be loaded offline.");
      const { $checkapi } = useNuxtApp();
      const res = await $checkapi("/api/user/me/notification-settings", {
        method: "get",
        skipErrorToast: true,
      });
      this.settings = res;
      return res;
    },

    // A partial patch: the body names only what changed, and the response is the
    // whole effective matrix afterwards, which is what we store. That makes the
    // dialog self-correcting: a rejected or capped entry comes back as the
    // server sees it rather than as the UI hoped.
    async saveSettings(update: NotificationSettingsUpdateType): Promise<NotificationSettingsType> {
      assertOnline("Notification settings can't be changed offline.");
      const { $checkapi } = useNuxtApp();
      const res = await $checkapi("/api/user/me/notification-settings", {
        method: "put",
        body: update,
        skipErrorToast: true,
      });
      this.settings = res;
      return res;
    },

    // 202 means queued, not delivered: the dispatcher sends it within its next
    // tick. 409 (no address / mail off) and 429 (one a minute) are the useful
    // failures and reach the caller as thrown FetchErrors.
    async sendTestEmail(): Promise<TestEmailResultType> {
      assertOnline("A test message needs a connection.");
      const { $checkapi } = useNuxtApp();
      return await $checkapi("/api/user/me/notification-settings/test-email", {
        method: "post",
        skipErrorToast: true,
      });
    },

    // The webhook twin (E6), with the same contract: 202 queued, 409 nowhere to
    // send it, 429 one a minute. A URL the server refuses to call (one resolving
    // into a private network) still gets a 202 here and fails in the queue: the
    // check happens at delivery time, since a host name's address can change
    // between saving it and using it.
    async sendTestWebhook(): Promise<TestWebhookResultType> {
      assertOnline("A test webhook needs a connection.");
      const { $checkapi } = useNuxtApp();
      return await $checkapi("/api/user/me/notification-settings/test-webhook", {
        method: "post",
        skipErrorToast: true,
      });
    },
  },
});
