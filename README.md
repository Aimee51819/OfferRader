<p align="center">
  <img src="docs/banner.png" alt="Job Application Tracker" width="100%" />
</p>

<h1 align="center">Job Application Feedback Agent</h1>

<p align="center">
  <strong>A local-first tracker that reads recruiting email read-only, extracts OA deadlines with a local Qwen model, and stops you from applying to the same opening twice.</strong>
</p>

Built on [liamgelfand/application-tracker](https://github.com/liamgelfand/application-tracker) (MIT). Everything runs on your Mac: FastAPI + SQLite backend, React dashboard, Ollama for the model. Nothing is sent to a cloud AI service, and no email is ever sent, moved, deleted or marked read.

## What it answers

| Question | Where |
| --- | --- |
| Where have I *actually* applied (company, exact role, req id / URL, date, channel, resume)? | Dashboard table → **Evidence** column (✓ Verified vs Unverified) |
| What is each application's latest verified status? | Stage badge + per-application timeline (append-only event history) |
| What do I need to do now, and by when (Eastern Time), in the email's own words? | **Deadlines** page, application detail |
| Which OAs are due in 72 h / 24 h / 3 h, overdue, or have no reliable deadline? | **Deadlines** page buckets, dashboard cards, reminders |
| Am I about to apply to the same opening again? | **Add application** live preflight: BLOCK / WARN / ALLOW with reasons |
| What changed since the last sync, and what needs my review? | Dashboard "Since last sync", **Review queue** |

## Safety properties

- **Read-only mail.** Gmail uses `gmail.readonly`, Microsoft Graph uses delegated `Mail.Read`, both via OAuth 2.0 + PKCE. The HTTP client used by both adapters can only issue `GET` requests to the provider's API host. Optional IMAP uses `EXAMINE` + `BODY.PEEK[]`.
- **Local model only.** Qwen3-4B-Instruct through Ollama on loopback. The Ollama URL must resolve to a loopback address; there is no OpenAI/Anthropic/Gemini fallback. If Ollama is down, mail waits in the local store and is parsed later.
- **Never invents facts.** The model only quotes. A deterministic normalizer computes deadlines; date-only deadlines stay date-only; a time without a timezone never becomes a UTC instant; DST gaps/overlaps and business-day counts go to review.
- **One event → one application.** Company-only evidence never auto-attaches; a rejection for one requisition can't touch another.
- **Review-first.** Terminal decisions (rejection, offer, withdrawal), ambiguous matches/deadlines, conflicting reminders, unknown applications and pinned stages always wait for you. Every approve / edit / reassign / reject / undo is audited.
- **No auto-applying.** This tool never submits applications. A separate, human-approved tool may report a *confirmed* receipt to `POST /api/integrations/submissions`.

## Quick start (Apple Silicon Mac)

Full walkthrough including OAuth app registration: **[docs/SETUP_MACOS.md](docs/SETUP_MACOS.md)**.

```bash
brew install python@3.12 node ollama
```

```bash
git clone <your fork> application-tracker && cd application-tracker
make setup
```

```bash
ollama serve   # or open the Ollama app
```

```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

```bash
cp backend/.env.example backend/.env   # then add OAuth client IDs
make migrate
make check-ollama
make dev       # backend http://127.0.0.1:8000, UI http://localhost:5173
```

Try it with synthetic mail first (separate data directory, no network):

```bash
cd backend && DATA_DIR=./demo-data .venv/bin/python -m app.cli demo --reset
DATA_DIR=./demo-data .venv/bin/uvicorn app.main:app --port 8000
```

## Tests and evaluation

```bash
make test                      # 104 backend tests + 12 frontend tests
make eval                      # score your installed Qwen on the synthetic corpus (needs Ollama)
cd backend && .venv/bin/python -m app.cli eval --backend rules   # regex baseline, for comparison
cd backend && .venv/bin/python -m app.cli reparse-fixtures --backend ollama 03_ibm_pm_oa  # re-parse fixtures in a sandbox
```

## Documentation

- [docs/SETUP_MACOS.md](docs/SETUP_MACOS.md) — install, Ollama model, OAuth consent checklist (Gmail + Microsoft), first sync, Docker option.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — ER diagram, pipeline, extraction JSON contract, matching / transition / duplicate policies, REST endpoints, known limitations.
- Interactive API docs: http://127.0.0.1:8000/docs

## Security notes

- Single-user and **local only**: the server binds `127.0.0.1`, rejects non-loopback clients and unexpected `Host` headers (DNS rebinding), and blocks cross-origin writes. Startup refuses a non-loopback `BIND_HOST` unless `ALLOW_NON_LOOPBACK=true`.
- **Do not expose this to the internet.** A remote deployment needs authentication, TLS and a security review first; none of that is provided here. Never port-forward 8000, 5173, or Ollama's 11434.
- OAuth tokens and IMAP passwords are encrypted at rest (Fernet) in SQLite, keyed by `DATA_DIR/secret.key` (mode 0600) or, with `USE_KEYCHAIN=true`, the macOS Keychain. Tokens are never logged or returned by the API. **Disconnect** deletes the token (and revokes it at Google).
- Email bodies are converted to plain text, capped in size, wrapped as untrusted data in the prompt and never rendered as HTML. Attachments are skipped. Bodies are cached encrypted only until parsed; a short plain-text evidence excerpt is kept for review.
- The integration endpoint needs `Authorization: Bearer <token>` (`python -m app.cli integration-token`).
- No telemetry or third-party analytics. The only outbound calls are to Google/Microsoft APIs (when you connect a mailbox) and the optional, user-triggered update check inherited from upstream (Windows builds only).
- Keep `backend/.env`, `DATA_DIR` and backups out of git and cloud-sync folders.

## License

MIT — see [LICENSE](LICENSE).
