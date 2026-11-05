import { Link } from "react-router-dom";
import type { DuplicateCheck } from "../api/types";
import { DecisionBadge } from "./Badges";

const KIND_LABELS: Record<string, string> = {
  exact_opening: "Same opening",
  same_title_no_id: "Probably the same opening",
  same_title_different_req: "Same title, different requisition",
  other_role_same_company: "Different role, same employer",
  same_opening_other_cycle: "Same opening, other cycle (still active)",
  reopened_cycle: "Reopened requisition, new cycle",
  same_title_other_cycle: "Same title, other cycle",
};

export default function DuplicateNotice({ check }: { check: DuplicateCheck | null }) {
  if (!check) return null;
  const tone = check.decision === "BLOCK" ? "alert-error" : check.decision === "WARN" ? "alert-warn" : "alert-success";
  return (
    <div className={`alert ${tone}`} role="status" aria-live="polite" data-testid="duplicate-notice">
      <div className="row">
        <DecisionBadge decision={check.decision} />
        <strong>
          {check.decision === "BLOCK"
            ? "You already applied to this exact opening."
            : check.decision === "WARN"
              ? "Check before applying."
              : "No conflicting application found."}
        </strong>
      </div>
      <ul className="reasons">
        {check.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
      {check.matches.length > 0 && (
        <table className="mini-table">
          <thead>
            <tr>
              <th>Existing application</th>
              <th>Req / cycle</th>
              <th>Stage</th>
              <th>Why it matched</th>
            </tr>
          </thead>
          <tbody>
            {check.matches.map((m) => (
              <tr key={m.application_id}>
                <td>
                  <Link to={`/applications/${m.application_id}`}>{m.role_title}</Link>
                </td>
                <td>
                  {m.external_job_id ?? "—"} / {m.recruiting_cycle ?? "—"}
                </td>
                <td>{m.stage}</td>
                <td>
                  {KIND_LABELS[m.match_kind] ?? m.match_kind} ({m.level})
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
