import { useEffect, useState } from "react";
import { api, type Reminder } from "../services/api";

/** Shown on every page when a repost reminder is due. Nothing is ever posted without the user. */
export default function ReminderBanner() {
  const [due, setDue] = useState<Reminder[]>([]);

  const check = () => api.dueReminders().then(setDue).catch(() => undefined);
  useEffect(() => {
    check();
    const timer = setInterval(check, 60_000);
    window.addEventListener("focus", check);
    window.addEventListener("hashchange", check);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", check);
      window.removeEventListener("hashchange", check);
    };
  }, []);

  async function repost(r: Reminder) {
    await api.advanceReminder(r.id);
    window.location.hash = `#/?reminder=${r.id}`;
    check();
  }

  async function skip(r: Reminder) {
    await api.advanceReminder(r.id);
    check();
  }

  async function stop(r: Reminder) {
    if (!window.confirm("Stop reminding you about this post?")) return;
    await api.stopReminder(r.id);
    check();
  }

  if (!due.length) return null;
  return (
    <div className="reminder-banner" role="region" aria-label="Repost reminders">
      {due.map((r) => (
        <div className="notice warn row" key={r.id}>
          <span className="spacer">
            🔔 <strong>Time to repost?</strong> “{r.label}”
          </span>
          <button className="primary" onClick={() => repost(r)}>Repost…</button>
          <button onClick={() => skip(r)}>{r.repeat === "once" ? "Dismiss" : "Skip this time"}</button>
          {r.repeat !== "once" && <button className="link-button" onClick={() => stop(r)}>Stop reminding</button>}
        </div>
      ))}
    </div>
  );
}
