import { useEffect, useState } from "react";
import { api, type HashtagSet } from "../services/api";

interface Props {
  current: string; // the hashtags (or text) currently in the form
  onPick: (tags: string[]) => void;
  disabled?: boolean;
  canSave?: boolean; // offer "Save these as a set"
}

/** Saved hashtag sets as one-click chips. */
export default function HashtagSets({ current, onPick, disabled, canSave }: Props) {
  const [sets, setSets] = useState<HashtagSet[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = () => api.hashtagSets().then(setSets).catch(() => undefined);
  useEffect(() => {
    reload();
  }, []);

  async function saveCurrent() {
    const name = window.prompt("Name for this hashtag set (e.g. “Launch”):");
    if (!name) return;
    try {
      setError(null);
      await api.createHashtagSet(name, current);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function remove(set: HashtagSet) {
    if (!window.confirm(`Delete the hashtag set “${set.name}”?`)) return;
    await api.deleteHashtagSet(set.id);
    reload();
  }

  if (!sets.length && !canSave) return null;
  return (
    <div className="hashtag-sets">
      {sets.length > 0 && <span className="help">Add a saved set:</span>}
      {sets.map((set) => (
        <span className="chip" key={set.id}>
          <button type="button" className="chip-main" disabled={disabled} onClick={() => onPick(set.tags)}
            title={set.tags.map((t) => `#${t}`).join(" ")}>
            + {set.name}
          </button>
          <button type="button" className="chip-remove" aria-label={`Delete hashtag set ${set.name}`}
            onClick={() => remove(set)} disabled={disabled}>
            ×
          </button>
        </span>
      ))}
      {canSave && current.trim() && (
        <button type="button" className="link-button" onClick={saveCurrent} disabled={disabled}>
          Save these hashtags as a set
        </button>
      )}
      {error && <span className="counter over">{error}</span>}
    </div>
  );
}
