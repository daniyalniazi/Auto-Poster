import { useEffect, useRef, useState } from "react";
import { api } from "../services/api";

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Backup, restore and freeing up space for stored images. */
export default function DataCard({ onRestored }: { onRestored: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [mediaBytes, setMediaBytes] = useState<number | null>(null);
  const [days, setDays] = useState("90");
  const fileRef = useRef<HTMLInputElement>(null);

  const loadStorage = () => api.storage().then((s) => setMediaBytes(s.media_bytes)).catch(() => undefined);
  useEffect(() => {
    loadStorage();
  }, []);

  async function run(label: string, action: () => Promise<string>) {
    setBusy(label);
    setMessage(null);
    try {
      setMessage({ kind: "ok", text: await action() });
    } catch (e) {
      setMessage({ kind: "bad", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  const exportBackup = () =>
    run("export", async () => {
      const { blob, filename } = await api.downloadBackup();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      return `Backup saved as ${filename} (in your Downloads folder). Keep it somewhere safe.`;
    });

  const restore = (file: File) => {
    if (!window.confirm(
      "Restore this backup? It replaces your current history, drafts, scheduled posts, reminders and settings. " +
      "Passwords and tokens aren't in backups, so you may need to reconnect some platforms afterwards.",
    )) return;
    run("restore", async () => {
      const result = await api.restoreBackup(file);
      onRestored();
      loadStorage();
      return `Backup restored: ${result.posts} posts and ${result.images} images. Check Settings and reconnect any ` +
        "platform that shows “Not connected”.";
    });
  };

  const cleanup = () => {
    if (!window.confirm(`Remove stored images from posts older than ${days} days? The posts themselves stay on the ` +
      "platforms and in History; you just can't “Post again” with those images.")) return;
    run("cleanup", async () => {
      const result = await api.cleanupStorage(Number(days));
      loadStorage();
      return result.freed_bytes > 0 ? `Freed ${size(result.freed_bytes)}.` : "There was nothing to remove.";
    });
  };

  return (
    <section className="card" aria-labelledby="data-heading">
      <h2 id="data-heading">Your data</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Everything Auto Poster stores is on this computer. A backup contains your history, drafts, scheduled posts,
        reminders, hashtag sets, settings and images, but never passwords or tokens.
      </p>
      <div className="row">
        <button onClick={exportBackup} disabled={busy !== null}>
          {busy === "export" ? "Preparing…" : "Export backup"}
        </button>
        <button onClick={() => fileRef.current?.click()} disabled={busy !== null}>
          {busy === "restore" ? "Restoring…" : "Restore a backup…"}
        </button>
        <input ref={fileRef} type="file" accept=".zip,application/zip" hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) restore(file);
          }} />
      </div>

      <h3 style={{ marginTop: 18 }}>Storage</h3>
      <p className="help" style={{ marginTop: 0 }}>
        Images are kept so you can “Post again”.{" "}
        {mediaBytes !== null && <>They currently use <strong>{size(mediaBytes)}</strong>.</>}
      </p>
      <div className="row">
        <label htmlFor="cleanup-days" style={{ margin: 0, fontWeight: 500 }}>Remove images from posts older than</label>
        <select id="cleanup-days" value={days} onChange={(e) => setDays(e.target.value)} style={{ width: "auto" }}>
          <option value="30">30 days</option>
          <option value="90">90 days</option>
          <option value="365">1 year</option>
        </select>
        <button onClick={cleanup} disabled={busy !== null}>{busy === "cleanup" ? "Removing…" : "Free up space"}</button>
      </div>

      {message && <div className={`notice ${message.kind}`} role="status"><p>{message.text}</p></div>}
    </section>
  );
}
