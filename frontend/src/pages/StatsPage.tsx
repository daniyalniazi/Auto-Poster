import { Fragment, useEffect, useState } from "react";
import { api, type Comment, type StatsOverview } from "../services/api";
import { formatDate } from "../services/dates";

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

/** "—" means the platform doesn't report this number (never shown as a fake 0). */
function num(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : compact.format(value);
}

function preview(text: string): string {
  return text.length > 160 ? `${text.slice(0, 160)}…` : text || "(no text)";
}

interface CommentsState {
  loading: boolean;
  comments: Comment[];
  error: string | null;
}

export default function StatsPage() {
  const [data, setData] = useState<StatsOverview | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [comments, setComments] = useState<Record<string, CommentsState>>({});

  async function refresh(force = false) {
    setRefreshing(true);
    setError(null);
    try {
      setData(await api.refreshStats(force));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    api.stats().then(setData).catch((e) => setError(e.message)); // show saved numbers at once…
    refresh(); // …then fetch newer ones (posts checked in the last 15 minutes are skipped)
  }, []);

  async function toggleComments(postId: number, platform: string) {
    const key = `${postId}:${platform}`;
    if (comments[key]) {
      setComments(({ [key]: _closed, ...rest }) => rest);
      return;
    }
    setComments((prev) => ({ ...prev, [key]: { loading: true, comments: [], error: null } }));
    try {
      const result = await api.statsComments(postId, platform);
      setComments((prev) => ({ ...prev, [key]: { loading: false, ...result } }));
    } catch (e) {
      setComments((prev) => ({
        ...prev,
        [key]: { loading: false, comments: [], error: e instanceof Error ? e.message : String(e) },
      }));
    }
  }

  if (!data) {
    return error ? <div className="notice bad" role="alert"><p>{error}</p></div> : <p className="muted">Loading…</p>;
  }

  const names = Object.fromEntries(Object.entries(data.platforms).map(([id, p]) => [id, p.name]));
  const unsupported = Object.values(data.platforms).filter((p) => !p.supported);
  const tiles = [
    { label: "Likes", value: data.totals.likes },
    { label: "Shares and reposts", value: data.totals.shares },
    { label: "Comments and replies", value: data.totals.replies },
    { label: "Views", value: data.totals.views, note: "Facebook Pages only" },
  ];

  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}>
        <h1 style={{ margin: 0 }}>Stats</h1>
        <span className="spacer" />
        <button onClick={() => refresh(true)} disabled={refreshing}>{refreshing ? "Updating…" : "Refresh"}</button>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Current totals for posts made with Auto Poster, fetched straight from each platform and kept on this computer.
      </p>
      {error && <div className="notice bad" role="alert"><p>{error}</p></div>}

      <section aria-label={`Totals for the last ${data.totals.days} days`}>
        <p className="help" style={{ margin: "0 0 6px" }}>
          Last {data.totals.days} days · {data.totals.posts} post{data.totals.posts === 1 ? "" : "s"}
        </p>
        <div className="stat-tiles">
          {tiles.map((t) => (
            <div className="stat-tile card" key={t.label}>
              <div className="stat-label">{t.label}</div>
              <div className="stat-value">{num(t.value)}</div>
              {t.note && <div className="help">{t.note}</div>}
            </div>
          ))}
        </div>
      </section>

      {unsupported.length > 0 && (
        <p className="help">{unsupported.map((p) => p.reason).join(" ")} Posts there are listed without numbers.</p>
      )}

      {data.posts.length === 0 && (
        <p className="empty">No published posts yet. Stats appear here after you publish with Auto Poster.</p>
      )}

      {data.posts.map((post) => (
        <article className="card" key={post.history_id}>
          <div className="row">
            <strong>{formatDate(post.created_at)}</strong>
          </div>
          <p className="history-text">{preview(post.text)}</p>
          <div className="table-scroll">
            <table className="stats-table">
              <thead>
                <tr>
                  <th scope="col">Platform</th>
                  <th scope="col" className="num">Likes</th>
                  <th scope="col" className="num">Shares</th>
                  <th scope="col" className="num">Comments</th>
                  <th scope="col" className="num">Views</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {post.results.map((r) => {
                  const info = data.platforms[r.platform];
                  const key = `${post.history_id}:${r.platform}`;
                  const open = comments[key];
                  return (
                    <Fragment key={key}>
                      <tr>
                        <th scope="row">
                          {names[r.platform] ?? r.platform}
                          {r.deleted && <span className="badge" style={{ marginLeft: 6 }}>Deleted</span>}
                        </th>
                        {info?.supported && !r.deleted ? (
                          <>
                            <td className="num">{num(r.stats?.likes)}</td>
                            <td className="num">{num(r.stats?.shares)}</td>
                            <td className="num">{num(r.stats?.replies)}</td>
                            <td className="num">{num(r.stats?.views)}</td>
                          </>
                        ) : (
                          <td colSpan={4} className="muted">
                            {r.deleted ? "Deleted from the platform" : "Not available"}
                          </td>
                        )}
                        <td className="actions">
                          {r.post_url && !r.deleted && (
                            <a href={r.post_url} target="_blank" rel="noreferrer">View</a>
                          )}
                          {info?.supported && !r.deleted && (
                            <button className="link-button" onClick={() => toggleComments(post.history_id, r.platform)}>
                              {open ? "Hide comments" : "Comments"}
                            </button>
                          )}
                        </td>
                      </tr>
                      {r.error && !r.deleted && (
                        <tr>
                          <td colSpan={6} className="help">⚠ {r.error}</td>
                        </tr>
                      )}
                      {open && (
                        <tr>
                          <td colSpan={6}>
                            {open.loading && <span className="muted">Loading comments…</span>}
                            {open.error && <div className="notice bad"><p>{open.error}</p></div>}
                            {!open.loading && !open.error && open.comments.length === 0 && (
                              <span className="muted">No comments yet.</span>
                            )}
                            <ul className="comments">
                              {open.comments.map((c, i) => (
                                <li key={i}>
                                  <strong>{c.author}</strong>
                                  {c.created_at && <span className="muted"> · {formatDate(c.created_at)}</span>}
                                  <div className="history-text" style={{ margin: "2px 0 0" }}>{c.text}</div>
                                </li>
                              ))}
                            </ul>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </article>
      ))}
    </>
  );
}
