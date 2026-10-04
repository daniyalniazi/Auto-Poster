import { describe, expect, it } from "vitest";
import { charLimit, textLength } from "./textLength";

const limits = {
  max_chars: 4096,
  max_chars_with_images: 1024,
  count_method: "utf16" as const,
  max_images: 10,
  max_image_bytes: 0,
  image_formats: [],
  requires_text: false,
};

describe("textLength", () => {
  it("counts UTF-16 units", () => {
    expect(textLength("😀", "utf16")).toBe(2);
  });
  it("counts code points", () => {
    expect(textLength("😀a", "chars")).toBe(2);
  });
  it("counts graphemes", () => {
    expect(textLength("👨‍👩‍👧é", "graphemes")).toBe(2);
  });
});

describe("charLimit", () => {
  it("uses the caption limit when images are attached", () => {
    expect(charLimit(limits, true)).toBe(1024);
    expect(charLimit(limits, false)).toBe(4096);
  });
});
