// ── Notification-settings display logic (chunk E5) ───────────────────────────
//
// Turns the server's preference matrix (GET /api/user/me/notification-settings)
// into the rows, selects and hints the dialog renders, and turns a change back
// into the partial PUT body. Framework-free on purpose: components/NotificationSettingsModal.vue
// stays a thin template over these functions, and the interesting part (what a
// cell shows, and what a change sends) is unit-testable in plain vitest.
//
// Two things the server contract makes explicit and this module must not
// flatten:
//
//   * `user_choice: null` means *inheriting*, which is not the same as having
//     picked the value that happens to be today's default: the two behave
//     differently the moment an administrator changes `NOTIFY_DEFAULT_MODES`.
//     The select therefore carries an explicit "Default (…)" entry, and picking
//     it sends `null` (which drops the override server-side).
//   * `locked` means the entry is not the user's to decide. The effective mode
//     is then whatever the administrator's cap produced (always `off` today),
//     regardless of any override the user saved earlier, so a locked cell shows
//     `mode`, not `user_choice`.
//   * `allowed_modes` is a property of the *cell*, not of the channel. Some
//     types narrow what their channel would otherwise accept (`reminder_due`
//     offers only `off` and `immediate` on email, R3 / plan decision 8), and
//     `mode_restriction_reason` says why. That reason is distinct from
//     `locked_reason`: a lock is something an administrator did and could undo,
//     a restriction is a property of the notification type.

export type NotificationModeValue = "off" | "immediate" | "hourly" | "daily";

/** The select value that means "no override, follow the instance default". */
export const INHERIT_VALUE = "__default__";

export type ModeChoice = NotificationModeValue | typeof INHERIT_VALUE;

/** One cell of the matrix, structurally the generated `NotificationChannelSetting`. */
export type ChannelCell = {
  mode: NotificationModeValue;
  user_choice?: NotificationModeValue | null;
  default_mode: NotificationModeValue;
  locked: boolean;
  locked_reason?: string | null;
  allowed_modes: NotificationModeValue[];
  mode_restriction_reason?: string | null;
};

export type TypeCells = {
  type: string;
  channels: Record<string, ChannelCell>;
};

// Wording, not enum names: "immediate" is a delivery policy to us and jargon to
// a user. The email channel is the only one that can honour hourly/daily.
const MODE_LABELS: Record<NotificationModeValue, string> = {
  off: "Off",
  immediate: "As it happens",
  hourly: "Hourly summary",
  daily: "Daily summary",
};

export function modeLabel(mode: string): string {
  return MODE_LABELS[mode as NotificationModeValue] ?? mode;
}

// Every channel the dialog can render. The caller drops the ones this instance
// cannot deliver on (see `visibleChannels`), where every entry is locked off
// anyway and a control would only be something to explain.
export const VISIBLE_CHANNELS = ["in_app", "email", "webhook", "push"] as const;
export type VisibleChannel = (typeof VISIBLE_CHANNELS)[number];

/**
 * Which channel columns to show, given what the instance can actually do.
 *
 * `in_app` is always there: the bell is the base feature and has no master
 * switch. The other three follow the flags on the settings response itself
 * rather than the public-config copy, because those are what decided whether
 * the entries in the matrix being rendered are locked.
 */
export function visibleChannels(flags: {
  email_enabled?: boolean;
  webhook_enabled?: boolean;
  push_enabled?: boolean;
}): VisibleChannel[] {
  const channels: VisibleChannel[] = ["in_app"];
  if (flags.email_enabled) channels.push("email");
  if (flags.webhook_enabled) channels.push("webhook");
  if (flags.push_enabled) channels.push("push");
  return channels;
}

const CHANNEL_WORDING: Record<string, { title: string; description: string }> = {
  in_app: {
    title: "In the app",
    description: "The bell in the navigation bar.",
  },
  email: {
    title: "Email",
    description: "Sent to your account's address.",
  },
  webhook: {
    title: "Webhook",
    description: "POSTed to a URL of your own.",
  },
  push: {
    title: "Push",
    description: "A notification on a device you've subscribed.",
  },
};

export function channelWording(channel: string): { title: string; description: string } {
  return CHANNEL_WORDING[channel] ?? { title: humanize(channel), description: "" };
}

