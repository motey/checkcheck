// @vitest-environment jsdom
//
// Security is the whole point of utils/markdown.ts, so TDD it here before any UI
// wiring: prove the supported subset renders AND that XSS vectors are neutralised.
// Runs under jsdom because both DOMPurify and the search-highlight DOM walk need
// a real DOM (the app itself is a client-only SPA, so a DOM is always present).
import { describe, it, expect } from "vitest";
import { renderMarkdown, renderMarkdownInline } from "@/utils/markdown";

describe("renderMarkdown — supported subset", () => {
  it("renders emphasis", () => {
    expect(renderMarkdown("**bold**")).toContain("<strong>bold</strong>");
    expect(renderMarkdown("*italic*")).toContain("<em>italic</em>");
    expect(renderMarkdown("_italic_")).toContain("<em>italic</em>");
    expect(renderMarkdown("~~strike~~")).toContain("<s>strike</s>");
  });

  it("renders inline and fenced code", () => {
    expect(renderMarkdown("`code`")).toContain("<code>code</code>");
    const fenced = renderMarkdown("```\nline\n```");
    expect(fenced).toContain("<pre>");
    expect(fenced).toContain("<code>");
  });

  it("renders lists", () => {
    const ul = renderMarkdown("- a\n- b");
    expect(ul).toContain("<ul>");
    expect(ul).toContain("<li>a</li>");
    const ol = renderMarkdown("1. a\n2. b");
    expect(ol).toContain("<ol>");
  });

  it("renders headings, blockquotes and horizontal rules", () => {
    expect(renderMarkdown("## Heading")).toContain("<h2>Heading</h2>");
    expect(renderMarkdown("> quote")).toContain("<blockquote>");
    expect(renderMarkdown("---")).toContain("<hr>");
  });

  it("turns single newlines into hard <br> breaks", () => {
    expect(renderMarkdown("a\nb")).toContain("<br>");
  });

  it("renders explicit and autolinked links", () => {
    expect(renderMarkdown("[text](https://example.com)")).toContain('href="https://example.com"');
    expect(renderMarkdown("visit https://example.com")).toContain('href="https://example.com"');
  });

  it("gives every link target=_blank and a safe rel", () => {
    const html = renderMarkdown("[x](https://example.com)");
    expect(html).toContain('target="_blank"');
    expect(html).toContain("noopener");
    expect(html).toContain("noreferrer");
    expect(html).toContain("nofollow");
  });
});

describe("renderMarkdown — sanitization", () => {
  it("returns empty string for empty/nullish source", () => {
    expect(renderMarkdown("")).toBe("");
    expect(renderMarkdown(null)).toBe("");
    expect(renderMarkdown(undefined)).toBe("");
  });

  it("neutralises raw script/iframe HTML", () => {
    const html = renderMarkdown("<script>alert(1)</script>");
    expect(html).not.toContain("<script");
    expect(renderMarkdown("<iframe src=x></iframe>")).not.toContain("<iframe");
  });

  it("never turns a dangerous scheme into a clickable link", () => {
    // markdown-it's validateLink rejects these, so no <a> is produced at all —
    // the source survives only as inert escaped text, never an executable href.
    expect(renderMarkdown("[x](javascript:alert(1))")).not.toContain("<a");
    expect(renderMarkdown("[x](vbscript:msgbox(1))")).not.toContain("<a");
    expect(renderMarkdown("[x](data:text/html,<script>alert(1)</script>)")).not.toContain("<a");
    expect(renderMarkdown("[x](data:text/html,<script>alert(1)</script>)")).not.toContain("<script");
  });

  it("strips images (v1 non-goal) — no live <img> tag survives", () => {
    // With html:false the raw tag is escaped to inert text; either way there is
    // no live element carrying the onerror handler.
    const html = renderMarkdown("<img src=x onerror=alert(1)>");
    expect(html).not.toContain("<img");
    expect(renderMarkdown("![alt](https://example.com/x.png)")).not.toContain("<img");
  });
});

