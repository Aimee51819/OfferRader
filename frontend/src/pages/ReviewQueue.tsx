import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Review } from "../api/types";
import { Empty, ErrorBox, Loading } from "../components/States";
import { eventLabel, formatDateOnly, formatInstant } from "../lib/format";

function Deadline({ d }: { d: Review["proposed_changes"]["events"][number]["deadline"] }) {
  if (!d) return null;
  return (
    <div className={d.needs_review ? "alert alert-warn" : ""}>
      <div>
        Due: <strong>{d.due_at ? formatInstant(d.due_at) : d.due_date ? `${formatDateOnly(d.due_date)} (date only)` : "could not determine"}</strong>
      </div>
      {d.raw && <blockquote className="evidence">“{d.raw}”</blockquote>}
      {d.assumptions.length > 0 && <ul className="reasons">{d.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>}
    </div>
  );
}

function ReviewCard({ r }: { r: Review }) {
  const qc = useQueryClient();
  const pc = r.proposed_changes;
  const [target, setTarget] = useState<string>(r.target?.id ?? (pc.create_application ? "__new__" : ""));
  const [company, setCompany] = useState(pc.company.resolved_name ?? pc.company.name ?? "");
  const [role, setRole] = useState(pc.role.title ?? "");
  const [req, setReq] = useState(pc.role.requisition_id ?? "");
  const [conflict, setConflict] = useState<"use_new" | "keep_existing">("use_new");
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const done = () => qc.invalidateQueries();

  const approve = useMutation({
    mutationFn: () => {
      const edits: Record<string, unknown> = {};
      if (company && company !== (pc.company.resolved_name ?? pc.company.name)) edits.company = company;
      if (role && role !== pc.role.title) edits.role_title = role;
      if (req && req !== pc.role.requisition_id) edits.external_job_id = req;
      if (pc.conflicts.length) edits.conflict_resolution = conflict;
      return api.approve(r.id, {
        target_application_id: target && target !== "__new__" ? target : undefined,
        create_new: target === "__new__",
        edits: Object.keys(edits).length ? edits : undefined,
        notes: notes || undefined,
      });
    },
    onSuccess: done,
    onError: (e) => setErr(String(e)),
  });
  const reject = useMutation({ mutationFn: () => api.reject(r.id, notes || undefined), onSuccess: done });

  return (
    <article className="card review-card" aria-label={`Review: ${r.email?.subject ?? r.id}`}>
      <div className="flex-between">
        <div>
          <span className="chip">{r.proposal_type.replace("_", " ")}</span> <strong className="review-reason" title={r.reason}>{r.reason}</strong>
        </div>
        <span className="muted">confidence {(r.confidence * 100).toFixed(0)}%</span>
      </div>
      <div className="two-col">
        <section>
          <h4>Email (evidence)</h4>
          {r.email ? (
            <>
              <div><strong>{r.email.subject}</strong></div>
              <div className="muted">{r.email.sender_name} &lt;{r.email.sender_address}&gt; · {formatInstant(r.email.received_at)}</div>
              {/* Plain-text excerpt; never rendered as HTML. */}
              <pre className="email-excerpt">{r.email.excerpt}</pre>
              {r.email.web_link && <a href={r.email.web_link} target="_blank" rel="noopener noreferrer nofollow">Open source message ↗</a>}
            </>
          ) : <p className="muted">No source email.</p>}
        </section>
        <section>
          <h4>What the agent extracted</h4>
          <label className="field">Company <input value={company} onChange={(e) => setCompany(e.target.value)} /></label>
          <div className="muted">{pc.company.resolution}{pc.company.ambiguous ? " (ambiguous)" : ""}</div>
          <label className="field">Role <input value={role} onChange={(e) => setRole(e.target.value)} /></label>
          <label className="field">Requisition <input value={req} onChange={(e) => setReq(e.target.value)} /></label>
          {pc.events.map((e) => (
            <div key={e.index} className="event-block">
              <strong>{eventLabel(e.type)}</strong>
              {e.implied_stage && <span className="muted"> → {e.implied_stage}</span>}
              {e.action_required && <div>{e.action_required}</div>}
              {e.evidence_quotes.map((q, i) => <blockquote key={i} className="evidence">“{q}”</blockquote>)}
              <Deadline d={e.deadline} />
              {e.interview_time && <Deadline d={e.interview_time} />}
            </div>
          ))}
          {pc.flags.length > 0 && <ul className="reasons warn">{pc.flags.map((f, i) => <li key={i}>{f}</li>)}</ul>}
          {pc.conflicts.map((c) => (
            <div key={c.action_item_id} className="alert alert-warn">
              <strong>Deadline conflict.</strong> On file: {c.existing.due_at ? formatInstant(c.existing.due_at) : c.existing.due_date} (“{c.existing.raw}”).
              New email: {c.new.due_at ? formatInstant(c.new.due_at) : c.new.due_date} (“{c.new.raw}”).
              <div className="row">
                <label><input type="radio" checked={conflict === "use_new"} onChange={() => setConflict("use_new")} /> Use the new deadline</label>
                <label><input type="radio" checked={conflict === "keep_existing"} onChange={() => setConflict("keep_existing")} /> Keep the existing one</label>
              </div>
            </div>
          ))}
        </section>
      </div>
      <fieldset className="candidates">
        <legend>Attach to</legend>
        {r.alternatives.map((c) => (
          <label key={c.application_id} className={`candidate ${c.disqualified ? "muted" : ""}`}>
            <input type="radio" name={`t-${r.id}`} checked={target === c.application_id} disabled={c.disqualified} onChange={() => setTarget(c.application_id)} />
            <span>
              <Link to={`/applications/${c.application_id}`}>{c.company} — {c.role_title}</Link> {c.external_job_id && `#${c.external_job_id}`} · {c.stage} · score {c.score.toFixed(2)}
              <div className="muted">{c.reasons.join("; ")}</div>
            </span>
          </label>
        ))}
        <label className="candidate">
          <input type="radio" name={`t-${r.id}`} checked={target === "__new__"} onChange={() => setTarget("__new__")} />
          <span>Create a new application{pc.events.some((e) => e.type.startsWith("application_")) ? " (verified by this acknowledgment)" : " (unverified: no submission evidence)"}</span>
        </label>
      </fieldset>
      <textarea aria-label="Review notes" placeholder="Notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} rows={1} />
      {err && <div className="alert alert-error">{err}</div>}
      <div className="row">
        <button className="btn-primary" disabled={!target || approve.isPending} onClick={() => approve.mutate()}>Approve</button>
        <button className="btn-danger" disabled={reject.isPending} onClick={() => reject.mutate()}>Reject</button>
        <span className="muted">{r.parser && `Parsed by ${r.parser.model} · prompt ${r.parser.prompt_version}`}</span>
      </div>
    </article>
  );
}

function History() {
  const qc = useQueryClient();
  const { data: approved = [] } = useQuery({ queryKey: ["reviews", "approved"], queryFn: () => api.reviews("approved") });
  const { data: edited = [] } = useQuery({ queryKey: ["reviews", "edited"], queryFn: () => api.reviews("edited") });
  const { data: rejected = [] } = useQuery({ queryKey: ["reviews", "rejected"], queryFn: () => api.reviews("rejected") });
  const undo = useMutation({ mutationFn: (id: string) => api.undo(id), onSuccess: () => qc.invalidateQueries() });
  const rows = [...approved, ...edited, ...rejected].sort((a, b) => (b.reviewed_at ?? "").localeCompare(a.reviewed_at ?? ""));
  if (rows.length === 0) return <Empty>No reviewed items yet.</Empty>;
  return (
    <ul className="stack plain">
      {rows.slice(0, 50).map((r) => (
        <li key={r.id} className="card row flex-between">
          <span>
            <span className="chip">{r.state}</span> {r.email?.subject} {r.target && <>→ <Link to={`/applications/${r.target.id}`}>{r.target.company}: {r.target.role_title}</Link></>}
            <span className="muted"> {formatInstant(r.reviewed_at)}</span>
          </span>
          <button className="btn-ghost" onClick={() => undo.mutate(r.id)}>Undo</button>
        </li>
      ))}
    </ul>
  );
}

export default function ReviewQueue() {
  const [tab, setTab] = useState<"pending" | "history">("pending");
  const { data, error, isLoading } = useQuery({ queryKey: ["reviews", "pending"], queryFn: () => api.reviews("pending"), refetchInterval: 60000 });
  return (
    <div className="stack">
      <div className="page-header">
        <h1 className="page-title">Review queue</h1>
        <p className="subtitle">Uncertain matches, terminal decisions, ambiguous deadlines and unknown applications wait here. Nothing is applied until you approve.</p>
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "pending"} className={`tab ${tab === "pending" ? "active" : ""}`} onClick={() => setTab("pending")}>Pending {data ? `(${data.length})` : ""}</button>
        <button role="tab" aria-selected={tab === "history"} className={`tab ${tab === "history" ? "active" : ""}`} onClick={() => setTab("history")}>Reviewed</button>
      </div>
      {tab === "history" ? <History /> : (
        <>
          {isLoading && <Loading />}
          {error && <ErrorBox error={error} />}
          {data && data.length === 0 && <Empty>Nothing to review.</Empty>}
          {data?.map((r) => <ReviewCard key={r.id} r={r} />)}
        </>
      )}
    </div>
  );
}