const TYPE_WORDING: Record<string, { title: string; description: string }> = {
  card_shared: {
    // The merged share row (chunk K2). Worded to cover both share policies
    // without naming either: which of the two types actually fires is
    // `SHARING_REQUIRE_INVITE_ACCEPT`, an instance-wide decision the user did
    // not make and cannot change, so spelling it out here would explain the
    // server's configuration rather than the notification.
    title: "A card is shared with me",
    description: "Someone gives you access to one of their cards, or invites you to one.",
  },
  card_invited: {
    title: "I am invited to a card",
    description: "Someone invites you to a card and you decide whether to accept.",
  },
  public_link_opened: {
    title: "One of my links is opened",
    description: "The first time somebody opens a link you shared.",
  },
  reminder_due: {
    title: "A reminder I set comes due",
    description: "The reminders you set on a card, at the time you picked. Nobody else is told.",
  },
};

export function typeWording(type: string): { title: string; description: string } {
  return TYPE_WORDING[type] ?? { title: humanize(type), description: "" };
}

/** `public_link_opened` -> `Public link opened`, for a type this build predates. */
function humanize(value: string): string {
  const spaced = value.replace(/_/g, " ").trim();
  return spaced ? spaced[0]!.toUpperCase() + spaced.slice(1) : value;
}

export type ModeOption = { label: string; value: ModeChoice };

/**
 * The select's options: the inherit entry first (naming what it resolves to),
 * then every mode this channel accepts.
 */
export function modeOptions(cell: ChannelCell): ModeOption[] {
  return [
    { label: `Default (${modeLabel(cell.default_mode)})`, value: INHERIT_VALUE },
    ...(cell.allowed_modes ?? []).map((mode) => ({ label: modeLabel(mode), value: mode })),
  ];
}

export type CellDisplay = {
  /** What the select shows. */
  value: ModeChoice;
  options: ModeOption[];
  disabled: boolean;
  /** True while the user has no override here (the "Default (…)" entry). */
  inheriting: boolean;
  /** The mode actually in force, already worded. */
  effectiveLabel: string;
  /** One line under the control, or null when there is nothing to explain. */
  hint: string | null;
};

/**
 * What this cell has to explain, or null when it explains itself.
 *
 * A locked entry says only that, because the administrator's decision is the
 * whole story there. Otherwise the one thing left worth saying is why this type
 * offers fewer modes than its channel would; that wording comes from the server
 * rather than being hardcoded here, so a rule added later needs no frontend
 * release.
 *
 * An inherited cell used to carry "Following the server default (Off)." as well.
 * It was the most repeated line in the dialog (every untouched cell of every
 * type had one) and it said nothing the control did not: the select's own value
 * for that state is labelled `Default (Off)`. Dropped in S2 rather than
 * re-typeset into the grid.
 */
function cellHint(cell: ChannelCell): string | null {
  if (cell.locked) return cell.locked_reason || "Your administrator decided this.";
  return cell.mode_restriction_reason || null;
}

export function cellDisplay(cell: ChannelCell): CellDisplay {
  const inheriting = cell.user_choice == null;
  // A locked cell shows what the administrator's cap produced, not the override
  // underneath it: that override is real (it comes back the day the cap is
  // lifted) but showing it here would claim something the server does not do.
  const value: ModeChoice = cell.locked
    ? cell.mode
    : inheriting
    ? INHERIT_VALUE
    : (cell.user_choice as NotificationModeValue);
  return {
    value,
    options: modeOptions(cell),
    disabled: cell.locked,
    inheriting,
    effectiveLabel: modeLabel(cell.mode),
    hint: cellHint(cell),
  };
}

export type CellView = CellDisplay & { channel: string; title: string; description: string };
export type TypeRow = {
  /** The row's identity: its `data-testid` suffix and its key. */
  type: string;
  /**
   * Every notification type this row's controls write. One entry for an ordinary
   * row, two for the merged share row (see `typeRows`), and always what
   * `prefsPatch` should be given.
   */
  types: string[];
  title: string;
  description: string;
  cells: CellView[];
};

