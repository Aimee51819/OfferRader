import type {
  Account, ActionItem, Application, ApplicationDetail, AppEvent, ChangeEvent, Deadlines, DuplicateCheck,
  ImportRow, LlmHealth, NotificationRule, Resume, Review, Settings, Summary, SyncHealth, SyncRun,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : (detail as { message?: string })?.message || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export interface ApplicationFilters {
  q?: string;
  category?: string;
  stage?: string[];
  source?: string;
  resume_version_id?: string;
  submitted_from?: string;
  submitted_to?: string;
  urgency?: string;
  needs_review?: boolean;
  verified?: boolean;
}

function qs(f: ApplicationFilters): string {
  const p = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => {
    if (v === undefined || v === "" || v === null) return;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
    else p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  summary: () => request<Summary>("/api/dashboard/summary"),
  deadlines: () => request<Deadlines>("/api/dashboard/deadlines"),
  changes: () => request<{ since: string; events: ChangeEvent[]; new_reviews: number }>("/api/dashboard/changes"),
  digest: () => request<{ lines: string[]; pending_reviews: number; email_events_last_24h: number }>("/api/dashboard/digest"),
  funnel: () => request<{ funnel: Record<string, number>; by_category: Record<string, { total: number; active: number; rejected: number; offers: number }>; total: number }>("/api/dashboard/funnel"),
  notifications: () => request<{ id: string; triggered_at: string; action_item: ActionItem }[]>("/api/dashboard/notifications"),
  dismissNotification: (id: string) => post(`/api/dashboard/notifications/${id}/dismiss`),

  applications: (f: ApplicationFilters = {}) => request<Application[]>(`/api/applications${qs(f)}`),
  trashPeek: (kind: "application" | "deadline") =>
    request<{ last: { id: string; label: string; deleted_at: string; count: number } | null }>(`/api/trash/${kind}`),
  trashRestore: (kind: "application" | "deadline") => post<{ restored: string; label: string }>(`/api/trash/${kind}/restore`),
  deleteActionItem: (id: string) => request<{ deleted: string }>(`/api/action-items/${encodeURIComponent(id)}`, { method: "DELETE" }),
  deleteApplication: (id: string) => request<{ deleted: string }>(`/api/applications/${encodeURIComponent(id)}`, { method: "DELETE" }),
  application: (id: string) => request<ApplicationDetail>(`/api/applications/${id}`),
  createApplication: (body: Record<string, unknown>) => post<Application>("/api/applications", body),
  updateApplication: (id: string, body: Record<string, unknown>) =>
    request<Application>(`/api/applications/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  setStage: (id: string, body: { stage: string; note?: string; lock?: boolean }) => post<Application>(`/api/applications/${id}/stage`, body),
  addNote: (id: string, note: string) => post<Application>(`/api/applications/${id}/notes`, { note }),
  events: (id: string) => request<AppEvent[]>(`/api/applications/${id}/events`),
  duplicateCheck: (body: Record<string, unknown>) => post<DuplicateCheck>("/api/duplicates/check", body),
  editActionItem: (id: string, body: Record<string, unknown>) => post<ActionItem>(`/api/action-items/${id}`, body),
  completeAssessment: (id: string) => post<Application>(`/api/assessments/${id}/complete`),

  reviews: (state = "pending") => request<Review[]>(`/api/reviews?state=${state}`),
  approve: (id: string, body: Record<string, unknown> = {}) => post<Review>(`/api/reviews/${id}/approve`, body),
  reject: (id: string, notes?: string) => post<Review>(`/api/reviews/${id}/reject`, { notes }),
  undo: (id: string) => post<Review>(`/api/reviews/${id}/undo`),

  settings: () => request<Settings>("/api/settings"),
  saveSettings: (body: Partial<Settings>) => request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  rules: () => request<NotificationRule[]>("/api/notification-rules"),
  saveRules: (body: { lead_time_minutes: number; enabled: boolean; channel: string }[]) =>
    request<NotificationRule[]>("/api/notification-rules", { method: "PUT", body: JSON.stringify(body) }),
  llmHealth: () => request<LlmHealth>("/api/llm/health"),

  accounts: () => request<Account[]>("/api/accounts"),
  updateAccount: (id: string, body: Record<string, unknown>) =>
    request<Account>(`/api/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  disconnectAccount: (id: string) => request<Account>(`/api/accounts/${id}`, { method: "DELETE" }),
  oauthStart: (provider: "gmail" | "outlook") => request<{ url: string; scopes: string[] }>(`/api/oauth/${provider}/start`),
  syncStatus: () => request<SyncHealth & { running: boolean }>("/api/sync/status"),
  syncRuns: () => request<SyncRun[]>("/api/sync/runs"),
  syncNow: () => post<{ started: boolean; message?: string }>("/api/sync/run"),
  fetchOnly: (accountId: string) => post<SyncRun>(`/api/sync/fetch-only?account_id=${encodeURIComponent(accountId)}`),
  dryRun: () => post<{ messages: { subject: string; state: string; auto_apply?: boolean; proposal_type?: string; reason?: string; events?: string[]; error?: string }[] }>("/api/sync/dry-run"),

  resumes: () => request<Resume[]>("/api/resumes"),
  addResume: (body: Record<string, unknown>) => post<Resume>("/api/resumes", body),

  importCsv: (file: File, dryRun: boolean, overrides: number[] = []) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("dry_run", String(dryRun));
    fd.append("override_rows", overrides.join(","));
    return request<{ dry_run: boolean; created: number; rows: ImportRow[] }>("/api/import/csv", { method: "POST", body: fd });
  },
};
