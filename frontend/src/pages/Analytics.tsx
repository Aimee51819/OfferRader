import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorBox, Loading } from "../components/States";

const STEPS: [string, string][] = [
  ["submitted", "Submitted"],
  ["assessment", "Assessment"],
  ["screening", "Screening"],
  ["interviewing", "Interview"],
  ["offer", "Offer"],
];

export default function Analytics() {
  const { data, error, isLoading } = useQuery({ queryKey: ["funnel"], queryFn: api.funnel });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const top = Math.max(1, data.funnel.submitted ?? 0);
  return (
    <div className="stack">
      <div className="page-header">
        <h1 className="page-title">Funnel</h1>
        <p className="subtitle">Counts applications that ever reached each step (from the event history, not just the current stage).</p>
      </div>
      <div className="card funnel" role="list">
        {STEPS.map(([k, label], i) => {
          const n = data.funnel[k] ?? 0;
          const prev = i === 0 ? n : data.funnel[STEPS[i - 1][0]] ?? 0;
          return (
            <div className="funnel-step" role="listitem" key={k}>
              <div className="funnel-label">{label}</div>
              <div className="funnel-track">
                <div className="funnel-fill" style={{ width: `${(100 * n) / top}%` }} />
              </div>
              <div className="funnel-count">{n}</div>
              <div className="funnel-conversion muted">{i > 0 && prev ? `${Math.round((100 * n) / prev)}% of previous` : ""}</div>
            </div>
          );
        })}
      </div>
      <div className="card">
        <h3 className="card-title">By category</h3>
        <table className="analytics-table">
          <thead><tr><th>Category</th><th>Total</th><th>Active</th><th>Rejected</th><th>Offers</th></tr></thead>
          <tbody>
            {Object.entries(data.by_category).map(([c, v]) => (
              <tr key={c}><td>{c}</td><td>{v.total}</td><td>{v.active}</td><td>{v.rejected}</td><td>{v.offers}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