/**
 * The two halves of "somebody gave me access to a card", which chunk K2 merges
 * into one row.
 *
 * They are one event to a user and they are mutually exclusive to the server:
 * `SHARING_REQUIRE_INVITE_ACCEPT` decides which of the two an instance emits, so
 * on any given deployment the other one can never fire and its row was dead
 * space in the dialog. First entry is the one whose identity the merged row
 * keeps.
 */
export const SHARE_TYPES = ["card_shared", "card_invited"] as const;

/**
 * One cell standing for both share types.
 *
 * The *live* type's cell is what is displayed (decision 7), since that is the
 * one whose mode is actually in force. The lock, though, is the OR of the two:
 * a cap the administrator put on the type that is currently dormant would
 * otherwise be invisible until they flipped the flag, and the control would let
 * a user pick a mode the server was never going to honour. Same for the reason
 * text, which comes from whichever half is locked.
 */
function mergeShareCells(live: ChannelCell, other: ChannelCell | undefined): ChannelCell {
  if (!other?.locked || live.locked) return live;
  return {
    ...live,
    locked: true,
    locked_reason: live.locked_reason || other.locked_reason,
    // A locked cell displays `mode` rather than the user's override, and the
    // dormant half's cap is what will apply the moment it goes live.
    mode: other.mode,
  };
}

function cellViews(cells: Record<string, ChannelCell>, channels: readonly string[]): CellView[] {
  return channels
    .filter((channel) => cells[channel])
    .map((channel) => {
      const cellWording = channelWording(channel);
      return {
        channel,
        title: cellWording.title,
        description: cellWording.description,
        ...cellDisplay(cells[channel]!),
      };
    });
}

/**
 * The whole dialog body: one row per notification type, one cell per channel
 * that is shown at all, with the two share types collapsed into a single row.
 *
 * *channels* defaults to every channel; the caller drops `email` on an instance
 * without mail, where there is nothing to configure and every email entry is
 * locked off anyway.
 *
 * *requireInviteAccept* is the instance's `SHARING_REQUIRE_INVITE_ACCEPT`, which
 * picks which half of the merged share row is the live one. Left out (the client
 * has not loaded the public config yet) it reads as false, which is the default
 * share policy.
 */
export function typeRows(
  types: TypeCells[] | undefined | null,
  channels: readonly string[] = VISIBLE_CHANNELS,
  requireInviteAccept: boolean | null | undefined = false
): TypeRow[] {
  const entries = types ?? [];
  const shareEntries = SHARE_TYPES.map((type) => entries.find((e) => e.type === type));
  const [shared, invited] = shareEntries;
  const live = requireInviteAccept ? invited ?? shared : shared ?? invited;
  const dormant = live === shared ? invited : shared;
  let mergedEmitted = false;

  const rows: TypeRow[] = [];
  for (const entry of entries) {
    if ((SHARE_TYPES as readonly string[]).includes(entry.type)) {
      if (mergedEmitted || !live) continue;
      mergedEmitted = true;
      // Keeps `card_shared`'s identity whenever that type exists at all, so the
      // testids and the saved-cell keys the dialog and its specs use survive the
      // merge.
      const wording = typeWording(shared ? SHARE_TYPES[0] : live.type);
      const merged: Record<string, ChannelCell> = {};
      for (const channel of Object.keys(live.channels ?? {})) {
        merged[channel] = mergeShareCells(
          live.channels[channel]!,
          dormant?.channels?.[channel]
        );
      }
      rows.push({
        type: shared ? SHARE_TYPES[0] : live.type,
        // Both halves, in the order the server sent them, so one change keeps
        // them in lockstep and neither can drift behind the other.
        types: shareEntries.filter((e): e is TypeCells => !!e).map((e) => e.type),
        title: wording.title,
        description: wording.description,
        cells: cellViews(merged, channels),
      });
      continue;
    }
    const wording = typeWording(entry.type);
    rows.push({
      type: entry.type,
      types: [entry.type],
      title: wording.title,
      description: wording.description,
      cells: cellViews(entry.channels ?? {}, channels),
    });
  }
  return rows;
}

