// Note-only card layout decisions (issue #22): which card counts as note-only,
// which clamp its board preview gets, when the "checked items" section shows and
// what the phone's reminder bell says.
import { describe, it, expect } from "vitest";
import {
  isNoteOnlyCard,
  previewNotesClampClass,
  reminderBadgeText,
  reminderBellLabel,
  showCheckedSection,
} from "@/utils/cardLayout";

describe("isNoteOnlyCard", () => {
  it("is a card with no items at all", () => {
    expect(isNoteOnlyCard(0)).toBe(true);
  });

  it("is not a card whose items are not known yet", () => {
    expect(isNoteOnlyCard(undefined)).toBe(false);
    expect(isNoteOnlyCard(null)).toBe(false);
  });

  it("is not a card with any item, checked or not", () => {
    expect(isNoteOnlyCard(1)).toBe(false);
    expect(isNoteOnlyCard(25)).toBe(false);
  });
});

describe("previewNotesClampClass", () => {
  it("gives a note-only card the long clamp", () => {
    expect(previewNotesClampClass(0).split(" ")).toEqual(["md-clamp", "md-clamp-long"]);
  });

  it("keeps the short clamp on a card with items, or with items not known yet", () => {
    expect(previewNotesClampClass(3)).toBe("md-clamp");
    expect(previewNotesClampClass(null)).toBe("md-clamp");
  });
});

describe("showCheckedSection", () => {
  it("hides the section while nothing is checked", () => {
    expect(showCheckedSection(0)).toBe(false);
    expect(showCheckedSection(undefined)).toBe(false);
  });

  it("shows it from the first checked item on", () => {
    expect(showCheckedSection(1)).toBe(true);
    expect(showCheckedSection(12)).toBe(true);
  });
});

describe("reminder bell", () => {
  it("shows no badge without reminders", () => {
    expect(reminderBadgeText(0)).toBeNull();
  });

  it("shows the count, capped at 9+", () => {
    expect(reminderBadgeText(1)).toBe("1");
    expect(reminderBadgeText(9)).toBe("9");
    expect(reminderBadgeText(10)).toBe("9+");
  });

  it("has an accessible name that carries the count", () => {
    expect(reminderBellLabel(0)).toBe("Reminders, none set");
    expect(reminderBellLabel(1)).toBe("Reminders, 1 set");
    expect(reminderBellLabel(3)).toBe("Reminders, 3 set");
  });
});
