import { useEffect, useState } from "react";
import ResultList from "../components/ResultList";
import { api, type HistoryEntry, type Platform } from "../services/api";
import { formatDate } from "../services/dates";

const STATUS_LABELS: Record<string, { text: string; kind: string }> = {
  success: { text: "Posted", kind: "ok" },
  partial: { text: "Partly posted", kind: "warn" },
  failed: { text: "Failed", kind: "bad" },
  publishing: { text: "Publishing…", kind: "" },
  interrupted: { text: "Interrupted", kind: "warn" },
};

const FILTERS = [
  { value: "", label: "All" },
  { value: "success", label: "Posted" },
  { value: "problems", label: "With problems" },
];

const PAGE_SIZE = 25;

export default function HistoryPage() {
  const [entries, setEntries] = useState<HistoryEntry[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [filter, setFilter] = useState("");
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.platforms().then(setPlatforms).catch(() => undefined);
  }, []);

  useEffect(() => {
    setEntries(null);
    api
      .history(PAGE_SIZE, 0, filter)
      .then((page) => {
        setEntries(page);
        setHasMore(page.length === PAGE_SIZE);
      })
      .catch((e) => setError(e.message));
  }, [filter]);

  async function loadMore() {
    const page = await api.history(PAGE_SIZE, entries?.length ?? 0, filter);
    setEntries((prev) => [...(prev ?? []), ...page]);
    setHasMore(page.length === PAGE_SIZE);
  }

  async function remove(id: number) {
    if (!window.confirm("Remove this entry from your history? This does not delete the posts on the platforms."))
      return;
    await api.deleteHistory(id);
    setEntries((prev) => prev?.filter((e) => e.id !== id) ?? null);
  }

  const names = Object.fromEntries(platforms.map((p) => [p.id, p.name]));

  return (
    <>
      <h1>History</h1>
      <p className="muted">Everything you posted with Auto Poster. Stored only on this computer.</p>
      <div className="row" style={{ marginBottom: 14 }} role="group" aria-label="Filter">
        {FILTERS.map((f) => (
          <button key={f.value} className={filter === f.value ? "primary" : ""} onClick={() => setFilter(f.value)}
            aria-pressed={filter === f.value}>
            {f.label}
          </button>
        ))}
      </div>
      {error && <div className="notice bad" role="alert"><p>{error}</p></div>}
      {!entries && !error && <p className="muted">Loading…</p>}
      {entries?.length === 0 && <p className="empty">Nothing here yet.</p>}
      {entries?.map((entry) => {
        const status = STATUS_LABELS[entry.status] ?? { text: entry.status, kind: "" };
        const notSelected = platforms.filter((p) => !entry.platforms.includes(p.id));
        return (
          <article className="card" key={entry.id}>
            <div className="row">
              <strong>{formatDate(entry.created_at)}</strong>
              <span className={`badge ${status.kind}`}>{status.text}</span>
              {entry.source === "scheduled" && <span className="badge">Scheduled</span>}
              {entry.image_count > 0 && (
                <span className="muted">
                  {entry.image_count} image{entry.image_count > 1 ? "s" : ""}
                </span>
              )}
              <span className="spacer" />
              <button className="link-button" onClick={() => remove(entry.id)}>Remove</button>
            </div>
            <p className="history-text">{entry.text || <span className="muted">(no text)</span>}</p>
            <ResultList results={entry.results} names={names} />
            {notSelected.length > 0 && (
              <p className="help">Not selected: {notSelected.map((p) => p.name).join(", ")}</p>
            )}
            {entry.status === "interrupted" && (
              <p className="help">
                Auto Poster was closed while this post was being published. Check the platforms to see whether it
                was posted.
              </p>
            )}
          </article>
        );
      })}
      {hasMore && <button onClick={loadMore}>Show older posts</button>}
    </>
  );
}
