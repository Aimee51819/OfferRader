import type {
  Analytics,
  AppSettings,
  Application,
  ApplicationDetail,
  ApplicationStatus,
  DismissedDuplicate,
  DuplicateGroup,
  EmailAccount,
  HealthStatus,
  LLMProvider,
  ParsedJob,
  Suggestion,
  SuggestionApprovePayload,
  SyncProgress,
  UpdateStatus,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthStatus>("/health"),

  // Applications
  listApplications: (params?: { status?: string; search?: string }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set("status", params.status);
    if (params?.search) q.set("search", params.search);
    const qs = q.toString();
    return request<Application[]>(`/applications${qs ? `?${qs}` : ""}`);
  },
  getApplication: (id: number) =>
    request<ApplicationDetail>(`/applications/${id}`),
  createApplication: (data: Partial<Application>) =>
    request<Application>("/applications", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateApplication: (id: number, data: Record<string, unknown>) =>
    request<ApplicationDetail>(`/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  updateStatus: (id: number, status: ApplicationStatus, note?: string) =>
    request<ApplicationDetail>(`/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status, status_note: note }),
    }),
  deleteApplication: (id: number) =>
    request<{ message: string }>(`/applications/${id}`, { method: "DELETE" }),
  mergeApplications: (sourceId: number, targetId: number) =>
    request<ApplicationDetail>("/applications/merge", {
      method: "POST",
      body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
    }),
  listReminders: () => request<Application[]>("/applications/reminders"),
  exportUrl: (format: "csv" | "json") =>
    `${BASE}/applications/export?format=${format}`,
  importApplications: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/applications/import`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch {
        // ignore
      }
      throw new Error(detail);
    }
    return res.json() as Promise<{ message: string }>;
  },

  listDuplicates: () => request<DuplicateGroup[]>("/applications/duplicates"),
  listDismissedDuplicates: () =>
    request<DismissedDuplicate[]>("/applications/duplicates/dismissed"),
  dismissDuplicate: (firstId: number, secondId: number) =>
    request<DismissedDuplicate>("/applications/duplicates/dismiss", {
      method: "POST",
      body: JSON.stringify({ first_id: firstId, second_id: secondId }),
    }),
  restoreDuplicate: (firstId: number, secondId: number) =>
    request<{ message: string }>(
      `/applications/duplicates/dismissed/${firstId}/${secondId}`,
      { method: "DELETE" }
    ),

  // Analytics
  getAnalytics: () => request<Analytics>("/analytics"),

  // Parse
  parseJob: (text: string) =>
    request<ParsedJob>("/parse", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  // Suggestions
  listSuggestions: (status = "pending") =>
    request<Suggestion[]>(`/suggestions?status=${status}`),
  approveSuggestion: (id: number, data?: SuggestionApprovePayload) =>
    request<{ message: string }>(`/suggestions/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(data ?? {}),
    }),
  rejectSuggestion: (id: number) =>
    request<{ message: string }>(`/suggestions/${id}/reject`, {
      method: "POST",
    }),
  bulkApproveSuggestions: (ids: number[]) =>
    request<{ message: string }>("/suggestions/bulk-approve", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  bulkRejectSuggestions: (ids: number[]) =>
    request<{ message: string }>("/suggestions/bulk-reject", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),

  // Settings + LLM providers
  getSettings: () => request<AppSettings>("/settings"),
  updateSettings: (data: Partial<AppSettings>) =>
    request<AppSettings>("/settings", {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  listProviders: () => request<LLMProvider[]>("/settings/llm-providers"),
  createProvider: (data: Record<string, unknown>) =>
    request<LLMProvider>("/settings/llm-providers", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  testProvider: (data: {
    provider: string;
    model: string;
    api_key?: string | null;
    api_base?: string | null;
  }) =>
    request<{ ok: boolean; message: string }>("/settings/llm-providers/test", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  testSavedProvider: (id: number) =>
    request<{ ok: boolean; message: string }>(
      `/settings/llm-providers/${id}/test`,
      { method: "POST" }
    ),
  updateProvider: (id: number, data: Record<string, unknown>) =>
    request<LLMProvider>(`/settings/llm-providers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  activateProvider: (id: number) =>
    request<LLMProvider>(`/settings/llm-providers/${id}/activate`, {
      method: "POST",
    }),
  deleteProvider: (id: number) =>
    request<{ message: string }>(`/settings/llm-providers/${id}`, {
      method: "DELETE",
    }),

  // Email accounts
  listEmailAccounts: () => request<EmailAccount[]>("/email-accounts"),
  createEmailAccount: (data: Record<string, unknown>) =>
    request<EmailAccount>("/email-accounts", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateEmailAccount: (id: number, data: Record<string, unknown>) =>
    request<EmailAccount>(`/email-accounts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteEmailAccount: (id: number) =>
    request<{ message: string }>(`/email-accounts/${id}`, { method: "DELETE" }),
  testEmailConnection: (data: Record<string, unknown>) =>
    request<{ ok: boolean; message: string }>("/email-accounts/test", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  resetEmailAccount: (id: number) =>
    request<{ message: string }>(`/email-accounts/${id}/reset`, { method: "POST" }),
  catchUpEmailAccount: (id: number, lookback = 75) =>
    request<{
      ok: boolean;
      message: string;
      stats: Record<string, number>;
    }>(`/email-accounts/${id}/catch-up?lookback=${lookback}`, { method: "POST" }),
  syncEmailAccount: (id: number) =>
    request<{ ok: boolean; stats: Record<string, number> }>(
      `/email-accounts/${id}/sync`,
      { method: "POST" }
    ),
  getSyncProgress: () =>
    request<SyncProgress>("/email-accounts/sync-progress"),

  // Backup
  backupUrl: () => `${BASE}/backup`,
  restoreBackup: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/backup/restore`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch {
        // ignore
      }
      throw new Error(detail);
    }
    return res.json() as Promise<{ message: string }>;
  },

  // Updates
  getUpdateStatus: () => request<UpdateStatus>("/updates"),
  checkForUpdate: () =>
    request<UpdateStatus>("/updates/check", { method: "POST" }),
  downloadUpdate: () =>
    request<UpdateStatus>("/updates/download", { method: "POST" }),
  installUpdate: () =>
    request<{ ok: boolean; version: string; message: string }>(
      "/updates/install",
      { method: "POST" }
    ),
};
