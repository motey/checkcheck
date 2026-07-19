// @vitest-environment jsdom
//
// Security is the whole point of utils/markdown.ts, so TDD it here before any UI
// wiring: prove the supported subset renders AND that XSS vectors are neutralised.
// Runs under jsdom because both DOMPurify and the search-highlight DOM walk need
// a real DOM (the app itself is a client-only SPA, so a DOM is always present).
import { describe, it, expect } from "vitest";
import { renderMarkdown } from "@/utils/markdown";

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
