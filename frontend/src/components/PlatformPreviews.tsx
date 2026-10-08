import { useState } from "react";
import type { Platform, Preview } from "../services/api";
import { charLimit, textLength } from "../services/textLength";

interface Props {
  platforms: Platform[]; // the selected ones, in display order
  previews: Record<string, Preview>;
  overrides: Record<string, string>;
  hasImages: boolean;
  disabled?: boolean;
  onEdit: (platformId: string, text: string) => void;
  onReset: (platformId: string) => void;
}

/** One tab per selected platform showing exactly what it will post. Editing a tab makes a
 *  custom version for that platform only; "Use automatic version" goes back to following the post. */
export default function PlatformPreviews({ platforms, previews, overrides, hasImages, disabled, onEdit, onReset }: Props) {
  const [active, setActive] = useState<string | null>(null);
  if (platforms.length === 0) return null;
  const current = platforms.find((p) => p.id === active) ?? platforms[0];

  const status = (p: Platform) => {
    const problems = previews[p.id]?.problems ?? [];
    if (problems.some((x) => x.level === "error")) return { kind: "bad", label: "Needs a fix" };
    if (p.id in overrides) return { kind: "", label: "Edited" };
    if (problems.length) return { kind: "warn", label: "Note" };
    return { kind: "ok", label: "Ready" };
  };

  const preview = previews[current.id];
  const customized = current.id in overrides;
  const value = customized ? overrides[current.id] : (preview?.text ?? "");
  const limit = charLimit(current.limits, hasImages);
  const length = textLength(value, current.limits.count_method);
  const problems = preview?.problems ?? [];
  const hasErrors = problems.some((p) => p.level === "error");

  return (
    <section className="card" aria-labelledby="preview-heading">
      <h2 id="preview-heading">How it will look</h2>
      <p className="help" style={{ marginTop: -4 }}>
        Each platform gets its own version. Edit one here to change it for that platform only.
      </p>
      <div className="tabs" role="tablist" aria-label="Platform versions">
        {platforms.map((p) => {
          const s = status(p);
          return (
            <button
              key={p.id}
              role="tab"
              id={`tab-${p.id}`}
              aria-selected={p.id === current.id}
              aria-controls="preview-panel"
              className={`tab ${p.id === current.id ? "active" : ""}`}
              onClick={() => setActive(p.id)}
            >
              {p.name} <span className={`badge ${s.kind}`}>{s.label}</span>
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id="preview-panel" aria-labelledby={`tab-${current.id}`} className="tab-panel">
        <label htmlFor="preview-text" className="sr-only">{current.name} version</label>
        <textarea
          id="preview-text"
          className="preview-text"
          rows={Math.max(6, value.split("\n").length + Math.ceil(value.length / 90) + 1)}
          value={value}
          disabled={disabled}
          onChange={(e) => onEdit(current.id, e.target.value)}
        />
        <div className="row" style={{ marginTop: 6 }}>
          <span className={`counter ${length > limit ? "over" : ""}`}>
            {length} / {limit} characters
          </span>
          <span className="spacer" />
          {customized ? (
            <>
              <span className="help">Your own version — changes to the post above won't affect it.</span>
              <button type="button" className="link-button" onClick={() => onReset(current.id)} disabled={disabled}>
                Use automatic version
              </button>
            </>
          ) : (
            <span className="help">Automatic — follows the post above.</span>
          )}
        </div>
        {problems.map((problem, i) => (
          <div key={i} className={`notice ${problem.level === "error" ? "bad" : "warn"}`}>
            <p>{problem.message}</p>
          </div>
        ))}
        {hasErrors && (
          <p className="help">
            Fix it for {current.name} only by editing the text above, or change the post at the top to fix it for every
            platform.
          </p>
        )}
      </div>
    </section>
  );
}
