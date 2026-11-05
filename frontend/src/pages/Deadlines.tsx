import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ActionItem } from "../api/types";
import { UrgencyBadge } from "../components/Badges";
import { Empty, ErrorBox, Loading } from "../components/States";
import { URGENCY_LABELS, describeDue, formatDateOnly, formatInstant, relativeTo } from "../lib/format";

const ORDER = ["overdue", "within_3h", "within_24h", "within_72h", "later", "no_reliable_deadline"];

export function DeadlineRow({ item }: { item: ActionItem }) {
  const qc = useQueryClient();
  const done = useMutation({
    mutationFn: () => api.editActionItem(item.id, { state: "completed" }),
    onSuccess: () => qc.invalidateQueries(),
  });
  return (
    <li className="deadline-row card">
      <div className="flex-between">
        <div>
          <strong>{item.title}</strong> — <Link to={`/applications/${item.application_id}`}>{item.company}: {item.role_title}</Link>
        </div>
        <UrgencyBadge urgency={item.urgency} />
      </div>
      <div>
        <strong>{describeDue(item)}</strong>
        {item.due_at && <span className="muted"> ({relativeTo(item.due_at)})</span>}
        {item.due_timezone && item.due_timezone !== "America/New_York" && <span className="muted"> · stated in {item.due_timezone}</span>}
      </div>
      {item.due_expression_raw && (
        <blockquote className="evidence">“{item.due_expression_raw}”</blockquote>
      )}
      {item.deadline_assumption && <div className="muted">Assumptions: {item.deadline_assumption}</div>}
      {item.due_anchor && <div className="muted">Counted from: {item.due_anchor.replace("=", " at ")}</div>}
      {item.state === "needs_review" && <div className="chip chip-warn">Deadline needs your confirmation</div>}
      <div className="row">
        {item.source_email_link && (
          <a href={item.source_email_link} target="_blank" rel="noopener noreferrer nofollow">Open source email ↗</a>
        )}
        {item.kind === "oa_deadline" && (
          <button className="btn-secondary" onClick={() => done.mutate()} disabled={done.isPending}>Mark completed</button>
        )}
      </div>
    </li>
  );
}

export default function Deadlines() {
  const { data, error, isLoading } = useQuery({ queryKey: ["deadlines"], queryFn: api.deadlines, refetchInterval: 60000 });
  if (isLoading) return <Loading what="Loading deadlines" />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const total = ORDER.reduce((n, k) => n + (data.buckets[k]?.length ?? 0), 0);
  return (
    <div className="stack">
      <div className="page-header">
        <h1 className="page-title">Deadlines & actions</h1>
        <p className="subtitle">All times in America/New_York. Date-only deadlines are shown without a time, on purpose.</p>
      </div>
      {data.pending_review.length > 0 && (
        <div className="card alert-warn">
          <h3 className="card-title">Waiting in the review queue</h3>
          <ul>
            {data.pending_review.map((d, i) => (
              <li key={i}>
                {d.company ?? "Unknown company"} · {d.role_title ?? "unknown role"}: {d.due_at ? formatInstant(d.due_at) : d.due_date ? `${formatDateOnly(d.due_date)} (date only)` : "unknown"}
                {d.needs_review && <span className="chip chip-warn">ambiguous</span>} — <Link to="/review">review</Link>
                {d.raw && <div className="muted">“{d.raw}”</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {total === 0 && <Empty>No open deadlines or actions.</Empty>}
      {ORDER.map((k) =>
        (data.buckets[k]?.length ?? 0) > 0 ? (
          <section key={k}>
            <h2 className="section-title">{URGENCY_LABELS[k]} ({data.buckets[k].length})</h2>
            <ul className="stack plain">
              {data.buckets[k].map((i) => <DeadlineRow key={i.id} item={i} />)}
            </ul>
          </section>
        ) : null,
      )}
    </div>
  );
}
