function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function highlightText(text: string | null | undefined, needle: string | null | undefined): string {
  if (!text) return "";
  const safe = escapeHtml(text);
  if (!needle) return safe;
  const escapedNeedle = escapeHtml(needle).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return safe.replace(new RegExp(`(${escapedNeedle})`, "gi"), '<mark class="search-highlight">$1</mark>');
}
