import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Dashboard from "./Dashboard";
import ReviewQueue from "./ReviewQueue";

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
  pending_reviews: 0, source: "email",
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
    expect(screen.getByText("✓ Verified")).toBeInTheDocument();
    expect(screen.getByText(/date only — no time given/)).toBeInTheDocument();
    expect(screen.getByText("Needs review", { selector: ".stat-label" }).closest(".stat-card")).toHaveTextContent("2");
  });

  it("shows an error state when the backend is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    renderWith(<Dashboard />);
    expect(await screen.findAllByRole("alert")).not.toHaveLength(0);
  });
});

describe("ReviewQueue", () => {
  it("shows evidence, candidates and a create-new option side by side", async () => {
    mockFetch({
      "/api/reviews": [{
        id: "p1", proposal_type: "match", state: "pending", reason: "company-only evidence; the email does not identify the role",
        confidence: 0.8, created_at: "", reviewed_at: null, review_notes: null,
        proposed_changes: {
          company: { name: "IBM", resolved_name: "IBM", resolution: "exact company name 'IBM'", ambiguous: false, confidence: 0.9 },
          role: { title: null, requisition_id: null, job_url: null, confidence: 0 },
          match: { decision: "review", application_id: "a1", score: 0.3, reason: "" },
          create_application: false,
          events: [{ index: 0, type: "assessment_invitation", label: "Assessment invitation", event_at: "2027-02-08T16:00:00Z", implied_stage: "assessment_invited", action_required: "Complete the OA", assessment_type: "OA", assessment_provider: null, evidence_quote: "please complete the online assessment", evidence_quotes: ["please complete the online assessment"], confidence: 0.9,
            deadline: { raw: "within 72 hours of receiving this email", due_at: "2027-02-11T16:00:00Z", due_date: null, due_timezone: null, precision: "relative", anchor: "email_received_at", assumptions: [], needs_review: false, confidence: 0.95, kind: "relative" }, interview_time: null }],
          conflicts: [], flags: [], overall_confidence: 0.8,
        },
        alternatives: [
          { application_id: "a1", company: "IBM", role_title: "Product Manager Intern", external_job_id: "84211", stage: "acknowledged", score: 0.3, reasons: ["same company"], disqualified: false },
          { application_id: "a2", company: "IBM", role_title: "Software Engineer Intern", external_job_id: "84377", stage: "acknowledged", score: 0.3, reasons: ["same company"], disqualified: false },
        ],
        target: null,
        email: { id: "m1", subject: "IBM: an update", sender_name: "IBM", sender_address: "x@ibm.example", received_at: "2027-02-08T16:00:00Z", excerpt: "Hi Aimee, please complete the online assessment...", web_link: null },
        parser: { model: "ollama:qwen3:4b-instruct-2507-q4_K_M", prompt_version: "2026-11-05.1", schema_version: "1.0", notes: null },
      }],
    });
    renderWith(<ReviewQueue />);
    expect(await screen.findByText(/company-only evidence/)).toBeInTheDocument();
    expect(screen.getAllByText(/please complete the online assessment/).length).toBeGreaterThan(0);
    expect(screen.getByText(/within 72 hours of receiving this email/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Software Engineer Intern/ })).toBeInTheDocument();
    expect(screen.getByText(/Create a new application/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled(); // nothing chosen yet
  });
});
