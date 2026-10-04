import { useEffect, useState } from "react";
import ResultList from "../components/ResultList";
import { api, type HistoryEntry, type Platform } from "../services/api";

const STATUS_LABELS: Record<string, { text: string; kind: string }> = {
  success: { text: "Posted", kind: "ok" },
  partial: { text: "Partly posted", kind: "warn" },
  failed: { text: "Failed", kind: "bad" },
  publishing: { text: "Publishing…", kind: "" },
  interrupted: { text: "Interrupted", kind: "warn" },
};

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function HistoryPage() {
  const [entries, setEntries] = useState<HistoryEntry[] | null>(null);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.history().then(setEntries).catch((e) => setError(e.message));
    api.platforms().then(setPlatforms).catch(() => undefined);
  }, []);

  const names = Object.fromEntries(platforms.map((p) => [p.id, p.name]));

  if (error) return <div className="notice bad" role="alert"><p>{error}</p></div>;
  if (!entries) return <p className="muted">Loading…</p>;

  return (
    <>
      <h1>History</h1>
      <p className="muted">Your post history is stored only on this computer.</p>
      {entries.length === 0 && <p className="empty">Nothing posted yet.</p>}
      {entries.map((entry) => {
        const status = STATUS_LABELS[entry.status] ?? { text: entry.status, kind: "" };
        return (
          <article className="card" key={entry.id}>
            <div className="row">
              <strong>{formatDate(entry.created_at)}</strong>
              <span className={`badge ${status.kind}`}>{status.text}</span>
              {entry.image_count > 0 && (
                <span className="muted">
                  {entry.image_count} image{entry.image_count > 1 ? "s" : ""}
                </span>
              )}
            </div>
            <p className="history-text">{entry.text || <span className="muted">(no text)</span>}</p>
            <ResultList results={entry.results} names={names} />
            {entry.status === "interrupted" && (
              <p className="help">
                Auto Poster was closed while this post was being published. Check the platforms to see whether it
                was posted.
              </p>
            )}
          </article>
        );
      })}
    </>
  );
}
