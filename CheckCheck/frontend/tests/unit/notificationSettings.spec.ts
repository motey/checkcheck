import { describe, it, expect } from "vitest";
import {
  INHERIT_VALUE,
  UTC_VALUE,
  cellDisplay,
  modeLabel,
  modeOptions,
  prefsPatch,
  timezoneItems,
  timezonePatchValue,
  typeRows,
  typeWording,
  type ChannelCell,
} from "@/utils/notificationSettings";

// Display logic behind the notification settings dialog (chunk E5). The dialog
// itself is a template over these functions, so this is where the two rules the
// server contract cares about are pinned down: inheriting is not the same as
// picking today's default, and a locked entry shows the administrator's answer.

function cell(overrides: Partial<ChannelCell> = {}): ChannelCell {
  return {
    mode: "immediate",
    user_choice: null,
    default_mode: "immediate",
    locked: false,
    locked_reason: null,
    allowed_modes: ["off", "immediate", "hourly", "daily"],
    ...overrides,
  };
}

describe("cellDisplay", () => {
  it("shows the inherit entry, not the mode, while the user has no override", () => {
    const display = cellDisplay(cell({ mode: "immediate", user_choice: null, default_mode: "immediate" }));
    expect(display.value).toBe(INHERIT_VALUE);
    expect(display.inheriting).toBe(true);
    expect(display.hint).toBe("Following the server default (As it happens).");
  });

  it("distinguishes an explicit choice from the identical default", () => {
    const inherited = cellDisplay(cell({ mode: "off", user_choice: null, default_mode: "off" }));
    const chosen = cellDisplay(cell({ mode: "off", user_choice: "off", default_mode: "off" }));
    // Same effective mode, different state: the day an administrator changes
    // NOTIFY_DEFAULT_MODES these two stop agreeing, so they must not render alike.
    expect(inherited.value).not.toBe(chosen.value);
    expect(chosen.value).toBe("off");
    expect(chosen.inheriting).toBe(false);
    expect(chosen.hint).toBeNull();
  });

  it("offers the inherit entry first, labelled with what it falls back to", () => {
    const options = modeOptions(cell({ default_mode: "daily" }));
    expect(options[0]).toEqual({ label: "Default (Daily summary)", value: INHERIT_VALUE });
    expect(options.slice(1).map((o) => o.value)).toEqual(["off", "immediate", "hourly", "daily"]);
  });

  it("limits the options to the modes the channel accepts", () => {
    const options = modeOptions(cell({ allowed_modes: ["off", "immediate"] }));
    expect(options.map((o) => o.value)).toEqual([INHERIT_VALUE, "off", "immediate"]);
  });

  it("renders a locked entry disabled, showing the cap rather than the override", () => {
    const display = cellDisplay(
      cell({
        mode: "off",
        // The user chose daily before the administrator switched email off. The
        // server reports the cap in `mode`; that is what is true right now.
        user_choice: "daily",
        default_mode: "immediate",
        locked: true,
        locked_reason: "This server does not send email.",
      })
    );
    expect(display.disabled).toBe(true);
    expect(display.value).toBe("off");
    expect(display.hint).toBe("This server does not send email.");
  });

  it("falls back to a generic reason when the server sends none", () => {
    const display = cellDisplay(cell({ mode: "off", locked: true, locked_reason: null }));
    expect(display.hint).toBe("Your administrator decided this.");
  });

  it("words the effective mode for display", () => {
    expect(cellDisplay(cell({ mode: "hourly" })).effectiveLabel).toBe("Hourly summary");
    expect(modeLabel("something_new")).toBe("something_new");
  });
});

describe("typeRows", () => {
  const types = [
    { type: "card_shared", channels: { in_app: cell(), email: cell(), webhook: cell() } },
    { type: "card_invited", channels: { in_app: cell(), email: cell(), webhook: cell() } },
  ];

  it("renders in-app and email only, in that order, never the webhook", () => {
    const rows = typeRows(types);
    expect(rows.map((r) => r.type)).toEqual(["card_shared", "card_invited"]);
    expect(rows[0]!.cells.map((c) => c.channel)).toEqual(["in_app", "email"]);
    expect(rows[0]!.title).toBe("A card is shared with me");
  });

  it("drops the email column on an instance without mail", () => {
    const rows = typeRows(types, ["in_app"]);
    expect(rows[0]!.cells.map((c) => c.channel)).toEqual(["in_app"]);
  });

  it("skips a channel the server did not send", () => {
    const rows = typeRows([{ type: "card_shared", channels: { in_app: cell() } }]);
    expect(rows[0]!.cells.map((c) => c.channel)).toEqual(["in_app"]);
  });

  it("humanises a notification type this build has never heard of", () => {
    // A type added server-side needs no frontend release to be configurable.
    expect(typeWording("reminder_due").title).toBe("Reminder due");
    const rows = typeRows([{ type: "reminder_due", channels: { in_app: cell() } }]);
    expect(rows[0]!.title).toBe("Reminder due");
  });

  it("tolerates a missing matrix", () => {
    expect(typeRows(null)).toEqual([]);
  });
});

describe("prefsPatch", () => {
  it("sends the mode for an explicit choice", () => {
    expect(prefsPatch("card_shared", "email", "daily")).toEqual({
      prefs: { card_shared: { email: "daily" } },
    });
  });

  it("sends null for the inherit entry, which drops the override server-side", () => {
    expect(prefsPatch("card_shared", "email", INHERIT_VALUE)).toEqual({
      prefs: { card_shared: { email: null } },
    });
  });

  it("names exactly one cell, so nothing untouched can be rewritten", () => {
    const patch = prefsPatch("card_invited", "in_app", "off");
    expect(Object.keys(patch.prefs)).toEqual(["card_invited"]);
    expect(Object.keys(patch.prefs.card_invited!)).toEqual(["in_app"]);
  });
});

describe("timezone picker", () => {
  it("puts UTC first and keeps the stored zone in the list", () => {
    const items = timezoneItems("Antarctica/Troll", "Europe/Berlin");
    expect(items[0]).toEqual({ label: "UTC", value: UTC_VALUE });
    expect(items.some((i) => i.value === "Antarctica/Troll")).toBe(true);
    expect(items.some((i) => i.value === "Europe/Berlin")).toBe(true);
    // UTC is the first entry and must not appear twice.
    expect(items.filter((i) => i.label === "UTC")).toHaveLength(1);
  });

  it("still offers the detected zone when the stored one is null", () => {
    const items = timezoneItems(null, "Pacific/Auckland");
    expect(items.some((i) => i.value === "Pacific/Auckland")).toBe(true);
  });

  it("maps the UTC entry back to a null timezone, which clears it", () => {
    expect(timezonePatchValue(UTC_VALUE)).toBeNull();
    expect(timezonePatchValue("Europe/Berlin")).toBe("Europe/Berlin");
  });
});
