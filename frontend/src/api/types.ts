export type Stage =
  | "tracked" | "ready" | "submitted" | "acknowledged" | "assessment_invited" | "assessment_completed"
  | "screening" | "interviewing" | "offer" | "accepted" | "rejected" | "withdrawn" | "closed";

export type Urgency =
  | "overdue" | "within_3h" | "within_24h" | "within_72h" | "later" | "no_reliable_deadline"
  | "completed" | "cancelled" | "expired";

export type Decision = "BLOCK" | "WARN" | "ALLOW";

export interface ActionItem {
  id: string;
  application_id: string;
  assessment_id: string | null;
  interview_id: string | null;
  kind: string;
  title: string;
  instruction: string | null;
  state: string;
  due_at: string | null;
  due_date: string | null;
  due_timezone: string | null;
  due_precision: "instant" | "day" | "relative" | "unknown";
  due_expression_raw: string | null;
  due_anchor: string | null;
  deadline_assumption: string | null;
  deadline_confidence: number | null;
  evidence_quote: string | null;
  completed_at: string | null;
  created_at: string;
  urgency: Urgency | null;
  company: string | null;
  role_title: string | null;
  source_email_subject?: string | null;
  source_email_link?: string | null;
}

export interface Application {
  id: string;
  company_id: string;
  company: string;
  role_title: string;
  external_job_id: string | null;
  posting_url: string | null;
  ats_platform: string | null;
  recruiting_cycle: string | null;
  category: string | null;
  location: string | null;
  application_channel: string | null;
  resume_version_id: string | null;
  resume_label: string | null;
  stage: Stage;
  stage_updated_at: string;
  submitted_at: string | null;
  acknowledged_at: string | null;
  submission_evidence_type: string | null;
  verified: boolean;
  manual_stage_lock: boolean;
  notes: string | null;
  source_reference: string | null;
  created_at: string;
  updated_at: string;
  next_action: ActionItem | null;
  pending_reviews: number;
  source: string | null;
  applied_at?: string | null;
  source_email_link?: string | null;
  source_email_subject?: string | null;
}

export interface AppEvent {
  id: string;
  application_id: string | null;
  email_message_id: string | null;
  event_type: string;
  source: string;
  event_at: string;
  recorded_at: string;
  from_stage: string | null;
  to_stage: string | null;
  description: string;
  confidence: number | null;
  evidence_quote: string | null;
  evidence_locator: Record<string, unknown> | null;
  actor: string;
  supersedes_event_id: string | null;
}

export interface Evidence {
  email_message_id: string;
  subject: string | null;
  sender: string | null;
  received_at: string | null;
  excerpt: string | null;
  web_link: string | null;
}

export interface ApplicationDetail extends Application {
  events: AppEvent[];
  assessments: { id: string; provider: string | null; assessment_type: string; assessment_url: string | null; invited_at: string; completed_at: string | null; state: string }[];
  interviews: { id: string; round_label: string | null; starts_at: string | null; original_timezone: string | null; location_or_video_url: string | null; state: string }[];
  action_items: ActionItem[];
  evidence: Evidence[];
  proposals: { id: string; state: string; proposal_type: string; reason: string; created_at: string }[];
}

export interface DuplicateMatch {
  application_id: string;
  role_title: string;
  external_job_id: string | null;
  posting_url: string | null;
  recruiting_cycle: string | null;
  stage: string;
  submitted_at: string | null;
  match_kind: string;
  level: Decision;
}

export interface DuplicateCheck {
  decision: Decision;
  reasons: string[];
  matches: DuplicateMatch[];
  company_id: string | null;
}

export interface DeadlineInfo {
  raw: string | null;
  due_at: string | null;
  due_date: string | null;
  due_timezone: string | null;
  precision: string;
  anchor: string | null;
  assumptions: string[];
  needs_review: boolean;
  confidence: number;
  kind: string;
}

export interface PlanEvent {
  index: number;
  type: string;
  label: string;
  event_at: string;
  implied_stage: string | null;
  action_required: string | null;
  assessment_type: string | null;
  assessment_provider: string | null;
  evidence_quote: string | null;
  evidence_quotes: string[];
  confidence: number;
  deadline: DeadlineInfo | null;
  interview_time: DeadlineInfo | null;
}

export interface Candidate {
  application_id: string;
  company: string | null;
  role_title: string;
  external_job_id: string | null;
  stage: string;
  score: number;
  reasons: string[];
  disqualified: boolean;
}

