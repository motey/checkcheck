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
};

export const useNotificationStore = defineStore("notification", {
  state: () =>
    ({
      unreadCount: 0,
      items: [],
      open: false,
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
  },
});