/**
 * The PUT body for one changed cell. `INHERIT_VALUE` becomes `null`, which is
 * how the API says "drop my override for this entry"; everything else is sent
 * as the mode itself. The patch names exactly the cells that changed, so nothing
 * the user did not touch can be rewritten by a stale copy of the matrix.
 *
 * *type* takes the row's whole `types` list, which is how the merged share row
 * writes both halves in one request: the API has always accepted a multi-type
 * patch, so the two can never end up disagreeing, and an operator who later
 * flips `SHARING_REQUIRE_INVITE_ACCEPT` finds the user's choice already applies
 * to the type that is now live.
 */
export function prefsPatch(
  type: string | readonly string[],
  channel: string,
  choice: ModeChoice
): { prefs: Record<string, Record<string, string | null>> } {
  const value = choice === INHERIT_VALUE ? null : choice;
  const types = typeof type === "string" ? [type] : type;
  const prefs: Record<string, Record<string, string | null>> = {};
  for (const one of types) prefs[one] = { [channel]: value };
  return { prefs };
}

// ── time zone ────────────────────────────────────────────────────────────────
//
// Only the digest modes care about it (a daily digest goes out at 08:00 in the
// user's own zone), so the picker sits with the email controls.

/** The select value that means "no zone stored", which the server reads as UTC. */
export const UTC_VALUE = "";

/** Every IANA zone this browser knows, or an empty list on an engine without it. */
export function availableTimezones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    return typeof intl.supportedValuesOf === "function" ? intl.supportedValuesOf("timeZone") : [];
  } catch {
    return [];
  }
}

/** The device's own zone, or null when the engine will not say. */
export function detectTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/**
 * Items for the time-zone picker: "UTC" first, then every zone the browser
 * knows, with the stored and the detected one folded in so neither can be
 * missing from the list that is supposed to contain them.
 */
export function timezoneItems(
  stored: string | null | undefined,
  detected: string | null | undefined = detectTimezone()
): { label: string; value: string }[] {
  const zones = new Set(availableTimezones());
  if (stored) zones.add(stored);
  if (detected) zones.add(detected);
  zones.delete("UTC");
  return [
    { label: "UTC", value: UTC_VALUE },
    ...[...zones].sort().map((zone) => ({ label: zone, value: zone })),
  ];
}

/** Select value -> what the PUT sends (`null` clears the stored zone). */
export function timezonePatchValue(value: string): string | null {
  return value === UTC_VALUE ? null : value;
}

/**
 * Stored zone -> the value the picker should show.
 *
 * A stored literal `"UTC"` and a stored `null` are the same instant, and
 * `timezoneItems` offers only one entry for them (the `UTC_VALUE` one), so the
 * literal has to be folded onto it or the select shows nothing at all. A row
 * really can hold `"UTC"`: the API accepts it, and the login-time re-sync
 * (`utils/timezoneSync.ts`) writes it over a previously stored zone when the
 * device is on UTC.
 */
export function timezoneSelectValue(stored: string | null | undefined): string {
  return !stored || stored === "UTC" ? UTC_VALUE : stored;
}

// ── webhook target (chunk E6) ────────────────────────────────────────────────
//
// The same shallow check the server does on the way in. What actually matters,
// refusing a URL that resolves into the server's own network, cannot be decided
// here and is not attempted: it happens when the request is made, because a host
// name's address can change in between.

/** Whether *url* is worth sending to the server at all. */
export function looksLikeWebhookUrl(url: string): boolean {
  const value = (url ?? "").trim();
  if (!value || value.length > 2048) return false;
  return /^https?:\/\/[^\s/]+/i.test(value);
}

/** Select value -> what the PUT sends (`null` clears the stored URL). */
export function webhookUrlPatchValue(url: string): string | null {
  return url.trim() ? url.trim() : null;
}

/** Wording for a failed "send test webhook". */
export function testWebhookMessage(status: number | undefined, url?: string): string {
  if (status === undefined) {
    return `Queued a request to ${url}. If it does not arrive, check the server log: a URL pointing into a private network is refused there, not here.`;
  }
  if (status === 409) {
    return "There is nowhere to send it: save a webhook URL first, or this server does not send webhooks.";
  }
  if (status === 429) {
    return "A test webhook was queued less than a minute ago. Try again shortly.";
  }
  return "Could not queue the test webhook.";
}
