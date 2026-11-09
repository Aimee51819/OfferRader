import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SyncButton } from "./Dashboard";
import { api } from "../api/client";
import type { Account, Settings as SettingsT } from "../api/types";
import { ErrorBox, Loading } from "../components/States";
import { formatInstant } from "../lib/format";

function ModelHealth() {
  const { data, refetch, isFetching } = useQuery({ queryKey: ["llm"], queryFn: api.llmHealth });
  return (
    <div className="card">
      <div className="flex-between">
        <h3 className="card-title">Local model (Ollama)</h3>
        <button className="btn-ghost" onClick={() => refetch()} disabled={isFetching}>Check again</button>
      </div>
      {!data ? <Loading what="Checking Ollama" /> : data.ok ? (
        <div className="alert alert-success">Ollama {data.version} is running and <code>{data.model}</code> is installed.</div>
      ) : (
        <div className="alert alert-warn" role="alert">
          <strong>Model not ready.</strong> {data.error ?? ""} {data.hint ?? ""}
          {data.installed_qwen3_models && data.installed_qwen3_models.length > 0 && (
            <div>Installed Qwen3 tags: {data.installed_qwen3_models.join(", ")}</div>
          )}
          <div className="muted">New mail is stored and parsed once the model is available. There is no cloud fallback.</div>
        </div>
      )}
    </div>
  );
}

function AccountRow({ a }: { a: Account }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries();
  const toggle = useMutation({ mutationFn: () => api.updateAccount(a.id, { enabled: !a.enabled }), onSuccess: refresh });
  const disconnect = useMutation({ mutationFn: () => api.disconnectAccount(a.id), onSuccess: refresh });
  const fetchOnly = useMutation({ mutationFn: () => api.fetchOnly(a.id), onSuccess: refresh });
  const scope = a.sync_scope as { include_spam?: boolean; inbox_only?: boolean; folders?: string[] };
  const setScope = useMutation({ mutationFn: (s: Record<string, unknown>) => api.updateAccount(a.id, { sync_scope: s }), onSuccess: refresh });
  return (
    <tr>
      <td>{a.provider}</td>
      <td>{a.address}</td>
      <td>
        {a.connected ? "connected" : "not connected"}
        {a.last_error_code && <div className="alert-error">{a.last_error_code === "auth_expired" ? "Authorization expired — reconnect" : a.last_error_code}</div>}
      </td>
      <td>{a.last_successful_sync_at ? formatInstant(a.last_successful_sync_at) : "never"}</td>
      <td>
        {a.provider === "gmail" && (
          <>
            <label className="row"><input type="checkbox" checked={!!scope.inbox_only} onChange={(e) => setScope.mutate({ inbox_only: e.target.checked })} /> Inbox only</label>
            <label className="row"><input type="checkbox" checked={!!scope.include_spam} onChange={(e) => setScope.mutate({ include_spam: e.target.checked })} /> Include spam</label>
          </>
        )}
        {a.provider === "outlook" && (
          <label className="row">
            <input type="checkbox" checked={(scope.folders ?? []).includes("junkemail")}
              onChange={(e) => setScope.mutate({ folders: e.target.checked ? ["inbox", "junkemail"] : ["inbox"] })} /> Include Junk
          </label>
        )}
      </td>
      <td className="row">
        <button className="btn-ghost" onClick={() => toggle.mutate()}>{a.enabled ? "Pause" : "Enable"}</button>
        <button className="btn-ghost" onClick={() => fetchOnly.mutate()} disabled={!a.connected || fetchOnly.isPending}>Fetch only</button>
        <button className="btn-danger" onClick={() => { if (confirm(`Disconnect ${a.address}? The stored token is deleted (and revoked for Gmail).`)) disconnect.mutate(); }}>Disconnect</button>
      </td>
    </tr>
  );
}

