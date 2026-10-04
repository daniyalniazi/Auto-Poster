import { useEffect, useState } from "react";
import PlatformSettingsCard from "../components/PlatformSettingsCard";
import { api, type Platform } from "../services/api";

export default function SettingsPage() {
  const [platforms, setPlatforms] = useState<Platform[] | null>(null);
  const [storage, setStorage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    api.platforms().then(setPlatforms).catch((e) => setError(e.message));
  }

  useEffect(() => {
    reload();
    api.info().then((i) => setStorage(i.secret_storage)).catch(() => undefined);
    // Coming back from a sign-in tab: show the new connection state.
    window.addEventListener("focus", reload);
    return () => window.removeEventListener("focus", reload);
  }, []);

  if (error) return <div className="notice bad" role="alert"><p>{error}</p></div>;
  if (!platforms) return <p className="muted">Loading…</p>;

  return (
    <>
      <h1>Settings</h1>
      <p className="muted">
        Connect the platforms you want to post to. Everything you enter here stays on this computer. Passwords and
        tokens are kept in {storage === "file" ? "a private file" : "your system's secure password storage"} and are
        only sent to the platform they belong to.
      </p>
      {storage === "file" && (
        <div className="notice warn">
          <p>
            Your computer has no secure password storage (keyring), so Auto Poster saves passwords and tokens in a
            file that only your user account can read. Anyone who can log in as you could read it. Installing GNOME
            Keyring or KWallet gives better protection.
          </p>
        </div>
      )}
      {platforms.map((p) => (
        <PlatformSettingsCard key={p.id} platform={p} onChanged={reload} />
      ))}
    </>
  );
}
