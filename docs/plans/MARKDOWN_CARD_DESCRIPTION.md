# Markdown support for the card description (`text`) field

**Status:** 📋 Planned. Implementation happens in a separate session. Read this
document top to bottom before writing any code; it captures the research so you
do not have to re-derive it.

**Feature:** Render the card description as Markdown. The card description is the
`text` field on `CheckList` (labelled "notes" in the UI, "A text that will be
shown at the header" in the model). Users type Markdown into the notes field and
see it rendered as formatted text on the board, in the open card when they are
not actively editing, on the public share page, and for view-only
collaborators.

**Two settled UX decisions (from the maintainer):**

1. **Focus-swap editing.** Inside an open card the notes region shows *rendered*
   Markdown when it is not focused. Clicking or tabbing into it swaps to the raw
   `UTextarea` so the user edits the Markdown source. Blur swaps back to the
   rendered view. No Write/Preview buttons. This reuses the focus lifecycle that
   already exists on this field.
2. **Hint with a help popup.** Below the notes field, a small unobtrusive
   "Markdown supported" hint. The hint is a link that opens a small popup (help
   panel) describing what Markdown is and the basic formatting rules the app
   supports. No formatting toolbar in v1.

**What this feature deliberately does NOT change:** the backend, the database,
the sync protocol, the outbox, or the offline write path. `text` is already a
free-text column edited through the existing debounced
`checkListsStore.update(...)`. We are changing *rendering and edit-surface
presentation only*. This is the single most important scoping fact: it keeps the
feature small and low-risk.

---

## 1. How the description field works today (orientation)

The card and its editor are the **same component**,
[`CheckList.vue`](../../CheckCheck/frontend/components/CheckList.vue), toggled by
the `editModeActive` prop. There are three surfaces where `text` appears:

### 1.1 Board preview (`editModeActive = false`)

[`CheckList.vue:38`](../../CheckCheck/frontend/components/CheckList.vue#L38):

```vue
<p v-if="!editModeActive && checkList!.text"
   class="flex-none line-clamp-2 sm:line-clamp-3 text-sm opacity-80 whitespace-pre-wrap break-words"
   v-html="highlightText(checkList!.text, searchQuery)" />
```

Read-only. Plain text, line-clamped to 2 or 3 lines, with `whitespace-pre-wrap`
so newlines survive. It already uses `v-html`, but only to inject the search
`<mark>` highlight via
[`utils/highlight.ts`](../../CheckCheck/frontend/utils/highlight.ts) (which HTML
escapes first, then wraps the needle). So the board is **already an `v-html`
sink**; whatever we render there must be sanitized.

### 1.2 Open card / editor (`editModeActive = true`)

[`CheckList.vue:39-52`](../../CheckCheck/frontend/components/CheckList.vue#L39-L52):
the notes field is an **always-on** `UTextarea` bound to a local ref `localText`.
Key mechanics that the focus-swap design must preserve:

- `localText` (line 228) is a **local copy decoupled from the store** so that an
  incoming SSE/delta patch never clobbers text the user is mid-typing.
- A store watcher (line 232) syncs store to `localText` **only when the field is
  not focused** (`textFocused`).
- `onFieldFocus('text')` / `onFieldBlur('text')` (lines 191-200) flip
  `textFocused` AND call `markEditing` / `clearEditing` on the **editGuard**
  (`checklist`, id, `"text"`). The editGuard is what protects the focused field
  from remote delta application (see SYNC protocol section 4). `onBeforeUnmount`
  clears the mark (line 204) so a card closed mid-edit does not leave a stale
  guard.
- A `useDebounceFn` (500ms, maxWait 3000ms) fires
  `checkListsStore.update(id, { text })` (lines 234-246). That store method is
  the same one used everywhere and already routes through the offline
  outbox when `localFirst` is on.

**Consequence:** the focus-swap must keep `onFieldFocus`/`onFieldBlur` firing on
the textarea exactly as today, so both `textFocused` and the editGuard stay
correct. We are only adding a *rendered view* that is shown when the textarea is
not present, plus the click-to-focus swap.

### 1.3 Public share page (`pages/p/[token].vue`)

[`pages/p/[token].vue:72-73`](../../CheckCheck/frontend/pages/p/[token].vue#L72-L73):

```vue
<p v-if="card.text" class="mt-1 text-sm text-muted whitespace-pre-wrap break-words">
  {{ card.text }}
</p>
```

Plain interpolation, read-only, rendered for **anonymous visitors**. This must
also render Markdown, and must be sanitized because the audience is untrusted /
public. (Note: the item text on this page, `PublicChecklistItem.vue`, is out of
scope. Only the card description gets Markdown.)

### 1.4 App is a client-only SPA

[`nuxt.config.ts`](../../CheckCheck/frontend/nuxt.config.ts) sets `ssr: false`.
Everything renders in the browser, so a DOM-based sanitizer (DOMPurify) is
always available and there is no server-render XSS surface to reason about. It
also means the Markdown library is bundled into the client and works offline
inside the PWA with no network dependency.

### 1.5 No backend or model change

`CheckList.text` is `Optional[str]` with **no `max_length`** (see
[`model/checklist.py:55`](../../CheckCheck/backend/checkcheckserver/model/checklist.py#L55)).
Server-side card search (`db/checklist.py`, `CheckList.text.ilike(needle)`)
matches against the **raw Markdown source**, which is the desired behaviour:
searching "milk" still matches `**milk**`. No backend work is needed.

---

## 2. Library choice and security

### 2.1 Renderer + sanitizer

We render user text into `v-html`, so **output must be sanitized**. Recommended
stack:

- **`markdown-it`** as the parser/renderer. It is well maintained, has a strict
  `html: false` mode (raw HTML in the source is escaped, not passed through),
  ships a built-in `validateLink` that already blocks `javascript:` and other
  dangerous URL schemes, and supports `linkify` (bare URLs become links) and
  `breaks` (single newline becomes `<br>`, which matches the current
  `whitespace-pre-wrap` feel).
- **`dompurify`** as a defence-in-depth pass over markdown-it's HTML output,
  restricted to an explicit tag/attribute allowlist. Even with `html: false`,
  sanitizing the final HTML is cheap insurance and lets us hard-limit the tag
  set to exactly the subset we style.

Add both to `frontend/package.json` dependencies. Bundle cost is roughly
100 to 120 KB minified for markdown-it plus about 45 KB for DOMPurify; acceptable
for a bundled SPA, and both are pure client libs so they do not affect the PWA
offline story beyond size. If bundle size becomes a concern later, markdown-it
can be swapped for a smaller parser without touching call sites, because all
rendering goes through one util (below). Do not hand-roll a regex "Markdown"
renderer; it will be an XSS hole.

### 2.2 Allowlist (the supported subset)

Configure markdown-it and DOMPurify to permit exactly this subset. Keep it tight;
this is a small notes field, not a document editor.

- Emphasis: `**bold**`, `*italic*` / `_italic_`, `~~strikethrough~~`
- Inline code `` `code` `` and fenced code blocks
- Links `[text](https://...)` and autolinked bare URLs
- Unordered lists (`-`, `*`) and ordered lists (`1.`)
- Headings, but **clamp visual size** in CSS (a level-1 heading on a small card
  must not be huge). Consider allowing only `##`..`######` or styling all
  headings to a modest size.
- Blockquotes (`>`)
- Horizontal rule (`---`) and hard line breaks

DOMPurify `ALLOWED_TAGS` should be roughly:
`p, br, strong, em, del, code, pre, a, ul, ol, li, h1..h6, blockquote, hr`.
`ALLOWED_ATTR`: `href` (on `a`) only. Disallow images (`img`) in v1 to avoid
remote-content / tracking-pixel concerns on the offline-first, shareable board;
call this out as an explicit non-goal that can be revisited.

### 2.3 Links: safe and context-aware

- All rendered links get `target="_blank"` and `rel="noopener noreferrer nofollow"`.
  Add this via a markdown-it render rule (override `renderer.rules.link_open`) or
  a DOMPurify `afterSanitizeAttributes` hook. `nofollow` matters on the public
  page.
- **Links are clickable only in read-only contexts** (board preview, public page,
  view-only collaborators). In the *editable* open card, the rendered view is a
  click-to-edit target, so links there must be non-interactive
  (`pointer-events: none` on `a` within the editable rendered block), otherwise a
  click would open the link instead of focusing the editor. The user can still
  read the link text; they see the real URL once they focus into the source.

---

## 3. The one rendering util

Create **`frontend/utils/markdown.ts`** as the single render path. Every surface
calls it; nothing else touches markdown-it or DOMPurify directly. This mirrors
how `highlight.ts` centralizes the existing escape+highlight logic.

Proposed surface:

```ts
// Render trusted-subset Markdown to sanitized HTML. Optionally highlight a
// search needle in the rendered text nodes.
export function renderMarkdown(
  source: string | null | undefined,
  opts?: { search?: string | null },
): string
```

Behaviour:

1. Return `""` for empty/nullish source.
2. Render with the shared, module-singleton markdown-it instance
   (`{ html: false, linkify: true, breaks: true }`).
3. Sanitize with DOMPurify using the section 2.2 allowlist and the link hook.
4. If `opts.search` is set, apply search highlighting **to text nodes only** (see
   section 4). Do this after sanitize so we never highlight inside a tag or
   attribute.

Construct markdown-it and DOMPurify once at module scope, not per call.

### 3.1 Interplay with search highlighting (the fiddly part)

The board preview currently highlights the search needle. Today `highlightText`
works on plain text: escape, then wrap the needle in `<mark>`. With Markdown we
now have HTML, so a naive `String.replace` on the needle would corrupt tags and
attributes (for example the needle "a" inside `href="..."`).

Correct approach: after rendering+sanitizing, parse the HTML into a
`DocumentFragment` (or use DOMPurify's `RETURN_DOM`), walk **text nodes only**
with a `TreeWalker`, and wrap needle matches in `<mark class="search-highlight">`.
Never regex across the raw HTML string. Skip text inside `a[href]` if you want to
avoid highlighting URL text (optional). Serialize back to a string.

If this proves more effort than it is worth for v1, an acceptable fallback is to
**render Markdown without search highlighting in the notes block** (titles and
item text keep their highlight). The board preview title already highlights, so
search remains visibly useful. Decide during implementation; the DOM-walk is
preferred, the fallback is the escape hatch. Document whichever you pick.

---

## 4. Frontend changes, surface by surface

### 4.1 `CheckList.vue` board preview (read-only render)

Replace the plain-text `<p>` (line 38) with a rendered block:

```vue
<div v-if="!editModeActive && checkList!.text"
     class="md-notes md-clamp flex-none text-sm opacity-80 break-words"
     v-html="renderMarkdown(checkList!.text, { search: searchQuery })" />
```

- Keep the line-clamp behaviour. `line-clamp` still works on a block container,
  but block children (lists, headings, blockquotes) introduce margins that make a
  2-line clamp look inconsistent. Add a scoped `.md-clamp` rule that zeroes top
  margins on the first child and tightens inter-block spacing so the clamped
  preview stays tidy. See section 5 (styling).
- Drop `whitespace-pre-wrap` here; `breaks: true` in the renderer produces `<br>`
  for newlines, so we no longer need CSS whitespace preservation (and it would
  fight the block layout).

### 4.2 `CheckList.vue` open-card notes (focus-swap edit surface)

This is the core interaction. Today the textarea is `v-if="editModeActive"` and
always shown. Change to a two-state region shown only in edit mode:

- **Rendered state** (`editModeActive && !editingNotes`): a focusable block
  showing `renderMarkdown(localText)` (render the *local* copy so it reflects the
  latest typed text after blur, before the debounce/store round-trips). Empty
  text shows the same placeholder styling as the textarea ("Enter some notes...").
  This block is the click-to-edit target.
- **Editing state** (`editModeActive && editingNotes`): the existing `UTextarea`
  bound to `localText`, unchanged except it is now conditionally mounted.

Interaction wiring:

- Introduce `const editingNotes = ref(false)` (or reuse `textFocused`; a dedicated
  flag is clearer because we want the textarea mounted slightly before focus
  lands).
- Rendered block `@click` (and `@keydown.enter`/`@keydown.space` for keyboard):
  set `editingNotes = true`, then `nextTick(() => notesTextField.value?.focus?.())`
  so the caret lands in the textarea. `UTextarea` exposes the underlying
  textarea; confirm the focus call path (it may be `notesTextField.value.$el` /
  `.textareaRef`; verify against Nuxt UI v4 during implementation).
- Textarea `@focus`: call the existing `onFieldFocus('text')` (sets `textFocused`,
  `markEditing`). Keep this.
- Textarea `@blur`: call the existing `onFieldBlur('text')` AND set
  `editingNotes = false` so the block swaps back to rendered. Keep `clearEditing`.
- Accessibility: the rendered block gets `role="textbox"`, `tabindex="0"`,
  `aria-label="Notes"` so keyboard and screen-reader users can enter edit mode.
  Follow the memory note
  [[formkit-draggable-focus-cancels-drag]] in spirit: this field is inside the
  modal (not a drag handle), so there is no drag conflict, but keep focus attrs on
  the intended element.
- View-only collaborators (`!canEdit`): **never** enter edit state. Show the
  rendered block only, with links clickable (read-only context), no `tabindex`,
  no click-to-edit. Today the textarea is merely `:disabled`; rendering read-only
  Markdown is a strict improvement.

Preserve every existing guard: `localText`, the not-focused store watcher, the
debounced `update`, and the `onBeforeUnmount` `clearEditing('text')`. None of
that logic changes; we only gate *which* element is mounted.

**Height-jump note:** the rendered block and the textarea should have matching
font size, line-height, and padding so the swap does not visibly jump. Give both
the same text classes. `autoresize` on the textarea and natural block height on
the rendered div will differ slightly; keep padding identical to minimize it.

### 4.3 The hint + Markdown help popup

Below the notes field (only in `editModeActive` and when `canEdit`), add a small
muted hint:

```
Markdown supported · [Formatting help]
```

- Style: `text-xs text-dimmed`, low emphasis so it does not compete with content.
- "Formatting help" is a link/button that opens a **popup** describing Markdown
  and the supported rules. Use a Nuxt UI overlay (`UPopover` for a lightweight
  anchored panel, or `UModal` for a centered dialog; `UPopover` is the lighter
  fit for a hint). The popup content is a static cheat-sheet:

  - One sentence on what Markdown is ("a simple way to add formatting using plain
    text symbols").
  - A compact table of the supported subset with an example and result for each:
    `**bold**`, `*italic*`, `~~strike~~`, `` `code` ``, `- list`, `1. list`,
    `[link](https://...)`, `> quote`, `# heading`.
  - Keep copy free of em dashes (repo docs writing rule [[no-em-dashes-in-docs]]).

- Consider extracting the cheat-sheet into a tiny presentational component
  (`components/MarkdownHelp.vue` or similar) so the same content can be reused if
  another surface ever needs it. Optional.
- The hint must not appear on the board preview or the public page; it belongs
  only to the editable notes surface.

### 4.4 Public share page `pages/p/[token].vue`

Replace the plain interpolation (lines 72-73) with a sanitized render:

```vue
<div v-if="card.text" class="md-notes mt-1 text-sm text-muted break-words"
     v-html="renderMarkdown(card.text)" />
```

Read-only, links clickable (with `rel="... nofollow"` from the util),
sanitized (anonymous audience). No search highlight here (the public page has no
search). No edit affordances.

---

## 5. Styling (`.md-notes`)

There is **no Tailwind Typography (`prose`) plugin installed** (confirmed:
`@tailwindcss/typography` is not a dependency). Hand-roll a small scoped
stylesheet for the rendered subset. Do not add the typography plugin just for
this; the subset is tiny and a plugin would over-style.

Provide a `.md-notes` class (global CSS in
[`assets/css/main.css`](../../CheckCheck/frontend/assets/css/main.css), or scoped
+ `:deep()` where used) that styles the allowed tags at *card scale*:

- `p`: normal size, small vertical margin; first child margin-top 0.
- `strong`/`em`/`del`: default weight/style/line-through.
- `ul`/`ol`: modest left padding, correct list markers (Tailwind's preflight
  resets list styles, so re-assert `list-style` here).
- `li`: tight spacing.
- `h1..h6`: clamp to a small size ladder (for example h1 maps to roughly
  `text-base font-semibold`, descending gently); never card-dominating.
- `code`: mono, subtle background, small padding, rounded.
- `pre`: mono, subtle background, `overflow-x: auto` so long code scrolls inside
  its own box and never widens the card.
- `blockquote`: left border + muted text.
- `a`: primary color, underline on hover.
- Color inheritance: the card can be themed (custom text color via
  `textareas-inherit-color`). Ensure `.md-notes` inherits `color` so a colored
  card keeps its text color across all rendered tags (`color: inherit`), matching
  the existing `.textareas-inherit-color` treatment for the textarea.
- Dark mode: rely on inherited color and Nuxt UI CSS variables
  (`--ui-text-dimmed`, elevated backgrounds) rather than hard-coded hex, so both
  themes work.

Add the `.md-clamp` companion used by the board preview (section 4.1): flatten
block margins so `line-clamp` produces an even 2/3-line preview.

---

## 6. Testing

### 6.1 Unit (vitest, `frontend/tests/unit/`)

Add `markdown.spec.ts` covering `renderMarkdown`:

- Renders each supported element correctly (bold, italic, strike, code, lists,
  links, headings, blockquote, hr, line breaks).
- **XSS vectors are neutralized:** `<script>alert(1)</script>`,
  `[x](javascript:alert(1))`, `<img src=x onerror=...>`, raw `<iframe>`, event
  handler attributes, `data:`/`vbscript:` URLs. Assert none survive sanitization.
- Links get `target="_blank"` and `rel` including `noopener noreferrer nofollow`.
- `img` is stripped (v1 non-goal).
- Empty/nullish source returns `""`.
- Search highlight (if implemented in the util): needle wrapped in `<mark>`
  within text only; a needle that also appears inside a URL/attribute does not
  corrupt the HTML.

Follow the existing util-test style (see `normalizeItemText.spec.ts`,
`editGuard.spec.ts`).

### 6.2 E2E (Playwright, `frontend/tests/e2e/`)

Run via the local `@playwright/test` CLI through bun, not bare `bunx playwright`
(see [[frontend-e2e-playwright-cli]]). Add `markdown-notes.spec.ts`:

- Open a card, focus the notes, type Markdown (`**bold** and _italic_`), blur,
  assert the rendered block contains `<strong>`/`<em>` and the raw asterisks are
  gone. Click the rendered block, assert the textarea reappears with the raw
  source and is focused.
- Board preview: a card with Markdown notes shows rendered formatting (not raw
  markers) in the preview.
- View-only collaborator sees rendered notes and cannot enter edit mode (no
  textarea on click).
- Public page (`/p/[token]`): shared card renders Markdown notes.
- The "Formatting help" hint opens the popup and shows the cheat-sheet.

Be aware some DnD/sharing E2E specs are known flaky and fail non-deterministically
per run ([[flaky-e2e-dnd-sharing]]); re-run before blaming a Markdown change. Use
the robust modal/selector patterns from [[frontend-e2e-double-dialog]]
(`[data-testid=card-title]` to open a card, declarative `v-model:open` modals).

### 6.3 Manual verification

Use the `/run` app-launch flow. Check: focus-swap feels smooth (no jarring height
jump), colored cards keep their text color in rendered notes, dark mode, mobile
tap-to-edit, long code block scrolls inside the card, offline edit still saves
(edit notes offline, confirm it renders and the outbox drains on reconnect).

---

## 7. Step-by-step implementation order

1. Add `markdown-it` and `dompurify` (and `@types/markdown-it`,
   `@types/dompurify` if needed) to `frontend/package.json`; install.
2. Write `utils/markdown.ts` (`renderMarkdown`) with the singleton parser +
   sanitizer + link hook. Get `markdown.spec.ts` green first (TDD the security
   surface before wiring any UI).
3. Add `.md-notes` (+ `.md-clamp`) styles.
4. Board preview render (`CheckList.vue:38`), including search-highlight
   composition (or the documented fallback).
5. Focus-swap edit surface in `CheckList.vue` (rendered block + conditional
   textarea + click/keyboard-to-edit), preserving `localText`, the store watcher,
   the debounce, editGuard focus/blur, and `onBeforeUnmount` cleanup. Handle the
   `!canEdit` read-only path.
6. Hint + Markdown help popup (and optional `MarkdownHelp.vue`).
7. Public page render (`pages/p/[token].vue`).
8. E2E spec.
9. Manual pass (`/run`), including an offline edit.
10. `CHANGELOG.md` entry. No backend, migration, OpenAPI, or SYNC-protocol
    changes are needed; do not touch them.

---

## 8. Risks and edge cases (read before coding)

- **XSS is the primary risk.** All rendering goes through the sanitized util. Do
  not add a second, un-sanitized `v-html` path. Review the DOMPurify allowlist
  during implementation.
- **Search-highlight/HTML composition** is the fiddliest bit; DOM-walk text nodes,
  never string-replace across HTML. Fallback documented in 3.1.
- **Focus-swap correctness with the editGuard:** the whole point of the existing
  `markEditing`/`clearEditing` is that a remote delta must not overwrite the field
  while the user edits. Keep those calls bound to the textarea focus/blur exactly
  as today, or offline/collaborative edits can flicker or be lost. Verify the
  rendered block reads `localText` (not the store value) so text typed just before
  blur is not momentarily lost before the debounce commits.
- **Height jump on swap:** match typography/padding between rendered block and
  textarea; test on a card with multi-line notes.
- **Line-clamp with block elements:** `.md-clamp` must flatten margins or the
  preview looks ragged.
- **Colored cards:** rendered tags must inherit the card's custom text color, like
  the textarea does via `.textareas-inherit-color`.
- **Bundle size / offline:** both libs are bundled and work offline in the PWA; no
  `/api` or network dependency is introduced. Just note the added weight.
- **Backwards compatibility:** existing plain-text descriptions render unchanged
  (plain text is valid Markdown). No data migration. Text that happens to contain
  Markdown-significant characters (for example a literal `*`) will now be
  interpreted; this is expected and acceptable for a notes field, and is the
  reason the help popup exists.
- **Images are a non-goal** in v1 (remote-content/tracking concerns on a shareable
  board). Revisit later if requested.
- **Item text stays plain.** Only the card description gets Markdown. Do not
  change `CheckListItem` rendering.
