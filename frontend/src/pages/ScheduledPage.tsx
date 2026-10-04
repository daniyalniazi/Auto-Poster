import { useEffect, useState } from "react";
import { api, type Platform, type ScheduledPost } from "../services/api";
import { formatDate } from "../services/dates";

const STATUS: Record<string, { text: string; kind: string }> = {
  scheduled: { text: "Scheduled", kind: "" },
  sending: { text: "Sending…", kind: "" },
  sent: { text: "Sent", kind: "ok" },
  partial: { text: "Partly sent", kind: "warn" },
  failed: { text: "Failed", kind: "bad" },
  missed: { text: "Missed", kind: "warn" },
};

export default function ScheduledPage() {
  const [posts, setPosts] = useState<ScheduledPost[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  function reload() {
    api.scheduled().then(setPosts).catch((e) => setError(e.message));
  }

  useEffect(() => {
    reload();
    api.platforms().then((ps: Platform[]) => setNames(Object.fromEntries(ps.map((p) => [p.id, p.name]))));
    const timer = setInterval(reload, 15000); // pick up posts the scheduler just sent
    return () => clearInterval(timer);
  }, []);

  async function act(id: number, action: () => Promise<unknown>) {
    setBusyId(id);
    setError(null);
    try {
      await action();
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  if (!posts) return error ? <div className="notice bad"><p>{error}</p></div> : <p className="muted">Loading…</p>;

  const upcoming = posts.filter((p) => ["scheduled", "sending", "missed"].includes(p.status));
  const finished = posts.filter((p) => !upcoming.includes(p));

  function card(post: ScheduledPost) {
    const status = STATUS[post.status] ?? { text: post.status, kind: "" };
    const editable = ["scheduled", "missed", "failed", "partial"].includes(post.status);
    const busy = busyId === post.id;
    return (
      <article className="card" key={post.id}>
        <div className="row">
          <strong>{formatDate(post.scheduled_at)}</strong>
          <span className={`badge ${status.kind}`}>{status.text}</span>
          {post.status === "scheduled" && !post.enabled && <span className="badge warn">Paused</span>}
          <span className="spacer" />
          <span className="muted">{post.platforms.map((id) => names[id] ?? id).join(", ")}</span>
        </div>
        <p className="history-text">{post.text || <span className="muted">(no text)</span>}</p>
        {post.images.length > 0 && (
          <p className="muted" style={{ marginTop: 0 }}>
            {post.images.length} image{post.images.length > 1 ? "s" : ""}
          </p>
        )}
        {post.note && <div className={`notice ${post.status === "sent" ? "ok" : "warn"}`}><p>{post.note}</p></div>}
        <div className="row">
          {editable && <a className="button" href={`#/?edit=${post.id}`}>Edit</a>}
          {post.status === "scheduled" && (
            <button disabled={busy} onClick={() => act(post.id, () => api.setScheduledEnabled(post.id, !post.enabled))}>
              {post.enabled ? "Pause" : "Resume"}
            </button>
          )}
          {["scheduled", "missed"].includes(post.status) && (
            <button
              disabled={busy}
              onClick={() => {
                if (window.confirm("Post this now to all its platforms?")) act(post.id, () => api.sendScheduledNow(post.id));
              }}
            >
              {busy ? "Sending…" : "Send now"}
            </button>
          )}
          {post.history_post_id && <a className="button" href="#/history">See results</a>}
          <span className="spacer" />
          {post.status !== "sending" && (
            <button
              className="danger"
              disabled={busy}
              onClick={() => {
                if (window.confirm("Delete this scheduled post?")) act(post.id, () => api.deleteScheduled(post.id));
              }}
            >
              Delete
            </button>
          )}
        </div>
      </article>
    );
  }

  return (
    <>
      <h1>Scheduled</h1>
      <p className="muted">
        Scheduled posts are sent by Auto Poster while it is running on this computer. If it is closed at the
        scheduled time, the post is sent when you next open it (up to an hour late); after that it is marked
        “Missed” so nothing goes out unexpectedly.
      </p>
      {error && <div className="notice bad" role="alert"><p>{error}</p></div>}
      {upcoming.length === 0 && (
        <p className="empty">
          Nothing scheduled. On <a href="#/">Create post</a>, choose “Later” to schedule a post.
        </p>
      )}
      {upcoming.map(card)}
      {finished.length > 0 && (
        <>
          <h2 style={{ marginTop: 28 }}>Finished</h2>
          {finished.map(card)}
        </>
      )}
    </>
  );
}
