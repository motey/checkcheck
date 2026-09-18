# Plan: mobile card editor (closable header, keyboard-aware layout, visible suggestions)

**Status:** done (2026-09-18). M1, M2, M3 and M4 landed. The on-device
checklist in section 5 is with the maintainer; the flip-up fallback in
decision 5 is only built if that check finds a case scrolling does not cover.

**Deviations so far:**

- M1: `keyboardOpen` does not compare against `window.innerHeight` alone. With
  `interactive-widget=resizes-content` Android shrinks the layout viewport together
  with the visual one, so that check would never fire there. It compares against
  the largest height seen at the current window width instead (reset on a width
  change, i.e. rotation). Threshold stays 150px, exported as `KEYBOARD_THRESHOLD_PX`.
- M1: `useVisualViewport(active)` takes an optional getter. The editor modal stays
  mounted after closing (`pages/index.vue` keeps the last card id), so it passes
  `() => open.value` and only holds the listeners while the editor is open.
- M2: `CheckList.vue` got both shapes the plan offered, combined: a
  `fullscreen` prop switches to the header bar, and a `#header-start` slot lets
  the modal put its own back arrow in it. Pin and sync state are rendered by
  `CheckList.vue` inside the bar. `CheckListSyncIndicator` gained an `inline`
  prop (no corner float) for that.
- M2: the title cap uses `UTextarea`'s `maxrows="4"` instead of `max-h` plus
  `overflow-y-auto`. Autoresize sets an inline height, so a CSS `max-h` would
  fight it; `maxrows` caps the rows and leaves the textarea scrollable.
- M2: the safe-area paddings are `max(<normal padding>, env(safe-area-inset-*))`.
  The viewport meta has no `viewport-fit=cover`, so the insets are 0 today and
  the browser keeps the page out of the notch itself. The paddings only start
  to matter if `viewport-fit=cover` is ever added. It was not added here
  because it would affect every page, not just the editor.
- M2: on desktop the rendered styles are unchanged, but the card root's
  `border border-default rounded-xl` moved from the static `class` to the
  bound one, so the class attribute order differs.
- M3: the reveal is not `scrollIntoView({ block: "nearest" })`. With five
  suggestions and a small visible area, "nearest" aligns the bottom of the
  row-plus-list block and can push the row being typed out of the top. A small
  helper, `utils/revealRow.ts`, scrolls the nearest scrollable ancestor by
  just enough to show the block plus its `scroll-margin-bottom`, capped so the
  row's top edge stays visible. It measures against the scroll region clipped
  to `window.visualViewport`, so the keyboard counts as covered even where the
  layout viewport does not shrink (iOS Safari).
- M3: `scroll-margin-bottom` is Tailwind's `scroll-mb-16` (4rem) on the row
  wrapper in `CardParts/ItemRow.vue`. The helper reads it back from the
  computed style, so there is one number.
- M3: `CheckListItem.vue` holds `useVisualViewport` only while its own row is
  focused in the open card, and re-reveals on every `height` change then.
  Reduced motion gives `behavior: "auto"` (instant) instead of `"smooth"`.
- M4 folded into the M3 session. The changelog entry lives under 2.0.0,
  *Changed*, since that release is still unreleased.

Two pieces of user feedback, same root cause:

> Liste manchmal schlecht schliessbar auf mobile. Maybe schliessbutton und titel
> sticky machen?

> Manchmal item suggestions auf mobile nicht sichtbar weil unter keyboard.
> (Vielleicht, Focus item immer etwas hochrücken? oder suggestions in dem fall
> von oben kommen lassen?)

Both say "manchmal", and both happen while typing. The open card editor sizes
itself against the layout viewport and does not know where the on-screen
keyboard is. This plan makes the editor keyboard-aware first, then builds the
two fixes on top of that.

---

## 0. Standing rules

- No em dashes or en dashes anywhere: docs, comments, commit messages, UI copy.
- Anything a user clicks gets vitest and/or Playwright coverage.
- **The maintainer commits, nobody else.** Leave the work dirty in the tree and
  report what changed.
- Frontend only. No backend change, no schema change, no OpenAPI change, nothing
  in the outbox, sync protocol or `localFirst` path.
- **Desktop is not touched.** Every visual change is scoped below the `sm`
  breakpoint (640px) or to "keyboard open". The desktop modal must look
  exactly as it does today; an E2E in the `chromium` project guards that.
- Keep `aria-label="Close"` on whatever the close control becomes, so the
  existing `getByRole("button", { name: "Close" })` selectors in
  `card-editor.spec.ts` and `checked-items-count.spec.ts` keep working.

---

## 1. What the code does today

