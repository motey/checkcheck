// ── Reminder display and form logic (chunk R4) ───────────────────────────────
//
// Everything the card editor's reminder panel needs to decide, as pure
// functions: how a due time reads, what the repeat rules are called, what the
// date and time fields are pre-filled with, what a filled-in form sends, and
// what a rejected request means. Framework-free on purpose (the E5 pattern):
// components/CheckListReminders.vue stays a thin template over this module, and
// the parts that are easy to get wrong are unit-tested in plain vitest.
//
// Three things the server contract makes explicit and this module must not
// flatten:
//
//   * **`remind_at` comes back as naive UTC**, with no `Z` and no offset
//     (`2026-08-03T09:00:00`). `new Date()` reads such a string as *local*
//     time, which would show every reminder shifted by the viewer's offset. See
//     `parseServerTime`, which is the only place a server timestamp is parsed.
//   * **The reminder's time zone is a snapshot**, taken from the account's
//     notification settings when it was created (plan decision 5). It is what a
//     recurring reminder rolls forward in, so it is worth showing when it is not
//     the zone the user is currently looking at the app from.
//   * **The form sends a local wall-clock time with its offset**
//     (`2026-08-03T09:00:00+02:00`) rather than a UTC instant, so what the user
//     typed and what the server stores cannot drift apart on a device whose
//     clock is set to a different zone than the browser reports.

/** Mirrors `REMINDER_NOTE_MAX_LENGTH` in `model/scheduled_notification.py`. */
export const NOTE_MAX_LENGTH = 200;

export const RECURRENCE_VALUES = ["none", "daily", "weekly", "monthly"] as const;
export type Recurrence = (typeof RECURRENCE_VALUES)[number];

/** The subset of `ReminderRead` this module reads. */
export type ReminderLike = {
  remind_at: string;
  recurrence: string;
  timezone?: string | null;
  note?: string | null;
};

// Wording, not enum names: `none` is a value to the API and a non-answer to a
// user, who is choosing between "just once" and "every day".
const RECURRENCE_LABELS: Record<Recurrence, string> = {
  none: "Once",
  daily: "Every day",
  weekly: "Every week",
  monthly: "Every month",
};

export function recurrenceLabel(recurrence: string): string {
  return RECURRENCE_LABELS[recurrence as Recurrence] ?? recurrence;
}

/** Items for the repeat select, in escalating order. */
export function recurrenceOptions(): { label: string; value: Recurrence }[] {
  return RECURRENCE_VALUES.map((value) => ({ label: recurrenceLabel(value), value }));
}

// ── reading a server timestamp ───────────────────────────────────────────────

const HAS_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/**
 * Parse a timestamp from the API.
 *
 * The backend stores and returns naive UTC, so a value that carries no zone
 * designator is UTC and gets one. A value that already carries one (which a
 * future endpoint may well send) is left alone rather than corrected.
 */
export function parseServerTime(value: string): Date {
  const raw = (value ?? "").trim();
  if (!raw) return new Date(NaN);
  return new Date(HAS_ZONE.test(raw) ? raw : `${raw}Z`);
}

