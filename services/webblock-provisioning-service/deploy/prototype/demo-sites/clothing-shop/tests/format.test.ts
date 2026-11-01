import { describe, expect, it } from "vitest";

import { displayAttributes, formatOpeningRange, formatPrice, humanizeKey } from "@/lib/format";

describe("formatPrice", () => {
  it("formats NUMERIC(12,2) strings as US dollars", () => {
    expect(formatPrice("28.00")).toBe("$28.00");
    expect(formatPrice("245.5")).toBe("$245.50");
    expect(formatPrice("1234.99")).toBe("$1,234.99");
    expect(formatPrice("9999999999.99")).toBe("$9,999,999,999.99");
  });

  it("shows a zero price as a real price", () => {
    expect(formatPrice("0.00")).toBe("$0.00");
    expect(formatPrice("0")).toBe("$0.00");
  });

  it("returns null when there is no price", () => {
    expect(formatPrice(null)).toBeNull();
    expect(formatPrice(undefined)).toBeNull();
  });

  it("returns null for values that are not a catalog price", () => {
    for (const bad of ["", "abc", "-1.00", "1.999", "1e3", "NaN", "12,00", "$5"]) {
      expect(formatPrice(bad), bad).toBeNull();
    }
  });
});

describe("displayAttributes", () => {
  it("orders known keys first, labels them, and keeps lists as options", () => {
    const rows = displayAttributes({
      care: "Hand wash",
      material: "Merino",
      zipper: "YKK",
      color: ["Oatmeal", "Navy"],
      size: ["S", "M", "L"],
    });
    expect(rows.map((r) => r.label)).toEqual(["Sizes", "Colors", "Material", "Care", "Zipper"]);
    expect(rows[0]).toEqual({ key: "size", label: "Sizes", values: ["S", "M", "L"], isList: true });
    expect(rows[2]).toEqual({ key: "material", label: "Material", values: ["Merino"], isList: false });
  });

  it("renders numbers and booleans and drops values it cannot show", () => {
    const rows = displayAttributes({ weight_g: 220, lined: true, nested: { a: 1 }, empty: [], blank: " " });
    expect(rows).toEqual([
      { key: "lined", label: "Lined", values: ["Yes"], isList: false },
      { key: "weight_g", label: "Weight g", values: ["220"], isList: false },
    ]);
  });

  it("returns nothing for a value that is not an object", () => {
    expect(displayAttributes(null)).toEqual([]);
    expect(displayAttributes([1, 2])).toEqual([]);
    expect(displayAttributes("size")).toEqual([]);
  });
});

describe("humanizeKey and formatOpeningRange", () => {
  it("humanizes attribute keys", () => {
    expect(humanizeKey("care_instructions")).toBe("Care instructions");
    expect(humanizeKey("fit")).toBe("Fit");
  });

  it("formats opening-hours ranges", () => {
    expect(
      formatOpeningRange({ from_day: "monday", to_day: "friday", opens_at: "10:00", closes_at: "19:00", closes_next_day: false }),
    ).toBe("Mon–Fri 10:00–19:00");
    expect(
      formatOpeningRange({ from_day: "saturday", to_day: "saturday", opens_at: "20:00", closes_at: "02:00", closes_next_day: true }),
    ).toBe("Sat 20:00–02:00 (next day)");
  });
});
