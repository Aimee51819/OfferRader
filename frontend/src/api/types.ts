export type ApplicationStatus =
  | "saved"
  | "applied"
  | "online_assessment"
  | "phone_screen"
  | "interview"
  | "offer"
  | "rejected"
  | "ghosted"
  | "accepted";

export interface Application {
  id: number;
  company: string;
  title: string;
  location: string | null;
  url: string | null;
  source: string | null;
  salary: string | null;
  status: ApplicationStatus;
  description: string | null;
  skills: string | null;
  notes: string | null;
  contact_email: string | null;
  job_id: string | null;
  date_applied: string | null;
  created_at: string;
  updated_at: string;
}

export interface DuplicateGroup {
  company: string;
  reason: string;
  applications: Application[];
}

export interface DismissedDuplicate {
  low_id: number;
  high_id: number;
  created_at: string;
  applications: Application[];
}

export interface StatusEvent {
  id: number;
  from_status: ApplicationStatus | null;
  to_status: ApplicationStatus | null;
  note: string | null;
  source: "manual" | "email" | "system";
  created_at: string;
}

export interface EmailActivity {
  id: number;
  kind: "suggestion" | "processed_email" | string;
  subject: string | null;
  sender: string | null;
  snippet: string | null;
  summary: string | null;
  suggestion_status: SuggestionStatusType | null;
  is_job_related: boolean | null;
  created_at: string;
}

export interface ApplicationDetail extends Application {
  events: StatusEvent[];
  related_emails: EmailActivity[];
}

export interface ParsedJob {
  company: string | null;
  title: string | null;
  location: string | null;
  url: string | null;
  salary: string | null;
  source: string | null;
  description: string | null;
  skills: string[];
}

export interface LLMProvider {
  id: number;
  name: string;
  provider: string;
  model: string;
  api_base: string | null;
  is_active: boolean;
  has_api_key: boolean;
  created_at: string;
}

export interface EmailAccount {
  id: number;
  name: string;
  imap_host: string;
  imap_port: number;
  username: string;
  use_ssl: boolean;
  folder: string;
  active: boolean;
  last_synced_at: string | null;
  created_at: string;
}

export type SuggestionKind = "status_change" | "new_application" | "note";
export type SuggestionStatusType = "pending" | "approved" | "rejected";

export interface Suggestion {
  id: number;
  application_id: number | null;
  kind: SuggestionKind;
  status: SuggestionStatusType;
  suggested_status: ApplicationStatus | null;
  summary: string | null;
  confidence: number | null;
  company: string | null;
  title: string | null;
  email_subject: string | null;
  email_sender: string | null;
  email_snippet: string | null;
  email_date: string | null;
  created_at: string;
}

export interface SuggestionApprovePayload {
  company?: string | null;
  title?: string | null;
  suggested_status?: ApplicationStatus | null;
}

export interface AppSettings {
  email_poll_interval_seconds: number;
  auto_apply_suggestions: boolean;
  min_suggestion_confidence: number;
  follow_up_days: number;
  hidden_board_statuses: ApplicationStatus[];
}

export interface SyncProgress {
  running: boolean;
  account_id: number | null;
  account_name: string | null;
  phase: string;
  current: number;
  total: number;
  message: string;
  stalled: boolean;
  idle_seconds: number;
}

export interface UpdateStatus {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  release_url: string;
  notes: string;
  published_at: string | null;
  supported: boolean;
  reason: string | null;
  staged_version: string | null;
  phase: "idle" | "checking" | "downloading" | "verifying" | "ready" | "error";
  message: string;
  percent: number;
  busy: boolean;
  error: string | null;
}

export interface HealthStatus {
  status: string;
  version?: string;
  llm: {
    configured: boolean;
    name: string | null;
    provider: string | null;
    model: string | null;
  };
  email: {
    accounts: number;
    active: number;
    last_synced_at: string | null;
  };
  sync: SyncProgress;
}

export interface AnalyticsFunnelStep {
  stage: string;
  label: string;
  count: number;
  rate: number;
  conversion: number | null;
}

export interface AnalyticsStageCount {
  stage: ApplicationStatus;
  count: number;
}

export interface AnalyticsGroupRow {
  submitted: number;
  response_rate: number;
  oa_rate: number;
  interview_rate: number;
  offer_rate: number;
  assessments: number;
  interviews: number;
  offers: number;
}

export interface AnalyticsSourceRow extends AnalyticsGroupRow {
  source: string;
}

export interface AnalyticsCompanyRow extends AnalyticsGroupRow {
  company: string;
}

export interface Analytics {
  total: number;
  submitted: number;
  active: number;
  status_counts: Record<ApplicationStatus, number>;
  response_count: number;
  oa_count: number;
  interview_count: number;
  offer_count: number;
  ghost_count: number;
  response_rate: number;
  oa_rate: number;
  interview_rate: number;
  offer_rate: number;
  ghost_rate: number;
  avg_days_to_response: number | null;
  avg_days_to_oa: number | null;
  avg_days_to_interview: number | null;
  avg_days_to_offer: number | null;
  funnel: AnalyticsFunnelStep[];
  rejection_by_stage: AnalyticsStageCount[];
  by_source: AnalyticsSourceRow[];
  by_company: AnalyticsCompanyRow[];
  over_time: { week: string; count: number }[];
}
