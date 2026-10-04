import { useEffect, useMemo, useRef, useState } from "react";
import FieldInput from "../components/FieldInput";
import ImagePicker, { type AttachedImage } from "../components/ImagePicker";
import ResultList from "../components/ResultList";
import {
  api,
  type Platform,
  type PostRequest,
  type Problem,
  type PublishResponse,
  type ScheduledPost,
} from "../services/api";
import { formatDate, fromLocalInput, timeZoneName, toLocalInput } from "../services/dates";
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

function editIdFromHash(): number | null {
  const match = window.location.hash.match(/[?&]edit=(\d+)/);
  return match ? Number(match[1]) : null;
}

function defaultScheduleTime(): string {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setMinutes(0, 0, 0);
  return toLocalInput(date);
}

type Done = { kind: "published"; response: PublishResponse } | { kind: "scheduled"; post: ScheduledPost };

export default function ComposePage() {
  const editId = useMemo(editIdFromHash, []);
  const [platforms, setPlatforms] = useState<Platform[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>(editId ? [] : loadSelected);
  const [text, setText] = useState("");
  const [images, setImages] = useState<AttachedImage[]>([]);
  const [options, setOptions] = useState<Record<string, Record<string, string>>>({});
  const [when, setWhen] = useState<"now" | "later">(editId ? "later" : "now");
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleTime);
  const [problems, setProblems] = useState<Record<string, Problem[]>>({});
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Done | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const submitLock = useRef(false);

  useEffect(() => {
    api.platforms().then(setPlatforms).catch((e) => setLoadError(e.message));
  }, []);

  // Editing a scheduled post: load it into the form.
  useEffect(() => {
    if (!editId) return;
    api
      .getScheduled(editId)
      .then(async (post) => {
        setText(post.text);
        setSelected(post.platforms);
        setOptions(post.options);
        setScheduleAt(toLocalInput(new Date(post.scheduled_at)));
        const loaded = await Promise.all(
          post.images.map(async (info) => ({
            info,
            previewUrl: await api.imageUrl(info.id).catch(() => ""),
            alt: post.alt_texts[info.id] ?? "",
          })),
        );
        setImages(loaded);
      })
      .catch((e) => setLoadError(e.message));
  }, [editId]);

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
    if (!editId) saveSelected(next);
  }

  function setOption(platformId: string, key: string, value: string) {
    setOptions((prev) => ({ ...prev, [platformId]: { ...prev[platformId], [key]: value } }));
  }

  const scheduleDate = fromLocalInput(scheduleAt);
  const scheduleProblem =
    when !== "later"
      ? null
      : !scheduleDate
        ? "Choose a date and time."
        : scheduleDate.getTime() < Date.now()
          ? "That time has already passed. Choose a time in the future."
          : null;

  const errorCount = Object.values(problems).flat().filter((p) => p.level === "error").length;
  const canSubmit =
    activeSelection.length > 0 && errorCount === 0 && !scheduleProblem && !checking && !busy;

  async function submit() {
    if (!canSubmit || submitLock.current) return;
    submitLock.current = true; // blocks a second click before React re-renders
    setBusy(true);
    setSubmitError(null);
    try {
      if (when === "now") {
        setDone({ kind: "published", response: await api.publish(postRequest, crypto.randomUUID()) });
      } else if (editId) {
        setDone({ kind: "scheduled", post: await api.updateScheduled(editId, postRequest, scheduleDate!) });
      } else {
        setDone({ kind: "scheduled", post: await api.createScheduled(postRequest, scheduleDate!) });
      }
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      submitLock.current = false;
    }
  }

  function startNew() {
    images.forEach((i) => i.previewUrl && URL.revokeObjectURL(i.previewUrl));
    setText("");
    setImages([]);
    setOptions({});
    setDone(null);
    setWhen("now");
    if (editId) window.location.hash = "#/";
  }

  if (loadError) return <div className="notice bad" role="alert"><p>{loadError}</p></div>;
  if (!platforms) return <p className="muted">Loading…</p>;

  if (done?.kind === "scheduled") {
    return (
      <>
        <h1>{editId ? "Changes saved" : "Post scheduled"}</h1>
        <div className="card">
          <p style={{ marginTop: 0 }}>
            It will be posted to {done.post.platforms.map((id) => names[id] ?? id).join(", ")} on{" "}
            <strong>{formatDate(done.post.scheduled_at)}</strong>.
          </p>
          <p className="help">Auto Poster must be running at that time for the post to be sent.</p>
        </div>
        <div className="row">
          <button className="primary" onClick={startNew}>Write a new post</button>
          <a className="button" href="#/scheduled">View scheduled posts</a>
        </div>
      </>
    );
  }

  if (done?.kind === "published") {
    const { response } = done;
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
          {ok < total && <button onClick={() => setDone(null)}>Back to this post</button>}
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
  const buttonLabel = busy
    ? when === "now" ? "Publishing…" : "Saving…"
    : when === "now" ? "Publish now" : editId ? "Save changes" : "Schedule post";

  return (
    <>
      <h1>{editId ? "Edit scheduled post" : "Create post"}</h1>

      <section className="card" aria-labelledby="content-heading">
        <h2 id="content-heading">Your post</h2>
        <label htmlFor="post-text" className="sr-only">Post text</label>
        <textarea
          id="post-text"
          value={text}
          placeholder="What do you want to share?"
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
        />
        <div style={{ marginTop: 10 }}>
          <ImagePicker images={images} onChange={setImages} disabled={busy} deleteOnRemove={!editId} />
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
                    disabled={busy}
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
                  <div className="platform-options">
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

      <section className="card" aria-labelledby="when-heading">
        <h2 id="when-heading">When</h2>
        <div className="row" role="radiogroup" aria-labelledby="when-heading">
          {!editId && (
            <label className="checkbox-row">
              <input type="radio" name="when" checked={when === "now"} onChange={() => setWhen("now")} />
              Now
            </label>
          )}
          <label className="checkbox-row">
            <input type="radio" name="when" checked={when === "later"} onChange={() => setWhen("later")} />
            Later
          </label>
        </div>
        {when === "later" && (
          <div className="field" style={{ marginTop: 10, maxWidth: 320 }}>
            <label htmlFor="schedule-at">Date and time</label>
            <input
              id="schedule-at"
              type="datetime-local"
              value={scheduleAt}
              onChange={(e) => setScheduleAt(e.target.value)}
            />
            <p className="help">
              Your time zone: {timeZoneName()}. Auto Poster must be running at this time.
            </p>
            {scheduleProblem && <div className="notice bad"><p>{scheduleProblem}</p></div>}
          </div>
        )}
      </section>

      {generalProblems.length > 0 && activeSelection.length > 0 && (
        <div className="notice bad">{generalProblems.map((p, i) => <p key={i}>{p.message}</p>)}</div>
      )}
      {submitError && <div className="notice bad" role="alert"><p>{submitError}</p></div>}

      <div className="row">
        <button className="primary big" onClick={submit} disabled={!canSubmit} aria-busy={busy}>
          {buttonLabel}
        </button>
        {editId && <a className="button" href="#/scheduled">Cancel</a>}
        <span className="help" aria-live="polite">
          {activeSelection.length === 0
            ? "Choose at least one platform."
            : checking
              ? "Checking…"
              : errorCount > 0
                ? `Fix ${errorCount === 1 ? "the problem" : `the ${errorCount} problems`} above to continue.`
                : `Ready for ${activeSelection.map((id) => names[id]).join(", ")}.`}
        </span>
      </div>
    </>
  );
}