function Accounts({ s }: { s: SettingsT }) {
  const { data: accounts = [] } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const [err, setErr] = useState<string | null>(null);
  const connect = async (p: "gmail" | "outlook") => {
    setErr(null);
    try {
      const { url } = await api.oauthStart(p);
      window.location.href = url; // human consent happens on Google / Microsoft
    } catch (e) {
      setErr(String(e));
    }
  };
  return (
    <div className="card">
      <h3 className="card-title">Mailboxes (read-only)</h3>
      <p className="muted">Gmail uses <code>gmail.readonly</code>; Outlook uses <code>Mail.Read</code>. The app never sends, deletes, moves or marks mail.</p>
      <div className="row">
        <button className="btn-primary" disabled={!s.oauth.gmail_configured} onClick={() => connect("gmail")}>Connect Gmail</button>
        <button className="btn-primary" disabled={!s.oauth.outlook_configured} onClick={() => connect("outlook")}>Connect Outlook / Microsoft 365</button>
      </div>
      {(!s.oauth.gmail_configured || !s.oauth.outlook_configured) && (
        <div className="alert alert-info">
          OAuth client IDs are not configured{!s.oauth.gmail_configured ? " for Gmail" : ""}{!s.oauth.outlook_configured ? " for Outlook" : ""}.
          Add them to <code>backend/.env</code> (see README → OAuth app registration). Redirect URIs to register:
          <div><code>{s.oauth.gmail_redirect_uri}</code></div>
          <div><code>{s.oauth.outlook_redirect_uri}</code></div>
        </div>
      )}
      {err && <ErrorBox error={err} />}
      {accounts.length > 0 && (
        <table className="analytics-table">
          <thead><tr><th>Provider</th><th>Address</th><th>Status</th><th>Last sync</th><th>Scope</th><th /></tr></thead>
          <tbody>{accounts.map((a) => <AccountRow key={a.id} a={a} />)}</tbody>
        </table>
      )}
    </div>
  );
}

