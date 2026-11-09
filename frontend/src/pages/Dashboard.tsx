import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ApplicationFilters } from "../api/client";
import type { Application, Stage } from "../api/types";
import { StageBadge, UrgencyBadge } from "../components/Badges";
import Modal from "../components/Modal";
import RecoverButton from "../components/RecoverButton";
import { Empty, ErrorBox, Loading } from "../components/States";
import { STAGE_LABELS, STAGES, describeDue, formatDay, relativeTo } from "../lib/format";

export const CATEGORIES = ["PM", "SE", "SDE/SWE", "Data", "Other"] as const;

const CATEGORY_CLASS: Record<string, string> = {
  PM: "cat-pm",
  SE: "cat-se",
  "SDE/SWE": "cat-swe",
  Data: "cat-data",
  Other: "cat-other",
};

export function CategoryChip({ category }: { category: string | null }) {
  if (!category) return <span className="muted">—</span>;
  return <span className={`cat ${CATEGORY_CLASS[category] ?? "cat-other"}`}>{category}</span>;
}

/** The date the application went in: confirmation date, else when it was first tracked. */
export const appliedAt = (a: Application) => a.applied_at ?? a.submitted_at ?? a.created_at;

function StatCard({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className={`stat-card ${tone ?? ""}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function SummaryCards() {
  const { data, error } = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 60000 });
  if (error) return <ErrorBox error={error} hint="Is the backend running on 127.0.0.1:8000?" />;
  if (!data) return null;
  return (
    <>
      {data.sync.warnings.map((w) => (
        <div key={w} className="alert alert-warn" role="alert">{w}</div>
      ))}
      <div className="stat-grid">
        <StatCard label="Applications" value={data.active_applications} />
        <StatCard label="Submitted / acknowledged" value={data.submitted_or_acknowledged} />
        <StatCard label="Pending OAs" value={data.pending_oa} tone={data.pending_oa ? "stat-accent" : ""} />
        <StatCard label="Due within 72 h" value={data.deadlines_within_72h} tone={data.deadlines_within_72h ? "stat-hot" : ""} />
        <StatCard label="Interviews" value={data.interviews} />
        <StatCard label="Offers" value={data.offers} />
        <StatCard label="Rejected" value={data.rejected} />
      </div>
    </>
  );
}

/** Manual sync, with the last-sync time next to it. */
export function SyncButton() {
  const qc = useQueryClient();
  const [polling, setPolling] = useState(false);
  const { data: status } = useQuery({
    queryKey: ["sync-status"],
    queryFn: api.syncStatus,
    refetchInterval: polling ? 3000 : 60000,
  });
  const running = polling || !!status?.running;
  useEffect(() => {
    if (polling && status && !status.running) {
      setPolling(false);
      qc.invalidateQueries();
    }
  }, [polling, status, qc]);
  const start = useMutation({
    mutationFn: api.syncNow,
    onSuccess: () => setPolling(true),
  });
  const last = status?.last_successful_sync_at;
  return (
    <div className="sync-box">
      <span className="muted small">{running ? "Syncing mail…" : last ? `Last synced ${relativeTo(last)}` : "Not synced yet"}</span>
      <button className="btn-secondary" onClick={() => start.mutate()} disabled={running || start.isPending}>
        <span className={running ? "spin" : ""} aria-hidden="true">↻</span> {running ? "Syncing" : "Sync now"}
      </button>
    </div>
  );
}

export interface ChipFilters {
  categories: string[];
  stages: Stage[];
}

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

function ChipRow<T extends string>({ label, options, selected, onChange, render, counts }: {
  label: string;
  options: readonly T[];
  selected: T[];
  onChange: (v: T[]) => void;
  render: (v: T) => string;
  counts: Record<string, number>;
}) {
  return (
    <div className="chip-row" role="group" aria-label={label}>
      <span className="filter-label chip-row-label">{label}</span>
      <div className="chip-options">
        <button type="button" className={`filter-chip ${selected.length === 0 ? "selected" : ""}`} aria-pressed={selected.length === 0} onClick={() => onChange([])}>
          All
        </button>
        {options.map((o) => (
          <button
            type="button"
            key={o}
            className={`filter-chip ${selected.includes(o) ? "selected" : ""}`}
            aria-pressed={selected.includes(o)}
            onClick={() => onChange(toggle(selected, o))}
          >
            {render(o)} <span className="filter-chip-count">{counts[o] ?? 0}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Filters({ value, onChange, chips, onChips, apps }: {
  value: ApplicationFilters;
  onChange: (f: ApplicationFilters) => void;
  chips: ChipFilters;
  onChips: (c: ChipFilters) => void;
  apps: Application[];
}) {
  const set = (k: keyof ApplicationFilters, v: unknown) => onChange({ ...value, [k]: v === "" ? undefined : v });
  const active = Object.values(value).some((v) => v !== undefined && v !== "") || chips.categories.length > 0 || chips.stages.length > 0;
  const catCounts: Record<string, number> = {};
  const stageCounts: Record<string, number> = {};
  for (const a of apps) {
    catCounts[a.category ?? "Other"] = (catCounts[a.category ?? "Other"] ?? 0) + 1;
    stageCounts[a.stage] = (stageCounts[a.stage] ?? 0) + 1;
  }
  // Only statuses that occur (plus any still selected) — no wall of empty options.
  const stageOptions = STAGES.filter((st) => (stageCounts[st] ?? 0) > 0 || chips.stages.includes(st));
  return (
    <div className="filter-card" role="search">
      <div className="filter-top">
        <div className="filter-field filter-search">
          <span className="filter-label">Search</span>
          <input aria-label="Search" placeholder="Company, role, req id…" value={value.q ?? ""} onChange={(e) => set("q", e.target.value)} />
        </div>
        <div className="filter-field">
          <span className="filter-label">Applied between</span>
          <div className="date-range">
            <input type="date" aria-label="Submitted from" value={value.submitted_from ?? ""} onChange={(e) => set("submitted_from", e.target.value)} />
            <span className="date-sep">–</span>
            <input type="date" aria-label="Submitted to" value={value.submitted_to ?? ""} onChange={(e) => set("submitted_to", e.target.value)} />
          </div>
        </div>
        <button className="btn-ghost filter-clear" onClick={() => { onChange({}); onChips({ categories: [], stages: [] }); }} disabled={!active}>
          Clear all
        </button>
      </div>
      <ChipRow label="Category" options={CATEGORIES} selected={chips.categories as (typeof CATEGORIES)[number][]} onChange={(v) => onChips({ ...chips, categories: v })} render={(c) => c} counts={catCounts} />
      <ChipRow label="Status" options={stageOptions} selected={chips.stages} onChange={(v) => onChips({ ...chips, stages: v })} render={(st) => STAGE_LABELS[st]} counts={stageCounts} />
    </div>
  );
}

/** Client-side category / status filtering (both multi-select). */
export function applyChips(apps: Application[], chips: ChipFilters): Application[] {
  return apps.filter(
    (a) =>
      (chips.categories.length === 0 || chips.categories.includes(a.category ?? "Other")) &&
      (chips.stages.length === 0 || chips.stages.includes(a.stage)),
  );
}

type SortDir = "desc" | "asc";
export type SortKey = "company" | "applied";

interface CompanyGroup {
  key: string;
  company: string;
  apps: Application[];
}

/** One group per company name (case-insensitive), roles sorted by applied date. */
export function groupByCompany(apps: Application[], dir: SortDir): CompanyGroup[] {
  const sign = dir === "desc" ? -1 : 1;
  const cmp = (a: Application, b: Application) => sign * appliedAt(a).localeCompare(appliedAt(b));
  const groups = new Map<string, CompanyGroup>();
  for (const a of apps) {
    const key = a.company.trim().toLowerCase();
    const g = groups.get(key) ?? { key, company: a.company, apps: [] };
    g.apps.push(a);
    groups.set(key, g);
  }
  const out = [...groups.values()];
  for (const g of out) g.apps.sort(cmp);
  out.sort((x, y) => cmp(x.apps[0], y.apps[0]));
  return out;
}

/** Company mode: companies A→Z, each company's roles by applied date. */
export function sortByCompany(apps: Application[], dir: SortDir): CompanyGroup[] {
  const groups = groupByCompany(apps, dir);
  return groups.sort((x, y) => x.company.localeCompare(y.company, undefined, { sensitivity: "base" }));
}

/** Applied mode: one flat list by applied date, no grouping. */
export function sortByApplied(apps: Application[], dir: SortDir): Application[] {
  const sign = dir === "desc" ? -1 : 1;
  return [...apps].sort((a, b) => sign * appliedAt(a).localeCompare(appliedAt(b)));
}

/** Opens the latest email behind this application in Outlook / Gmail (new tab). */
export function EmailLink({ app }: { app: Application }) {
  const href = app.source_email_link;
  if (!href || !/^https:\/\//i.test(href)) return null;
  return (
    <a className="email-link" href={href} target="_blank" rel="noopener noreferrer nofollow" title={app.source_email_subject ?? "Open email"}>
      <svg className="email-icon" viewBox="0 0 16 16" aria-hidden="true">
        <rect x="1.75" y="3.25" width="12.5" height="9.5" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <path d="M2.5 4.5 8 8.75l5.5-4.25" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      Email
    </a>
  );
}

export function ApplicationTable({ apps }: { apps: Application[] }) {
  const qc = useQueryClient();
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "applied", dir: "desc" });
  const clickSort = (key: SortKey) =>
    setSort((cur) => (cur.key === key ? { key, dir: cur.dir === "desc" ? "asc" : "desc" } : { key, dir: key === "company" ? "asc" : "desc" }));
  const arrow = (key: SortKey) => (sort.key === key ? (sort.dir === "desc" ? "↓" : "↑") : "↕");
  const [toRemove, setToRemove] = useState<Application | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteApplication(id),
    onSuccess: () => {
      setToRemove(null);
      qc.invalidateQueries();
    },
  });
  // In applied mode every app is its own one-row "group", so the same row markup works.
  const groups = useMemo<CompanyGroup[]>(
    () =>
      sort.key === "company"
        ? sortByCompany(apps, sort.dir)
        : sortByApplied(apps, sort.dir).map((a) => ({ key: a.id, company: a.company, apps: [a] })),
    [apps, sort],
  );
  if (apps.length === 0) return <Empty>No applications match. <Link to="/add">Add one</Link> or sync your inbox.</Empty>;
  return (
    <div className="table-card">
      <table className="app-table">
        <colgroup>
          <col className="col-company" />
          <col />
          <col className="col-cat" />
          <col className="col-date" />
          <col className="col-status" />
          <col className="col-deadline" />
          <col className="col-remove" />
        </colgroup>
        <thead>
          <tr>
            <th aria-sort={sort.key === "company" ? (sort.dir === "desc" ? "descending" : "ascending") : "none"}>
              <button className={`sort-btn ${sort.key === "company" ? "active" : ""}`} onClick={() => clickSort("company")} title="Group by company; click again to flip the date order">
                Company <span aria-hidden="true">{arrow("company")}</span>
              </button>
            </th>
            <th>Role</th>
            <th>Category</th>
            <th aria-sort={sort.key === "applied" ? (sort.dir === "desc" ? "descending" : "ascending") : "none"}>
              <button className={`sort-btn ${sort.key === "applied" ? "active" : ""}`} onClick={() => clickSort("applied")} title="Sort all rows by applied date">
                Applied <span aria-hidden="true">{arrow("applied")}</span>
              </button>
            </th>
            <th>Status</th>
            <th>Next deadline</th>
            <th aria-label="Remove" />
          </tr>
        </thead>
        <tbody>
          {groups.map((g) =>
            g.apps.map((a, i) => (
              <tr key={a.id} className={i === 0 ? "group-start" : "group-cont"}>
                {i === 0 && (
                  <td rowSpan={g.apps.length} className="company-cell">
                    <div className="company-name">{g.company}</div>
                    {sort.key === "company" && g.apps.length > 1 && <div className="muted small">{g.apps.length} roles</div>}
                  </td>
                )}
                <td>
                  <Link to={`/applications/${a.id}`} className="role-link">{a.role_title}</Link>
                  <div className="role-meta">
                    {a.external_job_id && <span className="muted small">#{a.external_job_id}</span>}
                    <EmailLink app={a} />
                  </div>
                </td>
                <td><CategoryChip category={a.category} /></td>
                <td className="nowrap">{formatDay(appliedAt(a))}</td>
                <td><StageBadge stage={a.stage} locked={a.manual_stage_lock} /></td>
                <td>
                  {a.next_action ? (
                    <div className="deadline-cell">
                      <span>{describeDue(a.next_action)}</span>
                      <UrgencyBadge urgency={a.next_action.urgency} />
                    </div>
                  ) : <span className="muted">—</span>}
                </td>
                <td className="remove-cell">
                  <button className="icon-btn" aria-label={`Remove ${a.company} ${a.role_title}`} title="Remove" onClick={() => setToRemove(a)}>
                    ✕
                  </button>
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>
      {toRemove && (
        <Modal title="Remove this application?" onClose={() => setToRemove(null)}>
          <p>
            <strong>{toRemove.company}</strong> · {toRemove.role_title}
          </p>
          <p className="muted">It will be removed from the dashboard together with its deadlines and history. You can bring it back with Recover.</p>
          {remove.error && <ErrorBox error={remove.error} />}
          <div className="modal-actions">
            <button className="btn-secondary" onClick={() => setToRemove(null)}>Cancel</button>
            <button className="btn-danger is-strong" onClick={() => remove.mutate(toRemove.id)} disabled={remove.isPending}>
              {remove.isPending ? "Removing…" : "Remove"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

const BOARD: { title: string; stages: Stage[] }[] = [
  { title: "Tracked", stages: ["tracked", "ready"] },
  { title: "Submitted", stages: ["submitted", "acknowledged"] },
  { title: "Assessment", stages: ["assessment_invited", "assessment_completed"] },
  { title: "Interviews", stages: ["screening", "interviewing"] },
  { title: "Offer", stages: ["offer", "accepted"] },
  { title: "Closed", stages: ["rejected", "withdrawn", "closed"] },
];

/**
 * Board: every column has the same fixed height and scrolls on its own, so a long
 * "Submitted" column never pushes the page down. Inside a column, roles are grouped
 * under their company and shown as compact one-line rows.
 */
function Kanban({ apps }: { apps: Application[] }) {
  return (
    <div className="board">
      {BOARD.map((col) => {
        const items = apps.filter((a) => col.stages.includes(a.stage));
        const groups = groupByCompany(items, "desc");
        return (
          <section className="column" key={col.title} aria-label={col.title}>
            <div className="column-header">
              {col.title} <span className="column-count">{items.length}</span>
            </div>
            <div className="column-body">
              {groups.length === 0 && <div className="column-empty">—</div>}
              {groups.map((g) => (
                <div key={g.key} className="board-group">
                  <div className="board-company">
                    {g.company}
                    {g.apps.length > 1 && <span className="muted small"> · {g.apps.length}</span>}
                  </div>
                  {g.apps.map((a) => (
                    <Link key={a.id} to={`/applications/${a.id}`} className="board-item" title={a.role_title}>
                      <span className="board-role">{a.role_title}</span>
                      <CategoryChip category={a.category} />
                    </Link>
                  ))}
                </div>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

export default function Dashboard() {
  const [filters, setFilters] = useState<ApplicationFilters>({});
  const [chips, setChips] = useState<ChipFilters>({ categories: [], stages: [] });
  const [view, setView] = useState<"table" | "board">("table");
  const key = useMemo(() => JSON.stringify(filters), [filters]);
  const { data: apps, error, isLoading } = useQuery({
    queryKey: ["applications", key],
    queryFn: () => api.applications(filters),
    refetchInterval: 60000,
  });
  return (
    <div className="stack-lg">
      <div className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <div className="header-actions">
          <SyncButton />
          <Link className="btn-primary" to="/add">Add application</Link>
        </div>
      </div>
      <SummaryCards />
      <section className="stack">
        <div className="section-head">
          <h2 className="section-title">
            Applications {apps && <span className="count-pill">{applyChips(apps, chips).length}</span>}
          </h2>
          <div className="section-tools">
          <RecoverButton kind="application" />
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={view === "table"} className={`tab ${view === "table" ? "active" : ""}`} onClick={() => setView("table")}>Table</button>
            <button role="tab" aria-selected={view === "board"} className={`tab ${view === "board" ? "active" : ""}`} onClick={() => setView("board")}>Board</button>
          </div>
          </div>
        </div>
        <Filters value={filters} onChange={setFilters} chips={chips} onChips={setChips} apps={apps ?? []} />
        {isLoading && <Loading what="Loading applications" />}
        {error && <ErrorBox error={error} />}
        {apps && (view === "table" ? <ApplicationTable apps={applyChips(apps, chips)} /> : <Kanban apps={applyChips(apps, chips)} />)}
      </section>
    </div>
  );
}
