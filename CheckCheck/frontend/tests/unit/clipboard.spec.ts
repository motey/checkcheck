// The clipboard helper's HTML flavor (issue #31 "Copy notes"): plain text and
// HTML go into one clipboard item, and the fallbacks still copy the text.
import { describe, it, expect, vi, afterEach } from "vitest";
import { copyTextToClipboard } from "@/utils/clipboard";

class FakeClipboardItem {
  constructor(public items: Record<string, Promise<Blob> | Blob>) {}
}

function installClipboard(clip: Partial<Clipboard>) {
  vi.stubGlobal("navigator", { clipboard: clip });
  vi.stubGlobal("ClipboardItem", FakeClipboardItem);
}

async function flavorText(item: FakeClipboardItem, type: string): Promise<string | undefined> {
  const blob = await item.items[type];
  return blob ? await blob.text() : undefined;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("copyTextToClipboard", () => {
  it("writes text and html into one clipboard item", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    const writeText = vi.fn().mockResolvedValue(undefined);
    installClipboard({ write, writeText });

    await copyTextToClipboard("bold", { html: "<p><strong>bold</strong></p>" });

    expect(writeText).not.toHaveBeenCalled();
    expect(write).toHaveBeenCalledTimes(1);
    const [item] = write.mock.calls[0]![0] as FakeClipboardItem[];
    expect(await flavorText(item!, "text/plain")).toBe("bold");
    expect(await flavorText(item!, "text/html")).toBe("<p><strong>bold</strong></p>");
  });

  it("uses writeText for a plain string without html", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    const writeText = vi.fn().mockResolvedValue(undefined);
    installClipboard({ write, writeText });

    await copyTextToClipboard("# raw markdown");

    expect(write).not.toHaveBeenCalled();
    expect(writeText).toHaveBeenCalledWith("# raw markdown");
  });

  it("falls back to plain text when the html write fails", async () => {
    const write = vi.fn().mockRejectedValue(new Error("not allowed"));
    const writeText = vi.fn().mockResolvedValue(undefined);
    installClipboard({ write, writeText });

    await copyTextToClipboard("bold", { html: "<strong>bold</strong>" });

    expect(writeText).toHaveBeenCalledWith("bold");
  });

  it("falls back to plain text without ClipboardItem support", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    vi.stubGlobal("ClipboardItem", undefined);

    await copyTextToClipboard("bold", { html: "<strong>bold</strong>" });

    expect(writeText).toHaveBeenCalledWith("bold");
  });
});
