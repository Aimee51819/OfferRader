import { describe, expect, it } from "vitest";
import { describeDue, formatDateOnly, formatInstant } from "./format";
import { easternLocalToIso } from "../pages/ApplicationDetail";

describe("deadline formatting", () => {
  it("renders instants in Eastern time with the right DST abbreviation", () => {
    expect(formatInstant("2027-03-15T14:30:00Z")).toContain("10:30");
    expect(formatInstant("2027-03-15T14:30:00Z")).toContain("EDT");
    expect(formatInstant("2027-02-11T16:00:00Z")).toContain("11:00");
    expect(formatInstant("2027-02-11T16:00:00Z")).toContain("EST");
  });

  it("never adds a time to a date-only deadline", () => {
    const s = describeDue({ due_at: null, due_date: "2027-03-20", due_precision: "day" });
    expect(s).toContain("Mar 20, 2027");
    expect(s).not.toMatch(/\d{1,2}:\d{2}/);
    expect(formatDateOnly("2027-03-20")).toContain("Sat");
  });

  it("says when there is no reliable deadline", () => {
    expect(describeDue({ due_at: null, due_date: null, due_precision: "unknown" })).toBe("No reliable deadline");
  });

  it("converts an Eastern wall-clock input to UTC across DST", () => {
    expect(easternLocalToIso("2027-03-12T10:30")).toBe("2027-03-12T15:30:00.000Z");
    expect(easternLocalToIso("2027-03-15T10:30")).toBe("2027-03-15T14:30:00.000Z");
  });
});
