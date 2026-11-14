/**
 * Put text on the clipboard. Accepts a promise so the caller can start the copy
 * inside the click handler while the text is still being prepared (for example a
 * card's items are fetched first): Safari only allows a clipboard write during
 * the user gesture, and a `ClipboardItem` holding a promise keeps that gesture.
 *
 * Falls back to `writeText`, then to the legacy `execCommand("copy")`, which also
 * works on a plain-http deployment where `navigator.clipboard` does not exist.
 * Throws when every route fails.
 */
export async function copyTextToClipboard(text: string | Promise<string>): Promise<void> {
  const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (typeof text !== "string" && clip?.write && typeof ClipboardItem !== "undefined") {
    try {
      const blob = Promise.resolve(text).then((t) => new Blob([t], { type: "text/plain" }));
      await clip.write([new ClipboardItem({ "text/plain": blob })]);
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
