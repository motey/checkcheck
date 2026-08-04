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
  timezoneSelectValue,
  typeRows,
  typeWording,
  looksLikeWebhookUrl,
  testWebhookMessage,
  visibleChannels,
  webhookUrlPatchValue,
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

  it("explains why a type offers fewer modes than its channel", () => {
    // R3 / plan decision 8: `reminder_due` accepts only off and immediate on
    // email, and the server sends both the narrowed list and the reason. The
    // dialog renders the server's wording rather than inventing its own.
    const reason = "A reminder is sent when it is due, so it cannot go into a digest.";
    const display = cellDisplay(
      cell({
        user_choice: "immediate",
        allowed_modes: ["off", "immediate"],
        mode_restriction_reason: reason,
      })
    );
    expect(display.disabled).toBe(false);
    expect(display.hint).toBe(reason);
    expect(display.options.map((o) => o.value)).toEqual([INHERIT_VALUE, "off", "immediate"]);
  });

  it("stacks the restriction on top of the inherit line", () => {
    const display = cellDisplay(
      cell({ user_choice: null, default_mode: "immediate", mode_restriction_reason: "Because." })
    );
    expect(display.hint).toBe("Following the server default (As it happens). Because.");
  });

  it("says only the administrator's reason on a locked entry", () => {
    // A lock is something an administrator did and could undo; a restriction is
    // a property of the type. When both apply, the lock is what is in force.
    const display = cellDisplay(
      cell({
        locked: true,
        locked_reason: "This server does not send email.",
        mode_restriction_reason: "A reminder cannot go into a digest.",
      })
    );
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

  it("renders every channel it is given, in that order", () => {
    const rows = typeRows(types);
    expect(rows.map((r) => r.type)).toEqual(["card_shared", "card_invited"]);
    expect(rows[0]!.cells.map((c) => c.channel)).toEqual(["in_app", "email", "webhook"]);
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
    // `reminder_due` used to be the example here and is real wording now (R4),
    // so this uses a type that does not exist, the way the backend suite does.
    expect(typeWording("card_commented").title).toBe("Card commented");
    const rows = typeRows([{ type: "card_commented", channels: { in_app: cell() } }]);
    expect(rows[0]!.title).toBe("Card commented");
  });

  it("words the reminder type as something the user set, not as an event", () => {
    expect(typeWording("reminder_due").title).toBe("A reminder I set comes due");
    expect(typeWording("reminder_due").description).toContain("Nobody else is told");
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

  it("shows a stored literal UTC on the one UTC entry the list has", () => {
    // The list carries UTC exactly once, as `UTC_VALUE`, so a row holding the
    // literal string would otherwise select nothing and the picker would look
    // empty. A row really can hold it: the login-time re-sync writes the
    // device's zone, and a device can be on UTC.
    expect(timezoneSelectValue("UTC")).toBe(UTC_VALUE);
    expect(timezoneSelectValue(null)).toBe(UTC_VALUE);
    expect(timezoneSelectValue(undefined)).toBe(UTC_VALUE);
    expect(timezoneSelectValue("")).toBe(UTC_VALUE);
    expect(timezoneSelectValue("Europe/Berlin")).toBe("Europe/Berlin");
  });
});


describe("visibleChannels", () => {
  // A channel the instance cannot deliver on is locked off in the matrix
  // anyway, so a control for it would only be something else to explain.
  it("always keeps the bell, which has no master switch", () => {
    expect(visibleChannels({})).toEqual(["in_app"]);
    expect(visibleChannels({ email_enabled: false, webhook_enabled: false })).toEqual([
      "in_app",
    ]);
  });

  it("adds each channel the instance can actually deliver on", () => {
    expect(visibleChannels({ email_enabled: true })).toEqual(["in_app", "email"]);
    expect(visibleChannels({ webhook_enabled: true })).toEqual(["in_app", "webhook"]);
    expect(visibleChannels({ push_enabled: true })).toEqual(["in_app", "push"]);
    expect(
      visibleChannels({ email_enabled: true, webhook_enabled: true, push_enabled: true })
    ).toEqual(["in_app", "email", "webhook", "push"]);
  });
});

describe("the webhook target", () => {
  it("accepts an http(s) URL and rejects everything else", () => {
    expect(looksLikeWebhookUrl("https://example.com/hooks/x")).toBe(true);
    expect(looksLikeWebhookUrl("http://example.com")).toBe(true);
    expect(looksLikeWebhookUrl("HTTPS://EXAMPLE.COM/x")).toBe(true);
    expect(looksLikeWebhookUrl("ftp://example.com")).toBe(false);
    expect(looksLikeWebhookUrl("example.com/hook")).toBe(false);
    expect(looksLikeWebhookUrl("")).toBe(false);
    expect(looksLikeWebhookUrl(`https://example.com/${"x".repeat(3000)}`)).toBe(false);
  });

  it("does NOT try to judge where the URL points", () => {
    // Deliberate: whether a URL resolves into the server's own network can only
    // be decided at delivery time, because a host name's address can change in
    // between. Blocking it here would be a check the client cannot honour.
    expect(looksLikeWebhookUrl("http://127.0.0.1:9000/hook")).toBe(true);
    expect(looksLikeWebhookUrl("http://169.254.169.254/latest")).toBe(true);
  });

  it("sends null for an emptied field, which clears the stored URL", () => {
    expect(webhookUrlPatchValue("  ")).toBeNull();
    expect(webhookUrlPatchValue(" https://example.com/x ")).toBe("https://example.com/x");
  });

  it("explains each way a test webhook can fail", () => {
    expect(testWebhookMessage(409)).toContain("nowhere to send it");
    expect(testWebhookMessage(429)).toContain("less than a minute ago");
    expect(testWebhookMessage(500)).toBe("Could not queue the test webhook.");
    // No status means it was queued: the wording must not promise delivery.
    const queued = testWebhookMessage(undefined, "https://example.com/x");
    expect(queued).toContain("Queued");
    expect(queued).toContain("https://example.com/x");
    expect(queued).not.toContain("delivered");
  });
});
