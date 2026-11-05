import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ActionItem, Stage } from "../api/types";
import { EvidenceBadge, StageBadge, UrgencyBadge } from "../components/Badges";
import Modal from "../components/Modal";
import { ErrorBox, Loading } from "../components/States";
import { STAGES, STAGE_LABELS, describeDue, eventLabel, evidenceLabel, formatDay, formatInstant } from "../lib/format";

function DeadlineEditor({ item, onClose }: { item: ActionItem; onClose: () => void }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"instant" | "day">(item.due_at ? "instant" : "day");
  const [value, setValue] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => {
      if (mode === "day") return api.editActionItem(item.id, { due_date: value, due_at: null });
      // datetime-local has no zone: interpret it explicitly as Eastern Time.
      return api.editActionItem(item.id, { due_at: easternLocalToIso(value), due_date: null });
    },
    onSuccess: () => {
      qc.invalidateQueries();
      onClose();
    },
    onError: (e) => setErr(String(e)),
  });
  return (
    <Modal title="Set deadline" onClose={onClose}>
      <p className="muted">Original wording: “{item.due_expression_raw ?? "none"}”</p>
      <div className="row">
        <label><input type="radio" checked={mode === "day"} onChange={() => setMode("day")} /> Date only</label>
        <label><input type="radio" checked={mode === "instant"} onChange={() => setMode("instant")} /> Date and time (Eastern)</label>
      </div>
      <input aria-label="New deadline" type={mode === "day" ? "date" : "datetime-local"} value={value} onChange={(e) => setValue(e.target.value)} />
      {err && <div className="alert alert-error">{err}</div>}
      <div className="modal-actions">
        <button className="btn-primary" disabled={!value || save.isPending} onClick={() => save.mutate()}>Save</button>
      </div>
    </Modal>
  );
}

/** Convert 'YYYY-MM-DDTHH:mm' wall-clock in America/New_York to an ISO instant (DST-aware). */
export function easternLocalToIso(local: string): string {
  const [d, t] = local.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [hh, mm] = t.split(":").map(Number);
  let guess = Date.UTC(y, m - 1, day, hh, mm);
  for (let i = 0; i < 2; i++) {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
      .formatToParts(new Date(guess))
      .reduce<Record<string, string>>((acc, p) => ({ ...acc, [p.type]: p.value }), {});
    const shown = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour % 24, +parts.minute);
    guess += Date.UTC(y, m - 1, day, hh, mm) - shown;
  }
  return new Date(guess).toISOString();
}

