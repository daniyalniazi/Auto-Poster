import { useEffect, useState } from "react";
import { api, type ConnectionStatus, type Platform } from "../services/api";
import FieldInput from "./FieldInput";
import { TechDetails } from "./ResultList";

interface Props {
  platform: Platform;
  onChanged: () => void;
}

export default function PlatformSettingsCard({ platform, onChanged }: Props) {
  const [saved, setSaved] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<ConnectionStatus | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);

  const secretKeys = new Set(platform.settings_fields.filter((f) => f.kind === "secret").map((f) => f.key));

  function load() {
    api.getSettings(platform.id).then((s) => {
      setSaved(s);
      // Secret inputs start empty; the saved value is only shown masked as a placeholder.
      setValues(Object.fromEntries(Object.entries(s).filter(([k]) => !secretKeys.has(k))));
    });
  }

  // Reload when the connection state changes (e.g. after signing in on another tab).
  useEffect(load, [platform.id, platform.configured]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      setMessage({ kind: "bad", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  const save = () =>
    run("save", async () => {
      await api.saveSettings(platform.id, values);
      load();
      onChanged();
      setStatus(null);
      setMessage({ kind: "ok", text: "Saved. Click “Test connection” to check it works." });
    });

  const test = () =>
    run("test", async () => {
      setStatus(await api.testConnection(platform.id));
    });

  const disconnect = () => {
    if (!window.confirm(`Remove the saved ${platform.name} settings from this computer?`)) return;
    run("disconnect", async () => {
      await api.disconnect(platform.id);
      load();
      onChanged();
      setStatus(null);
      setMessage({ kind: "ok", text: `${platform.name} was disconnected.` });
    });
  };

  const runAction = (actionId: string) => {
    // Open the tab now, while we're still inside the click, so pop-up blockers allow it.
    // Its address is filled in once the server returns the sign-in link.
    const tab = window.open("about:blank", "_blank");
    return run(actionId, async () => {
      try {
        await api.saveSettings(platform.id, values);
        const result = await api.runAction(platform.id, actionId);
        if (result.open_url && tab) {
          tab.opener = null;
          tab.location.href = result.open_url;
        } else {
          tab?.close();
          if (result.open_url) window.open(result.open_url, "_blank", "noopener");
        }
        setMessage({ kind: result.ok ? "ok" : "bad", text: result.message });
        load();
        onChanged();
      } catch (e) {
        tab?.close();
        throw e;
      }
    });
  };

  const guide = platform.setup_guide;

  return (
    <section className="card" aria-labelledby={`settings-${platform.id}`}>
      <div className="row" style={{ marginBottom: 6 }}>
        <h2 id={`settings-${platform.id}`} style={{ margin: 0 }}>{platform.name}</h2>
        {platform.configured ? <span className="badge ok">Connected</span> : <span className="badge">Not connected</span>}
      </div>
      <p className="muted" style={{ marginTop: 0 }}>{platform.description}</p>

      <details className="guide" open={!platform.configured}>
        <summary>How to connect {platform.name}</summary>
        <ol>
          {guide.steps.map((step, i) => <li key={i}>{step}</li>)}
        </ol>
        {guide.notes.map((note, i) => <p className="help" key={i}>{note}</p>)}
        {guide.docs_url && (
          <p className="help">
            Official guide: <a href={guide.docs_url} target="_blank" rel="noreferrer">{guide.docs_url}</a>
          </p>
        )}
      </details>

      <form
        style={{ marginTop: 14 }}
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        {platform.settings_fields.map((field) => (
          <FieldInput
            key={field.key}
            field={field}
            idPrefix={`set-${platform.id}`}
            value={values[field.key] ?? ""}
            savedMask={secretKeys.has(field.key) ? saved[field.key] : undefined}
            onChange={(v) => setValues((prev) => ({ ...prev, [field.key]: v }))}
          />
        ))}
        <div className="row">
          <button type="submit" className="primary" disabled={busy !== null}>
            {busy === "save" ? "Saving…" : "Save"}
          </button>
          {platform.actions.map((a) => (
            <button type="button" key={a.id} onClick={() => runAction(a.id)} disabled={busy !== null} title={a.help}>
              {busy === a.id ? "Working…" : a.label}
            </button>
          ))}
          <button type="button" onClick={test} disabled={busy !== null || !platform.configured}>
            {busy === "test" ? "Testing…" : "Test connection"}
          </button>
          <span className="spacer" />
          {platform.configured && (
            <button type="button" className="danger" onClick={disconnect} disabled={busy !== null}>
              Disconnect
            </button>
          )}
        </div>
      </form>

      {message && <div className={`notice ${message.kind}`} role="status"><p>{message.text}</p></div>}
      {status && (
        <div className={`notice ${status.ok ? "ok" : "bad"}`} role="status">
          <p>{status.ok ? "✓ " : "✗ "}{status.message}</p>
          <TechDetails text={status.technical_details} />
        </div>
      )}
    </section>
  );
}
