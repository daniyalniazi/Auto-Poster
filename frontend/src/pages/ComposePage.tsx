import { useEffect, useMemo, useRef, useState } from "react";
import FieldInput from "../components/FieldInput";
import ImagePicker, { type AttachedImage } from "../components/ImagePicker";
import ResultList from "../components/ResultList";
import { api, type Platform, type PostRequest, type Problem, type PublishResponse } from "../services/api";
import { charLimit, textLength } from "../services/textLength";

const SELECTED_KEY = "auto-poster.selected-platforms";

function loadSelected(): string[] {
  try {
    return JSON.parse(localStorage.getItem(SELECTED_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function saveSelected(ids: string[]) {
  try {
    localStorage.setItem(SELECTED_KEY, JSON.stringify(ids));
  } catch {
    /* storage unavailable: selection just isn't remembered */
  }
}

function newRequestId(): string {
  return crypto.randomUUID();
}

export default function ComposePage() {
  const [platforms, setPlatforms] = useState<Platform[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>(loadSelected);
  const [text, setText] = useState("");
  const [images, setImages] = useState<AttachedImage[]>([]);
  const [options, setOptions] = useState<Record<string, Record<string, string>>>({});
  const [problems, setProblems] = useState<Record<string, Problem[]>>({});
  const [checking, setChecking] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [response, setResponse] = useState<PublishResponse | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);
  const publishLock = useRef(false);

  useEffect(() => {
    api.platforms().then(setPlatforms).catch((e) => setLoadError(e.message));
  }, []);

  const names = useMemo(
    () => Object.fromEntries((platforms ?? []).map((p) => [p.id, p.name])),
    [platforms],
  );
  const activeSelection = useMemo(
    () => selected.filter((id) => platforms?.some((p) => p.id === id)),
    [selected, platforms],
  );

  const postRequest: PostRequest = useMemo(
    () => ({
      text,
      platforms: activeSelection,
      image_ids: images.map((i) => i.info.id),
      alt_texts: Object.fromEntries(images.map((i) => [i.info.id, i.alt])),
      options: Object.fromEntries(activeSelection.map((id) => [id, options[id] ?? {}])),
    }),
    [text, activeSelection, images, options],
  );

  // Check the post against each selected platform's rules while the user types.
  useEffect(() => {
    if (!platforms) return;
    setChecking(true);
    const timer = setTimeout(() => {
      api
        .validate(postRequest)
        .then(setProblems)
        .catch((e) => setProblems({ all: [{ platform: "all", message: e.message, level: "error", field: null }] }))
        .finally(() => setChecking(false));
    }, 350);
    return () => clearTimeout(timer);
  }, [postRequest, platforms]);

  function toggle(id: string) {
    const next = selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id];
    setSelected(next);
    saveSelected(next);
  }

  function setOption(platformId: string, key: string, value: string) {
    setOptions((prev) => ({ ...prev, [platformId]: { ...prev[platformId], [key]: value } }));
  }

  const errorCount = Object.values(problems).flat().filter((p) => p.level === "error").length;
  const canPublish = activeSelection.length > 0 && errorCount === 0 && !checking && !publishing;

  async function publish() {
    if (!canPublish || publishLock.current) return;
    publishLock.current = true; // blocks a second click before React re-renders
    setPublishing(true);
    setPublishError(null);
    try {
      setResponse(await api.publish(postRequest, newRequestId()));
    } catch (e) {
      setPublishError(e instanceof Error ? e.message : String(e));
    } finally {
      setPublishing(false);
      publishLock.current = false;
    }
  }

  function startNew() {
    images.forEach((i) => URL.revokeObjectURL(i.previewUrl));
    setText("");
    setImages([]);
    setOptions({});
    setResponse(null);
  }

  if (loadError) return <div className="notice bad" role="alert"><p>{loadError}</p></div>;
  if (!platforms) return <p className="muted">Loading…</p>;

  if (response) {
    const ok = response.results.filter((r) => r.success).length;
    const total = response.results.length;
    return (
      <>
        <h1>{ok === total ? "Posted!" : ok === 0 ? "The post was not published" : "Posted to some platforms"}</h1>
        <div className="card" aria-live="polite">
          <ResultList results={response.results} names={names} />
        </div>
        <div className="row">
          <button className="primary" onClick={startNew}>Write a new post</button>
          {ok < total && (
            <button onClick={() => setResponse(null)}>Back to this post</button>
          )}
          <a className="button" href="#/history">View history</a>
        </div>
        {ok < total && ok > 0 && (
          <p className="help">
            If you go back and publish again, it will be posted again to every selected platform. Untick the
            platforms that already worked first.
          </p>
        )}
      </>
    );
  }

  const generalProblems = problems.all ?? [];

  return (
    <>
      <h1>Create post</h1>

      <section className="card" aria-labelledby="content-heading">
        <h2 id="content-heading">Your post</h2>
        <label htmlFor="post-text" className="sr-only">Post text</label>
        <textarea
          id="post-text"
          value={text}
          placeholder="What do you want to share?"
          onChange={(e) => setText(e.target.value)}
          disabled={publishing}
        />
        <div style={{ marginTop: 10 }}>
          <ImagePicker images={images} onChange={setImages} disabled={publishing} />
        </div>
      </section>

      <section className="card" aria-labelledby="platforms-heading">
        <h2 id="platforms-heading">Post to</h2>
        <div className="platform-list">
          {platforms.map((p) => {
            const isSelected = activeSelection.includes(p.id);
            const limit = charLimit(p.limits, images.length > 0);
            const length = textLength(text, p.limits.count_method);
            const platformProblems = problems[p.id] ?? [];
            return (
              <div key={p.id}>
                <div className={`platform-choice ${isSelected ? "selected" : ""}`}>
                  <input
                    id={`select-${p.id}`}
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggle(p.id)}
                    disabled={publishing}
                  />
                  <label htmlFor={`select-${p.id}`} className="name" style={{ margin: 0 }}>{p.name}</label>
                  <span className="spacer" />
                  {isSelected && (
                    <span className={`counter ${length > limit ? "over" : ""}`}>
                      {length} / {limit}
                    </span>
                  )}
                  {p.configured ? (
                    <span className="badge ok">Connected</span>
                  ) : (
                    <>
                      <span className="badge warn">Not connected</span>
                      <a href="#/settings">Set up</a>
                    </>
                  )}
                </div>
                {isSelected && (platformProblems.length > 0 || p.post_fields.length > 0) && (
                  <div style={{ padding: "10px 12px 0 40px" }}>
                    {platformProblems.map((problem, i) => (
                      <div key={i} className={`notice ${problem.level === "error" ? "bad" : "warn"}`}>
                        <p>{problem.message}</p>
                      </div>
                    ))}
                    {p.configured && p.post_fields.map((field) => (
                      <FieldInput
                        key={field.key}
                        field={field}
                        idPrefix={`opt-${p.id}`}
                        value={options[p.id]?.[field.key] ?? field.default}
                        onChange={(v) => setOption(p.id, field.key, v)}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {generalProblems.length > 0 && activeSelection.length > 0 && (
        <div className="notice bad">{generalProblems.map((p, i) => <p key={i}>{p.message}</p>)}</div>
      )}
      {publishError && <div className="notice bad" role="alert"><p>{publishError}</p></div>}

      <div className="row">
        <button className="primary big" onClick={publish} disabled={!canPublish} aria-busy={publishing}>
          {publishing ? "Publishing…" : "Publish now"}
        </button>
        <span className="help" aria-live="polite">
          {activeSelection.length === 0
            ? "Choose at least one platform."
            : checking
              ? "Checking…"
              : errorCount > 0
                ? `Fix ${errorCount === 1 ? "the problem" : `the ${errorCount} problems`} above to publish.`
                : `Ready to post to ${activeSelection.map((id) => names[id]).join(", ")}.`}
        </span>
      </div>
    </>
  );
}
