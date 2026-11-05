import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type ApplicationFilters } from "../api/client";
import type { Application, Stage } from "../api/types";
import { EvidenceBadge, StageBadge, UrgencyBadge } from "../components/Badges";
import { Empty, ErrorBox, Loading } from "../components/States";
import { STAGE_LABELS, STAGES, describeDue, eventLabel, formatDay, formatInstant } from "../lib/format";

function StatCard({ label, value, hint, to, tone }: { label: string; value: number | string; hint?: string; to?: string; tone?: string }) {
  const body = (
    <div className={`stat-card ${tone ?? ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
  return to ? <Link to={to} className="stat-link">{body}</Link> : body;
}

function SummaryCards() {
  const { data, error, isLoading } = useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 60000 });
  if (isLoading) return <Loading what="Loading summary" />;
  if (error) return <ErrorBox error={error} hint="Is the backend running on 127.0.0.1:8000?" />;
  if (!data) return null;
  const s = data.sync;
  return (
    <>
      {s.warnings.map((w) => (
        <div key={w} className="alert alert-warn" role="alert">{w}</div>
      ))}
      <div className="stat-grid">
        <StatCard label="Active applications" value={data.active_applications} hint={`${data.verified_submissions} verified · ${data.unverified_records} unverified`} />
        <StatCard label="Submitted / acknowledged" value={data.submitted_or_acknowledged} />
        <StatCard label="Pending OAs" value={data.pending_oa} to="/deadlines" />
        <StatCard label="Due within 72 h" value={data.deadlines_within_72h} to="/deadlines" tone={data.deadlines_within_72h ? "stat-hot" : ""} />
        <StatCard label="Overdue" value={data.overdue} to="/deadlines" tone={data.overdue ? "stat-danger" : ""} />
        <StatCard label="No reliable deadline" value={data.no_reliable_deadline} to="/deadlines" />
        <StatCard label="Screens / interviews" value={data.interviews} />
        <StatCard label="Offers" value={data.offers} />
        <StatCard label="Rejected" value={data.rejected} />
        <StatCard label="Needs review" value={data.review_needed} to="/review" tone={data.review_needed ? "stat-hot" : ""} />
        <StatCard
          label="Last sync"
          value={s.last_run ? s.last_run.status : s.enabled_accounts ? "never" : "no inbox"}
          hint={s.last_successful_sync_at ? formatInstant(s.last_successful_sync_at) : s.pending_messages ? `${s.pending_messages} messages waiting to parse` : "Connect an inbox in Settings"}
          to="/settings"
        />
      </div>
    </>
  );
}

function Digest() {
  const { data } = useQuery({ queryKey: ["digest"], queryFn: api.digest, refetchInterval: 300000 });
  if (!data || data.lines.length === 0) return null;
  return (
    <div className="card">
      <h3 className="card-title">Today</h3>
      <ul className="digest">
        {data.lines.map((l, i) => <li key={i}>{l}</li>)}
      </ul>
    </div>
  );
}

function Changes() {
  const { data } = useQuery({ queryKey: ["changes"], queryFn: api.changes, refetchInterval: 120000 });
  if (!data) return null;
  return (
    <div className="card">
      <div className="flex-between">
        <h3 className="card-title">Since last sync</h3>
        <span className="muted">from {formatInstant(data.since)}</span>
      </div>
      {data.new_reviews > 0 && (
        <p><Link to="/review">{data.new_reviews} new item(s) need your review →</Link></p>
      )}
      {data.events.length === 0 ? (
        <p className="muted">No changes.</p>
      ) : (
        <ul className="timeline compact">
          {data.events.slice(0, 12).map((e) => (
            <li key={e.id}>
              <span className="muted">{formatDay(e.recorded_at)}</span>{" "}
              <strong>{eventLabel(e.event_type)}</strong>{" "}
              {e.application_id ? <Link to={`/applications/${e.application_id}`}>{e.company} · {e.role_title}</Link> : null}
              {e.to_stage && <> → {STAGE_LABELS[e.to_stage as Stage] ?? e.to_stage}</>}
              <span className="muted"> ({e.actor})</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Filters({ value, onChange }: { value: ApplicationFilters; onChange: (f: ApplicationFilters) => void }) {
  const { data: resumes = [] } = useQuery({ queryKey: ["resumes"], queryFn: api.resumes });
  const set = (k: keyof ApplicationFilters, v: unknown) => onChange({ ...value, [k]: v === "" ? undefined : v });
  return (
    <div className="toolbar filters" role="search">
      <input aria-label="Search" placeholder="Search company, role, req id…" value={value.q ?? ""} onChange={(e) => set("q", e.target.value)} />
      <select aria-label="Category" value={value.category ?? ""} onChange={(e) => set("category", e.target.value)}>
        <option value="">All categories</option>
        {["PM", "TPM", "SE", "SA", "SWE", "Data", "Other"].map((c) => <option key={c}>{c}</option>)}
      </select>
      <select aria-label="Stage" value={value.stage?.[0] ?? ""} onChange={(e) => set("stage", e.target.value ? [e.target.value] : undefined)}>
        <option value="">All stages</option>
        {STAGES.map((s) => <option key={s} value={s}>{STAGE_LABELS[s]}</option>)}
      </select>
      <select aria-label="Source" value={value.source ?? ""} onChange={(e) => set("source", e.target.value)}>
        <option value="">Any source</option>
        {["email", "manual", "import", "agent", "legacy"].map((s) => <option key={s}>{s}</option>)}
      </select>
      <select aria-label="Resume version" value={value.resume_version_id ?? ""} onChange={(e) => set("resume_version_id", e.target.value)}>
        <option value="">Any resume</option>
        {resumes.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
      </select>
      <select aria-label="Urgency" value={value.urgency ?? ""} onChange={(e) => set("urgency", e.target.value)}>
        <option value="">Any urgency</option>
        <option value="overdue">Overdue</option>
        <option value="within_3h">≤ 3 h</option>
        <option value="within_24h">≤ 24 h</option>
        <option value="within_72h">≤ 72 h</option>
        <option value="no_reliable_deadline">No reliable deadline</option>
      </select>
      <select aria-label="Verification" value={value.verified === undefined ? "" : String(value.verified)} onChange={(e) => set("verified", e.target.value === "" ? undefined : e.target.value === "true")}>
        <option value="">Verified + unverified</option>
        <option value="true">Verified submissions</option>
        <option value="false">Unverified records</option>
      </select>
      <label className="row">
        <input type="checkbox" checked={!!value.needs_review} onChange={(e) => set("needs_review", e.target.checked || undefined)} /> Needs review
      </label>
      <label className="row">
        From <input type="date" aria-label="Submitted from" value={value.submitted_from ?? ""} onChange={(e) => set("submitted_from", e.target.value)} />
      </label>
      <label className="row">
        To <input type="date" aria-label="Submitted to" value={value.submitted_to ?? ""} onChange={(e) => set("submitted_to", e.target.value)} />
      </label>
    </div>
  );
}

export function ApplicationTable({ apps }: { apps: Application[] }) {
  if (apps.length === 0) return <Empty>No applications match. <Link to="/add">Add one</Link> or connect an inbox.</Empty>;
  return (
    <div className="analytics-table-wrap">
      <table className="analytics-table app-table">
        <thead>
          <tr>
            <th>Company</th>
            <th>Role / requisition</th>
            <th>Cat.</th>
            <th>Applied</th>
            <th>Status</th>
            <th>Next action</th>
            <th>Deadline (ET)</th>
            <th>Source</th>
            <th>Resume</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {apps.map((a) => (
            <tr key={a.id}>
              <td>{a.company}</td>
              <td>
                <Link to={`/applications/${a.id}`}>{a.role_title}</Link>
                {a.external_job_id && <div className="muted">#{a.external_job_id}</div>}
                {a.pending_reviews > 0 && <span className="chip chip-warn">{a.pending_reviews} to review</span>}
              </td>
              <td>{a.category ?? "—"}</td>
              <td>{a.submitted_at ? formatDay(a.submitted_at) : "—"}</td>
              <td><StageBadge stage={a.stage} locked={a.manual_stage_lock} /></td>
              <td>{a.next_action ? a.next_action.title : <span className="muted">—</span>}</td>
              <td>
                {a.next_action ? (
                  <>
                    <div>{describeDue(a.next_action)}</div>
                    <UrgencyBadge urgency={a.next_action.urgency} />
                  </>
                ) : "—"}
              </td>
              <td>{a.source}</td>
              <td>{a.resume_label ?? "—"}</td>
              <td><EvidenceBadge verified={a.verified} type={a.submission_evidence_type} /></td>
            </tr>
          ))}
        </tbody>
      </table>
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

function Kanban({ apps }: { apps: Application[] }) {
  return (
    <div className="board">
      {BOARD.map((col) => {
        const items = apps.filter((a) => col.stages.includes(a.stage));
        return (
          <section className="column" key={col.title} aria-label={col.title}>
            <div className="column-header">
              {col.title} <span className="column-count">{items.length}</span>
            </div>
            <div className="column-body">
              {items.map((a) => (
                <Link key={a.id} to={`/applications/${a.id}`} className="app-card">
                  <strong>{a.company}</strong>
                  <div>{a.role_title}</div>
                  <div className="app-card-foot">
                    <StageBadge stage={a.stage} />
                    <EvidenceBadge verified={a.verified} type={a.submission_evidence_type} />
                  </div>
                  {a.next_action && (
                    <div className="muted">
                      {a.next_action.title}: {describeDue(a.next_action)} <UrgencyBadge urgency={a.next_action.urgency} />
                    </div>
                  )}
                </Link>
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
  const [view, setView] = useState<"table" | "board">("table");
  const key = useMemo(() => JSON.stringify(filters), [filters]);
  const { data: apps, error, isLoading } = useQuery({
    queryKey: ["applications", key],
    queryFn: () => api.applications(filters),
    refetchInterval: 60000,
  });
  return (
    <div className="stack">
      <div className="page-header flex-between">
        <h1 className="page-title">Dashboard</h1>
        <Link className="btn-primary" to="/add">Add application</Link>
      </div>
      <SummaryCards />
      <div className="two-col">
        <Digest />
        <Changes />
      </div>
      <div className="flex-between">
        <h2 className="section-title">Applications</h2>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={view === "table"} className={`tab ${view === "table" ? "active" : ""}`} onClick={() => setView("table")}>Table</button>
          <button role="tab" aria-selected={view === "board"} className={`tab ${view === "board" ? "active" : ""}`} onClick={() => setView("board")}>Board</button>
        </div>
      </div>
      <Filters value={filters} onChange={setFilters} />
      {isLoading && <Loading what="Loading applications" />}
      {error && <ErrorBox error={error} />}
      {apps && (view === "table" ? <ApplicationTable apps={apps} /> : <Kanban apps={apps} />)}
    </div>
  );
}
