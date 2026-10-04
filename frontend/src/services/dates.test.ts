import { describe, expect, it } from "vitest";
import { fromLocalInput, toLocalInput } from "./dates";

describe("datetime-local helpers", () => {
  it("round-trips local time", () => {
    const date = new Date(2026, 9, 4, 15, 30);
    expect(toLocalInput(date)).toBe("2026-10-04T15:30");
    expect(fromLocalInput("2026-10-04T15:30")?.getTime()).toBe(date.getTime());
  });
  it("rejects empty values", () => {
    expect(fromLocalInput("")).toBeNull();
  });
});
