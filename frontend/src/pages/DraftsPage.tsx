import { useEffect, useState } from "react";
import { api, type Draft } from "../services/api";
import { formatDate } from "../services/dates";

function preview(draft: Draft): string {
  const r = draft.request;
  const text = [r.title, r.text].filter((t) => t?.trim()).join(" — ");
  return text.length > 220 ? `${text.slice(0, 220)}…` : text || "(no text yet)";
}

export default function DraftsPage() {
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const reload = () => api.drafts().then(setDrafts).catch((e) => setError(e.message));
  useEffect(() => {
    reload();
    api.platforms().then((ps) => setNames(Object.fromEntries(ps.map((p) => [p.id, p.name])))).catch(() => undefined);
  }, []);

  async function remove(draft: Draft) {
    if (!window.confirm("Delete this draft?")) return;
    await api.deleteDraft(draft.id);
    reload();
  }

  if (error) return <div className="notice bad" role="alert"><p>{error}</p></div>;
  if (!drafts) return <p className="muted">Loading…</p>;

  return (
    <>
      <h1>Drafts</h1>
      <p className="muted">Posts you saved to finish later. Stored only on this computer.</p>
      {drafts.length === 0 && (
        <p className="empty">
          No drafts. On <a href="#/">Create post</a>, click “Save as draft” to keep a post for later.
        </p>
      )}
      {drafts.map((draft) => (
        <article className="card" key={draft.id}>
          <div className="row">
            <strong>Saved {formatDate(draft.updated_at)}</strong>
            {draft.request.mode === "structured" && <span className="badge">Structured</span>}
            {draft.images.length > 0 && (
              <span className="muted">{draft.images.length} image{draft.images.length > 1 ? "s" : ""}</span>
            )}
            <span className="spacer" />
            <span className="muted">{draft.request.platforms.map((id) => names[id] ?? id).join(", ")}</span>
          </div>
          <p className="history-text">{preview(draft)}</p>
          <div className="row">
            <a className="button primary" href={`#/?draft=${draft.id}`}>Open</a>
            <span className="spacer" />
            <button className="danger" onClick={() => remove(draft)}>Delete</button>
          </div>
        </article>
      ))}
    </>
  );
}