- [CheckListEditModal.vue](../../CheckCheck/frontend/components/CheckListEditModal.vue)
  renders a `UModal`, `max-w-2xl w-[calc(100vw-1rem)] max-h-[92dvh]`, with an
  absolutely positioned `size="sm"` X at `top-2 right-2`.
- [CheckList.vue](../../CheckCheck/frontend/components/CheckList.vue) in edit
  mode is a flex column: title textarea (`flex-none`, autoresize, no height cap),
  one scroll region (notes, items, reminders), footer (`flex-none`). The pin
  button sits at `right-10`, directly left of the X.
- So the title and the X are **already sticky** relative to the list. Scrolling
  a long list does not hide them. The feedback's proposed fix alone would change
  nothing.
- Opening a card is a `router.push` to `/card/:id`, so the Android back gesture
  already closes the editor. Keep that.
- Item suggestions (the "Uncheck ..." list) render inline under the focused row
  through the `#below` slot of
  [CardParts/ItemRow.vue](../../CheckCheck/frontend/components/CardParts/ItemRow.vue),
  filled by [CheckListItem.vue](../../CheckCheck/frontend/components/CheckListItem.vue).
  Nothing scrolls them into view when they appear.
- The viewport meta is Nuxt's default (`width=device-width, initial-scale=1`),
  no `interactive-widget`.

## 2. Why it breaks on phones

1. **Keyboard.** `dvh` does not shrink when the keyboard opens. Chrome on Android
   (since 108, by default) and iOS Safari only resize the *visual* viewport and
   pan the page to keep the caret visible. The top of the modal (title, X, pin)
   gets panned off screen, and anything below the caret (the suggestion list)
   ends up behind the keyboard. This is the "manchmal": it only happens with the
   keyboard open.
2. **Touch targets.** The X is about 28px and sits right next to the pin, so taps
   miss or hit the pin instead.
3. **No outside area.** At `100vw - 1rem` wide and 92% high, there is almost
   nothing to tap outside the modal to close it.
4. **Unbounded title.** A long title grows the `flex-none` header and squeezes
   the scroll region to nothing.

## 3. Decisions

1. **Below `sm`, the editor is full screen with a header bar**: back arrow
   (`i-lucide-arrow-left`, `aria-label="Close"`) on the left, pin and sync
   indicator on the right, title underneath. This is the phone convention (Keep
   does the same) and makes the "sticky close + title" of the feedback real by
   construction. At `sm` and above the current centered modal stays.
2. **Touch targets at least 44px** for close and pin in the mobile header, with
   a visible gap between them.
3. **The editor follows the visual viewport, not `dvh`.** Two layers:
   - `interactive-widget=resizes-content` in the viewport meta. Chrome and
     Firefox on Android then shrink the layout viewport when the keyboard opens,
     so plain CSS heights already do the right thing.
   - A `useVisualViewport` composable for iOS Safari (which ignores
     `interactive-widget`): listens to `window.visualViewport` `resize` and
     `scroll`, writes `--vv-height` and `--vv-top` on `<html>`. The full-screen
     editor uses `top: var(--vv-top)` and `height: var(--vv-height)`, falling
     back to `0` and `100dvh` when the API is missing.
4. **Title height capped** in edit mode (about 4 lines, then it scrolls inside
   itself) on every breakpoint. On desktop this is invisible unless a title is
   already absurd.
5. **Suggestions: scroll into view first, no flip-up for now.** Once the scroll
   region ends at the keyboard (decision 3), the focused row plus its suggestion
   list can always be scrolled into the visible part, because the region still
   has content below. So when the list appears or changes length, call
   `scrollIntoView({ block: "nearest" })` on the list (row plus list as one unit,
   so the row itself never scrolls out of the top). In addition, the focused
   row gets a `scroll-margin-bottom` so it never sits flush against the
   keyboard edge. Flipping the list above the row ("von oben kommen lassen")
   is only a fallback, built only if the on-device check after M3 shows a case
   where scrolling is not enough. It would mean a second layout for the same
   list and a position calculation, so it has to earn its place.

---

## 4. Chunks

Each chunk is one session and leaves the app shippable.

### M1: keyboard-aware viewport foundation

- Add `interactive-widget=resizes-content` to the viewport meta via
  `app.head.viewport` in `nuxt.config.ts` (keep `width=device-width,
  initial-scale=1`).
- New `composables/useVisualViewport.ts`: one global listener set (started once,
  ref-counted or started from the editor), rAF-throttled, writes `--vv-height`
  and `--vv-top` on `document.documentElement`, exposes
  `{ height, offsetTop, keyboardOpen }`. `keyboardOpen` is a heuristic:
  visual height at least 150px below `window.innerHeight`. No-op when
  `window.visualViewport` is undefined.