// ── formatting a due time ────────────────────────────────────────────────────

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Local wall clock as `09:00`. 24 hours, like the digest wording elsewhere. */
function clock(when: Date): string {
  return `${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

/** Whole local calendar days from *from* to *to* (negative when in the past). */
function calendarDaysBetween(from: Date, to: Date): number {
  const a = new Date(from.getFullYear(), from.getMonth(), from.getDate()).getTime();
  const b = new Date(to.getFullYear(), to.getMonth(), to.getDate()).getTime();
  // Divide before rounding so a DST change inside the span cannot push the
  // difference to 23 or 25 hours and lose (or invent) a day.
  return Math.round((b - a) / 86_400_000);
}

export type FormatOptions = { now?: Date; locale?: string };

/**
 * When this reminder is next due, as a phrase to put in a list row.
 *
 * Relative while relative is the useful answer ("In 3 hours"), then the wall
 * clock with as much of the date as it takes to be unambiguous ("Tomorrow at
 * 09:00", "Monday at 09:00", "4 Aug 2026 at 09:00"). Everything is rendered in
 * the viewer's own zone: `zoneNote` covers the case where that is not the zone
 * the reminder repeats in.
 */
export function formatRemindAt(value: string, opts: FormatOptions = {}): string {
  const when = parseServerTime(value);
  if (Number.isNaN(when.getTime())) return "";
  const now = opts.now ?? new Date();
  const diffMs = when.getTime() - now.getTime();

  if (diffMs <= 0) return "Due now";
  if (diffMs < 60_000) return "In less than a minute";
  const minutes = Math.round(diffMs / 60_000);
  if (minutes < 60) return `In ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  // Six hours is where "in N hours" stops being easier to act on than a clock
  // time: past that, the answer a user wants is which day and what time.
  if (diffMs < 6 * 3_600_000) {
    const hours = Math.round(diffMs / 3_600_000);
    return `In ${hours} ${hours === 1 ? "hour" : "hours"}`;
  }

  const days = calendarDaysBetween(now, when);
  if (days === 0) return `Today at ${clock(when)}`;
  if (days === 1) return `Tomorrow at ${clock(when)}`;
  if (days < 7) {
    return `${when.toLocaleDateString(opts.locale, { weekday: "long" })} at ${clock(when)}`;
  }
  const date = when.toLocaleDateString(opts.locale, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  return `${date} at ${clock(when)}`;
}

/** The full date and time, for the row's `title` (what the phrase is short for). */
export function formatAbsolute(value: string, opts: FormatOptions = {}): string {
  const when = parseServerTime(value);
  if (Number.isNaN(when.getTime())) return "";
  const date = when.toLocaleDateString(opts.locale, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  return `${date}, ${clock(when)}`;
}

/** The device's own zone, or null when the engine will not say. */
export function deviceTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/**
 * A line about the reminder's own time zone, or null when there is nothing to
 * say.
 *
 * Only recurring reminders have anything to say: a one-off is a fixed instant,
 * shown in the viewer's zone, and naming a second zone next to it would be
 * noise. A repeating one keeps its wall-clock hour in the zone snapshotted at
 * creation, so a user reading the app from somewhere else needs to be told
 * which clock "every day at 09:00" follows.
 */
export function zoneNote(
  reminder: ReminderLike,
  deviceZone: string | null = deviceTimezone()
): string | null {
  if (reminder.recurrence === "none") return null;
  const zone = reminder.timezone || "UTC";
  if (deviceZone && zone === deviceZone) return null;
  return `Repeats on ${zone} time.`;
}

/** What the date and time fields mean, or null when the zone is unknown. */
export function deviceZoneHint(deviceZone: string | null = deviceTimezone()): string | null {
  return deviceZone ? `Times are in your time zone (${deviceZone}).` : null;
}

// ── the form ─────────────────────────────────────────────────────────────────

export type ReminderInput = {
  /** `YYYY-MM-DD`, straight from an `<input type="date">`. */
  date: string;
  /** `HH:MM`, straight from an `<input type="time">`. */
  time: string;
  recurrence: Recurrence;
  note: string;
};

/** The hour a reminder nobody has thought about yet should default to. */
const DEFAULT_HOUR = 9;

/**
 * What the form opens with: the next 09:00 that has not happened yet.
 *
 * Today when the morning is still ahead, tomorrow otherwise, so the pre-filled
 * value is always a time the server would accept. That matters more than the
 * choice of hour: a form that opens on a rejected value teaches the user to
 * distrust it.
 */
export function defaultReminderInput(now: Date = new Date()): ReminderInput {
  const when = new Date(now.getFullYear(), now.getMonth(), now.getDate(), DEFAULT_HOUR, 0, 0, 0);
  if (when.getTime() <= now.getTime()) when.setDate(when.getDate() + 1);
  return {
    date: `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`,
    time: `${pad(DEFAULT_HOUR)}:00`,
    recurrence: "none",
    note: "",
  };
}

/** A `YYYY-MM-DD` plus `HH:MM` pair as a local `Date`, or an invalid one. */
export function toLocalDate(date: string, time: string): Date {
  const dateParts = /^(\d{4})-(\d{2})-(\d{2})$/.exec((date ?? "").trim());
  const timeParts = /^(\d{1,2}):(\d{2})(?::\d{2})?$/.exec((time ?? "").trim());
  if (!dateParts || !timeParts) return new Date(NaN);
  const [year, month, day] = [+dateParts[1]!, +dateParts[2]!, +dateParts[3]!];
  const [hour, minute] = [+timeParts[1]!, +timeParts[2]!];
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59) {
    return new Date(NaN);
  }
  const when = new Date(year, month - 1, day, hour, minute, 0, 0);
  // `new Date(2026, 1, 30)` silently becomes 2 March; a date the calendar does
  // not have is a typo, not a reminder.
  if (when.getMonth() !== month - 1 || when.getDate() !== day) return new Date(NaN);
  return when;
}

/**
 * A local `Date` as `2026-08-03T09:00:00+02:00`.
 *
 * The offset is what makes the wall clock the user typed survive the trip: the
 * server converts it to UTC itself, and a value with no offset would be read
 * there as UTC (`_as_naive_utc` in `routes_reminder.py`).
 */
export function localIsoWithOffset(when: Date): string {
  const offsetMinutes = -when.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMinutes);
  const date = `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`;
  const clockPart = `${pad(when.getHours())}:${pad(when.getMinutes())}:00`;
  return `${date}T${clockPart}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/**
 * What is wrong with the form, or null when nothing is.
 *
 * Deliberately the same wording the API uses for the past-time case: the
 * browser check exists to answer instantly, not to say something different from
 * what the server would have said if it had been asked.
 */
export function validateReminderInput(
  input: Pick<ReminderInput, "date" | "time" | "note">,
  now: Date = new Date()
): string | null {
  if (!input.date?.trim()) return "Pick a date.";
  if (!input.time?.trim()) return "Pick a time.";
  const when = toLocalDate(input.date, input.time);
  if (Number.isNaN(when.getTime())) return "That is not a date and time this calendar has.";
  if (when.getTime() <= now.getTime()) {
    return "That time has already passed. Pick a time in the future.";
  }
  if ((input.note ?? "").length > NOTE_MAX_LENGTH) {
    return `A note can be at most ${NOTE_MAX_LENGTH} characters.`;
  }
  return null;
}

export type ReminderCreateBody = {
  remind_at: string;
  recurrence: Recurrence;
  note: string | null;
  timezone: string | null;
};

/**
 * The POST body for a valid form.
 *
 * The device zone always goes along, and the server uses it only for an account
 * that has never chosen one in its notification settings (plan decision 5). An
 * empty note is sent as null rather than as "", so a reminder with nothing
 * written on it looks the same however it was created.
 */
export function reminderCreateBody(
  input: ReminderInput,
  timezone: string | null = deviceTimezone()
): ReminderCreateBody {
  return {
    remind_at: localIsoWithOffset(toLocalDate(input.date, input.time)),
    recurrence: input.recurrence,
    note: input.note?.trim() ? input.note.trim() : null,
    timezone,
  };
}

/**
 * Wording for a rejected write.
 *
 * The server's own `detail` is preferred wherever it exists, because it is the
 * only thing that knows which limit was hit or that an administrator switched
 * the feature off; the fallbacks are for a failure that carries no body at all.
 */
export function reminderErrorMessage(
  status: number | undefined,
  detail?: string | null
): string {
  if (detail) return detail;
  if (status === 400) return "That time has already passed. Pick a time in the future.";
  if (status === 403) return "You can no longer set reminders on this card.";
  if (status === 404) return "That reminder is gone already.";
  if (status === 409) return "You have too many reminders. Remove one first.";
  return "Could not save the reminder.";
}

/** Soonest first, the order every reminder list is shown in. */
export function sortByRemindAt<T extends { remind_at: string }>(rows: T[]): T[] {
  return [...rows].sort(
    (a, b) => parseServerTime(a.remind_at).getTime() - parseServerTime(b.remind_at).getTime()
  );
}
