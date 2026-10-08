import { useEffect, useMemo, useRef, useState } from "react";
import FieldInput from "../components/FieldInput";
import HashtagSets from "../components/HashtagSets";
import ImagePicker, { type AttachedImage } from "../components/ImagePicker";
import PlatformPreviews from "../components/PlatformPreviews";
import ResultList from "../components/ResultList";
import {
  api,
  type ComposeMode,
  type ImageInfo,
  type Platform,
  type PostRequest,
  type Prepared,
  type PublishResponse,
  type ScheduledPost,
} from "../services/api";
import { formatDate, fromLocalInput, timeZoneName, toLocalInput } from "../services/dates";

const SELECTED_KEY = "auto-poster.selected-platforms";
const MODE_KEY = "auto-poster.compose-mode";

function loadMode(): ComposeMode {
  try {
    return localStorage.getItem(MODE_KEY) === "structured" ? "structured" : "quick";
  } catch {
    return "quick";
  }
}

function saveMode(mode: ComposeMode) {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* storage unavailable: mode just isn't remembered */
  }
}

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

const AUTOSAVE_KEY = "auto-poster.autosave";

interface Autosave {
  request: PostRequest;
  images: ImageInfo[];
  draftId: number | null;
}

function readAutosave(): Autosave | null {
  try {
    const saved = JSON.parse(localStorage.getItem(AUTOSAVE_KEY) ?? "null");
    return saved?.request ? saved : null;
  } catch {
    return null;
  }
}

function writeAutosave(value: Autosave | null) {
  try {
    if (value) localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(value));
    else localStorage.removeItem(AUTOSAVE_KEY);
  } catch {
    /* storage unavailable: no autosave */
  }
}

function hasContent(r: PostRequest): boolean {
  return Boolean(r.text.trim() || r.title.trim() || r.link.trim() || r.hashtags.trim() || r.image_ids.length);
}

// The form can be opened for: a scheduled post (?edit=), a draft (?draft=) or a past post (?from=).
function hashParam(name: string): number | null {
  const match = window.location.hash.match(new RegExp(`[?&]${name}=(\\d+)`));
  return match ? Number(match[1]) : null;
}

function defaultScheduleTime(): string {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setMinutes(0, 0, 0);
  return toLocalInput(date);
}

type Done = { kind: "published"; response: PublishResponse } | { kind: "scheduled"; post: ScheduledPost };

