/**
 * Put text on the clipboard. Accepts a promise so the caller can start the copy
 * inside the click handler while the text is still being prepared (for example a
 * card's items are fetched first): Safari only allows a clipboard write during
 * the user gesture, and a `ClipboardItem` holding a promise keeps that gesture.
 *
 * Falls back to `writeText`, then to the legacy `execCommand("copy")`, which also
 * works on a plain-http deployment where `navigator.clipboard` does not exist.
 * Throws when every route fails.
 *
 * `opts.html` adds a `text/html` flavor to the same clipboard item: a rich
 * editor (mail, Word) pastes the formatting, a plain field still pastes `text`.
 * Only the `ClipboardItem` route can carry it; the fallbacks copy `text` alone.
 */
export async function copyTextToClipboard(
  text: string | Promise<string>,
  opts?: { html?: string },
): Promise<void> {
  const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  const html = opts?.html;
  if ((typeof text !== "string" || html) && clip?.write && typeof ClipboardItem !== "undefined") {
    try {
      const blob = Promise.resolve(text).then((t) => new Blob([t], { type: "text/plain" }));
      const flavors: Record<string, Promise<Blob>> = { "text/plain": blob };
      if (html) flavors["text/html"] = Promise.resolve(new Blob([html], { type: "text/html" }));
      await clip.write([new ClipboardItem(flavors)]);
      return;
    } catch {
      // Fall through to the plain-text routes.
    }
  }
  const value = await text;
  if (clip?.writeText) {
    try {
      await clip.writeText(value);
      return;
    } catch {
      // Fall through to the legacy route.
    }
  }
  if (!legacyCopy(value)) throw new Error("Clipboard is not available");
}

function legacyCopy(value: string): boolean {
  if (typeof document === "undefined") return false;
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}
