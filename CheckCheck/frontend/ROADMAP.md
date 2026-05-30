# Frontend Roadmap

Tracked gaps and planned improvements for the CheckCheck frontend (some of them may also need some backend work). Ordered roughly by priority within each section.

---

## Bugs & Broken Stubs

These are things that exist in the UI but don't work yet.

- **Search input** – Navbar has a search field but it is not wired to any filtering logic.
- **Textarea auto-height bug** – Single long rows in item textareas don't auto-size correctly (TODO comment in `CheckListItem.vue`).
- **Item textarea new-line/new-item behaviour improvements** - When pressing enter lets create a new item under the curren one. Only shift+enter should introduce a linebreak.
- **Label reorder not persisted** – Drag-and-drop in the label manager reorders labels visually but never calls the backend to save the new `sort_order`. Changes are lost on reload.
- **Tidy up Card Menu** - Remove mockup item in submenu (show downloads, show history), move delete to submenu, find better shorter name for "Seperate Checked Items"
- **Chrome shows scrollbars on cards item list** - we do not want that. should be same behaviour as in firefox
- **Large text items not expanded** - When opening a card on a fresh loaded webclient some larger text items are cutoff and have a scrollbar. when editing somehting closing the card and reopening it all is fine. 
---

## Sharing & Collaboration

Core missing feature — sharing is entirely absent. No UI, no flows, the Share button in the footer is a stub that does nothing.

- **Share a board** – Generate a shareable link for a checklist (read-only or edit access).
- **Access levels** – At minimum: view-only vs. full-edit. Ideally also a "check-off only" mode for guests who should only be able to tick items, not edit text.
- **Manage collaborators** – List who has access, change their permission level, revoke access.
- **Accept an invitation** – Flow for a recipient: open a share link → sign in or continue as guest → see the shared board.
- **Shared boards in the sidebar** – Distinguish boards you own from boards shared with you or board you share. Lets have a filter similar to the labels.
- **Real-time presence** – Show when another user is viewing or editing the same board (avatar indicators, live cursor or edit lock per item).
- **Activity / history** – Log who checked/unchecked or edited what and when (also ties into the "Show History" stub).

---

## Labels

- **Label positioning / reorder persistence** – Fix the drag-and-drop in `LabelManager/index.vue` to actually PATCH the new sort order to the backend.
- **Label editor UX** – The edit modal is functional but crude. Improvements:
  - Inline name editing with `Enter` to confirm and tab to the next label.
  - Better color swatch layout (currently a flat list, could be a small grid).
  - Confirmation before deleting a label that is in use.
  - Show how many checklists each label is applied to.
- **Label display on cards** – Labels are shown as colored pills but with no text visible on cards in board view; consider toggling label name visibility.
- **Pinned / favorite checklists** – The backend already supports a `pinned` field, but there is no UI to pin a checklist or filter by pinned status.

---

## Mobile

The app was primarily developed and tested on desktop. Known mobile gaps:

- **Board layout** – The auto-fill column grid collapses poorly on narrow screens; cards can be too wide or too narrow.
- **Drag-and-drop on touch** – Reordering checklists and items with touch events (FormKit DnD) needs end-to-end testing on real mobile devices.
- **Checklist edit modal** – The modal fills the screen on desktop but may be awkward on small phones; consider a full-screen sheet on mobile.
- **Label selector popover** – The popover that opens when assigning labels may overflow or be hard to dismiss on touch screens.
- **Footer toolbar buttons** – The icon buttons at the bottom of each card are small tap targets; need at least 44×44 px hit areas.
- **Sidebar drawer** – The mobile drawer (`SideMenuDrawer.vue`) exists but needs cross-device testing and swipe-to-close gesture support.
- **Font sizes and spacing** – Review Tailwind breakpoint usage; several elements use fixed `text-sm` / `text-xs` that may be too small on mobile.

---

## UX & Polish

- **Pinned checklists section** – Show pinned lists at the top of the board, separated from the rest.
- **Keyboard shortcuts** – No keyboard shortcuts are implemented or documented. At minimum: `n` to create a new list, `Esc` to close modals, `/` to focus search.
- **Undo last action** – Accidental deletes (items, checklists) are unrecoverable. A short-lived undo toast after destructive actions would help.
- **Checklist duplication / templates** – A "Duplicate" action in the more-options menu to clone a checklist with all its items.
- **Sort options** – Currently checklists can only be reordered manually. Add sort-by options: alphabetical, created date, last modified.
- **Bulk operations** – Multi-select checklists for bulk archive, bulk label assignment, or bulk delete.
- **Empty state** – The board shows nothing useful when there are no checklists; add an illustrated empty state with a "Create your first list" CTA.
- **Loading skeletons** – Initial page load and pagination show no skeleton placeholders; adding them avoids layout shift.
- **Inline checklist rename** – Currently the name must be edited inside the edit modal; allow double-clicking the title on a card to rename it in place.
- **Item count badge on label filter** – When filtering by label, show how many checklists match.

---

## Accessibility

- **ARIA labels** – Drag handles, icon-only buttons, and checkboxes lack `aria-label` attributes.
- **Focus management** – When a modal opens, focus should move into it and be trapped; when it closes, focus should return to the trigger.
- **Keyboard-accessible drag-and-drop** – FormKit DnD doesn't expose keyboard reordering; provide an alternative (e.g., up/down arrow buttons in the edit modal).
- **Color-only label differentiation** – Label pills on cards use only a color dot with no text; screen-reader users get no label name in board view.
- **Strikethrough for completed items** – Completed items use strikethrough styling only; add an icon or `aria-checked` state so the status is unambiguous.

---

## Longer-term / Nice to Have

These are out of scope for the near term but worth tracking.
- **Due dates on checklists** – Optional deadline field with visual overdue indicator.
- **Recurring checklists** – Schedule a checklist to reset on a cron-like interval.
- **Export** – Download a checklist as plain text, Markdown, or PDF.
- **Import** – Paste plain-text lines to bulk-create items; import from CSV.
- **Item sub-tasks / nesting** – One level of nesting under a checklist item.
- **Comments / notes per item** – Expandable notes field below an item's text.
- **File attachments** – Attach images or files to a checklist or item.
- **Push / email notifications** – Notify when someone checks off an item in a shared list.
- **PWA / installable** – `manifest.json` and service worker so the app can be pinned to a home screen and work partially offline.