- Not wired into any layout yet, except starting it from `CheckListEditModal`.
- **Tests:** vitest for the composable with a faked `visualViewport` (resize
  and scroll events update the vars, missing API is a no-op, listeners are
  removed on the last unmount).

### M2: full-screen mobile editor with header bar

- `CheckListEditModal.vue`: `useMediaQuery("(max-width: 639px)")` switches the
  `UModal` to full screen (`fullscreen` prop or `ui.content` classes), positioned
  with the M1 variables. Desktop keeps the current classes unchanged.
- Mobile header bar in the editor: back arrow left (44px, `aria-label="Close"`),
  pin and `CheckListSyncIndicator` right. On mobile the absolute X and the
  absolute pin in `CheckList.vue` are not rendered; on desktop they stay as they
  are. Best shape: `CheckList.vue` gets a `#header-actions` slot or a
  `compactHeader` prop so the modal owns the header layout instead of more
  `right-10` offsets.
- Title cap (decision 4): `max-h` plus `overflow-y-auto` on the title textarea
  in edit mode.
- Safe areas: header respects `env(safe-area-inset-top)`, footer
  `env(safe-area-inset-bottom)` (installed PWA on notched phones).
- **Tests:**
  - New `tests/e2e/touch-editor.spec.ts` (runs in the `mobile` project, Pixel 7):
    open a card, the Close control is visible, at least 44x44, not overlapping
    the pin; tap it closes and returns to `/`; browser back also closes.
  - Same spec: fill a card with 40 items, scroll to the bottom, Close and title
    still `toBeInViewport()`.
  - Keyboard simulation: `page.setViewportSize` to half height after focusing
    an item (this is exactly what `resizes-content` does on Android). Close and
    title stay in viewport.
  - `chromium` project: the existing card-editor specs pass unchanged, plus one
    assertion that the desktop dialog is not full width.
- Check `docs/screenshots.md` / `gen_screenshots.sh` for a mobile editor shot
  and regenerate it if one exists.

### M3: item suggestions stay visible above the keyboard

- `CheckListItem.vue`: watch `suggestions.length`; when it goes from 0 to >0 or
  changes, `nextTick` then `scrollIntoView({ block: "nearest" })` on the row
  wrapper (row plus list). Also re-run on `visualViewport` resize while the row
  is focused (the keyboard often finishes animating after focus).
- `CardParts/ItemRow.vue`: `scroll-margin-bottom` (about 4rem) on the row
  wrapper, so a focused row keeps some air above the keyboard. Also applies to
  the public viewer, which is fine and wanted.
- Respect `prefers-reduced-motion` (instant, not smooth scroll).
- **Tests:**
  - `touch-editor.spec.ts`: small viewport (simulated keyboard), card with
    enough items that the last one is at the bottom edge, a checked item
    "Milk"; focus the last item, type "Mi"; the `uncheck-suggestion` button is
    `toBeInViewport()` and tapping it unchecks "Milk".
  - Negative: the suggestion list never pushes the focused row out of the top.
- **On-device check** (maintainer, see section 5). If a case remains where the
  list is hidden, decide on the flip-up fallback then, as its own small chunk.

### M4: release notes and cleanup

- `CHANGELOG.md` entry under the next version (user-facing wording: "card editor
  on phones: full screen with a back button, stays usable while typing, item
  suggestions no longer hide behind the keyboard").
- Remove the plan's "planned" status, record any deviations at the top as the
  other plans do.
- Small enough to fold into M3 if that session has room.

---

## 5. On-device checklist (maintainer, after M2 and again after M3)

Playwright cannot open a real software keyboard, so the keyboard behaviour is
only simulated by viewport resizing. These checks need a real phone:

- [ ] Android Chrome, in the browser and as an installed PWA: open a long card,
      focus an item near the bottom, keyboard opens, header (back arrow, title)
      stays visible, back arrow closes.
- [ ] iOS Safari, in the browser and from the home screen: same as above. This is
      the path that depends on `useVisualViewport`, not on `interactive-widget`.
- [ ] Both: type the start of a checked item's text into the last item, the
      suggestion is visible above the keyboard and tapping it works.
- [ ] Both: long title (5+ lines) stays capped and the list is still usable.
- [ ] Both: rotate to landscape with the keyboard open, nothing is unreachable.
- [ ] Android: system back gesture closes the editor and does not leave the app.
- [ ] Desktop: editor looks identical to before.

## 6. Out of scope

- Swipe-down-to-close gesture on the mobile editor. Nice, but conflicts with
  scrolling the list and with drag-and-drop long-press; revisit only if asked.
- Changing how suggestions are matched or which items are suggested.
- The other modals (share, labels, settings). If M1 and M2 work well they can
  reuse the same full-screen pattern later.