export default function ApplicationDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const { data: app, error, isLoading } = useQuery({ queryKey: ["application", id], queryFn: () => api.application(id) });
  const [stage, setStage] = useState<Stage | "">("");
  const [lock, setLock] = useState(false);
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState<ActionItem | null>(null);
  const refresh = () => qc.invalidateQueries();
  const stageMut = useMutation({ mutationFn: () => api.setStage(id, { stage: stage as Stage, lock }), onSuccess: refresh });
  const noteMut = useMutation({ mutationFn: () => api.addNote(id, note), onSuccess: () => { setNote(""); refresh(); } });
  const submitMut = useMutation({ mutationFn: () => api.updateApplication(id, { confirmed_submitted: true }), onSuccess: refresh });
  const itemMut = useMutation({ mutationFn: (p: { id: string; state: string }) => api.editActionItem(p.id, { state: p.state }), onSuccess: refresh });
  const oaMut = useMutation({ mutationFn: (aid: string) => api.completeAssessment(aid), onSuccess: refresh });

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!app) return null;

  return (
    <div className="stack">
      <div className="page-header">
        <Link to="/" className="muted">← Dashboard</Link>
        <h1 className="page-title">{app.company} — {app.role_title}</h1>
        <div className="row">
          <StageBadge stage={app.stage} locked={app.manual_stage_lock} />
          <EvidenceBadge verified={app.verified} type={app.submission_evidence_type} />
          {app.external_job_id && <span className="chip">Req {app.external_job_id}</span>}
          {app.recruiting_cycle && <span className="chip">{app.recruiting_cycle}</span>}
          {app.category && <span className="chip">{app.category}</span>}
          {app.ats_platform && <span className="chip">{app.ats_platform}</span>}
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <h3 className="card-title">Submission</h3>
          <dl className="facts">
            <dt>Applied</dt><dd>{app.submitted_at ? formatInstant(app.submitted_at) : "Not recorded"}</dd>
            <dt>Evidence</dt><dd>{evidenceLabel(app.submission_evidence_type)}</dd>
            <dt>Acknowledged</dt><dd>{app.acknowledged_at ? formatInstant(app.acknowledged_at) : "—"}</dd>
            <dt>Channel</dt><dd>{app.application_channel ?? "—"}</dd>
            <dt>Resume</dt><dd>{app.resume_label ?? "—"}</dd>
            <dt>Posting</dt>
            <dd>{app.posting_url ? <a href={app.posting_url} target="_blank" rel="noopener noreferrer nofollow">{app.posting_url}</a> : "—"}</dd>
          </dl>
          {!app.verified && (
            <button className="btn-secondary" onClick={() => submitMut.mutate()}>I submitted this application</button>
          )}
        </div>
        <div className="card">
          <h3 className="card-title">Change stage</h3>
          <div className="row">
            <select aria-label="New stage" value={stage} onChange={(e) => setStage(e.target.value as Stage)}>
              <option value="">Choose…</option>
              {STAGES.map((s) => <option key={s} value={s}>{STAGE_LABELS[s]}</option>)}
            </select>
            <label className="row"><input type="checkbox" checked={lock} onChange={(e) => setLock(e.target.checked)} /> Pin (email can't change it)</label>
            <button className="btn-primary" disabled={!stage} onClick={() => stageMut.mutate()}>Apply</button>
          </div>
          <h3 className="card-title">Notes</h3>
          <p className="pre">{app.notes || <span className="muted">No notes.</span>}</p>
          <textarea aria-label="Add note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          <button className="btn-secondary" disabled={!note.trim()} onClick={() => noteMut.mutate()}>Add note</button>
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">Actions & deadlines</h3>
        {app.action_items.length === 0 ? <p className="muted">None.</p> : (
          <ul className="stack plain">
            {app.action_items.map((i) => (
              <li key={i.id} className={`deadline-row ${i.state !== "open" && i.state !== "needs_review" ? "muted" : ""}`}>
                <div className="flex-between">
                  <span><strong>{i.title}</strong> · {i.state}</span>
                  <UrgencyBadge urgency={i.urgency} />
                </div>
                <div>{describeDue(i)}</div>
                {i.due_expression_raw && <blockquote className="evidence">“{i.due_expression_raw}”</blockquote>}
                {i.deadline_assumption && <div className="muted">{i.deadline_assumption}</div>}
                {(i.state === "open" || i.state === "needs_review") && (
                  <div className="row">
                    <button className="btn-secondary" onClick={() => itemMut.mutate({ id: i.id, state: "completed" })}>Done</button>
                    <button className="btn-ghost" onClick={() => setEditing(i)}>{i.state === "needs_review" ? "Confirm deadline" : "Edit deadline"}</button>
                    <button className="btn-ghost" onClick={() => itemMut.mutate({ id: i.id, state: "cancelled" })}>Not needed</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="two-col">
        <div className="card">
          <h3 className="card-title">Assessments</h3>
          {app.assessments.length === 0 ? <p className="muted">None.</p> : app.assessments.map((a) => (
            <div key={a.id} className="row">
              <span>{a.provider ?? "Assessment"} ({a.assessment_type}) — {a.state}, invited {formatDay(a.invited_at)}</span>
              {(a.state === "invited" || a.state === "started") && (
                <button className="btn-secondary" onClick={() => oaMut.mutate(a.id)}>Mark OA completed</button>
              )}
            </div>
          ))}
        </div>
        <div className="card">
          <h3 className="card-title">Interviews</h3>
          {app.interviews.length === 0 ? <p className="muted">None.</p> : app.interviews.map((iv) => (
            <div key={iv.id}>{iv.round_label ?? "Interview"} — {iv.state} — {iv.starts_at ? formatInstant(iv.starts_at) : "time not set"}</div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">Timeline</h3>
        <ol className="timeline">
          {app.events.map((e) => (
            <li key={e.id} className={e.event_type === "undo" ? "muted" : ""}>
              <div>
                <strong>{eventLabel(e.event_type)}</strong> <span className="muted">{formatInstant(e.event_at)} · {e.source} · {e.actor}</span>
              </div>
              <div>{e.description}</div>
              {e.evidence_quote && <blockquote className="evidence">“{e.evidence_quote}”</blockquote>}
              {e.supersedes_event_id && <div className="muted">Corrects an earlier entry.</div>}
            </li>
          ))}
        </ol>
      </div>

      {app.evidence.length > 0 && (
        <div className="card">
          <h3 className="card-title">Source emails</h3>
          {app.evidence.map((m) => (
            <details key={m.email_message_id}>
              <summary>{formatDay(m.received_at)} — {m.subject} <span className="muted">({m.sender})</span></summary>
              {/* Plain text only; email HTML is never rendered. */}
              <pre className="email-excerpt">{m.excerpt}</pre>
              {m.web_link && <a href={m.web_link} target="_blank" rel="noopener noreferrer nofollow">Open in mailbox ↗</a>}
            </details>
          ))}
        </div>
      )}
      {editing && <DeadlineEditor item={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
