import { describe, it, expect } from "vitest";
import {
  NOTE_MAX_LENGTH,
  defaultReminderInput,
  formatAbsolute,
  formatRemindAt,
  localIsoWithOffset,
  parseServerTime,
  recurrenceLabel,
  recurrenceOptions,
  reminderCreateBody,
  reminderErrorMessage,
  sortByRemindAt,
  toLocalDate,
  validateReminderInput,
  zoneNote,
} from "@/utils/reminders";

// Display and form logic behind the card editor's reminder panel (chunk R4).
// The panel is a template over these functions, so this is where the two things
// that are easy to get wrong are pinned down: a naive-UTC timestamp is UTC (not
// local), and the form sends the wall clock the user typed with its offset.
//
// Every case here is built from a *local* Date and converted, so the suite says
// the same thing in every time zone a contributor happens to run it in.

/** A local Date as the naive-UTC string the API returns. */
function serverStamp(when: Date): string {
  return when.toISOString().slice(0, 19);
}

/** A local Date *offset* minutes into the future from *now*. */
function inMinutes(now: Date, minutes: number): Date {
  return new Date(now.getTime() + minutes * 60_000);
}

describe("parseServerTime", () => {
  it("reads a timestamp without a zone as UTC, not as local time", () => {
    // The trap this module exists for: `new Date("2026-08-03T09:00:00")` is
    // local, so on any device east or west of Greenwich every reminder would be
    // shown at the wrong hour.
    expect(parseServerTime("2026-08-03T09:00:00").toISOString()).toBe("2026-08-03T09:00:00.000Z");
  });

  it("leaves a timestamp that carries its own zone alone", () => {
    expect(parseServerTime("2026-08-03T09:00:00Z").toISOString()).toBe("2026-08-03T09:00:00.000Z");
    expect(parseServerTime("2026-08-03T11:00:00+02:00").toISOString()).toBe(
      "2026-08-03T09:00:00.000Z"
    );
  });

  it("is invalid for an empty value rather than being the epoch", () => {
    expect(Number.isNaN(parseServerTime("").getTime())).toBe(true);
  });
});

describe("formatRemindAt", () => {
  const now = new Date(2026, 7, 3, 8, 0, 0); // a Monday morning, local

  function phrase(when: Date): string {
    return formatRemindAt(serverStamp(when), { now, locale: "en-GB" });
  }

  it("counts in minutes within the hour", () => {
    expect(phrase(inMinutes(now, 1))).toBe("In 1 minute");
    expect(phrase(inMinutes(now, 45))).toBe("In 45 minutes");
  });

  it("counts in hours up to six, which is the plan's 'in 3 hours'", () => {
    expect(phrase(inMinutes(now, 180))).toBe("In 3 hours");
    expect(phrase(inMinutes(now, 60))).toBe("In 1 hour");
  });

  it("switches to a clock time once relative stops being actionable", () => {
    // 20:00 the same day: eight hours away, so the useful answer is the hour.
    expect(phrase(new Date(2026, 7, 3, 20, 0))).toBe("Today at 20:00");
    expect(phrase(new Date(2026, 7, 4, 9, 0))).toBe("Tomorrow at 09:00");
  });

  it("names the weekday inside the coming week and the date beyond it", () => {
    expect(phrase(new Date(2026, 7, 6, 9, 0))).toBe("Thursday at 09:00");
    // Seven days out is no longer "Monday": that would be ambiguous with today.
    expect(phrase(new Date(2026, 7, 10, 9, 0))).toBe("10 Aug 2026 at 09:00");
  });

  it("says a reminder in the past is due now rather than counting backwards", () => {
    expect(phrase(inMinutes(now, -120))).toBe("Due now");
    expect(phrase(inMinutes(now, 0.5))).toBe("In less than a minute");
  });

  it("returns an empty string for a value it cannot read", () => {
    expect(formatRemindAt("not a date", { now })).toBe("");
  });
});

describe("formatAbsolute", () => {
  it("spells out the whole thing for the row's title attribute", () => {
    const absolute = formatAbsolute(serverStamp(new Date(2026, 7, 4, 9, 30)), {
      locale: "en-GB",
    });
    expect(absolute).toContain("4 Aug 2026");
    expect(absolute).toContain("09:30");
  });
});

describe("zoneNote", () => {
  it("says nothing about a one-off, which is a fixed instant", () => {
    expect(
      zoneNote({ remind_at: "2026-08-03T09:00:00", recurrence: "none", timezone: "Asia/Tokyo" }, "Europe/Berlin")
    ).toBeNull();
  });

  it("names the zone a repeat follows when it is not the viewer's", () => {
    expect(
      zoneNote({ remind_at: "2026-08-03T09:00:00", recurrence: "daily", timezone: "Asia/Tokyo" }, "Europe/Berlin")
    ).toBe("Repeats on Asia/Tokyo time.");
  });

  it("stays quiet when the repeat follows the zone the user is already in", () => {
    expect(
      zoneNote({ remind_at: "2026-08-03T09:00:00", recurrence: "daily", timezone: "Europe/Berlin" }, "Europe/Berlin")
    ).toBeNull();
  });

  it("treats a reminder with no stored zone as UTC, which is what the server does", () => {
    expect(
      zoneNote({ remind_at: "2026-08-03T09:00:00", recurrence: "weekly", timezone: null }, "Europe/Berlin")
    ).toBe("Repeats on UTC time.");
  });
});

