import { useEffect, useRef } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api/client";
import AddApplication from "./pages/AddApplication";
import Analytics from "./pages/Analytics";
import ApplicationDetail from "./pages/ApplicationDetail";
import Dashboard from "./pages/Dashboard";
import Deadlines from "./pages/Deadlines";
import ImportExport from "./pages/ImportExport";
import Settings from "./pages/Settings";
import { describeDue } from "./lib/format";

function BrandMark() {
  return (
    <svg className="brand-icon" viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#7d8f7b" />
      <rect x="14" y="37" width="8" height="13" rx="3" fill="#f4f1ec" />
      <rect x="29" y="26" width="8" height="24" rx="3" fill="#f4f1ec" />
      <rect x="44" y="14" width="8" height="36" rx="3" fill="#d8b4ac" />
    </svg>
  );
}

/** Shows reminder banners from the local reminder log; optional browser notification. */
function Reminders() {
  const { data = [] } = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, refetchInterval: 60000 });
  const seen = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const n of data) {
      if (seen.current.has(n.id)) continue;
      seen.current.add(n.id);
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        new Notification(n.action_item.title, { body: `${n.action_item.company}: due ${describeDue(n.action_item)}` });
      }
    }
  }, [data]);
  if (data.length === 0) return null;
  return (
    <div className="reminders" role="region" aria-label="Reminders">
      {data.slice(0, 3).map((n) => (
        <div key={n.id} className="alert alert-warn reminder">
          <span>
            ⏰ <strong>{n.action_item.title}</strong> — {n.action_item.company}: {n.action_item.role_title}. Due {describeDue(n.action_item)}
          </span>
          <button className="btn-ghost" onClick={() => api.dismissNotification(n.id)}>Dismiss</button>
        </div>
      ))}
    </div>
  );
}

function Sidebar() {
  const { data: summary } = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 60000 });
  const urgent = (summary?.overdue ?? 0) + (summary?.deadlines_within_72h ?? 0);
  return (
    <aside className="sidebar">
      <div className="brand">
        <BrandMark />
        OfferRadar
      </div>
      <nav aria-label="Main">
        <NavLink to="/" end className="nav-link">Dashboard</NavLink>
        <NavLink to="/deadlines" className="nav-link">
          Deadlines {urgent > 0 && <span className="nav-badge">{urgent}</span>}
        </NavLink>
        <NavLink to="/add" className="nav-link">Add application</NavLink>
        <NavLink to="/analytics" className="nav-link">Funnel</NavLink>
        <NavLink to="/import" className="nav-link">Import / export</NavLink>
        <NavLink to="/settings" className="nav-link">Settings</NavLink>
      </nav>
      <div className="spacer" />
      {typeof Notification !== "undefined" && Notification.permission === "default" && (
        <button className="btn-ghost" onClick={() => Notification.requestPermission()}>Enable browser reminders</button>
      )}
      <div className="muted small sidebar-foot">Local only · read-only mail</div>
    </aside>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="content">
        <Reminders />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/deadlines" element={<Deadlines />} />
          <Route path="/add" element={<AddApplication />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/import" element={<ImportExport />} />
          <Route path="/applications/:id" element={<ApplicationDetail />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
