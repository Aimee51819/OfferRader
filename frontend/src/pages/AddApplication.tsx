import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { DuplicateCheck } from "../api/types";
import DuplicateNotice from "../components/DuplicateNotice";

const EMPTY = {
  company: "",
  role_title: "",
  external_job_id: "",
  posting_url: "",
  recruiting_cycle: "Summer 2027",
  category: "",
  location: "",
  application_channel: "",
  resume_version_id: "",
  notes: "",
};

export default function AddApplication() {
  const nav = useNavigate();
  const [form, setForm] = useState(EMPTY);
  const [submitted, setSubmitted] = useState(false);
  const [submittedAt, setSubmittedAt] = useState("");
  const [check, setCheck] = useState<DuplicateCheck | null>(null);
  const [override, setOverride] = useState(false);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const { data: resumes = [] } = useQuery({ queryKey: ["resumes"], queryFn: api.resumes });
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  // Live preflight as the user types company / role / req / URL.
  useEffect(() => {
    if (!form.company.trim()) {
      setCheck(null);
      return;
    }
    const t = setTimeout(() => {
      api
        .duplicateCheck({
          company: form.company,
          role_title: form.role_title || null,
          external_job_id: form.external_job_id || null,
          posting_url: form.posting_url || null,
          recruiting_cycle: form.recruiting_cycle || null,
        })
        .then(setCheck)
        .catch(() => setCheck(null));
    }, 350);
    return () => clearTimeout(t);
  }, [form.company, form.role_title, form.external_job_id, form.posting_url, form.recruiting_cycle]);

  const create = useMutation({
    mutationFn: () =>
      api.createApplication({
        ...Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v === "" ? null : v])),
        confirmed_submitted: submitted,
        submitted_at: submitted && submittedAt ? new Date(submittedAt).toISOString() : null,
        override_duplicate: override,
        override_reason: override ? reason : null,
      }),
    onSuccess: (a) => nav(`/applications/${a.id}`),
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        setCheck((e.detail as { duplicate_check: DuplicateCheck }).duplicate_check);
        setErr("Blocked as a duplicate. Review the match below; override only if this really is a different opening.");
      } else setErr(String(e));
    },
  });

  const blocked = check?.decision === "BLOCK" && !(override && reason.trim());
  return (
    <div className="stack narrow">
      <div className="page-header">
        <h1 className="page-title">Add application</h1>
        <p className="subtitle">The duplicate check runs before anything is saved. This app never submits applications for you.</p>
      </div>
      <form
        className="card stack"
        onSubmit={(e) => {
          e.preventDefault();
          setErr(null);
          create.mutate();
        }}
      >
        <label className="field">Company *<input required value={form.company} onChange={set("company")} /></label>
        <label className="field">Exact role title *<input required value={form.role_title} onChange={set("role_title")} /></label>
        <div className="two-col">
          <label className="field">Requisition / job ID<input value={form.external_job_id} onChange={set("external_job_id")} /></label>
          <label className="field">Recruiting cycle<input value={form.recruiting_cycle} onChange={set("recruiting_cycle")} /></label>
        </div>
        <label className="field">Posting URL<input type="url" value={form.posting_url} onChange={set("posting_url")} /></label>
        <div className="two-col">
          <label className="field">Category
            <select value={form.category} onChange={set("category")}>
              <option value="">Infer from title</option>
              {["PM", "TPM", "SE", "SA", "SWE", "Data", "Other"].map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
          <label className="field">Channel
            <select value={form.application_channel} onChange={set("application_channel")}>
              <option value="">—</option>
              {["company_site", "ATS", "referral", "LinkedIn", "other"].map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
        </div>
        <div className="two-col">
          <label className="field">Location<input value={form.location} onChange={set("location")} /></label>
          <label className="field">Resume version
            <select value={form.resume_version_id} onChange={set("resume_version_id")}>
              <option value="">—</option>
              {resumes.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
            </select>
          </label>
        </div>
        <label className="field">Notes<textarea rows={2} value={form.notes} onChange={set("notes")} /></label>
        <fieldset className="card">
          <label className="row">
            <input type="checkbox" checked={submitted} onChange={(e) => setSubmitted(e.target.checked)} />
            I have actually submitted this application (makes it a verified submission)
          </label>
          {submitted && (
            <label className="field">When (your local time)<input type="datetime-local" value={submittedAt} onChange={(e) => setSubmittedAt(e.target.value)} /></label>
          )}
          {!submitted && <p className="muted">Saved as "Tracked" — not counted as applied.</p>}
        </fieldset>
        <DuplicateNotice check={check} />
        {check && check.decision !== "ALLOW" && (
          <label className="row">
            <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} /> Override (recorded in the audit log)
          </label>
        )}
        {override && <label className="field">Override reason *<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>}
        {err && <div className="alert alert-error" role="alert">{err}</div>}
        <div className="row">
          <button className="btn-primary" type="submit" disabled={create.isPending || blocked}>Save</button>
        </div>
      </form>
    </div>
  );
}
