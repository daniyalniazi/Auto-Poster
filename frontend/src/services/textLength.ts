import type { PlatformLimits } from "./api";

function graphemeCount(text: string): number {
  const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  let count = 0;
  for (const _ of segmenter.segment(text)) count++;
  return count;
}

// Mirrors the backend's text_length() and connector count_text() so the counter matches validation.
export function textLength(text: string, method: PlatformLimits["count_method"]): number {
  if (method === "utf16") return text.length;
  if (method === "graphemes") return graphemeCount(text);
  if (method === "mastodon") {
    // Mastodon counts every link as 23 characters and @user@server mentions as @user.
    const countable = text
      .replace(/https?:\/\/\S+/gi, "x".repeat(23))
      .replace(/(@[a-zA-Z0-9_]+)@[a-zA-Z0-9.-]+[a-zA-Z0-9]/g, "$1");
    return graphemeCount(countable);
  }
  return Array.from(text).length;
}

export function charLimit(limits: PlatformLimits, hasImages: boolean): number {
  if (hasImages && limits.max_chars_with_images != null) return limits.max_chars_with_images;
  return limits.max_chars;
}
