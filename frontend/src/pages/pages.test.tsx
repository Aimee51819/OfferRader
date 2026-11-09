import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Dashboard from "./Dashboard";
import Deadlines from "./Deadlines";

function renderWith(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const sync = { accounts: 1, enabled_accounts: 1, auto_sync_enabled: true, last_successful_sync_at: null, last_run: null, pending_messages: 3, warnings: ["Local model unavailable — new mail is stored and will be parsed when Ollama is back."] };
const app = {
  id: "a1", company_id: "c1", company: "IBM", role_title: "Product Manager Intern", external_job_id: "84211", posting_url: null,
  ats_platform: "workday", recruiting_cycle: "Summer 2027", category: "PM", location: null, application_channel: null,
  resume_version_id: null, resume_label: "PM 2027", stage: "assessment_invited", stage_updated_at: "2027-02-08T16:00:00Z",
  submitted_at: "2027-02-01T14:05:00Z", acknowledged_at: "2027-02-01T14:05:00Z", submission_evidence_type: "acknowledgment_email",
  verified: true, manual_stage_lock: false, notes: null, source_reference: "email", created_at: "", updated_at: "",
  pending_reviews: 0, source: "email", source_email_link: "https://outlook.live.com/owa/?ItemID=abc", source_email_subject: "Your IBM application",
  next_action: { id: "i1", application_id: "a1", title: "Complete HackerRank OA", kind: "oa_deadline", state: "open", due_at: null, due_date: "2027-03-20", due_precision: "day", urgency: "later", company: "IBM", role_title: "PM" },
};

function mockFetch(routes: Record<string, unknown>, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = Object.keys(routes).find((p) => url.startsWith(p));
    if (!path) return new Response(JSON.stringify({ detail: "nope" }), { status: 404 });
    return new Response(JSON.stringify(routes[path]), { status });
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("Dashboard", () => {
  it("shows summary, sync warnings and verified applications with date-only deadlines", async () => {
    mockFetch({
      "/api/dashboard/summary": { active_applications: 1, tracked_not_submitted: 0, submitted_or_acknowledged: 0, verified_submissions: 1, unverified_records: 0, pending_oa: 1, deadlines_within_72h: 0, overdue: 0, no_reliable_deadline: 0, interviews: 0, offers: 0, rejected: 0, review_needed: 2, sync },
      "/api/dashboard/digest": { lines: [], pending_reviews: 0, email_events_last_24h: 0 },
      "/api/dashboard/changes": { since: "2027-02-01T00:00:00Z", events: [], new_reviews: 0 },
      "/api/resumes": [],
      "/api/applications": [app],
    });
    renderWith(<Dashboard />);
    expect(await screen.findByText("Product Manager Intern")).toBeInTheDocument();
    expect(screen.getByText(/Local model unavailable/)).toBeInTheDocument();
    expect(screen.getByText("Pending OAs", { selector: ".stat-label" }).closest(".stat-card")).toHaveTextContent("1");
    expect(screen.queryByText(/Needs review/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Email/ })).toHaveAttribute("href", "https://outlook.live.com/owa/?ItemID=abc");
    expect(screen.getByRole("button", { name: /Sync now/ })).toBeInTheDocument();
  });

  it("groups roles under one company, sorts by applied date and confirms removal", async () => {
    const swe = { ...app, id: "a2", role_title: "Software Engineer Intern", category: "SDE/SWE", submitted_at: "2027-01-15T10:00:00Z", next_action: null };
    const other = { ...app, id: "a3", company: "Hooli", role_title: "Solutions Engineer", category: "SE", submitted_at: "2027-01-01T10:00:00Z", next_action: null };
    mockFetch({
      "/api/dashboard/summary": { active_applications: 3, tracked_not_submitted: 0, submitted_or_acknowledged: 3, verified_submissions: 3, unverified_records: 0, pending_oa: 0, deadlines_within_72h: 0, overdue: 0, no_reliable_deadline: 0, interviews: 0, offers: 0, rejected: 0, review_needed: 0, sync },
      "/api/sync/status": { ...sync, running: false },
      "/api/applications": [other, swe, app],
    });
    renderWith(<Dashboard />);
    await screen.findByText("Product Manager Intern");
    const roles = () => screen.getAllByRole("link").map((l) => l.textContent).filter((t) => /Intern|Engineer$/.test(t ?? ""));
    // Default: Applied, newest first, flat (no company grouping).
    expect(roles()).toEqual(["Product Manager Intern", "Software Engineer Intern", "Solutions Engineer"]);
    expect(screen.queryByText("2 roles")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Applied/ }));
    expect(roles()).toEqual(["Solutions Engineer", "Software Engineer Intern", "Product Manager Intern"]);
    // Company: companies A→Z, roles by date (oldest first on the first click), grouped.
    fireEvent.click(screen.getByRole("button", { name: /Company/ }));
    expect(roles()).toEqual(["Solutions Engineer", "Software Engineer Intern", "Product Manager Intern"]);
    expect(screen.getByText("2 roles")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Company/ }));
    expect(roles()).toEqual(["Solutions Engineer", "Product Manager Intern", "Software Engineer Intern"]);
    // Multi-select status / category chips filter client-side.
    fireEvent.click(within(screen.getByRole("group", { name: "Category" })).getByRole("button", { name: /^SE/ }));
    expect(roles()).toEqual(["Solutions Engineer"]);
    fireEvent.click(within(screen.getByRole("group", { name: "Category" })).getByRole("button", { name: /^PM/ }));
    expect(roles()).toEqual(["Solutions Engineer", "Product Manager Intern"]);
    fireEvent.click(within(screen.getByRole("group", { name: "Category" })).getByRole("button", { name: /^All/ }));
    fireEvent.click(within(screen.getByRole("group", { name: "Status" })).getByRole("button", { name: /OA invited/ }));
    expect(roles()).toEqual(["Solutions Engineer", "Product Manager Intern", "Software Engineer Intern"]); // all are OA invited
    fireEvent.click(screen.getByRole("button", { name: "Remove Hooli Solutions Engineer" }));
    const dialog = screen.getByText("Remove this application?").closest(".modal") as HTMLElement;
    expect(within(dialog).getByText("Hooli")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("shows an error state when the backend is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    renderWith(<Dashboard />);
    expect(await screen.findAllByRole("alert")).not.toHaveLength(0);
  });
});

