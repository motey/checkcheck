// ── Email deep links: open the card, mark it read (chunk E5) ─────────────────
//
// Every notification email links to `{SERVER_PUBLIC_URL}/?card=<cl_id>&n=<notification_id>`
// (backend `notify/render.py`). Two things have to happen when somebody follows
// one, and neither belongs on the server:
//
//   1. `?card=` becomes the app's own card route, `/card/<cl_id>`, so the
//      overlay opens the way it does from anywhere else in the client. The mail
//      uses a query parameter rather than that path because it is built by a
//      renderer that must not know the client's routing table.
//   2. `?n=` marks exactly that notification read, **after** the card is on
//      screen, never in a redirect endpoint or a router guard. Mail scanners and
//      link previewers fetch URLs before a human ever sees them, and a redirect
//      that marked things read would clear the badge for messages nobody opened.
//      Doing it in the SPA means a real browser really rendered the card.
//
// The logic lives here, free of Vue and the router, so both halves are testable
// without a Nuxt harness. `composables/useNotificationDeepLink.ts` supplies the
// live route, the store and the waiting.

export type QueryValue = string | null | (string | null)[] | undefined;
export type DeepLinkQuery = Record<string, QueryValue>;

export type NotificationDeepLink = {
  /** The `?card=` id, or null when the URL carries none. */
  cardId: string | null;
  /** The `?n=` notification id, or null. */
  notificationId: string | null;
  /** The query to redirect a `?card=` link with: keeps `n` for step 2. */
  cardQuery: DeepLinkQuery;
  /** The query once the deep-link parameters are consumed. */
  strippedQuery: DeepLinkQuery;
};

/** First usable string of a query value (`?n=a&n=b` is a malformed link, not two). */
function firstValue(value: QueryValue): string | null {
  const raw = Array.isArray(value) ? value.find((v) => typeof v === "string" && v !== "") : value;
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  return trimmed === "" ? null : trimmed;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Whether a `?card=` value can name a card at all (finding 10).
 *
 * Card ids are UUIDs, and `card` is whatever the URL carried: it reaches a
 * router *path*, so a crafted value stays same-origin and this is a nit rather
 * than an open redirect, but "/card/../../whatever" is still a confusing place
 * to send somebody who clicked a link in their mail.
 */
export function looksLikeCardId(value: string | null | undefined): boolean {
  return typeof value === "string" && UUID_RE.test(value);
}

function without(query: DeepLinkQuery, ...keys: string[]): DeepLinkQuery {
  const next = { ...query };
  for (const key of keys) delete next[key];
  return next;
}

export function parseNotificationDeepLink(query: DeepLinkQuery | undefined | null): NotificationDeepLink {
  const source = query ?? {};
  return {
    cardId: firstValue(source.card),
    notificationId: firstValue(source.n),
    cardQuery: without(source, "card"),
    strippedQuery: without(source, "card", "n"),
  };
}

export type DeepLinkContext = {
  /** The URL as it is now: read again after the wait, since that takes time. */
  current: () => { path: string; query: DeepLinkQuery | undefined | null };
  /** Whether the route already points at a card overlay. */
  hasCard: boolean;
  /** Resolves once the card overlay is actually rendered (or gives up). */
  waitForCard: () => Promise<void>;
  markRead: (notificationId: string) => Promise<void>;
  replace: (to: { path: string; query: DeepLinkQuery }) => Promise<void> | void;
  /** Notification ids this page already consumed; mutated in place. */
  handled: Set<string>;
};

export type DeepLinkOutcome =
  /** Nothing in the URL for us. */
  | "none"
  /** `?card=` was rewritten to `/card/<id>`; the handler runs again on the new URL. */
  | "redirected"
  /** This `n` was consumed by an earlier pass. */
  | "already-handled"
  /** The notification was marked read and `n` stripped from the URL. */
  | "marked"
  /** Marking read failed (offline, deleted, someone else's); `n` still stripped. */
  | "mark-failed";

/**
 * Run one pass of the deep-link handling for the current URL.
 *
 * Split into two passes on purpose: the `?card=` rewrite is a navigation, and
 * the caller re-runs this on the resulting URL rather than trying to do both
 * against a route that is mid-change.
 *
 * `n` is stripped from the URL whether or not the mark-read succeeded, so a
 * reload cannot retry it forever and the address bar stops carrying an id that
 * means nothing to anyone else. The URL is read again for that rewrite: the
 * wait above can take seconds, and a user who navigated somewhere else in the
 * meantime must not be dragged back to the link's card.
 */
export async function handleNotificationDeepLink(ctx: DeepLinkContext): Promise<DeepLinkOutcome> {
  const here = ctx.current();
  const link = parseNotificationDeepLink(here.query);

  if (link.cardId) {
    // A value that cannot be a card id is dropped rather than routed to: the
    // user stays where the link landed them (the board, for a mail link) with
    // `card` stripped, and `n` is still consumed on the next pass.
    const path = looksLikeCardId(link.cardId) ? `/card/${link.cardId}` : here.path;
    await ctx.replace({ path, query: link.cardQuery });
    return "redirected";
  }

  const id = link.notificationId;
  if (!id) return "none";
  if (ctx.handled.has(id)) return "already-handled";
  ctx.handled.add(id);

  // Wait for the thing the link promised before claiming the user has seen it.
  if (ctx.hasCard) await ctx.waitForCard();

  let outcome: DeepLinkOutcome = "marked";
  try {
    await ctx.markRead(id);
  } catch {
    // Offline, already deleted, or not this user's notification. The badge
    // reconciles itself from the server on the next refresh; there is nothing
    // useful to say to the user here.
    outcome = "mark-failed";
  }

  const now = ctx.current();
  const stillHere = parseNotificationDeepLink(now.query);
  if (stillHere.notificationId === id) {
    await ctx.replace({ path: now.path, query: stillHere.strippedQuery });
  }
  return outcome;
}
