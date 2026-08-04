# Plan: settings as places, and the notification matrix

**Status:** not started (written 2026-08-04, after the notification branch review).

**Scope:** two maintainer findings about the notification settings dialog, plus
the same routing finding against the API keys dialog:

1. **The dialog is cluttered and repetitive.** Every notification type repeats
   the same channel titles and the same hint wording, and the per-channel setup
   blocks (time zone, test email, webhook URL, push devices) are interleaved
   below as four more bordered boxes. A user opening it sees eight boxes and has
   to read the same four words four times to find the one control they came for.
2. **A dialog is not a place.** The app's rule since the beginning is that every
   place has a URL, so it is shareable and the back button works. It is written
   down in [`composables/useAppRoute.ts`](../../CheckCheck/frontend/composables/useAppRoute.ts)'s
   own docstring and honoured by the card overlay, the label editor, the search
   text and the label filter. `NotificationSettingsModal` and `ApiKeysModal` both
   break it: they are `ref(false)` in
   [`components/Navbar.vue`](../../CheckCheck/frontend/components/Navbar.vue) and
   leave no trace in the URL.

Neither is a defect in the notification feature itself, so neither blocks the
branch. They are UI debt that is cheapest to pay while the feature is fresh.

And one goal that comes with them:

3. **The newer dialogs are missing from the screenshot gallery.**
   [`docs/screenshots.md`](../screenshots.md) walks a reader from the board
   through the card editor, the item menus and sharing. Nothing from this branch
   is in it: no notification settings, no reminders, no push devices, no "send
   this link by email". The redesign in S2 is the right moment to add them, since
   the shots have to be retaken either way.

---

## 0. Standing rules

- No em dashes or en dashes anywhere: docs, comments, commit messages, UI copy.
- Anything a user clicks gets vitest and/or Playwright coverage.
- **The maintainer commits, nobody else.** Leave the work dirty in the tree and
  report what changed.
- `CheckCheck/openapi.json` and `pdm.lock` are rewritten by a backend or E2E run.
  Check `git status` and `git checkout --` either if the diff is only version or
  relock churn.
- This plan is **frontend only**. No endpoint changes, no schema changes, no
  `openapi.json` regeneration, and no backend test run is required by any chunk.

---

## 1. Decisions settled before chunking

