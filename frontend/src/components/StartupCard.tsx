import { useEffect, useState } from "react";
import { api, type SystemInfo } from "../services/api";

/** "Start Auto Poster when I log in" switch. */
export default function StartupCard() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.system().then(setInfo).catch(() => undefined);
  }, []);

  if (!info?.autostart_supported) return null;

  async function toggle(enabled: boolean) {
    setBusy(true);
    setError(null);
    try {
      setInfo(await api.setAutostart(enabled));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="startup-heading">
      <h2 id="startup-heading">Start with your computer</h2>
      <label className="checkbox-row" htmlFor="autostart">
        <input id="autostart" type="checkbox" checked={info.autostart_enabled} disabled={busy}
          onChange={(e) => toggle(e.target.checked)} />
        Start Auto Poster quietly when I log in
      </label>
      <p className="help">
        Recommended. Auto Poster then runs in the background with an icon next to the clock, so scheduled posts and
        repost reminders work without you opening it. Click the icon (or open Auto Poster again) to show this page; use
        the icon's menu to quit. On some Linux desktops the icon needs a tray extension; Auto Poster works without it.
      </p>
      {error && <div className="notice bad" role="alert"><p>{error}</p></div>}
    </section>
  );
}