export interface Review {
  id: string;
  proposal_type: string;
  state: string;
  reason: string;
  confidence: number;
  created_at: string;
  reviewed_at: string | null;
  review_notes: string | null;
  proposed_changes: {
    company: { name: string | null; resolved_name: string | null; resolution: string; ambiguous: boolean; confidence: number };
    role: { title: string | null; requisition_id: string | null; job_url: string | null; confidence: number };
    match: { decision: string; application_id: string | null; score: number; reason: string };
    create_application: boolean;
    events: PlanEvent[];
    conflicts: { action_item_id: string; existing: { due_at: string | null; due_date: string | null; raw: string | null; evidence: string | null }; new: DeadlineInfo & { evidence: string | null } }[];
    flags: string[];
    overall_confidence: number;
  };
  alternatives: Candidate[];
  target: { id: string; company: string; role_title: string; stage: string; external_job_id: string | null } | null;
  email: { id: string; subject: string | null; sender_name: string | null; sender_address: string | null; received_at: string | null; excerpt: string | null; web_link: string | null } | null;
  parser: { model: string; prompt_version: string; schema_version: string; notes: string[] | null } | null;
}

export interface SyncHealth {
  accounts: number;
  enabled_accounts: number;
  auto_sync_enabled: boolean;
  last_successful_sync_at: string | null;
  last_run: { status: string; started_at: string; finished_at: string | null; summary: string | null } | null;
  pending_messages: number;
  warnings: string[];
}

export interface Summary {
  active_applications: number;
  tracked_not_submitted: number;
  submitted_or_acknowledged: number;
  verified_submissions: number;
  unverified_records: number;
  pending_oa: number;
  deadlines_within_72h: number;
  overdue: number;
  no_reliable_deadline: number;
  interviews: number;
  offers: number;
  rejected: number;
  review_needed: number;
  sync: SyncHealth;
}

export interface PendingDeadline extends DeadlineInfo {
  proposal_id: string;
  company: string | null;
  role_title: string | null;
  event_type: string;
}

export type DeadlineType = "OA" | "Interview" | "Feedback" | "To do" | "Other";

export interface Deadlines {
  items: (ActionItem & { deadline_type: DeadlineType })[];
  done: (ActionItem & { deadline_type: DeadlineType })[];
  buckets: Record<string, ActionItem[]>;
  pending_review: PendingDeadline[];
  now: string;
}

export interface ChangeEvent {
  id: string;
  event_type: string;
  description: string;
  recorded_at: string;
  event_at: string;
  to_stage: string | null;
  actor: string;
  source: string;
  application_id: string | null;
  company: string | null;
  role_title: string | null;
}

export interface Account {
  id: string;
  provider: "gmail" | "outlook" | "imap" | "fake";
  address: string;
  display_name: string | null;
  auth_method: string;
  enabled: boolean;
  sync_scope: Record<string, unknown>;
  connected: boolean;
  last_successful_sync_at: string | null;
  last_attempt_at: string | null;
  last_error_code: string | null;
}

export interface Settings {
  auto_apply_policy: "auto_all" | "review_first" | "auto_harmless";
  auto_apply_min_confidence: number;
  poll_interval_seconds: number;
  auto_sync_enabled: boolean;
  initial_lookback_days: number;
  display_timezone: string;
  desktop_notifications: boolean;
  ollama_model: string;
  ollama_base_url: string;
  missed_sync_warning_minutes: number;
  oauth: { gmail_configured: boolean; outlook_configured: boolean; gmail_redirect_uri: string; outlook_redirect_uri: string };
}

export interface NotificationRule {
  id: string;
  kind: string;
  lead_time_minutes: number;
  enabled: boolean;
  channel: "dashboard" | "desktop";
}

export interface Resume {
  id: string;
  label: string;
  filename: string | null;
  sha256: string | null;
  local_path: string | null;
  notes: string | null;
  created_at: string;
}

export interface LlmHealth {
  ok: boolean;
  reachable?: boolean;
  version?: string;
  model?: string;
  model_installed?: boolean;
  installed_qwen3_models?: string[];
  error?: string;
  hint?: string | null;
}

export interface ImportRow {
  row: number;
  company: string | null;
  role_title: string | null;
  action: string;
  error?: string;
  duplicate_check?: DuplicateCheck;
  application_id?: string;
}

export interface SyncRun {
  id: string;
  email_account_id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  messages_seen: number;
  messages_new: number;
  messages_parsed: number;
  proposals_created: number;
  events_applied: number;
  error_count: number;
  safe_error_summary: string | null;
}
