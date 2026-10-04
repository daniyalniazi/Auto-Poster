import { useEffect, useState } from "react";
import ComposePage from "./pages/ComposePage";
import HistoryPage from "./pages/HistoryPage";
import SettingsPage from "./pages/SettingsPage";

const PAGES = [
  { hash: "#/", label: "Create post" },
  { hash: "#/history", label: "History" },
  { hash: "#/settings", label: "Settings" },
];

function currentHash(): string {
  const hash = window.location.hash.split("?")[0];
  return PAGES.some((p) => p.hash === hash) ? hash : "#/";
}

export default function App() {
  const [page, setPage] = useState(currentHash);

  useEffect(() => {
    const onChange = () => setPage(currentHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return (
    <>
      <header className="app-header">
        <div className="app-header-inner">
          <span className="brand">📣 Auto Poster</span>
          <nav className="nav" aria-label="Main">
            {PAGES.map((p) => (
              <a key={p.hash} href={p.hash} aria-current={page === p.hash ? "page" : undefined}>
                {p.label}
              </a>
            ))}
          </nav>
        </div>
      </header>
      <main>
        {page === "#/" && <ComposePage />}
        {page === "#/history" && <HistoryPage />}
        {page === "#/settings" && <SettingsPage />}
      </main>
    </>
  );
}