function SyncPanel({ s, save }: { s: SettingsT; save: (p: Partial<SettingsT>) => void }) {
  const { data: runs = [] } = useQuery({ queryKey: ["runs"], queryFn: api.syncRuns, refetchInterval: 15000 });
  return (
    <div className="card stack">
      <div className="card-head">
        <h3 className="card-title">Sync</h3>
        <SyncButton />
      </div>
      <p className="muted">Recognised application emails go straight to the dashboard. Remove anything wrong from there.</p>
      <label className="switch-row"><input type="checkbox" checked={s.auto_sync_enabled} onChange={(e) => save({ auto_sync_enabled: e.target.checked })} /> Automatic background sync</label>
      <div className="form-grid">
      <label className="field">Interval (minutes)
        <input type="number" min={5} max={1440} defaultValue={Math.round(s.poll_interval_seconds / 60)} onBlur={(e) => save({ poll_interval_seconds: Number(e.target.value) * 60 })} />
      </label>
      <label className="field">Initial look-back (days)
        <input type="number" min={1} max={365} defaultValue={s.initial_lookback_days} onBlur={(e) => save({ initial_lookback_days: Number(e.target.value) })} />
      </label>
      </div>
      <h4>Recent runs</h4>
      <table className="analytics-table">
        <thead><tr><th>Started</th><th>Status</th><th>New</th><th>Parsed</th><th>Applied</th><th>Notes</th></tr></thead>
        <tbody>
          {runs.slice(0, 8).map((r) => (
            <tr key={r.id}><td>{formatInstant(r.started_at)}</td><td>{r.status}</td><td>{r.messages_new}</td><td>{r.messages_parsed}</td><td>{r.events_applied}</td><td>{r.safe_error_summary}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Notifications({ s, save }: { s: SettingsT; save: (p: Partial<SettingsT>) => void }) {
  const qc = useQueryClient();
  const { data: rules = [] } = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const [text, setText] = useState("");
  useEffect(() => setText(rules.filter((r) => r.enabled).map((r) => r.lead_time_minutes / 60).join(", ")), [rules]);
  const saveRules = useMutation({
    mutationFn: () =>
      api.saveRules(
        text.split(",").map((x) => Number(x.trim())).filter((h) => h > 0).map((h) => ({ lead_time_minutes: Math.round(h * 60), enabled: true, channel: "dashboard" })),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules"] }),
  });
  return (
    <div className="card stack">
      <h3 className="card-title">Reminders</h3>
      <label className="field">Remind me this many hours before a deadline (comma-separated)
        <input value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <button className="btn-secondary" onClick={() => saveRules.mutate()}>Save reminder times</button>
      <label className="switch-row"><input type="checkbox" checked={s.desktop_notifications} onChange={(e) => save({ desktop_notifications: e.target.checked })} /> macOS desktop notifications (no email is ever sent)</label>
    </div>
  );
}

function Policy({ s, save }: { s: SettingsT; save: (p: Partial<SettingsT>) => void }) {
  return (
    <div className="card stack">
      <h3 className="card-title">General</h3>
      <label className="field">Display timezone
        <input defaultValue={s.display_timezone} onBlur={(e) => save({ display_timezone: e.target.value })} />
      </label>
      <label className="field">Ollama model tag
        <input defaultValue={s.ollama_model} onBlur={(e) => save({ ollama_model: e.target.value })} />
      </label>
    </div>
  );
}

function Resumes() {
  const qc = useQueryClient();
  const { data = [] } = useQuery({ queryKey: ["resumes"], queryFn: api.resumes });
  const [label, setLabel] = useState("");
  const [filename, setFilename] = useState("");
  const add = useMutation({ mutationFn: () => api.addResume({ label, filename: filename || null }), onSuccess: () => { setLabel(""); setFilename(""); qc.invalidateQueries({ queryKey: ["resumes"] }); } });
  return (
    <div className="card stack">
      <h3 className="card-title">Resume versions</h3>
      <p className="muted">Labels and file names only — resume contents are never stored or sent anywhere.</p>
      <ul>{data.map((r) => <li key={r.id}>{r.label} {r.filename && <span className="muted">({r.filename})</span>}</li>)}</ul>
      <div className="row">
        <input placeholder="Label, e.g. PM 2027" value={label} onChange={(e) => setLabel(e.target.value)} />
        <input placeholder="File name (optional)" value={filename} onChange={(e) => setFilename(e.target.value)} />
        <button className="btn-secondary" disabled={!label} onClick={() => add.mutate()}>Add</button>
      </div>
    </div>
  );
}

export default function Settings() {
  const qc = useQueryClient();
  const { data: s, error, isLoading } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const mut = useMutation({ mutationFn: api.saveSettings, onSuccess: (d) => qc.setQueryData(["settings"], d) });
  const connected = new URLSearchParams(window.location.search).get("connected");
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!s) return null;
  return (
    <div className="stack">
      <div className="page-header"><h1 className="page-title">Settings</h1></div>
      {connected && <div className="alert alert-success">Connected {connected} (read-only).</div>}
      {mut.error && <ErrorBox error={mut.error} />}
      <ModelHealth />
      <Accounts s={s} />
      <SyncPanel s={s} save={mut.mutate} />
      <div className="two-col">
        <Policy s={s} save={mut.mutate} />
        <Notifications s={s} save={mut.mutate} />
      </div>
      <Resumes />
      <div className="card">
        <h3 className="card-title">Integration (career-ops / JobAppAgent)</h3>
        <p className="muted">
          A separate, human-approved application tool may call <code>POST /api/integrations/preflight</code> before submitting and
          <code> POST /api/integrations/submissions</code> with a confirmed receipt afterwards. Get the token with
          <code> cd backend &amp;&amp; .venv/bin/python -m app.cli integration-token</code>.
        </p>
      </div>
    </div>
  );
}
