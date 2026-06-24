# UI Polish Plan

A review of the CheckCheck frontend (Nuxt 4 + Nuxt UI v4 + Tailwind v4) with concrete,
prioritized opportunities to make it more polished, more modern, and better on mobile and
desktop. Nothing here is implemented yet — this is the plan.

The work is broken into **7 phases**, each sized to land as one focused session / PR. Phases
are ordered so the cheap, high-signal fixes come first and the more open-ended design work
comes later. Phases are independent unless noted.

Stack reference (so fixes use the right primitives):
- Nuxt UI **v4** → semantic color aliases are `primary` / `secondary` / `success` /
  `info` / `warning` / `error` / `neutral`. The old v2 names `red` / `gray` are **not valid**.
- Tailwind v4 + Nuxt UI design tokens are available: `bg-default`, `bg-muted`,
  `bg-elevated`, `text-default`, `text-muted`, `text-dimmed`, `text-highlighted`,
  `border-default`, `ring-default`. Prefer these over hardcoded `gray-*` / `white`.

---

## Testing approach (read before any phase)

The only automated coverage is the **Playwright E2E suite** (`CheckCheck/frontend/tests/e2e/`,
~21 specs). There are **no unit/component tests** and **no visual-regression** tooling. The
suite is **`data-testid`-driven** and behavioral, runs against a static `nuxt generate` build
served by the E2E backend on port 8182, **sequentially**, sharing one DB. See
[tests/e2e/LLM_GUIDE.md](CheckCheck/frontend/tests/e2e/LLM_GUIDE.md) for conventions; run via
`./run_e2e_tests.sh` (or the per-file form in the guide).

What this means for UI polish:

- **Styling-only changes need no test changes.** Color/token swaps, spacing, fonts, radii,
  hover effects, icon swaps — none of it is (or should be) asserted by E2E. Don't write tests
  for these; they'd just be brittle. Verify visually instead (the `/run` or `/verify` skills,
  or manual screenshots).
