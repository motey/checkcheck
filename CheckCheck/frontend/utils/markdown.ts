// The single render path for the card description (`text`) field's Markdown.
//
// Every surface that shows rendered notes — the board preview, the open card's
// read view, view-only collaborators, and the public share page — calls
// `renderMarkdown`; nothing else touches markdown-it or DOMPurify directly. This
// mirrors how utils/highlight.ts centralises the escape+highlight logic.
//
// Security: the output is fed to `v-html`, so it MUST be sanitized. We render a
// deliberately tiny trusted subset (see ALLOWED_TAGS) with markdown-it in
// `html: false` mode (raw HTML in the source is escaped, never passed through),
// then run DOMPurify as a defence-in-depth pass restricted to that same subset.
// Do not add a second, un-sanitized v-html path anywhere.
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import { highlightText } from "@/utils/highlight";

// Constructed once at module scope, not per call.
const md = new MarkdownIt({
  html: false, // raw HTML in the source is escaped, not rendered
  linkify: true, // bare URLs become links
  breaks: true, // a single newline becomes <br>, matching the old whitespace-pre-wrap feel
});

// The exact tag subset we style in `.md-notes` (assets/css/main.css). Images are
// a deliberate v1 non-goal (remote-content / tracking-pixel concerns on an
// offline-first, shareable board).
const ALLOWED_TAGS = [
  "p", "br", "strong", "em", "s", "del",
  "code", "pre",
  "a",
  "ul", "ol", "li",
  "h1", "h2", "h3", "h4", "h5", "h6",
  "blockquote", "hr",
];

// `href` is the only source attribute we keep; `target`/`rel` are (re)applied by
// the hook below so every link — including linkified bare URLs — is safe.
const ALLOWED_ATTR = ["href"];

// Force every rendered link to open safely in a new tab. `nofollow` matters on
// the public page. Registered once on the singleton. The hook runs after
// DOMPurify's own attribute sanitization, so the attributes it sets persist.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer nofollow");
  }
});

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Wrap search-needle matches in <mark> across TEXT NODES ONLY. We never
// String.replace across the raw HTML — that would corrupt tags/attributes (for
// example the needle "a" inside href="..."). The <mark> we inject here is added
// after sanitize, so it is trusted by construction.
function highlightHtml(html: string, needle: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const re = new RegExp(`(${escapeRegExp(needle)})`, "gi");

  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) {
    // Don't highlight the visible text of links (URL text stays intact).
    if ((walker.currentNode.parentElement?.closest("a"))) continue;
    textNodes.push(walker.currentNode as Text);
  }

  for (const node of textNodes) {
    const text = node.nodeValue ?? "";
    re.lastIndex = 0;
    if (!re.test(text)) continue;
    re.lastIndex = 0;

    const frag = doc.createDocumentFragment();
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      if (m.index > last) frag.appendChild(doc.createTextNode(text.slice(last, m.index)));
      const mark = doc.createElement("mark");
      mark.className = "search-highlight";
      mark.textContent = m[0];
      frag.appendChild(mark);
      last = m.index + m[0].length;
      if (m[0].length === 0) re.lastIndex++; // guard against zero-width loops
    }
    if (last < text.length) frag.appendChild(doc.createTextNode(text.slice(last)));
    node.parentNode?.replaceChild(frag, node);
  }

  return doc.body.innerHTML;
}

/**
 * Render trusted-subset Markdown to sanitized HTML. Optionally highlight a
 * search needle in the rendered text nodes.
 *
 * Use this for the card description (`text`) — a single field per card, rendered
 * in a block context. For the hot item-text path use `renderMarkdownInline`.
 */
export function renderMarkdown(
  source: string | null | undefined,
  opts?: { search?: string | null },
): string {
  if (!source) return "";
  const rawHtml = md.render(source);
  const clean = DOMPurify.sanitize(rawHtml, { ALLOWED_TAGS, ALLOWED_ATTR });
  const needle = opts?.search;
  if (!needle) return clean;
  return highlightHtml(clean, needle);
}

