import type { PostResult } from "../services/api";

interface Props {
  results: PostResult[];
  names: Record<string, string>;
}

export function TechDetails({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <details className="tech">
      <summary>Details</summary>
      <pre>{text}</pre>
    </details>
  );
}

function retryText(seconds: number | null): string {
  if (!seconds) return "";
  if (seconds < 120) return ` You can try again in about ${seconds} seconds.`;
  return ` You can try again in about ${Math.ceil(seconds / 60)} minutes.`;
}

export default function ResultList({ results, names }: Props) {
  return (
    <div>
      {results.map((r) => (
        <div className="result-row" key={r.platform}>
          <span className={`result-icon ${r.success ? "ok" : "bad"}`} aria-hidden="true">
            {r.success ? "✓" : "✗"}
          </span>
          <div className="spacer">
            <strong>{names[r.platform] ?? r.platform}</strong>{" "}
            <span className={`badge ${r.success ? "ok" : "bad"}`}>{r.success ? "Posted" : "Failed"}</span>
            <div>
              {r.message}
              {!r.success && retryText(r.retry_after)}
            </div>
            {r.post_url && (
              <a href={r.post_url} target="_blank" rel="noreferrer">
                View post
              </a>
            )}
            {!r.success && <TechDetails text={r.technical_details} />}
          </div>
        </div>
      ))}
    </div>
  );
}
