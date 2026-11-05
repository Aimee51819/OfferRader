import type { Decision, Stage, Urgency } from "../api/types";
import { STAGE_COLORS, STAGE_LABELS, URGENCY_COLORS, URGENCY_LABELS, evidenceLabel } from "../lib/format";

export function StageBadge({ stage, locked }: { stage: Stage; locked?: boolean }) {
  return (
    <span className="badge" title={locked ? "Pinned: email updates won't change this stage" : undefined}>
      <span className="status-dot" style={{ background: STAGE_COLORS[stage] }} />
      {STAGE_LABELS[stage] ?? stage}
      {locked && <span aria-label="pinned"> 📌</span>}
    </span>
  );
}

export function UrgencyBadge({ urgency }: { urgency: Urgency | null | undefined }) {
  if (!urgency) return null;
  return (
    <span className="badge" style={{ borderColor: URGENCY_COLORS[urgency], color: URGENCY_COLORS[urgency] ?? undefined }}>
      {URGENCY_LABELS[urgency] ?? urgency}
    </span>
  );
}

export function EvidenceBadge({ verified, type }: { verified: boolean; type: string | null }) {
  return (
    <span
      className={`badge ${verified ? "badge-verified" : "badge-unverified"}`}
      title={evidenceLabel(type)}
      aria-label={verified ? "Verified submission" : "Unverified record"}
    >
      {verified ? "✓ Verified" : "Unverified"}
    </span>
  );
}

export function DecisionBadge({ decision }: { decision: Decision }) {
  const cls = decision === "BLOCK" ? "alert-error" : decision === "WARN" ? "alert-warn" : "alert-success";
  return <span className={`badge decision ${cls}`}>{decision}</span>;
}