describe("renderMarkdownInline — item text", () => {
  it("renders the inline subset without a <p> wrapper", () => {
    expect(renderMarkdownInline("**bold**")).toBe("<strong>bold</strong>");
    expect(renderMarkdownInline("*italic*")).toBe("<em>italic</em>");
    expect(renderMarkdownInline("~~strike~~")).toBe("<s>strike</s>");
    expect(renderMarkdownInline("`code`")).toBe("<code>code</code>");
  });

  it("does NOT render block constructs (they stay literal)", () => {
    // The inline parser never sees headings / lists / quotes, so the markers
    // survive as text — exactly right for a single-line checkbox label.
    expect(renderMarkdownInline("# heading")).toBe("# heading");
    expect(renderMarkdownInline("- list item")).toBe("- list item");
    expect(renderMarkdownInline("> quote")).toBe("&gt; quote");
    expect(renderMarkdownInline("```\ncode\n```")).not.toContain("<pre");
  });

  it("renders a bare URL as inert text plus a boxed-arrow icon link", () => {
    const html = renderMarkdownInline("see https://example.com now");
    // The URL text stays present (inert) …
    expect(html).toContain("https://example.com");
    // … and gets a single ext-link icon that opens in a new tab.
    expect(html).toContain('class="ext-link"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain("noopener");
    expect(html).toContain("↗");
    // Exactly one icon anchor for one URL.
    expect((html.match(/class="ext-link"/g) ?? []).length).toBe(1);
  });

  it("gives each URL its own icon when an item has several", () => {
    const html = renderMarkdownInline("Compare https://example.com/a and https://example.org/b");
    expect((html.match(/class="ext-link"/g) ?? []).length).toBe(2);
    expect(html).toContain('href="https://example.com/a"');
    expect(html).toContain('href="https://example.org/b"');
  });

  it("gives a markdown link its label as text plus the icon (no clickable text)", () => {
    const html = renderMarkdownInline("see [store](https://example.com)");
    expect(html).toContain("store");
    expect(html).toContain('class="ext-link"');
    // The only anchor is the icon; the label itself is not wrapped in <a>.
    expect((html.match(/<a /g) ?? []).length).toBe(1);
  });

  it("never emits a link (or icon) for a dangerous scheme in item text", () => {
    // No http(s) and no markdown chars → fast path; the source survives as inert
    // escaped text, never an anchor.
    const html = renderMarkdownInline("[x](javascript:alert(1))");
    expect(html).not.toContain("<a");
    expect(html).not.toContain("ext-link");
  });

  it("takes the fast path for plain text (escaped, newlines preserved)", () => {
    expect(renderMarkdownInline("Buy milk")).toBe("Buy milk");
    expect(renderMarkdownInline("a < b & c")).toBe("a &lt; b &amp; c");
    expect(renderMarkdownInline("line1\nline2")).toBe("line1\nline2");
  });

  it("neutralises XSS on the fast and parsed paths", () => {
    expect(renderMarkdownInline("<script>alert(1)</script>")).not.toContain("<script");
    expect(renderMarkdownInline("**<img src=x onerror=y>**")).not.toContain("<img");
  });

  it("highlights a search needle, including inside formatting", () => {
    expect(renderMarkdownInline("milk", { search: "milk" })).toContain(
      '<mark class="search-highlight">milk</mark>',
    );
    expect(renderMarkdownInline("**milk**", { search: "milk" })).toContain("<mark");
  });

  it("returns empty string for empty/nullish source", () => {
    expect(renderMarkdownInline("")).toBe("");
    expect(renderMarkdownInline(null)).toBe("");
    expect(renderMarkdownInline(undefined)).toBe("");
  });
});

describe("renderMarkdown — search highlighting", () => {
  it("wraps the needle in a <mark> within text", () => {
    const html = renderMarkdown("buy some milk", { search: "milk" });
    expect(html).toContain('<mark class="search-highlight">milk</mark>');
  });

  it("is case-insensitive", () => {
    const html = renderMarkdown("Milk", { search: "milk" });
    expect(html.toLowerCase()).toContain("<mark");
  });

  it("does not corrupt HTML when the needle also appears inside a URL", () => {
    const html = renderMarkdown("see https://example.com/a", { search: "a" });
    // The link's href must stay intact (no <mark> injected into the attribute).
    expect(html).toContain('href="https://example.com/a"');
    expect(html).not.toContain("<mark");
  });

  it("returns un-highlighted HTML when no needle is given", () => {
    expect(renderMarkdown("milk", { search: "" })).not.toContain("<mark");
    expect(renderMarkdown("milk", { search: null })).not.toContain("<mark");
  });
});
