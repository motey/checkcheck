import { inject, provide, type InjectionKey } from "vue";

// The seam between the card editor's kebab menu and its reminder panel (R4).
//
// The menu item lives in components/CheckListFooter/Button/MoreOptionsMenu.vue,
// which renders inside the open card *and* on the board preview's hover
// toolbar; the panel only exists in the open card, and is the menu's sibling
// rather than its ancestor. So components/CheckList.vue, the one component that
// contains both, provides this handle (only in edit mode) and forwards it to
// the panel: on a board preview there is nothing to inject and the item is
// simply not offered, which is exactly right, since there is nowhere for the
// form to open.
//
// A provide/inject handle rather than module-level state on purpose: two cards
// can be mounted at once (a board preview behind an open editor), and a shared
// module ref would make "open the reminder form" ambiguous between them.

export type ReminderPanelHandle = {
  /** Open the inline "new reminder" form in this card's editor. */
  open: () => void;
};

export const ReminderPanelKey: InjectionKey<ReminderPanelHandle> = Symbol("reminderPanel");

export function provideReminderPanel(handle: ReminderPanelHandle): void {
  provide(ReminderPanelKey, handle);
}

/** The open card's panel, or null when there is none (board preview). */
export function useReminderPanel(): ReminderPanelHandle | null {
  return inject(ReminderPanelKey, null);
}
