// ── "Send this link to someone without an account" (chunk E6) ────────────────
//
// The display logic behind the invitation field inside the public-link block.
// Framework-free, like utils/notificationSettings.ts, so the part that is worth
// getting right is unit-testable in plain vitest and the component stays a thin
// template over it.
//
// **What this is designed against.** There is one mistake people reliably make
// with a field like this, and it is the reason the wording below is so insistent:
// someone wants to share with a *colleague who has an account*, thinks "I know
// their email", types it here, and hands out an anonymous capability link when
// they meant a per-user grant. Anyone holding that link is then in, without
// signing in, and revoking it revokes everybody.
//
// Four things push back on that, and none of them is decoration:
//
//   1. **Placement.** The field lives inside the public-link block and only once
//      a link exists, so it can never be somebody's first move. That is the
//      component's job, not this module's.
//   2. **Audience wording.** "People outside CheckCheck", "someone without an
//      account". Never "invite by email", which describes the mechanism and lets
//      a reader fill in the wrong purpose.
//   3. **The consequence lines** (`AUDIENCE_COMPARISON`), one per option, side
//      by side, because this is the difference people actually get wrong.
//   4. **The soft hint** (`internalDomainHint`), driven by the operator's own
//      `SHARING_INTERNAL_EMAIL_DOMAINS`. Never a block: a colleague's private
//      address, or a device they are not signed in on, is a real case.
//
// **What this deliberately does not do**: ask the server whether an address has
// an account. That would hand every account holder an oracle for "is this person
// a user here", which is exactly what the user search avoids by never matching
// on email. The domain list catches the real-world case (a colleague on the
// company domain) and discloses nothing the operator did not declare.

/** Shape of `GET /api/sharing/public-link-email-options`. */
export type PublicLinkEmailOptions = {
  enabled: boolean;
  internal_email_domains: string[];
  max_message_length: number;
  max_per_hour: number;
};

/** What the two ways of sharing actually mean, one line each. */
export const AUDIENCE_COMPARISON = {
  collaborator: {
    title: "Someone with an account",
    line: "The list appears on their board, tied to their account. You can change or revoke their access on its own.",
  },
  link: {
    title: "Someone without an account",
    line: "Anyone holding the link is in without signing in. Revoking the link revokes it for everybody who has it.",
  },
} as const;

/**
 * A deliberately forgiving check: enough to catch a typo before a message is
 * queued, not a second implementation of the server's validator (which is the
 * one that decides).
 */
export function looksLikeEmail(address: string): boolean {
  const value = (address ?? "").trim();
  if (!value || value.length > 254 || /\s/.test(value)) return false;
  const parts = value.split("@");
  if (parts.length !== 2) return false;
  const [local, domain] = parts;
  return Boolean(local) && domain.includes(".") && !domain.startsWith(".") && !domain.endsWith(".");
}

/** The domain part of an address, lower-cased, or "" when there is not one. */
export function emailDomain(address: string): string {
  const at = (address ?? "").trim().lastIndexOf("@");
  return at === -1 ? "" : address.trim().slice(at + 1).toLowerCase();
}

/**
 * Whether *address* is on one of the operator's declared domains.
 *
 * Subdomains count (`alice@mail.example.com` matches `example.com`), because an
 * organisation that declares its domain means its mail, not one exact host. An
 * empty list means the hint never fires, which is the default.
 */
export function isInternalAddress(address: string, domains: string[] | undefined | null): boolean {
  const domain = emailDomain(address);
  if (!domain) return false;
  return (domains ?? []).some((raw) => {
    const declared = (raw ?? "").trim().toLowerCase().replace(/^@/, "");
    if (!declared) return false;
    return domain === declared || domain.endsWith(`.${declared}`);
  });
}

export type InternalHint = {
  title: string;
  body: string;
  /** Pre-fills the collaborator search when the user takes the primary action. */
  searchTerm: string;
};

/**
 * The callout for an address that looks like a colleague's, or null.
 *
 * `searchTerm` is the address's **local part**, not the whole address: the user
 * search matches names, never email (deliberately, so it cannot be used to test
 * whether an address has an account), so carrying the full address over would
 * put a term in the box that can never match. The local part is usually close
 * enough to a username or a display name to find the person in one keystroke.
 */
export function internalDomainHint(
  address: string,
  domains: string[] | undefined | null
): InternalHint | null {
  if (!looksLikeEmail(address) || !isInternalAddress(address, domains)) return null;
  const local = address.trim().split("@")[0]!;
  return {
    title: "That address looks like a colleague's",
    body: "If they have an account here, add them as a collaborator instead: the list lands on their own board and you can revoke just them.",
    searchTerm: local,
  };
}

/**
 * How a link is named where one has to be picked out of several: its own name
 * first, then the two properties that decide what sending it actually hands
 * over. The creation date used to stand in for the name here, which told the
 * owner nothing about which capability they were about to mail.
 *
 * The passphrase note is part of the label rather than a detail: a recipient who
 * does not know the passphrase cannot open the link, and this is the moment to
 * notice that.
 */
export function linkLabel(link: {
  name: string;
  permission: string;
  password_protected: boolean;
}): string {
  const parts = [(link.name ?? "").trim() || "Link", link.permission];
  if (link.password_protected) parts.push("passphrase");
  return parts.join(" · ");
}

/**
 * What the confirm step says, in the words the recipient will be able to act in.
 *
 * Matches `notify/invitation.permission_sentence` on the server, so the promise
 * made in the dialog is the sentence that arrives in the message.
 */
export function permissionSentence(permission: string): string {
  if (permission === "edit") return "add, change and tick off items on this list, without signing in";
  if (permission === "check") return "tick items off this list, without signing in";
  return "read this list, without signing in";
}

/** The whole confirm line: what happens, to whom, at what level. */
export function confirmSentence(address: string, permission: string): string {
  return `${address.trim()} will be able to ${permissionSentence(permission)}.`;
}

export type SendValidation = { ok: boolean; error: string | null };

/** Everything the Send button checks before it is worth calling the server. */
export function validateSend(
  address: string,
  message: string,
  options: PublicLinkEmailOptions | null
): SendValidation {
  if (!looksLikeEmail(address)) {
    return { ok: false, error: "That does not look like an email address." };
  }
  const max = options?.max_message_length ?? 0;
  if (max > 0 && message.trim().length > max) {
    return { ok: false, error: `Keep your message to ${max} characters or fewer.` };
  }
  return { ok: true, error: null };
}

/**
 * Wording for a failed send. The server never repeats the address in an error,
 * and neither does this: the same reasoning applies to what ends up in a browser
 * console or a screenshot.
 */
export function sendErrorMessage(status: number | undefined): string {
  if (status === 429) {
    return "You have sent as many links as this server allows in an hour. Try again later.";
  }
  if (status === 409) {
    return "That link cannot be sent: it is switched off or expired, or this server does not send email.";
  }
  if (status === 404) return "That link no longer exists. Create a new one and try again.";
  if (status === 403) return "Only the owner of this list can send its links.";
  if (status === 400) return "That does not look like an email address.";
  return "Could not send the link.";
}
