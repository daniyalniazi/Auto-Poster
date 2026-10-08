import { useEffect, useState } from "react";
import ReminderBanner from "./components/ReminderBanner";
import ComposePage from "./pages/ComposePage";
import DraftsPage from "./pages/DraftsPage";
import HistoryPage from "./pages/HistoryPage";
import ScheduledPage from "./pages/ScheduledPage";
import SettingsPage from "./pages/SettingsPage";
import StatsPage from "./pages/StatsPage";

const PAGES = [
  { hash: "#/", label: "Create post" },
  { hash: "#/drafts", label: "Drafts" },
  { hash: "#/scheduled", label: "Scheduled" },
  { hash: "#/history", label: "History" },
  { hash: "#/stats", label: "Stats" },
  { hash: "#/settings", label: "Settings" },
];

function currentHash(): string {
  const hash = window.location.hash.split("?")[0];
  return PAGES.some((p) => p.hash === hash) ? hash : "#/";
}

export default function App() {
  const [page, setPage] = useState(currentHash);
  const [fullHash, setFullHash] = useState(window.location.hash);

  useEffect(() => {
    const onChange = () => {
      setPage(currentHash());
      setFullHash(window.location.hash);
    };
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
        <ReminderBanner />
        {/* key: switching between "edit scheduled post" and "new post" starts a fresh form */}
        {page === "#/" && <ComposePage key={fullHash} />}
        {page === "#/drafts" && <DraftsPage />}
        {page === "#/scheduled" && <ScheduledPage />}
        {page === "#/history" && <HistoryPage />}
        {page === "#/stats" && <StatsPage />}
        {page === "#/settings" && <SettingsPage />}
      </main>
    </>
  );
}