export default function ComposePage() {
  const editId = useMemo(() => hashParam("edit"), []);
  const fromHistoryId = useMemo(() => hashParam("from"), []);
  const [draftId, setDraftId] = useState<number | null>(() => hashParam("draft"));
  const [notice, setNotice] = useState<{ kind: "ok" | "warn"; text: string; undo?: boolean } | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);
  const loaded = useRef(false);
  const [platforms, setPlatforms] = useState<Platform[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>(editId ? [] : loadSelected);
  const [mode, setMode] = useState<ComposeMode>(loadMode);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [link, setLink] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [images, setImages] = useState<AttachedImage[]>([]);
  const [options, setOptions] = useState<Record<string, Record<string, string>>>({});
  const [when, setWhen] = useState<"now" | "later">(editId ? "later" : "now");
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleTime);
  const [prepared, setPrepared] = useState<Prepared>({ previews: {}, problems: [] });
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Done | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const submitLock = useRef(false);

  useEffect(() => {
    api.platforms().then(setPlatforms).catch((e) => setLoadError(e.message));
  }, []);

  async function applyRequest(r: PostRequest, infos: ImageInfo[]) {
    setMode(r.mode ?? "quick");
    setTitle(r.title ?? "");
    setText(r.text ?? "");
    setHashtags(r.hashtags ?? "");
    setLink(r.link ?? "");
    setOverrides(r.overrides ?? {});
    setSelected(r.platforms ?? []);
    setOptions(r.options ?? {});
    const withPreviews = await Promise.all(
      infos
        .filter((info) => r.image_ids.includes(info.id))
        .map(async (info) => ({
          info,
          previewUrl: await api.imageUrl(info.id).catch(() => ""),
          alt: r.alt_texts?.[info.id] ?? "",
        })),
    );
    setImages(withPreviews.filter((i) => i.previewUrl)); // images that no longer exist are left out
  }

  // Load what the form was opened for, or restore unsaved work.
  useEffect(() => {
    const done = () => (loaded.current = true);
    if (editId) {
      api
        .getScheduled(editId)
        .then(async (post) => {
          await applyRequest({ ...post, image_ids: post.images.map((i) => i.id) }, post.images);
          setScheduleAt(toLocalInput(new Date(post.scheduled_at)));
        })
        .catch((e) => setLoadError(e.message))
        .finally(done);
    } else if (draftId) {
      api
        .getDraft(draftId)
        .then((draft) => applyRequest(draft.request, draft.images))
        .catch((e) => setLoadError(e.message))
        .finally(done);
    } else if (fromHistoryId) {
      api
        .historyCompose(fromHistoryId)
        .then(async (data) => {
          await applyRequest(data.request, data.images);
          setNotice({
            kind: data.missing_images ? "warn" : "ok",
            text:
              "This is a copy of an earlier post. Change anything you like, then publish or schedule it." +
              (data.missing_images
                ? ` ${data.missing_images} image${data.missing_images > 1 ? "s are" : " is"} no longer stored; add ${data.missing_images > 1 ? "them" : "it"} again if needed.`
                : ""),
          });
        })
        .catch((e) => setLoadError(e.message))
        .finally(done);
    } else {
      const saved = readAutosave();
      if (saved && hasContent(saved.request)) {
        applyRequest(saved.request, saved.images).finally(done);
        setDraftId(saved.draftId);
        setNotice({ kind: "ok", text: "We restored the post you were writing.", undo: true });
      } else {
        done();
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
      mode,
      title,
      text,
      hashtags,
      link,
      overrides: Object.fromEntries(Object.entries(overrides).filter(([id]) => activeSelection.includes(id))),
      platforms: activeSelection,
      image_ids: images.map((i) => i.info.id),
      alt_texts: Object.fromEntries(images.map((i) => [i.info.id, i.alt])),
      options: Object.fromEntries(activeSelection.map((id) => [id, options[id] ?? {}])),
    }),
    [mode, title, text, hashtags, link, overrides, activeSelection, images, options],
  );

  // Keep unsaved work in this browser, so closing the tab by accident doesn't lose it.
  useEffect(() => {
    if (!loaded.current || editId || done) return;
    writeAutosave(hasContent(postRequest) ? { request: postRequest, images: images.map((i) => i.info), draftId } : null);
  }, [postRequest, images, draftId, editId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Build each platform's version and check it against that platform's rules while the user types.
  useEffect(() => {
    if (!platforms) return;
    setChecking(true);
    const timer = setTimeout(() => {
      api
        .prepare(postRequest)
        .then(setPrepared)
        .catch((e) =>
          setPrepared({ previews: {}, problems: [{ platform: "all", message: e.message, level: "error", field: null }] }),
        )
        .finally(() => setChecking(false));
    }, 350);
    return () => clearTimeout(timer);
  }, [postRequest, platforms]);

  function toggle(id: string) {
    const next = selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id];
    setSelected(next);
    if (!editId) saveSelected(next);
  }

  function addHashtags(tags: string[]) {
    const words = tags.map((t) => `#${t}`);
    if (mode === "structured") {
      const existing = new Set(hashtags.split(/[\s,]+/).map((t) => t.replace(/^#/, "").toLowerCase()));
      const fresh = words.filter((w) => !existing.has(w.slice(1).toLowerCase()));
      setHashtags((prev) => [prev.trim(), ...fresh].filter(Boolean).join(" "));
    } else {
      setText((prev) => `${prev.trimEnd()}${prev.trim() ? "\n\n" : ""}${words.join(" ")}`);
    }
  }

  async function saveDraft() {
    setSavingDraft(true);
    try {
      const draft = draftId ? await api.updateDraft(draftId, postRequest) : await api.createDraft(postRequest);
      setDraftId(draft.id);
      setNotice({ kind: "ok", text: "Draft saved. Find it any time under Drafts." });
    } catch (e) {
      setNotice({ kind: "warn", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSavingDraft(false);
    }
  }

  function changeMode(next: ComposeMode) {
    setMode(next);
    if (!editId) saveMode(next);
  }

  function editPlatformText(platformId: string, value: string) {
    setOverrides((prev) => ({ ...prev, [platformId]: value }));
  }

  function resetPlatformText(platformId: string) {
    setOverrides(({ [platformId]: _removed, ...rest }) => rest);
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

  const allProblems = [...prepared.problems, ...Object.values(prepared.previews).flatMap((p) => p.problems)];
  const errorCount = allProblems.filter((p) => p.level === "error").length;
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
      writeAutosave(null);
      // The post now lives in History or Scheduled, so its draft isn't needed any more.
      if (draftId) {
        api.deleteDraft(draftId).catch(() => undefined);
        setDraftId(null);
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
    setTitle("");
    setText("");
    setHashtags("");
    setLink("");
    setOverrides({});
    setImages([]);
    setOptions({});
    setDone(null);
    setWhen("now");
    setDraftId(null);
    setNotice(null);
    writeAutosave(null);
    if (editId || fromHistoryId || hashParam("draft")) window.location.hash = "#/";
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

  const generalProblems = prepared.problems;
  const selectedPlatforms = platforms.filter((p) => activeSelection.includes(p.id));
  const buttonLabel = busy
    ? when === "now" ? "Publishing…" : "Saving…"
    : when === "now" ? "Publish now" : editId ? "Save changes" : "Schedule post";

  return (
    <>
      <h1>{editId ? "Edit scheduled post" : draftId ? "Edit draft" : "Create post"}</h1>
      {notice && (
        <div className={`notice ${notice.kind}`} role="status">
          <p>
            {notice.text}{" "}
            {notice.undo && (
              <button type="button" className="link-button" onClick={startNew}>Start a new post instead</button>
            )}
          </p>
        </div>
      )}

      <section className="card" aria-labelledby="content-heading">
        <div className="row" style={{ marginBottom: 10 }}>
          <h2 id="content-heading" style={{ margin: 0 }}>Your post</h2>
          <span className="spacer" />
          <div className="segmented" role="radiogroup" aria-label="How to write the post">
            <button type="button" role="radio" aria-checked={mode === "quick"}
              className={mode === "quick" ? "active" : ""} onClick={() => changeMode("quick")}>
              Quick post
            </button>
            <button type="button" role="radio" aria-checked={mode === "structured"}
              className={mode === "structured" ? "active" : ""} onClick={() => changeMode("structured")}>
              Structured
            </button>
          </div>
        </div>
        {mode === "quick" ? (
          <>
            <label htmlFor="post-text" className="sr-only">Post text</label>
            <textarea
              id="post-text"
              value={text}
              placeholder="What do you want to share? It's posted as typed on every platform."
              onChange={(e) => setText(e.target.value)}
              disabled={busy}
            />
            <HashtagSets current={text} onPick={addHashtags} disabled={busy} />
          </>
        ) : (
          <>
            <p className="help" style={{ marginTop: 0 }}>
              Write it once. Each platform arranges the title, text, link and hashtags its own way. See “How it will
              look” below.
            </p>
            <div className="field">
              <label htmlFor="post-title">Title <span className="muted">(optional)</span></label>
              <input id="post-title" type="text" value={title} disabled={busy}
                placeholder="e.g. Our new product is here" onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="post-text">Text</label>
              <textarea id="post-text" value={text} disabled={busy}
                placeholder="What do you want to share?" onChange={(e) => setText(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="post-link">Link <span className="muted">(optional)</span></label>
              <input id="post-link" type="text" value={link} disabled={busy} inputMode="url"
                placeholder="https://…" onChange={(e) => setLink(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="post-hashtags">Hashtags <span className="muted">(optional)</span></label>
              <input id="post-hashtags" type="text" value={hashtags} disabled={busy}
                placeholder="#launch #OpenSource" onChange={(e) => setHashtags(e.target.value)} />
              <p className="help">
                Separate with spaces or commas. Capitalising each word (#OpenSource) helps screen readers.
              </p>
              <HashtagSets current={hashtags} onPick={addHashtags} disabled={busy} canSave />
            </div>
          </>
        )}
        <div style={{ marginTop: 10 }}>
          <ImagePicker images={images} onChange={setImages} disabled={busy} />
        </div>
      </section>

      <section className="card" aria-labelledby="platforms-heading">
        <h2 id="platforms-heading">Post to</h2>
        <div className="platform-list">
          {platforms.map((p) => {
            const isSelected = activeSelection.includes(p.id);
            const platformErrors = (prepared.previews[p.id]?.problems ?? []).filter((x) => x.level === "error");
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
                  {isSelected && platformErrors.length > 0 && p.configured && (
                    <span className="counter over">
                      {platformErrors.length === 1 ? "1 problem" : `${platformErrors.length} problems`}
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
                {isSelected && p.configured && p.post_fields.length > 0 && (
                  <div className="platform-options">
                    {p.post_fields.map((field) => (
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

      <PlatformPreviews
        platforms={selectedPlatforms}
        previews={prepared.previews}
        overrides={overrides}
        hasImages={images.length > 0}
        disabled={busy}
        onEdit={editPlatformText}
        onReset={resetPlatformText}
      />

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
        {!editId && (
          <button type="button" onClick={saveDraft} disabled={busy || savingDraft || !hasContent(postRequest)}>
            {savingDraft ? "Saving…" : draftId ? "Save draft" : "Save as draft"}
          </button>
        )}
        <span className="help" aria-live="polite">
          {activeSelection.length === 0
            ? "Choose at least one platform."
            : checking
              ? "Checking…"
              : errorCount > 0
                ? `Fix ${errorCount === 1 ? "the problem" : `the ${errorCount} problems`} in “How it will look” to continue.`
                : `Ready for ${activeSelection.map((id) => names[id]).join(", ")}.`}
        </span>
      </div>
    </>
  );
}
