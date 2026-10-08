import { useEffect, useState } from "react";
import ResultList from "../components/ResultList";
import { TechDetails } from "../components/ResultList";
import { api, type DeleteOutcome, type HistoryEntry, type Platform } from "../services/api";
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
  const [deleting, setDeleting] = useState<number | null>(null);
  const [outcomes, setOutcomes] = useState<Record<number, DeleteOutcome[]>>({});

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
    if (!window.confirm("Remove this entry from your history? The posts stay on the platforms (use “Delete everywhere” to remove them)."))
      return;
    await api.deleteHistory(id);
    setEntries((prev) => prev?.filter((e) => e.id !== id) ?? null);
  }

  const names = Object.fromEntries(platforms.map((p) => [p.id, p.name]));

  async function deleteEverywhere(entry: HistoryEntry) {
    const live = entry.results.filter((r) => r.success && !r.deleted_at).map((r) => names[r.platform] ?? r.platform);
    if (!window.confirm(`Delete this post from ${live.join(", ")}? This can't be undone.`)) return;
    setDeleting(entry.id);
    try {
      const result = await api.deleteEverywhere(entry.id);
      setOutcomes((prev) => ({ ...prev, [entry.id]: result }));
      const refreshed = await api.history(Math.max(entries?.length ?? PAGE_SIZE, PAGE_SIZE), 0, filter);
      setEntries(refreshed);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(null);
    }
  }

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
              <a className="button" href={`#/?from=${entry.id}`}>Post again</a>
              {entry.results.some((r) => r.success && !r.deleted_at) && (
                <button className="danger" onClick={() => deleteEverywhere(entry)} disabled={deleting === entry.id}>
                  {deleting === entry.id ? "Deleting…" : "Delete everywhere"}
                </button>
              )}
              <button className="link-button" onClick={() => remove(entry.id)}>Remove</button>
            </div>
            <p className="history-text">{entry.text || <span className="muted">(no text)</span>}</p>
            <ResultList results={entry.results} names={names} />
            {outcomes[entry.id]?.map((o) => (
              <div key={o.platform} className={`notice ${o.success ? "ok" : "bad"}`} role="status">
                <p>{o.message}</p>
                {!o.success && <TechDetails text={o.technical_details} />}
              </div>
            ))}
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