- **The golden rule for keeping the suite green: when restructuring markup, preserve the
  existing `data-testid` attributes, `aria-label`s, and the key drag selectors.** Almost every
  "break" in this plan comes from moving/removing one of those, not from CSS. The load-bearing
  ones to never drop:
  - Board list containers: `checklist-board`, `pinned-board`, `pinned-section` — these sit
    **directly on the grid `<ul>`s** ([CheckListBoard.vue:6,14](CheckCheck/frontend/components/CheckListBoard.vue#L6)).
    Any layout rework must keep them on whatever becomes the list container, and keep the card
    `<li>` order equal to DOM order (the movement/pin tests assert order via the DOM).
  - Card/item drag is selected by **CSS class**, not testid: `list-drag-handle` (card) and
    `list-item-drag-handle` (item). Swapping the drag *icon* (Phase 1b) is fine; keep those
    classes on the same elements.
  - Modal close is reached via `aria-label="Close"` (no testid). Restyling it (Phase 4) must
    keep that label.
- **Add a new `data-testid` for every new interactive element** introduced by a phase (user
  menu, theme toggle, empty-state CTA, FAB) plus a focused spec — see per-phase "Tests:" notes.
- **After each phase, run the whole suite** (it's sequential + shares state, so partial runs
  can mislead). Treat a green suite as the regression gate; do the visual check separately.
- **This is the strongest argument against full masonry (Phase 8):** `card-movement.spec.ts` /
  `item-movement.spec.ts` assert order through DOM position, which a column-flow or
  absolute-positioned masonry layout breaks even when nothing is visually wrong.

---

## Phase 1 — Correctness bugs, icons & tokens (quick win)

Small, mostly mechanical edits. High signal: fixes visibly-broken UI and removes
inconsistency. Good single first PR.

### 1a. Correctness bugs that show as broken UI

- **`pages/login.vue` — invalid v4 colors.** `<UAlert color="red">` and
  `<UButton color="gray">` don't resolve in Nuxt UI v4. The error alert almost certainly
  renders with no/wrong theming. Use `color="error"` and `color="neutral"`.
  ([login.vue:11](CheckCheck/frontend/pages/login.vue#L11), [login.vue:45](CheckCheck/frontend/pages/login.vue#L45))
- **`pages/login.vue` — undefined ref in the catch.** The mount handler does
  `error.value = "Failed to load login options."` but the declared ref is `errorMessage`.
  This throws (so the error is silently swallowed) instead of showing the message.
  ([login.vue:82](CheckCheck/frontend/pages/login.vue#L82))
- **`pages/login.vue` — `<UInput label=...>` doesn't render a label.** `UInput` has no
  `label` prop; labels need a wrapping `UFormField`. Right now the username/password fields
  have no visible labels (only icons + placeholder behavior). Wrap each in `UFormField`.

### 1b. Icon set consistency

Three icon families are mixed: **lucide** (the de-facto standard across the app),
**heroicons**, and **mdi**. This reads as inconsistent (different stroke weights/metrics) and
bloats the bundle (three icon JSON packs installed).

- `i-heroicons-arrow-path` → `i-lucide-refresh-cw` ([CheckListBoard.vue:24](CheckCheck/frontend/components/CheckListBoard.vue#L24))
- `i-heroicons-exclamation-triangle` → `i-lucide-triangle-alert` ([login.vue:12](CheckCheck/frontend/pages/login.vue#L12))
- `i-heroicons-chevron-double-down/right` → `i-lucide-chevrons-down` / `i-lucide-chevrons-right`
  ([Seperated.vue:16-17](CheckCheck/frontend/components/CheckListItemCollection/Seperated.vue#L16))
- `i-mdi-drag` → `i-lucide-grip-vertical` ([CheckListItem.vue:10](CheckCheck/frontend/components/CheckListItem.vue#L10))

Then drop `@iconify-json/heroicons` and `@iconify-json/mdi` from `package.json` (and the
redundant `@iconify-icons/lucide` — `@iconify-json/lucide` is the one Nuxt Icon uses).
Standardize on lucide everywhere.

### 1c. Theme tokens vs. hardcoded colors

The codebase is split between semantic tokens (`bg-elevated`, `text-muted`, the `var(--ui-*)`
CSS vars) and raw Tailwind grays. The raw grays don't always track the design system and can
look slightly off in dark mode.

- **Navbar:** `bg-white/75 dark:bg-gray-900/75` → `bg-default/75` + `border-default`.
  ([Navbar.vue:2](CheckCheck/frontend/components/Navbar.vue#L2))
- **Login:** `bg-gray-50 dark:bg-gray-900`, `text-gray-500`, `border-gray-200 dark:border-gray-800`
  → `bg-muted`, `text-muted`, `border-default`. ([login.vue](CheckCheck/frontend/pages/login.vue))
- **Logo folded corner:** `bg-white/100 dark:bg-gray-900/100` → `bg-default`.
  ([Logo.vue:10](CheckCheck/frontend/components/Logo.vue#L10))
- Converge the two token conventions. The app mixes Tailwind utility tokens (`text-muted`)
  and CSS-var tokens (`text-[var(--ui-text-muted)]`, heavily in NotificationBell / ShareModal /
  public page). The utility form is shorter and the project norm — standardize on it.
- **`z-[99999]` on the navbar** is an arbitrary escape hatch
  ([Navbar.vue:2](CheckCheck/frontend/components/Navbar.vue#L2)). Define a small z-index scale
  (or use Nuxt UI's layering) so navbar / drawer / modals / popovers stack predictably.

**Tests:**
- **New:** the login bug fixes are the one place new tests clearly pay off — assert the
  `login-error` alert renders (and shows the detail message) on a failed basic login, and on a
  failed auth-methods fetch. Those paths were previously dead/throwing, so they're untested
  today. The `login-error`, `login-username`, `login-password` testids already exist.
- **Keep green:** `auth.spec.ts` — wrapping inputs in `UFormField` must keep the
  `login-username` / `login-password` testids on the actual `<input>`. Icon and token swaps
  (1b/1c) need no test changes; just re-run the suite to confirm nothing keyed off the old
  markup.

---

## Phase 2 — Navbar & mobile chrome

The navbar is the biggest mobile problem. It's a single `h-14` row holding: hamburger, a
**hero-sized logo**, a centered search-box + "New Check List" button, notification bell, a
**3-button** theme switcher, and a Logout button. On phones this overflows / crushes the
search box.

- **Logo is sized for a hero, not a bar.** `Logo.vue` hardcodes `text-5xl` (48px) wordmark +
  a `w-16 h-16` (64px) tile — taller than the 56px navbar.
  ([Logo.vue:6](CheckCheck/frontend/components/Logo.vue#L6), [Logo.vue:13](CheckCheck/frontend/components/Logo.vue#L13))
  Make the Logo size-responsive (size prop, or `text-lg sm:text-xl` + `size-8` tile in the
  bar); hide the wordmark on `xs` (icon-only), show full on `sm+`.
- **Collapse the theme switcher.** `ColorModeSwitch` renders three labeled emoji buttons
  ("🌞 Light / 🌙 Dark / 🖥️ System") — heavy on navbar space and the emoji read as unpolished.
  Replace with a single sun/moon toggle icon button, or a small `UDropdownMenu` with the three
  options. ([ColorModeSwitch.vue](CheckCheck/frontend/components/ColorModeSwitch.vue))
- **Logout → user/avatar menu.** A bare "Logout" text button is dated. Add a `UDropdownMenu`
  triggered by a `UAvatar` (initials from `userStore.fetchMe()`) containing Theme + Logout
  (+ future settings). Frees the bar and is the modern pattern.
  ([Navbar.vue:22](CheckCheck/frontend/components/Navbar.vue#L22))
- **Search on mobile.** The centered `CreateCheckListBox` (search + "New Check List") is
  cramped at `h-14`. Mobile layout: search collapses to a search-icon button that expands;
  "New Check List" becomes an icon-only `+` (or a FAB — see Phase 3). On `sm+` keep the full
  inline search + labeled button.
- **Safe-area insets.** Add `env(safe-area-inset-top)` padding so the sticky bar clears the
  iOS notch / status bar.

**Tests:**
- **At risk:** logout currently has no testid. Moving it into an avatar `UDropdownMenu` will
  break any test that clicks logout by text — add `data-testid="user-menu"` +
  `data-testid="logout-button"` and update `auth.spec.ts` to open the menu first. Likewise the
  search input feeds `filter-search.spec.ts`; if it collapses behind an icon on mobile, give
  the trigger and input stable testids (e.g. `search-input`, `search-toggle`) so the spec
  doesn't depend on placeholder text or layout.
- **New:** add testids to the new controls (`theme-toggle`, `new-card-button`/FAB). Consider a
  **one mobile-viewport spec** (`page.setViewportSize`) asserting the hamburger shows, the
  search collapses, and the FAB/`+` opens a new card — this is currently the biggest untested
  surface and the suite only runs Desktop Chrome today.
- **Keep green:** the existing suite runs at desktop width, so most navbar restructuring won't
  trip it as long as testids survive — but `filter-search.spec.ts` and `auth.spec.ts` are the
  two to watch.

---

## Phase 3 — The board (core experience)

- **Responsive grid tuning (committed here).** The grid uses
  `grid-cols-[repeat(auto-fill,minmax(15rem,1fr))]`. Keep the CSS-grid engine (it preserves
  DnD and DOM order) but tighten the min column width responsively (`minmax(13rem,1fr)` on
  mobile) for a clean 1–2 column phone layout, and tune the `gap`. This is the low-risk win.
  ([CheckListBoard.vue:14](CheckCheck/frontend/components/CheckListBoard.vue#L14))
  - **True Google-Keep masonry is deliberately NOT in this phase.** CSS grid aligns rows, so
    uneven cards leave vertical gaps — but every masonry option (CSS `columns-*`, JS masonry
    like Muuri, experimental `grid masonry`) conflicts with `@formkit/drag-and-drop` and the
    DOM-order assumption the reorder tests rely on. It's the highest-risk, most token-hungry
    item in the whole plan and is split out to **Phase 8 (spike-first, optional)**. Treat the
    row gaps as an accepted cosmetic tradeoff until/unless Phase 8 proves a safe approach.
- **Empty states are missing.** Zero cards (new user), zero search results, and empty "Shared
  with me" all render nothing. Add friendly empty states (icon + one line of copy + a primary
  action, e.g. "Create your first list"). Distinguish "no cards yet" from "no results for 'foo'".
- **Loading skeleton.** First load shows nothing until `fetchNextPage()` resolves. Add a
  skeleton grid (a few `USkeleton` cards) so the board doesn't pop in.
- **"Load more" button** is a leftover-looking ghost button even though paging is driven by
  the IntersectionObserver (`v-element-visibility`). Replace with a subtle centered spinner /
  `USkeleton` rows while auto-paging; keep an accessible fallback button only if the observer
  is unavailable. ([CheckListBoard.vue:18-26](CheckCheck/frontend/components/CheckListBoard.vue#L18))
- **Card hover affordance.** The `translateY(-2px)` lift is nice; pair it with the existing
  `shadow-sm → shadow-md` transition on the same timing curve and consider a faint hover ring
  for a more tactile feel. ([CheckListBoard.vue:168-173](CheckCheck/frontend/components/CheckListBoard.vue#L168))
- **Consider a FAB for "New list" on mobile** (bottom-right `+`) — the expected mobile pattern,
  and it takes pressure off the navbar (ties into Phase 2).

**Tests:**
- **Highest-risk phase for the suite.** `card-movement.spec.ts`, `pin.spec.ts`,
  `filter-search.spec.ts`, `sync.spec.ts`, `checklist.spec.ts` all rely on the `checklist-board`
  / `pinned-board` / `pinned-section` testids and on **card `<li>` order matching DOM order**.
  The *responsive grid tuning* keeps both, so it's safe; this is exactly why true masonry is
  split out to Phase 8 (it breaks the DOM-order assumption).
- **New:** empty states are testable behavior — add `data-testid="board-empty"` and
  `data-testid="board-empty-search"`, and a spec asserting (a) empty board shows the CTA, (b) a
  search with no matches shows the no-results variant, (c) both hide once cards/results exist.
- **Don't bother testing** the skeleton or the load-more spinner (transient/visual). Verify the
  IntersectionObserver paging still works by re-running whichever existing spec scrolls to
  paginate, rather than asserting the spinner itself.

---

## Phase 4 — Cards & checklist items (touch ergonomics)

- **Drag handle discoverability.** Item drag handles fade to `opacity: 0.3` until hover
  ([CheckListItem.vue:140](CheckCheck/frontend/components/CheckListItem.vue#L140)); on touch
  there's no hover, so handles stay faint. Show them at full opacity on touch
  (`@media (hover: none)`).
- **Checkbox tap targets.** Items are `py-0.5`; checkbox + text rows are tight for thumbs.
  Bump vertical padding on touch and ensure the row hit area ≥ 40px.
- **Color picker swatches** (`size-6`) are small for touch and the selected ring
  (`ring-offset`) can clip inside the popover. Bump to `size-7`/`size-8` on touch and verify
  the ring isn't clipped. ([ColorSwatchPicker.vue:28](CheckCheck/frontend/components/ColorSwatchPicker.vue#L28))
- **Footer button row** (`CheckListFooter`) crams Archive / Color / Share / Label / More into a
  `UButtonGroup`. Confirm it doesn't overflow on a narrow (1-column) card; if it does, let it
  wrap or move secondary actions into the More menu at small widths.
  ([CheckListFooter/index.vue](CheckCheck/frontend/components/CheckListFooter/index.vue))
- **Editor modal close button** floats over scrolling content
  ([CheckListEditModal.vue:12](CheckCheck/frontend/components/CheckListEditModal.vue#L12)).
  Give it a subtle backing (`bg-default/60 backdrop-blur rounded-full`) so it stays legible over
  colored cards and long text.
- **Placeholder hierarchy.** Verify the edit-mode title placeholder ("Enter a checklist title…")
  uses `text-dimmed` so it doesn't look like real content.

**Tests:**
- **Keep green (no new tests):** this phase is ergonomics/CSS, so no new assertions are
  warranted — but it touches markup the drag specs depend on. `item-movement.spec.ts` selects
  the item handle by the `list-item-drag-handle` class, and `card-editor.spec.ts` opens/closes
  the editor and toggles checkboxes (`public-item-checkbox` analogues + `card-title`). Padding,
  swatch-size, and the close-button restyle must keep those classes/testids and the
  `aria-label="Close"`. Re-run `item-movement`, `card-editor`, `pin`, and `shares` after.
- The footer-overflow change (wrap vs. move-to-More on narrow cards) only matters at mobile
  width; the desktop suite won't catch a regression there — verify manually.

---

## Phase 5 — Sidebar & public share page

Two smaller surfaces grouped into one session.

### 5a. Sidebar / navigation

- The desktop sidebar (`SideMenu` / `SideMenuNav`) is already responsive (collapse + tooltips).
  Refinements:
  - The active-item highlight (`bg-elevated`) is very subtle — add a left accent bar or
    `text-primary` on the active icon for a clearer "you are here".
  - When a label filter is active, pair the active row with the label's own color for a
    stronger, more modern cue.
- **Mobile drawer** reuses `SideMenuNav` (good). Add a header inside the drawer (logo + close)
  so it reads as a first-class panel; confirm it closes on backdrop tap (it already closes on
  route change — [SideMenuDrawer.vue:18](CheckCheck/frontend/components/SideMenuDrawer.vue#L18)).

### 5b. Public share page (`/p/[token]`)

This is the page strangers see — the product's shop window.

- Already has good loading/locked/gone/ready states. Modernize:
  - Center the card vertically with more breathing room; add the brand color to the header.
  - Make the read-only permission line ([\[token\].vue:107](CheckCheck/frontend/pages/p/[token].vue#L107))
    a small `UBadge` instead of plain dimmed text.
  - The hand-rolled "+ Add new item" button ([\[token\].vue:89-98](CheckCheck/frontend/pages/p/[token].vue#L89))
    should match the in-app `AddNewButton` (lucide plus icon, `text-muted hover:text-default`).
- Converge its many `var(--ui-*)` classes onto the utility tokens (Phase 1c) for consistency.

**Tests:**
- **Public page is heavily testid-covered** (`public-viewer.spec.ts`,
  `sharing-public-links.spec.ts`) — `public-card`, `public-permission`, `public-add-item`,
  `public-item-*`, etc. The restyle must keep them: turning the permission line into a `UBadge`
  keeps `public-permission`; aligning the add-item control keeps `public-add-item`. No new
  tests needed — re-run those two specs plus `sharing-modal.spec.ts`.
- **Sidebar** active-state and label-color changes are cosmetic; the `shared-filter-*` testids
  and label-link `:to` targets used by `filter-search.spec.ts` / `shares.spec.ts` stay. The
  drawer-header addition is mobile-only — verify manually.

---

## Phase 6 — Typography, spacing & motion (global feel)

- **Fonts.** `@nuxt/fonts` is installed but no font family is configured — the app uses the
  system stack. A single tasteful sans (e.g. Inter / Geist) via `app.config` or CSS would lift
  the whole UI a tier. Define it once and let it cascade.
- **Reduced motion.** Add a `@media (prefers-reduced-motion: reduce)` block in
  `assets/css/main.css` that disables the card hover-lift and FormKit drag animations for users
  who opt out.
- **Consistent radii.** Cards `rounded-xl`, modals `rounded-2xl`, sidebar items `rounded-lg`,
  swatches `rounded-full`. Mostly a sensible hierarchy — document it so new components follow it.
- **Focus-visible rings.** Nuxt UI components handle this; the hand-rolled ones don't. Add
  visible `focus-visible:` states to custom `<button>`/`<NuxtLink>` elements (NotificationBell
  rows, public add-item, sidebar links).

**Tests:**
- **Purely visual — no new specs.** Fonts, radii, focus rings: verify by eye, not by E2E.
- **A welcome side effect:** the `prefers-reduced-motion` block disabling FormKit drag
  animations can make the drag specs *more* stable (no animation timing races). If you want
  belt-and-suspenders, the E2E run could set `reducedMotion: 'reduce'` in the Playwright
  `use` config — a small, optional config change, not a new test.

---

## Phase 7 — Accessibility pass

- Hand-rolled interactive `<div>`s (e.g. `AddNewButton`, card click targets) should be real
  buttons or carry `role`/`tabindex`/keydown handlers.
- `aria-label`s exist on icon-only buttons (good). Extend to the hamburger, theme toggle, and
  the new user menu (Phase 2).
- Color-only cues: label dots and card colors convey meaning by color alone; ensure text labels
  accompany them (they do in the sidebar; verify on cards).
- Verify contrast of `text-muted`/`text-dimmed` on colored card backgrounds — muted footer text
  may fail AA on some swatches.

**Tests:**
- **New (high value, low effort):** converting hand-rolled `<div>` click targets to real
  `<button>`s changes how they're driven. `AddNewButton` has no testid today — add
  `data-testid="add-item"` while you're in there so it becomes scriptable, and assert a click
  adds an item. Keep `public-add-item` as-is (already a button).
- **Optional, its own decision:** an automated a11y check (`@axe-core/playwright`) on the board,
  card editor, and public page would catch contrast/role regressions cheaply — but it's a new
  dev dependency and can be noisy, so treat adopting it as a separate call, not a given.

---

## Phase 8 — Masonry board (optional, spike-first, NOT one session)

Split out from Phase 3 because it's the one genuinely hard, open-ended, token-hungry item.
**Do not start by writing the implementation — start with a throwaway spike.**

- **Spike goal:** prove that *some* masonry layout coexists with card reorder before committing.
  Build a throwaway branch with ~6 dummy cards and confirm you can still drag-reorder them and
  read back the new order. Only if that works does the real phase begin.
- **Why it's hard:** CSS grid aligns rows; every masonry alternative fights the current stack:
  - CSS `columns-*` — easy layout, but items flow top→bottom within a column, so visual order ≠
    DOM order; `@formkit/drag-and-drop` computes drop index from geometry assuming linear flow,
    and the pinned/normal split + reorder persistence assume DOM order = list order.
  - JS masonry (Muuri, etc.) — does its own absolute positioning + transforms, which collides
    with FormKit's transform manipulation. Realistically means **ripping out FormKit and
    rebuilding drag on the masonry lib's own drag** (cross-list drag, pinned groups, the SSE
    mid-drag guard, the reorder call) — multi-session, high-risk.
  - CSS Grid `masonry` (`grid-template-rows: masonry`) — not in Chrome stable as of early 2026.
- **Test impact (the decisive factor):** `card-movement.spec.ts` / `item-movement.spec.ts` /
  `pin.spec.ts` assert order through DOM position. A column-flow or absolute-positioned layout
  breaks them even when nothing looks wrong. Budget for **rewriting those specs' ordering
  assertions** (e.g. asserting visual/geometry order instead of DOM order), not just the
  component — this is a chunk of the work, not an afterthought.
- **Honest recommendation:** ship Phases 1–7 first. The row gaps are minor; a working,
  reorderable board is core. Only pick this up as a deliberate, time-boxed experiment with the
  explicit option to abandon after the spike.

---

### Dependencies between phases

- Phases 1–7 are largely independent and can ship in any order.
- Phase 2 (navbar) and Phase 3 (board FAB) overlap on the "New list" affordance — decide the
  pattern in whichever lands first.
- Phase 1c (token convention) is referenced by Phases 2, 5b — doing Phase 1 first avoids
  re-touching those files.
- Phase 8 (masonry) depends on nothing but should come **last**, is **optional**, and is the
  only phase not sized to a single session — gate it behind a successful spike.

### Test workflow (all phases)

1. Before a phase: note which specs touch the files you'll edit (the per-phase "Tests:" notes
   list them).
2. Preserve `data-testid`s, `aria-label`s, and the drag classes (`list-drag-handle`,
   `list-item-drag-handle`); add new testids for new interactive elements.
3. Write the new specs the phase calls for (Phases 1, 3, 7 are the ones that warrant new tests;
   2 warrants an optional mobile-viewport spec).
4. Run the **full** suite (`./run_e2e_tests.sh`) — it's sequential and shares a DB, so run it
   whole, not piecemeal.
5. Do the visual check separately (`/run` or `/verify`); E2E does not cover look-and-feel.