| # | Question | Decision |
|---|---|---|
| 1 | Real pages (`pages/settings/notifications.vue`) or route-reflected overlays on the board? | **Overlays, via aliases on `pages/index.vue`**, exactly like `/card/:cardId`. The board stays mounted (its `key: () => "board"` already guarantees that), so opening settings does not tear down the FormKit drag instances, re-run the local-first boot, reconnect SSE or re-fire the time zone sync. A real page would do all four on every visit and every return, for a dialog people open for ten seconds. The page docstring already describes this shape as the app's answer for "a URL-reflected overlay on top of a persistent board". |
| 2 | Path or query parameter? | **Path**: `/settings/notifications` and `/settings/api-keys`. The label editor uses `?editlabels=true` because it is a *mode* layered on the current view; settings are a destination and read better as one. Both live in `useAppRoute`, which is the only thing components may talk to. |
| 3 | Does the backend need anything? | **No.** `routes_webclient.py`'s catch-all already serves `index.html` for any unknown path, so a cold load of `/settings/notifications` works today, and the PWA's `navigateFallback: "/"` covers the offline case. Verified, not assumed. |
| 4 | Matrix on mobile? | **Two layouts from one data source.** `utils/notificationSettings.ts` already produces exactly the matrix (`typeRows()` returns rows of cells, `cellDisplay()` resolves each cell), so this is a template question only, no logic changes. A real grid from `sm` up, and the existing stacked per-type layout below it. A four column grid at 360 px is unreadable, and a horizontally scrolling settings table is worse than a stack. |
| 5 | Where do the hints go in the grid? | **Into the cell, as an icon with a `title`, keeping `data-testid="notification-hint-{type}-{channel}"` on the element carrying the text.** The lock hint, the inherit hint and `mode_restriction_reason` are what makes the current layout tall; they are also what the E2E asserts on. Keeping the testid on whatever element holds the wording keeps roughly the whole 693-line spec valid. |
| 6 | Does the matrix swallow the per-channel setup blocks? | **No, it collapses them.** The matrix answers "what do I get told about, and where"; the time zone, the test buttons, the webhook URL and the push device list answer "is this channel set up". Those belong under the matrix as one section per enabled channel, not interleaved between notification types. |
| 7 | Scope of the API keys change | **Routing only.** Its dialog is not cluttered; it breaks only rule 2. |
| 8 | What goes in the gallery | **The notification settings dialog, the reminder panel, and the public-link email field.** Those are the three surfaces from this branch a reader would look for. The API keys dialog stays out: it is an operator-shaped feature, and the gallery is a product walkthrough. |
| 9 | Full viewport or crop? | **Notification settings as a light and dark pair** (it is a screen, and the walkthrough's convention for a screen is a pair), **the other two as single cropped shots** (`shootElement` / `shootUnion`), like the existing item-menu and share-menu shots. |

---

## 2. Chunks

| Chunk | Area | Size |
|---|---|---|
| **S1** | Both dialogs become places | small |
| **S2** | The notification matrix | medium, design work |
| **S3** | Docs and the E2E sweep | small |
| **S4** | Screenshots of the newer dialogs | medium, mostly setup |

S1 before S2: S2 rewrites the template S1 has to be able to open, and doing them
in the other order means resolving the same E2E helpers twice. **S4 last**, and
only once S2 is visually settled: every shot it takes is a binary file in the
repo, and taking them of a layout that is still moving means committing the same
PNGs twice.

---

## 3. S1: both dialogs become places

**Goal:** `/settings/notifications` and `/settings/api-keys` are real URLs that
open their dialog on top of the board, survive a reload, and close with the back
button.

**Changes:**

- **`composables/useAppRoute.ts`.** Add the readers and the two mutators next to
  the card ones, following their conventions exactly: `push` on open (so Back
  closes the dialog), `replace` on close (so Back afterwards does not reopen it),
  and preserve the rest of the query in both directions. Something like
  `settingsPane` (`"notifications" | "api-keys" | null`), `openSettings(pane)`,
  `closeSettings()`. Update the docstring's list of URL-reflected state: it is
  the closest thing this app has to a written rule, so it has to name the new
  places.
- **`pages/index.vue`.** Add both paths to the existing `alias` array. The
  comment above `definePageMeta` explains why the key is stable; extend it rather
  than leaving the reader to work out why settings do not remount the board.
- **`components/Navbar.vue`.** The two menu items navigate instead of setting a
  ref, and the two modals bind `:open` to the route with `@update:open` closing
  through `useAppRoute`. Delete `apiKeysOpen` and `notificationSettingsOpen`.
  Keep the `sharingEnabled` gate on the notifications entry, and keep the
  deliberate "not disabled offline" behaviour with its comment.
- **Closing.** Both dialogs have their own close button and both are `UModal`s
  that close on Escape and on a backdrop click; every one of those paths has to
  end up in `closeSettings()`, not in a local ref. This is the part most likely
  to be missed: a dialog that closes visually while the URL still says
  `/settings/notifications` cannot be reopened without navigating away first.

**Tests:**

- **Playwright**, in `tests/e2e/notification-settings.spec.ts` and
  `tests/e2e/api-keys.spec.ts`: change `openSettings()` / `openApiKeysModal()` to
  assert the URL after the menu click (the helpers are the only place either
  spec opens its dialog, so this is two functions). Then one new spec each:
  a cold `page.goto("/settings/notifications")` opens the dialog on a loaded
  board, and Escape (or the close button) returns the URL to `/`.
- No unit test: `useAppRoute` is thin router glue and the repo does not test the
  existing readers either.

**Done when:** both dialogs are reachable by URL, the full Playwright suite is
green, and no component holds a boolean for either dialog.

---

## 4. S2: the notification matrix

**Goal:** the dialog opens on one compact grid instead of four boxes that repeat
themselves, and the channel setup lives below it rather than between the types.

**The shape:**

```
Notification settings
Choose what you are told about, and where.

                    In the app     Email          Push
                    The bell       Your address   This device
A card is shared    [As it hap.]   [Daily sum.]   [Off]
I am invited        [As it hap.]   [Default]      [Off]
A link is opened    [Off] (lock)   [Off] (lock)   [Off] (lock)
A reminder is due   [As it hap.]   [As it hap.]   [As it hap.]

> Email setup        time zone, send test email
> Webhook setup      URL, send test webhook
> Push setup         enable this device, device list, send test push
```

**Changes** (all in
[`components/NotificationSettingsModal.vue`](../../CheckCheck/frontend/components/NotificationSettingsModal.vue),
which is 618 lines and mostly template):

- **The grid, from `sm` up.** Column headers carry the channel title and its
  one-line description **once** (`channelWording()` already returns both, and is
  already imported). Row headers carry the type title; the type description
  moves to a `title` attribute or a small second line, because four descriptions
  stacked is a third of the current height. A CSS grid rather than a `<table>`:
  the column count is dynamic (`visibleChannels()` returns one to four depending
  on what the instance can deliver) and a grid handles that with one class
  binding.
- **The stacked layout, below `sm`.** Keep what exists today, driven by the same
  `rows` computed. Both layouts render the same `data-testid` values, so the
  suite does not care which one is on screen; Playwright runs at desktop width.
- **Hints in a cell.** A lock icon with the reason as its `title`, keeping
  `data-testid="notification-hint-{type}-{channel}"` on the element that holds
  the wording (decision 5). The inherit hint ("Following the server default
  (Off).") is the most repetitive line in the dialog: with a `Default (Off)`
  entry already shown *in* the select, consider dropping the separate line
  entirely for the inherit case and keeping it for the lock and the restriction.
  That is a wording change, so check `tests/unit/notificationSettings.spec.ts`'s
  `cellHint` cases and change them deliberately rather than to match.
- **Channel setup below the matrix**, one block per enabled channel, in the same
  order as the columns. Consider `UAccordion` (collapsed by default, except when
  a channel needs attention, for example push with no device yet): it turns the
  bottom half of the dialog into three lines. If that costs more than it saves,
  plain sections in a fixed order are already an improvement over today's
  interleaving.
- **Do not touch `utils/notificationSettings.ts`.** If a change looks necessary
  there, stop: the data layer already returns the matrix, and a change there
  means the layout is being described in the wrong place.

**Tests:**

- The existing E2E specs are the regression gate, and they should need **no**
  changes if the testids survive. Any spec that has to be edited is a signal that
  something moved that users can see; look at it rather than fixing the selector.
- `tests/unit/notificationSettings.spec.ts` for whatever `cellHint` wording is
  decided.
- One new Playwright assertion: with every channel enabled (the E2E instance has
  email, webhook and push all on), the grid shows each channel title **once**.
  That is the finding, expressed as a test.

**Done when:** the full Playwright suite and the vitest suite are green, and the
dialog fits the four types on one screen at 1280 px without scrolling to reach
the email setup.

---

## 5. S3: docs and the sweep

- **`docs/`**: the user-facing notification documentation names "the user menu"
  as the way in. Give it the URL as well.
- **The rule itself.** `useAppRoute.ts`'s docstring is where this app states that
  every place has a URL. Make it a rule rather than a description: a new
  full-screen surface belongs in that list, and a reviewer should be able to
  point at one sentence.
- **Sweep for other offenders.** `ShareModal`, the label editor (already a query
  parameter), the card editor (already a path) and any confirm dialog. A
  transient confirm is not a place; a surface a user can sit in is. Write down
  which ones were looked at and why they were left, so the next reviewer does not
  redo it.

---

## 6. S4: screenshots of the newer dialogs

**Goal:** a reader of [`docs/screenshots.md`](../screenshots.md) sees what this
branch built, and `./gen_screenshots.sh` reproduces it deterministically on
anybody's machine.

**Two prerequisites, both non-obvious. Read these before writing a spec.**

- **The screenshot instance has every notification channel switched off.**
  [`backend/screenshots/start_screenshot_server.py`](../../CheckCheck/backend/screenshots/start_screenshot_server.py)
  sets no notification environment at all, so `EMAIL_ENABLED`,
  `NOTIFY_WEBHOOK_ENABLED` and `NOTIFY_PUSH_ENABLED` are all false. Today a shot
  of the dialog would show a single `in_app` column and the line "This server
  does not send email", which is the opposite of what the picture is for. It
  needs the same treatment
  [`backend/e2e/start_e2e_server.py`](../../CheckCheck/backend/e2e/start_e2e_server.py)
  already has, and that file is the template to copy: email on with the `null`
  transport, webhooks on, push on with the throwaway VAPID pair, and
  `SHARING_PUBLIC_LINK_EMAIL_ENABLED` with one declared internal domain. Use
  `setdefault` for each, as that file does.
- **The time zone picker is not deterministic.**
  `playwright.screenshots.config.ts` pins the viewport and the colour scheme but
  not `timezoneId`, and since chunk T the board writes the *device's* zone into
  the settings on every boot
  ([`utils/timezoneSync.ts`](../../CheckCheck/frontend/utils/timezoneSync.ts)).
  So the picker would render whichever zone the machine taking the picture is
  in: the shot diffs for every maintainer, and it publishes the maintainer's
  location into public docs. Pin `timezoneId` in the config (`Europe/Berlin` is
  the obvious choice for this project) before taking any shot that includes the
  picker. Worth doing regardless of this chunk, since it makes every future shot
  reproducible.

**Changes:**

- **A new `tests/screenshots/desktop-settings.spec.ts`**, next to
  `desktop-menus.spec.ts` and built on the same `helpers.ts` (`openBoard`,
  `stabilize`, `shootElement`, `shootUnion`, `closeSse`). Note that S1 makes the
  dialog reachable by URL, so the spec navigates rather than driving the user
  menu, which is both shorter and less brittle than what `desktop-menus.spec.ts`
  had to do.
- **The push device list needs a device**, or the shot is an Enable button and an
  empty list. Reuse the `mockPush` `addInitScript` shim from
  `tests/e2e/notification-settings.spec.ts` rather than writing a second one:
  a fake `PushManager` with a stable endpoint and a stable `user_agent`, so
  `deviceLabel()` renders the same string every run.
- **The reminder shot needs a reminder** at a fixed time. Create it through the
  API in the spec, the way `desktop-menus.spec.ts` creates its throwaway card,
  and delete it in `afterEach`. Pick a date far enough out that
  `formatRemindAt()` renders the absolute form ("4 Aug 2027 at 09:00") rather
  than a relative one ("In 3 hours"), which would change with the clock.
  This is the trap that will otherwise produce a diff on every run.
- **`docs/screenshots.md`**: one new section per shot, in the walkthrough's
  voice (what the reader can do, not what the control is called), placed after
  "Sharing a list" since notifications and reminders are what happens *after*
  the sharing. Light and dark pair for the dialog, single images for the crops,
  matching the existing table markup exactly.
- **`gen_screenshots.sh`** needs no change: it already discovers spec files, and
  `--only settings` will filter to the new one.

**Tests:** none. These specs *are* the test, and their gate is human: run
`./gen_screenshots.sh --only settings`, then `git diff --stat docs/screenshots/`
and look at the images. The script's own header says as much.

**Done when:** the three shots are in `docs/screenshots/`, referenced from
`docs/screenshots.md`, a second run of the script produces a byte-identical
result (the determinism check that catches both prerequisites above), and
`CheckCheck/openapi.json` is unchanged afterwards (the script restores it, but
verify).

---

## 7. Why this was not done in the review session

Sizing, for the record. S1 is small and mechanical. S2 is a template rewrite
whose entire value is a visual judgement ("is this less cluttered"), which needs
a running app and screenshots, not a diff. Together they are a full session with
several five minute Playwright runs, and the review session that found them had
already spent its budget reading the branch. Splitting it here costs one document
and buys a cold session with the whole budget for the part that needs judgement.

---

## 8. Progress

| Chunk | Status | Notes |
|---|---|---|
| S1 both dialogs become places | not started | |
| S2 the notification matrix | not started | |
| S3 docs and the sweep | not started | |
| S4 screenshots | not started | after S2 is visually settled |

Update this table at the end of every session and record deviations below it, the
way the other plans in this directory do.
