import { defineStore } from "pinia";
import { assertOnline } from "@/utils/connectivity";
import { sortByRemindAt } from "@/utils/reminders";

// Date reminders (chunk R4). A reminder is personal: it belongs to the user who
// set it, only they are ever notified, and a card shared with five people can
// carry five independent reminders none of them can see (plan decision 1). So
// this store holds the *caller's* reminders per card and nothing else.
//
// Online-only, exactly like the notification settings (plan decision 6 / WI-12):
// reminders are per-user side data attached to a card but not part of the card
// entity, so they stay out of the delta feed and out of the offline outbox.
// `assertOnline` throws before any request is made and nothing is queued, which
// is the point: a queued reminder whose fire time passes before the queue drains
// is worse than useless. The panel disables its controls while offline; this is
// the backstop.
//
// Nothing here is ever written to the IndexedDB snapshot, for the same reason
// the preference matrix is not: the only honest thing to show is what the server
// currently holds. The in-memory state is dropped on an account switch with the
// board stores (`resetBoardStores` in utils/localSnapshot.ts, chunk A1): it is
// keyed by card but belongs to the user who set it.
//
// Every call passes `skipErrorToast`, because the panel owns the wording (a 409
// names which limit was hit, and only the server knows that).

export type ReminderState = {
  /** Reminders by card id, soonest first. A missing key means "not loaded". */
  byCard: Record<string, ReminderReadType[]>;
};

export const useReminderStore = defineStore("reminder", {
  state: () => ({ byCard: {} } as ReminderState),

  getters: {
    // A card nobody has opened yet has no entry; an empty array is the right
    // answer for a template either way.
    forCard: (state) => (clId: string): ReminderReadType[] => state.byCard[clId] ?? [],
    loadedFor: (state) => (clId: string): boolean => state.byCard[clId] !== undefined,
  },

  actions: {
    // The caller's pending reminders on one card. Finished ones are left out:
    // they are kept server-side for 30 days but there is nothing to do with one,
    // and the card editor is not a history view.
    async fetchForCard(clId: string): Promise<ReminderReadType[]> {
      assertOnline("Reminders can't be loaded offline.");
      const { $checkapi } = useNuxtApp();
      const rows = await $checkapi("/api/checklist/{checklist_id}/reminders", {
        path: { checklist_id: clId },
        method: "get",
        skipErrorToast: true,
      });
      this.byCard[clId] = sortByRemindAt(rows);
      return this.byCard[clId]!;
    },

    // The created row is merged in rather than triggering a refetch: the POST
    // response is the row the server stored, including the time zone it
    // snapshotted, which is more than a list call would tell us anyway.
    async create(clId: string, body: ReminderCreateType): Promise<ReminderReadType> {
      assertOnline("Reminders can't be set offline.");
      const { $checkapi } = useNuxtApp();
      const row = await $checkapi("/api/checklist/{checklist_id}/reminders", {
        path: { checklist_id: clId },
        method: "post",
        body,
        skipErrorToast: true,
      });
      this.byCard[clId] = sortByRemindAt([...(this.byCard[clId] ?? []), row]);
      return row;
    },

    // A real delete server-side (the table is not part of the sync feed, so no
    // offline client could resurrect the row), and a plain splice here.
    async remove(clId: string, reminderId: string): Promise<void> {
      assertOnline("Reminders can't be removed offline.");
      const { $checkapi } = useNuxtApp();
      await $checkapi("/api/reminder/{reminder_id}", {
        path: { reminder_id: reminderId },
        method: "delete",
        skipErrorToast: true,
      });
      const rows = this.byCard[clId];
      if (rows) this.byCard[clId] = rows.filter((row) => row.id !== reminderId);
    },
  },
});
