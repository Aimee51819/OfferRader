import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ImportRow } from "../api/types";
import { DecisionBadge } from "../components/Badges";

export default function ImportExport() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<ImportRow[] | null>(null);
  const [overrides, setOverrides] = useState<number[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const preview = useMutation({ mutationFn: () => api.importCsv(file!, true, overrides), onSuccess: (r) => setRows(r.rows) });
  const commit = useMutation({
    mutationFn: () => api.importCsv(file!, false, overrides),
    onSuccess: (r) => {
      setRows(r.rows);
      setResult(`Imported ${r.created} row(s).`);
      qc.invalidateQueries();
    },
  });
  const toggle = (n: number) => setOverrides((o) => (o.includes(n) ? o.filter((x) => x !== n) : [...o, n]));
  return (
    <div className="stack">
      <div className="page-header">
        <h1 className="page-title">Import & export</h1>
      </div>
      <div className="card stack">
        <h3 className="card-title">Import CSV</h3>
        <p className="muted">
          Columns: company, role_title, external_job_id, posting_url, recruiting_cycle, category, location, application_channel,
          stage, submitted_at (ISO 8601), resume_label, notes. Every row goes through the same duplicate check. Preview first.
        </p>
        <input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setRows(null); setResult(null); }} />
        <div className="row">
          <button className="btn-secondary" disabled={!file || preview.isPending} onClick={() => preview.mutate()}>Preview (dry run)</button>
          <button className="btn-primary" disabled={!file || !rows || commit.isPending} onClick={() => commit.mutate()}>Import</button>
        </div>
        {result && <div className="alert alert-success">{result}</div>}
        {rows && (
          <table className="analytics-table">
            <thead><tr><th>#</th><th>Company</th><th>Role</th><th>Check</th><th>Action</th><th>Override</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.row}>
                  <td>{r.row}</td>
                  <td>{r.company}</td>
                  <td>{r.role_title}</td>
                  <td>{r.duplicate_check && <><DecisionBadge decision={r.duplicate_check.decision} /> <span className="muted">{r.duplicate_check.reasons[0]}</span></>}</td>
                  <td>{r.action}{r.error && <div className="alert-error">{r.error}</div>}</td>
                  <td>{r.duplicate_check && r.duplicate_check.decision !== "ALLOW" && <input type="checkbox" aria-label={`override row ${r.row}`} checked={overrides.includes(r.row)} onChange={() => toggle(r.row)} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card">
        <h3 className="card-title">Export</h3>
        <div className="row">
          <a className="btn-secondary" href="/api/export/applications.csv">Applications (CSV)</a>
          <a className="btn-secondary" href="/api/export/json">Everything (JSON, with timelines)</a>
          <a className="btn-secondary" href="/api/export/deadlines.ics">Deadlines (.ics)</a>
          <a className="btn-secondary" href="/api/backup">Full backup (zip)</a>
        </div>
        <p className="muted">The .ics file is for you to import into a calendar yourself; nothing is written to any calendar.</p>
      </div>
    </div>
  );
}
