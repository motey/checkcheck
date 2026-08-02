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
export const VISIBLE_CHANNELS = ["in_app", "email", "webhook"] as const;
export type VisibleChannel = (typeof VISIBLE_CHANNELS)[number];

/**
 * Which channel columns to show, given what the instance can actually do.
 *
 * `in_app` is always there: the bell is the base feature and has no master
 * switch. The other two follow the flags on the settings response itself rather
 * than the public-config copy, because those are what decided whether the
 * entries in the matrix being rendered are locked.
 */
export function visibleChannels(flags: {
  email_enabled?: boolean;
  webhook_enabled?: boolean;
}): VisibleChannel[] {
  const channels: VisibleChannel[] = ["in_app"];
  if (flags.email_enabled) channels.push("email");
  if (flags.webhook_enabled) channels.push("webhook");
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
};

export function channelWording(channel: string): { title: string; description: string } {
  return CHANNEL_WORDING[channel] ?? { title: humanize(channel), description: "" };
}

const TYPE_WORDING: Record<string, { title: string; description: string }> = {
  card_shared: {
    title: "A card is shared with me",
    description: "Someone gives you access to one of their cards.",
  },
  card_invited: {
    title: "I am invited to a card",
    description: "Someone invites you to a card and you decide whether to accept.",
  },
  public_link_opened: {
    title: "One of my links is opened",
    description: "The first time somebody opens a link you shared.",
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
    hint: cell.locked
      ? cell.locked_reason || "Your administrator decided this."
      : inheriting
      ? `Following the server default (${modeLabel(cell.default_mode)}).`
      : null,
  };
}

export type CellView = CellDisplay & { channel: string; title: string; description: string };
export type TypeRow = {
  type: string;
  title: string;
  description: string;
  cells: CellView[];
};

/**
 * The whole dialog body: one row per notification type, one cell per channel
 * that is shown at all.
 *
 * *channels* defaults to in-app plus email; the caller drops `email` on an
 * instance without mail, where there is nothing to configure and every email
 * entry is locked off anyway.
 */
export function typeRows(
  types: TypeCells[] | undefined | null,
  channels: readonly string[] = VISIBLE_CHANNELS
): TypeRow[] {
  return (types ?? []).map((entry) => {
    const wording = typeWording(entry.type);
    return {
      type: entry.type,
      title: wording.title,
      description: wording.description,
      cells: channels
        .filter((channel) => entry.channels?.[channel])
        .map((channel) => {
          const cellWording = channelWording(channel);
          return {
            channel,
            title: cellWording.title,
            description: cellWording.description,
            ...cellDisplay(entry.channels[channel]!),
          };
        }),
    };
  });
}

/**
 * The PUT body for one changed cell. `INHERIT_VALUE` becomes `null`, which is
 * how the API says "drop my override for this entry"; everything else is sent
 * as the mode itself. The patch names exactly one cell, so nothing the user did
 * not touch can be rewritten by a stale copy of the matrix.
 */
export function prefsPatch(
  type: string,
  channel: string,
  choice: ModeChoice
): { prefs: Record<string, Record<string, string | null>> } {
  return {
    prefs: { [type]: { [channel]: choice === INHERIT_VALUE ? null : choice } },
  };
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
