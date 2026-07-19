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
