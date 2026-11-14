// @vitest-environment jsdom
//
// "Copy notes" (issue #31): the plain-text export of the card notes and the
// "does this use Markdown" check that decides whether "Copy as Markdown" shows.
import { describe, it, expect } from "vitest";
import { markdownToPlainText, notesUseMarkdown } from "@/utils/markdown";

describe("markdownToPlainText", () => {
  it("is empty for empty notes", () => {
    expect(markdownToPlainText("")).toBe("");
    expect(markdownToPlainText(null)).toBe("");
    expect(markdownToPlainText(undefined)).toBe("");
  });

  it("drops emphasis markers", () => {
    expect(markdownToPlainText("**bold**, *italic*, ~~gone~~ and `code`")).toBe("bold, italic, gone and code");
  });

  it("keeps line breaks and separates paragraphs by one blank line", () => {
    expect(markdownToPlainText("line one\nline two\n\n\n\nnext paragraph")).toBe(
      "line one\nline two\n\nnext paragraph",
    );
  });

  it("drops heading markers", () => {
    expect(markdownToPlainText("# Title\n\nBody\n\n## Sub")).toBe("Title\n\nBody\n\nSub");
  });

  it("writes unordered and ordered lists with markers", () => {
    expect(markdownToPlainText("* a\n* **b**")).toBe("- a\n- b");
    expect(markdownToPlainText("1. a\n1. b\n1. c")).toBe("1. a\n2. b\n3. c");
  });

  it("indents nested lists", () => {
    expect(markdownToPlainText("- a\n  - a1\n  - a2\n- b")).toBe("- a\n  - a1\n  - a2\n- b");
    expect(markdownToPlainText("1. a\n   - x\n2. b")).toBe("1. a\n  - x\n2. b");
  });

  it("lines up continuation lines of a list item under its text", () => {
    expect(markdownToPlainText("- first\n  second line")).toBe("- first\n  second line");
  });

  it("writes links as label (url) and keeps bare URLs as they are", () => {
    expect(markdownToPlainText("see [the docs](https://example.com/docs)")).toBe(
      "see the docs (https://example.com/docs)",
    );
    expect(markdownToPlainText("go to https://example.com now")).toBe("go to https://example.com now");
    expect(markdownToPlainText("<https://example.com>")).toBe("https://example.com");
  });

  it("keeps a link with an unsafe target as the literal text the card shows", () => {
    // markdown-it refuses to make a link of it, so the card shows the source.
    expect(markdownToPlainText("[click](javascript:alert(1))")).toBe("[click](javascript:alert(1))");
  });

  it("keeps code blocks verbatim", () => {
    expect(markdownToPlainText("```\nconst a = 1;\n  **not bold**\n```")).toBe("const a = 1;\n  **not bold**");
  });

  it("prefixes quotes and writes rules", () => {
    expect(markdownToPlainText("> quoted\n> more\n\n---\n\nafter")).toBe("> quoted\n> more\n\n---\n\nafter");
  });

  it("keeps escaped characters and raw HTML as literal text", () => {
    expect(markdownToPlainText("\\*not italic\\*")).toBe("*not italic*");
    expect(markdownToPlainText("a <b>tag</b> & more")).toBe("a <b>tag</b> & more");
  });
});

describe("notesUseMarkdown", () => {
  it("is false for empty or plain notes", () => {
    expect(notesUseMarkdown("")).toBe(false);
    expect(notesUseMarkdown("   ")).toBe(false);
    expect(notesUseMarkdown("Buy milk\nand eggs")).toBe(false);
    expect(notesUseMarkdown("first\r\n\r\n\r\nsecond  ")).toBe(false);
  });

  it("is false when the Markdown reads the same as plain text", () => {
    expect(notesUseMarkdown("- a\n- b")).toBe(false);
    expect(notesUseMarkdown("see https://example.com")).toBe(false);
  });

  it("is true when Markdown changes the text", () => {
    expect(notesUseMarkdown("**bold**")).toBe(true);
    expect(notesUseMarkdown("# Title")).toBe(true);
    expect(notesUseMarkdown("[docs](https://example.com)")).toBe(true);
    expect(notesUseMarkdown("* star list")).toBe(true);
  });
});
