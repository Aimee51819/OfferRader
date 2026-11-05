import type { ActionItem, Stage, Urgency } from "../api/types";

export const DISPLAY_TZ = "America/New_York";

export const STAGES: Stage[] = [
  "tracked", "ready", "submitted", "acknowledged", "assessment_invited", "assessment_completed",
  "screening", "interviewing", "offer", "accepted", "rejected", "withdrawn", "closed",
];

export const STAGE_LABELS: Record<Stage, string> = {
  tracked: "Tracked (not applied)",
  ready: "Ready to apply",
  submitted: "Submitted",
  acknowledged: "Acknowledged",
  assessment_invited: "OA invited",
  assessment_completed: "OA completed",
  screening: "Screening",
  interviewing: "Interviewing",
  offer: "Offer",
  accepted: "Accepted",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  closed: "Closed",
};

export const STAGE_COLORS: Record<Stage, string> = {
  tracked: "#5d574f",
  ready: "#8b857d",
  submitted: "#7d9bc1",
  acknowledged: "#6d8fb8",
  assessment_invited: "#c8734a",
  assessment_completed: "#b88a5a",
  screening: "#a58fc4",
  interviewing: "#9b7fd1",
  offer: "#7aa35f",
  accepted: "#5f9a4a",
  rejected: "#d2564f",
  withdrawn: "#6f6a63",
  closed: "#4d4943",
};

export const TERMINAL: Stage[] = ["offer", "accepted", "rejected", "withdrawn", "closed"];

export const URGENCY_LABELS: Record<string, string> = {
  overdue: "Overdue",
  within_3h: "Due within 3 h",
  within_24h: "Due within 24 h",
  within_72h: "Due within 72 h",
  later: "Later",
  no_reliable_deadline: "No reliable deadline",
  completed: "Completed",
  cancelled: "Cancelled",
  expired: "Expired",
};

export const URGENCY_COLORS: Record<string, string> = {
  overdue: "#d2564f",
  within_3h: "#e0663f",
  within_24h: "#c8734a",
  within_72h: "#c9a24a",
  later: "#7d9bc1",
  no_reliable_deadline: "#8b857d",
};

const fmt = (opts: Intl.DateTimeFormatOptions, tz = DISPLAY_TZ) =>
  new Intl.DateTimeFormat("en-US", { timeZone: tz, ...opts });

/** An instant in Eastern Time, with the zone abbreviation (EST/EDT). */
export function formatInstant(iso: string | null | undefined, tz = DISPLAY_TZ): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return fmt({ weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }, tz).format(d);
}

export function formatDay(iso: string | null | undefined, tz = DISPLAY_TZ): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return fmt({ month: "short", day: "numeric", year: "numeric" }, tz).format(d);
}

/** A calendar date with no time. Rendered without converting through a timezone. */
export function formatDateOnly(yyyyMmDd: string): string {
  const [y, m, d] = yyyyMmDd.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d, 12));
  return fmt({ weekday: "short", month: "short", day: "numeric", year: "numeric" }, "UTC").format(date);
}

/** Human description of a deadline that never invents precision it doesn't have. */
export function describeDue(item: Pick<ActionItem, "due_at" | "due_date" | "due_precision">, tz = DISPLAY_TZ): string {
  if (item.due_at) return formatInstant(item.due_at, tz);
  if (item.due_date) return `${formatDateOnly(item.due_date)} (date only — no time given)`;
  return "No reliable deadline";
}

export function relativeTo(iso: string | null, now = new Date()): string {
  if (!iso) return "";
  const ms = new Date(iso).getTime() - now.getTime();
  const abs = Math.abs(ms);
  const h = Math.round(abs / 36e5);
  const label = h < 1 ? `${Math.max(1, Math.round(abs / 6e4))} min` : h < 48 ? `${h} h` : `${Math.round(h / 24)} days`;
  return ms < 0 ? `${label} ago` : `in ${label}`;
}

export function urgencyOf(u: Urgency | null | undefined): string {
  return u ? URGENCY_LABELS[u] ?? u : "";
}

export function evidenceLabel(type: string | null): string {
  switch (type) {
    case "manual_confirmed":
      return "You confirmed submission";
    case "agent_receipt":
      return "Confirmed receipt from application tool";
    case "acknowledgment_email":
      return "Employer acknowledgment email";
    default:
      return "Not verified as submitted";
  }
}

export function eventLabel(t: string): string {
  return t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
