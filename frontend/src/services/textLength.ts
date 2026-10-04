import type { PlatformLimits } from "./api";

// Mirrors backend/auto_poster/connectors/base.py:text_length so the counter matches validation.
export function textLength(text: string, method: PlatformLimits["count_method"]): number {
  if (method === "utf16") return text.length;
  if (method === "graphemes") {
    const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
    let count = 0;
    for (const _ of segmenter.segment(text)) count++;
    return count;
  }
  return Array.from(text).length;
}

export function charLimit(limits: PlatformLimits, hasImages: boolean): number {
  if (hasImages && limits.max_chars_with_images != null) return limits.max_chars_with_images;
  return limits.max_chars;
}