// ── Inline-only variant for checklist item text ──────────────────────────────
//
// Item text is the render hot path: up to ~10 items per card render across the
// whole board, and every one re-runs on each check/uncheck, drag-reorder, and —
// because the binding depends on the search query — every search keystroke. Full
// block Markdown there is both too slow (~100µs+/call, hundreds of calls per
// board) and semantically wrong (headings/lists/blockquotes make no sense in a
// single-line `line-clamp` checkbox label). So items get a deliberately tiny,
// inline-only subset with two safeguards that keep the common case free.

// `md.renderInline` skips the block parser: no <p>, no lists/headings/quotes/hr.
// We keep `a` so markdown-it's linkifier can flag URLs, but the URL text itself
// is NOT left clickable (that would fight the "click card to open" gesture on the
// board). Instead `linkifyToIcons` rewrites each detected link into plain URL text
// followed by a small boxed-arrow icon that is the only clickable affordance.
const INLINE_ALLOWED_TAGS = ["br", "strong", "em", "s", "del", "code", "a"];

// Fast-path detector: text with none of these characters cannot produce inline
// Markdown OR a link, so we skip the parser entirely and reuse the cheap
// escape+highlight path (the overwhelmingly common "Buy milk" case, ~0.1µs vs
// ~100µs). `https?://` is included so bare URLs take the parser (linkify) path.
const INLINE_TRIGGER = /[*_~`]|https?:\/\//i;

// North-east arrow; styled into a small bordered box by `.ext-link` (main.css).
const EXT_LINK_GLYPH = "↗";

// Turn every rendered link into "URL as plain text + a boxed-arrow icon link".
// The icon is the sole click target (opens in a new tab); the URL/label text is
// inert so a click on it falls through to the card-open handler on the board.
// Built after sanitize from an already-validated href, so it is safe by
// construction; the returned string is not re-sanitized.
function linkifyToIcons(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  for (const a of Array.from(doc.querySelectorAll("a[href]"))) {
    const href = a.getAttribute("href")!;
    const frag = doc.createDocumentFragment();
    frag.appendChild(doc.createTextNode(a.textContent ?? ""));
    const icon = doc.createElement("a");
    icon.setAttribute("href", href);
    icon.setAttribute("target", "_blank"); // external links always open a new tab
    icon.setAttribute("rel", "noopener noreferrer nofollow");
    icon.setAttribute("aria-label", "Open link in a new tab");
    icon.className = "ext-link";
    icon.textContent = EXT_LINK_GLYPH;
    frag.appendChild(icon);
    a.replaceWith(frag);
  }
  return doc.body.innerHTML;
}

// Item text is stable, so memoize the (needle-free) render by source string:
// search-keystroke re-renders and drag reflows of an already-seen string become
// a Map lookup instead of a re-parse. Bounded, then cleared wholesale.
const inlineCache = new Map<string, string>();
const INLINE_CACHE_MAX = 4000;

function renderInlineCached(source: string): string {
  const hit = inlineCache.get(source);
  if (hit !== undefined) return hit;
  let html = DOMPurify.sanitize(md.renderInline(source), {
    ALLOWED_TAGS: INLINE_ALLOWED_TAGS,
    ALLOWED_ATTR,
  });
  if (html.includes("<a ")) html = linkifyToIcons(html);
  if (inlineCache.size >= INLINE_CACHE_MAX) inlineCache.clear();
  inlineCache.set(source, html);
  return html;
}

/**
 * Render a single line of item text with an inline-only Markdown subset
 * (`**bold**`, `*italic*`, `~~strike~~`, `` `code` ``). Bare URLs are detected
 * and given a boxed-arrow "open in new tab" icon while the URL text stays inert
 * (see `linkifyToIcons`). Optionally highlight a search needle. Cheap by
 * construction: plain text takes the escape+highlight fast path; the rest is
 * memoized.
 */
export function renderMarkdownInline(
  source: string | null | undefined,
  opts?: { search?: string | null },
): string {
  if (!source) return "";
  const needle = opts?.search;
  // Fast path: no inline-Markdown chars and no URL → today's cheap escape.
  if (!INLINE_TRIGGER.test(source)) return highlightText(source, needle);
  const html = renderInlineCached(source);
  if (!needle) return html;
  return highlightHtml(html, needle);
}