describe("Deadlines", () => {
  it("lists one row per deadline with its type, nearest first", async () => {
    const base = { application_id: "a1", assessment_id: null, interview_id: null, instruction: null, state: "open", due_timezone: null, due_anchor: null, deadline_assumption: null, deadline_confidence: null, evidence_quote: null, completed_at: null, created_at: "", role_title: "PM Intern" };
    mockFetch({
      "/api/dashboard/deadlines": {
        items: [
          { ...base, id: "d1", kind: "oa_deadline", title: "Complete HackerRank OA", due_at: "2027-03-01T15:00:00Z", due_date: null, due_precision: "instant", due_expression_raw: "within 72 hours", urgency: "later", company: "IBM", deadline_type: "OA" },
          { ...base, id: "d2", kind: "feedback", title: "Expected response", due_at: null, due_date: "2027-03-10", due_precision: "day", due_expression_raw: "by March 10", urgency: "later", company: "Hooli", deadline_type: "Feedback" },
        ],
        buckets: {}, pending_review: [], now: "2027-02-01T00:00:00Z",
      },
    });
    renderWith(<Deadlines />);
    expect(await screen.findByText("Complete HackerRank OA")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("IBM");
    expect(rows[1]).toHaveTextContent("Hooli");
    expect(within(rows[1]).getByText("Feedback")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /OA/ }));
    expect(screen.queryByText("Expected response")).not.toBeInTheDocument();
    expect(screen.queryByText(/needs your confirmation/i)).not.toBeInTheDocument();
  });
});
