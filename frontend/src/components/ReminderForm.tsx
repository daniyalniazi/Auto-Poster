import { useState } from "react";
import type { ReminderRepeat } from "../services/api";
import { fromLocalInput, timeZoneName, toLocalInput } from "../services/dates";

export const REPEAT_OPTIONS: { value: ReminderRepeat; label: string }[] = [
  { value: "once", label: "Only once" },
  { value: "weekly", label: "Every week" },
  { value: "every_2_weeks", label: "Every 2 weeks" },
  { value: "monthly", label: "Every month" },
];

export function repeatLabel(repeat: ReminderRepeat): string {
  return REPEAT_OPTIONS.find((o) => o.value === repeat)?.label ?? repeat;
}

interface Props {
  initialDate?: Date;
  initialRepeat?: ReminderRepeat;
  saveLabel: string;
  onSave: (when: Date, repeat: ReminderRepeat) => Promise<void>;
  onCancel: () => void;
}

/** Pick when to be reminded to repost, and how often. */
export default function ReminderForm({ initialDate, initialRepeat = "once", saveLabel, onSave, onCancel }: Props) {
  const [when, setWhen] = useState(() => {
    const d = initialDate ?? new Date(Date.now() + 7 * 24 * 3600 * 1000);
    if (!initialDate) d.setMinutes(0, 0, 0);
    return toLocalInput(d);
  });
  const [repeat, setRepeat] = useState<ReminderRepeat>(initialRepeat);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    const date = fromLocalInput(when);
    if (!date || date.getTime() < Date.now()) {
      setError("Choose a date and time in the future.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSave(date, repeat);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="reminder-form">
      <div className="row">
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="reminder-when">Remind me on</label>
          <input id="reminder-when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="reminder-repeat">Repeat</label>
          <select id="reminder-repeat" value={repeat} onChange={(e) => setRepeat(e.target.value as ReminderRepeat)}>
            {REPEAT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
      </div>
      <p className="help">
        Time zone: {timeZoneName()}. Auto Poster will remind you; it never reposts on its own. You'll see the reminder
        when Auto Poster is running (a notification by the clock if it's in the tray), or next time you open it.
      </p>
      {error && <div className="notice bad"><p>{error}</p></div>}
      <div className="row">
        <button className="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : saveLabel}</button>
        <button onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}
