import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DeadlineType } from "../api/types";
import { UrgencyBadge } from "../components/Badges";
import Modal from "../components/Modal";
import RecoverButton from "../components/RecoverButton";
import { Empty, ErrorBox, Loading } from "../components/States";
import { describeDue, formatInstant, relativeTo } from "../lib/format";
import type { ActionItem } from "../api/types";

const TYPE_INFO: Record<DeadlineType, { label: string; hint: string; cls: string }> = {
  OA: { label: "OA", hint: "Online assessment you need to complete", cls: "dl-oa" },
  Interview: { label: "Interview", hint: "Interview to attend or schedule", cls: "dl-interview" },
  Feedback: { label: "Feedback", hint: "When the company said it would get back to you", cls: "dl-feedback" },
  "To do": { label: "To do", hint: "Something the company asked you to do by a date", cls: "dl-todo" },
  Other: { label: "Other", hint: "Other dated action", cls: "dl-other" },
};

const FILTERS: (DeadlineType | "All")[] = ["All", "OA", "Interview", "To do", "Feedback"];

export function DeadlineTypeChip({ type }: { type: DeadlineType }) {
  const info = TYPE_INFO[type] ?? TYPE_INFO.Other;
  return <span className={`dl-chip ${info.cls}`} title={info.hint}>{info.label}</span>;
}

export default function Deadlines() {
  const qc = useQueryClient();
  const [type, setType] = useState<DeadlineType | "All">("All");
  const { data, error, isLoading } = useQuery({ queryKey: ["deadlines"], queryFn: api.deadlines, refetchInterval: 60000 });
  const done = useMutation({
    mutationFn: (id: string) => api.editActionItem(id, { state: "completed" }),
    onSuccess: () => qc.invalidateQueries(),
  });
  const [toDelete, setToDelete] = useState<(ActionItem & { deadline_type: DeadlineType }) | null>(null);
  const del = useMutation({
    mutationFn: (id: string) => api.deleteActionItem(id),
    onSuccess: () => {
      setToDelete(null);
      qc.invalidateQueries();
    },
  });
  const doneRows = useMemo(() => (data?.done ?? []).filter((i) => type === "All" || i.deadline_type === type), [data, type]);
  const rows = useMemo(() => (data?.items ?? []).filter((i) => type === "All" || i.deadline_type === type), [data, type]);
  if (isLoading) return <Loading what="Loading deadlines" />;
  if (error) return <ErrorBox error={error} />;
  const counts = (t: DeadlineType | "All") => (data?.items ?? []).filter((i) => t === "All" || i.deadline_type === t).length;
  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1 className="page-title">Deadlines</h1>
          <p className="subtitle">Nearest first. Times in America/New_York.</p>
        </div>
      </div>
      <div className="tabs" role="tablist" aria-label="Deadline type">
        {FILTERS.map((t) => (
          <button key={t} role="tab" aria-selected={type === t} className={`tab ${type === t ? "active" : ""}`} onClick={() => setType(t)}>
            {t === "All" ? "All" : TYPE_INFO[t].label} <span className="tab-count">{counts(t)}</span>
          </button>
        ))}
      </div>
      {rows.length === 0 ? (
        <Empty>No open deadlines.</Empty>
      ) : (
        <div className="table-card">
          <table className="dl-table">
            <colgroup>
              <col className="col-due" />
              <col className="col-type" />
              <col />
              <col />
              <col className="col-actions" />
            </colgroup>
            <thead>
              <tr>
                <th>Due</th>
                <th>Type</th>
                <th>Application</th>
                <th>What</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={i.id}>
                  <td>
                    <div className="due-main">{describeDue(i)}</div>
                    <div className="due-sub">
                      {i.due_at && <span className="muted small">{relativeTo(i.due_at)}</span>}
                      <UrgencyBadge urgency={i.urgency} />
                    </div>
                  </td>
                  <td><DeadlineTypeChip type={i.deadline_type} /></td>
                  <td>
                    <Link to={`/applications/${i.application_id}`} className="role-link">{i.company}</Link>
                    <div className="muted small">{i.role_title}</div>
                  </td>
                  <td>
                    <div>{i.title}</div>
                    {i.due_expression_raw && <div className="quote">“{i.due_expression_raw}”</div>}
                  </td>
                  <td className="actions-cell">
                    {i.source_email_link && (
                      <a className="btn-ghost" href={i.source_email_link} target="_blank" rel="noopener noreferrer nofollow">Email ↗</a>
                    )}
                    <button className="btn-secondary" onClick={() => done.mutate(i.id)} disabled={done.isPending}>Done</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <section className="stack">
        <div className="section-head">
          <h2 className="section-title">Done <span className="count-pill">{doneRows.length}</span></h2>
          <RecoverButton kind="deadline" />
        </div>
        {doneRows.length === 0 ? (
          <p className="muted">Deadlines you mark as done are kept here.</p>
        ) : (
          <div className="table-card">
            <table className="dl-table done-table">
              <colgroup>
                <col className="col-due" />
                <col className="col-type" />
                <col />
                <col />
                <col className="col-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th>Completed</th>
                  <th>Type</th>
                  <th>Application</th>
                  <th>What</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {doneRows.map((i) => (
                  <tr key={i.id}>
                    <td>
                      <div className="due-main">{i.completed_at ? formatInstant(i.completed_at) : "—"}</div>
                      <div className="muted small">was due {describeDue(i)}</div>
                    </td>
                    <td><DeadlineTypeChip type={i.deadline_type} /></td>
                    <td>
                      <Link to={`/applications/${i.application_id}`} className="role-link">{i.company}</Link>
                      <div className="muted small">{i.role_title}</div>
                    </td>
                    <td>{i.title}</td>
                    <td className="actions-cell">
                      <button className="btn-danger" onClick={() => setToDelete(i)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {toDelete && (
        <Modal title="Delete this record?" onClose={() => setToDelete(null)}>
          <p><strong>{toDelete.company}</strong> · {toDelete.title}</p>
          <p className="muted">The completed deadline is deleted permanently. The application itself stays on the dashboard.</p>
          {del.error && <ErrorBox error={del.error} />}
          <div className="modal-actions">
            <button className="btn-secondary" onClick={() => setToDelete(null)}>Cancel</button>
            <button className="btn-danger is-strong" onClick={() => del.mutate(toDelete.id)} disabled={del.isPending}>
              {del.isPending ? "Deleting…" : "Delete"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
