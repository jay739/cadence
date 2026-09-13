import { describe, expect, it } from "vitest";
import { landedWhen } from "./homeTime";

// A fixed "now" so these never depend on when the suite runs.
const NOW = new Date("2026-06-15T12:00:00Z").getTime();
const daysAgo = (n: number) => new Date(NOW - n * 86_400_000).toISOString();

describe("landedWhen", () => {
  it("calls anything from the last 24 hours today", () => {
    expect(landedWhen(daysAgo(0), NOW)).toBe("today");
  });

  it("names yesterday rather than saying 1 days ago", () => {
    expect(landedWhen(daysAgo(1), NOW)).toBe("yesterday");
  });

  it("counts exact days up to a week", () => {
    expect(landedWhen(daysAgo(3), NOW)).toBe("3 days ago");
    expect(landedWhen(daysAgo(6), NOW)).toBe("6 days ago");
  });

  it("goes vague past a week, which is the whole point of the helper", () => {
    expect(landedWhen(daysAgo(7), NOW)).toBe("last week");
    expect(landedWhen(daysAgo(13), NOW)).toBe("last week");
    expect(landedWhen(daysAgo(21), NOW)).toBe("3 weeks ago");
    expect(landedWhen(daysAgo(90), NOW)).toBe("3 months ago");
  });

  it("returns an empty string for an unparseable timestamp instead of NaN text", () => {
    // The row renders this straight into the DOM, so "NaN days ago" would ship to the user.
    expect(landedWhen("not-a-date", NOW)).toBe("");
  });
});
