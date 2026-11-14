# Plan: copy card notes to the clipboard

**Status:** done (all chunks landed; on-device check open). Implements
[issue #31](https://github.com/motey/checkcheck/issues/31). Frontend only: no
backend, schema, sync, outbox or OpenAPI change. Works offline (client only).

## Decisions (settled with the maintainer, 2026-11-14)

- Two buttons, not three. **Copy notes** writes `text/plain` (readable plain
  text, Markdown markers removed) and `text/html` (the rendered, sanitized
  notes) in one clipboard item: a plain field pastes the text, a rich editor
  pastes the formatting. A separate "plain only" button is not needed, rich
  editors have "paste as plain text". **Copy as Markdown** writes only
  `text/plain` (the stored source), otherwise a rich editor would paste
  formatted text instead of the Markdown.
- The Markdown button is hidden when the notes use no Markdown: the plain-text
  conversion equals the stored text (after trim and line-ending normalization).
- Placement: small icon buttons with tooltips at the right end of the
  "Markdown supported · Formatting help" row under the notes, always visible,
  dimmed like the hint text, brighter on hover. Shared `CardParts/NotesField.vue`,
  so the open card and the `/p/<token>` viewer both get it.
- View-only users (collaborators, view-only public links) have no hint row; when
  the notes are not empty they get a slim right-aligned row with only the icons.
- No ⋮ menu entries, no change to the board preview.
- Plain text format: paragraphs and headings separated by a blank line, `<br>`
  as newline, list items as `- ` / `1. ` with indentation for nesting, links as
  `label (url)` (a bare URL stays just the URL), code kept verbatim, quotes
  prefixed with `> `, rules as `---`.
- The plain-text fallbacks (`writeText`, `execCommand`) carry no HTML; there
  the copy is plain text only.

## Chunks

1. **Helpers** (`utils/markdown.ts`, `utils/clipboard.ts`)
   - `markdownToPlainText(source)` and `notesUseMarkdown(source)`, built on the
     same markdown-it instance and sanitize step as `renderMarkdown`.
   - `copyTextToClipboard(text, { html })`: optional HTML flavor via one
     `ClipboardItem`; existing callers unchanged.
2. **UI** (`components/CardParts/NotesField.vue`)
   - Hint row becomes a flex row: hint left (editors only), icons right (notes
     not empty).
   - Trap: clicking an icon while the textarea is focused would blur it on
     mousedown and swap to the rendered view (different height), so the button
     moves before the click lands. `@mousedown.prevent` on the buttons keeps the
     focus and the layout.
   - Copies the live `modelValue`, so unsaved typing is included. Toasts as in
     "Copy list as text".
3. **Tests + changelog**
   - Vitest for the conversion helpers and the clipboard HTML path.
   - E2E: open card with Markdown notes, copy, check plain text; Markdown button
     hidden for plain notes; icons on a view-only public link.
   - CHANGELOG entry.

## Result

- Unit: `tests/unit/notesPlainText.spec.ts`, `tests/unit/clipboard.spec.ts`;
  full unit suite green (496).
- E2E: `tests/e2e/copy-notes.spec.ts` green, plus the existing Markdown notes,
  note cards, public viewer, card editor and touch editor specs.
- Found on the way: markdown-it writes every line break as `<br>\n`, so the
  text serializer drops source newlines in inline text (else doubled breaks).
  A `javascript:` link is never linkified, so it stays literal text in both the
  card and the copy.
- Open: a quick look on a phone (icon size and tap target in the hint row).