describe("defaultReminderInput", () => {
  it("opens on this morning's 09:00 while it is still ahead", () => {
    const input = defaultReminderInput(new Date(2026, 7, 3, 6, 30));
    expect(input).toEqual({ date: "2026-08-03", time: "09:00", recurrence: "none", note: "" });
  });

  it("rolls to tomorrow once 09:00 has passed, so it is never a rejected value", () => {
    const now = new Date(2026, 7, 3, 9, 0, 1);
    const input = defaultReminderInput(now);
    expect(input.date).toBe("2026-08-04");
    expect(validateReminderInput(input, now)).toBeNull();
  });

  it("rolls the month and the year over", () => {
    expect(defaultReminderInput(new Date(2026, 11, 31, 20, 0)).date).toBe("2027-01-01");
  });
});

describe("toLocalDate", () => {
  it("reads the two fields as one local wall-clock time", () => {
    const when = toLocalDate("2026-08-03", "09:05");
    expect([when.getFullYear(), when.getMonth(), when.getDate()]).toEqual([2026, 7, 3]);
    expect([when.getHours(), when.getMinutes()]).toEqual([9, 5]);
  });

  it("refuses a date the calendar does not have rather than rolling it forward", () => {
    // `new Date(2026, 1, 30)` is 2 March. A typo must not become a reminder.
    expect(Number.isNaN(toLocalDate("2026-02-30", "09:00").getTime())).toBe(true);
    expect(Number.isNaN(toLocalDate("2026-13-01", "09:00").getTime())).toBe(true);
    expect(Number.isNaN(toLocalDate("2026-08-03", "25:00").getTime())).toBe(true);
    expect(Number.isNaN(toLocalDate("", "09:00").getTime())).toBe(true);
  });
});

describe("localIsoWithOffset", () => {
  it("keeps the offset, so the wall clock survives the trip to the server", () => {
    const when = new Date(2026, 7, 3, 9, 0);
    const sent = localIsoWithOffset(when);
    expect(sent).toMatch(/^2026-08-03T09:00:00[+-]\d{2}:\d{2}$/);
    // Whatever the offset is here, it has to name the same instant.
    expect(new Date(sent).getTime()).toBe(when.getTime());
  });
});

describe("validateReminderInput", () => {
  const now = new Date(2026, 7, 3, 8, 0);

  it("accepts a future time", () => {
    expect(validateReminderInput({ date: "2026-08-03", time: "09:00", note: "" }, now)).toBeNull();
  });

  it("asks for the fields it does not have", () => {
    expect(validateReminderInput({ date: "", time: "09:00", note: "" }, now)).toBe("Pick a date.");
    expect(validateReminderInput({ date: "2026-08-03", time: "", note: "" }, now)).toBe("Pick a time.");
  });

  it("says what the server would say about a time that has passed", () => {
    // Same wording as `_require_future` in routes_reminder.py: the browser check
    // is there to answer instantly, not to disagree.
    expect(validateReminderInput({ date: "2026-08-03", time: "07:00", note: "" }, now)).toBe(
      "That time has already passed. Pick a time in the future."
    );
  });

  it("holds the note to the length the column has", () => {
    const note = "x".repeat(NOTE_MAX_LENGTH + 1);
    expect(validateReminderInput({ date: "2026-08-03", time: "09:00", note }, now)).toBe(
      "A note can be at most 200 characters."
    );
    const exact = "x".repeat(NOTE_MAX_LENGTH);
    expect(validateReminderInput({ date: "2026-08-03", time: "09:00", note: exact }, now)).toBeNull();
  });
});

describe("reminderCreateBody", () => {
  it("sends the wall clock with its offset, the repeat rule and the device zone", () => {
    const body = reminderCreateBody(
      { date: "2026-08-03", time: "09:00", recurrence: "daily", note: "  Call the plumber  " },
      "Europe/Berlin"
    );
    expect(body.remind_at).toMatch(/^2026-08-03T09:00:00[+-]\d{2}:\d{2}$/);
    expect(body.recurrence).toBe("daily");
    expect(body.note).toBe("Call the plumber");
    expect(body.timezone).toBe("Europe/Berlin");
  });

  it("sends null for a note nobody wrote, not an empty string", () => {
    const body = reminderCreateBody(
      { date: "2026-08-03", time: "09:00", recurrence: "none", note: "   " },
      null
    );
    expect(body.note).toBeNull();
    expect(body.timezone).toBeNull();
  });
});

describe("reminderErrorMessage", () => {
  it("prefers the server's own detail, which is the only thing that knows the limit", () => {
    expect(
      reminderErrorMessage(409, "You already have 10 reminders on this card. Remove one first.")
    ).toBe("You already have 10 reminders on this card. Remove one first.");
  });

  it("falls back per status when the failure carries no body", () => {
    expect(reminderErrorMessage(409)).toBe("You have too many reminders. Remove one first.");
    expect(reminderErrorMessage(400)).toBe("That time has already passed. Pick a time in the future.");
    expect(reminderErrorMessage(404)).toBe("That reminder is gone already.");
    expect(reminderErrorMessage(undefined)).toBe("Could not save the reminder.");
  });
});

describe("recurrence wording and ordering", () => {
  it("words the rules the way a user chooses between them", () => {
    expect(recurrenceLabel("none")).toBe("Once");
    expect(recurrenceLabel("monthly")).toBe("Every month");
    expect(recurrenceOptions().map((o) => o.value)).toEqual(["none", "daily", "weekly", "monthly"]);
  });

  it("passes a rule this build has never heard of through unchanged", () => {
    expect(recurrenceLabel("yearly")).toBe("yearly");
  });

  it("sorts soonest first without mutating the array it was given", () => {
    const rows = [{ remind_at: "2026-08-05T09:00:00" }, { remind_at: "2026-08-03T09:00:00" }];
    expect(sortByRemindAt(rows).map((r) => r.remind_at)).toEqual([
      "2026-08-03T09:00:00",
      "2026-08-05T09:00:00",
    ]);
    expect(rows[0]!.remind_at).toBe("2026-08-05T09:00:00");
  });
});
