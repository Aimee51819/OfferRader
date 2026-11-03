<p align="center">
  <img src="docs/banner.png" alt="Job Application Tracker" width="100%" />
</p>

<h1 align="center">Job Application Tracker</h1>

<p align="center">
  <strong>Self-hosted, AI-powered job application tracker that reads your inbox.</strong><br />
  Bring your own model — OpenAI, Claude, Gemini, or a local Ollama model. No accounts, no servers, no cost.
</p>

<p align="center">
  <a href="#quickstart"><img src="https://img.shields.io/badge/setup-one%20command-6366f1" alt="One-command setup" /></a>
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" />
  <img src="https://img.shields.io/badge/backend-FastAPI-009688" alt="FastAPI" />
  <img src="https://img.shields.io/badge/frontend-React-61dafb" alt="React" />
  <img src="https://img.shields.io/badge/LLM-OpenAI%20%7C%20Claude%20%7C%20Gemini%20%7C%20Ollama-8b5cf6" alt="LLM providers" />
</p>

---

Track your job search end to end. Paste a job listing and AI fills in the details; connect your email and it detects confirmations, interviews, and rejections and updates your board automatically. Everything runs locally on your machine.

## Screenshots

| Dashboard (kanban board) | Analytics |
| --- | --- |
| ![Dashboard](docs/screenshots/dashboard.png) | ![Analytics](docs/screenshots/analytics.png) |

| Review queue | Settings |
| --- | --- |
| ![Review queue](docs/screenshots/review.png) | ![Settings](docs/screenshots/settings.png) |

> Want to regenerate these? Run `cd backend && python -m scripts.seed_demo --reset` to load demo data, then `make dev` and capture the screens.

## Features

- **Kanban board + table** to track applications across stages: Saved, Applied, Online Assessment, Phone Screen, Interview, Offer, Rejected, Ghosted, Accepted.
- **Paste-to-add**: paste any job listing and an LLM extracts the company, title, location, salary, URL, skills, and a summary. Review, then save.
- **Browser extension**: capture the job listing on the page you're viewing in one click (see [`extension/`](extension/)).
- **Inbox monitoring (IMAP)**: connect any email provider. A background job reads new mail, detects job-related messages (confirmations, recruiter outreach, interview invites, rejections), matches them to your applications, and proposes updates.
- **Review queue**: proposed changes wait for your approval by default (flip on auto-apply if you trust it).
- **Drag-and-drop board** plus **desktop notifications** when new job emails need review.
- **Analytics**: response rate, interview rate, offer rate, average days to response, and applications-per-week.
- **CSV / JSON import and export** so you can bring in an existing spreadsheet or back up your data.
- **Multiple AI providers, toggleable**: configure several providers with their own keys and models, and switch the active one anytime. Local models via Ollama are fully supported.
- **Per-application timeline** recording every status change and email event.
- **Secrets encrypted at rest**: API keys and email passwords are encrypted with a locally generated key.

## Tech stack

- **Backend**: Python, FastAPI, SQLAlchemy, SQLite, APScheduler, LiteLLM (unified LLM interface).
- **Frontend**: React, TypeScript, Vite, TanStack Query.

## Architecture

```
frontend (React)  ->  backend (FastAPI)  ->  SQLite
                             |-> LLM layer (LiteLLM) -> OpenAI / Anthropic / Gemini / Ollama
                             |-> IMAP poller (APScheduler) -> your inbox -> review queue
```

## Quickstart

Requires **Python 3.11+** and **Node 18+**.

```bash
git clone <your-repo-url> application-tracker
cd application-tracker
make setup   # install backend + frontend dependencies (run once)
make app     # build the frontend, then launch the tray app
```

The app sits in the system tray (it does not auto-open the browser).
Right-click the tray icon to **Open**, run a **Health Check**, enable **Start on Login**, or **Quit**.

The server listens on `http://127.0.0.1:8000`. If that port is busy, the launcher takes the next free port up to 8100, logs which one it chose, and opens the browser there. Pass `--port <number>` to `launcher.py` to start the search somewhere else. Launching again while AppTracker is running just opens the running copy.

> **Windows note:** `make` isn't built in — install it with `winget install GnuWin32.Make` or use [Git Bash](https://gitforwindows.org/).  
> Alternatively run the two commands manually:
> ```powershell
> cd frontend; npm run build; cd ..
> backend\.venv\Scripts\python backend\launcher.py
> ```

### For contributors (live reload)

```bash
make dev    # backend on :8000 + Vite dev server on :5173 simultaneously
```

### Docker

```bash
docker compose up --build   # available at http://localhost:8080
```

Run `make help` to see all available commands.

## First-time setup

1. Open the app and go to **Settings**.
2. Under **AI Providers**, add a provider:
   - **OpenAI / Anthropic / Gemini**: pick a model and paste your API key.
   - **Ollama**: no key needed. Install [Ollama](https://ollama.com), run `ollama pull llama3.1`, and make sure it's running at `http://localhost:11434`.
   - You can add several and use **Set Active** to switch between them anytime.
3. (Optional) Under **Monitored Inboxes**, add an IMAP account and click **Test Connection**.
   - For Gmail/Outlook, create an **app password** (not your normal password) and use that.
   - Common IMAP hosts: `imap.gmail.com` (993), `outlook.office365.com` (993).
4. Go to **Add Application**, paste a job listing, and click **Parse with AI**.

## Configuration

Backend settings are read from environment variables or a `backend/.env` file (see `backend/.env.example`):

| Variable | Default | Description |
| --- | --- | --- |
| `DATA_DIR` | per-user app data dir | Where the SQLite DB and encryption key live. Unset it and the app uses `%LOCALAPPDATA%\AppTracker` on Windows, `~/Library/Application Support/AppTracker` on macOS, or `~/.local/share/AppTracker` on Linux. An existing `data/tracker.db` in the repo is copied there once on first start. |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | Allowed CORS origin. |
| `EMAIL_POLL_INTERVAL_SECONDS` | `900` | Default inbox poll interval (changeable in Settings). |
| `APP_SECRET_KEY` | auto-generated | Override the encryption key (base64 urlsafe, 32 bytes). |

## Security notes

- This is a **single-user, local-first** app with no authentication by default. Don't expose it directly to the public internet without adding auth and TLS.
- API keys and email passwords are encrypted at rest using a key stored in `DATA_DIR/secret.key`. Keep it private and backed up — if you lose the key, stored secrets can't be decrypted.
- Use **Settings → Backup → Download backup** for a single zip containing both the database and that key, and **Restore…** to load one back. Restoring keeps the replaced dataset in a `pre-restore-<timestamp>` folder beside the database.
- Your email/API credentials never leave your machine except to talk to the providers you configure.
- Storing data outside the repo also keeps SQLite away from cloud-sync clients, which corrupt WAL files.

## Testing

```bash
# Backend (pytest)
cd backend
pip install -r requirements-dev.txt
pytest

# Frontend (vitest)
cd frontend
npm run test
```

Both suites also run automatically in CI on every push and pull request.

## API

Interactive API docs are available at http://localhost:8000/docs when the backend is running (swap in the launcher's port if it moved off 8000). `GET /api/identity` returns `{"app": "apptracker"}` so tools can confirm they found AppTracker and not some other app on the port.

## Download a prebuilt Windows app

Grab `AppTracker.exe` from the [latest release](../../releases/latest). It needs no
Python install. SmartScreen will warn that the publisher is unknown because the
build isn't code-signed — choose **More info → Run anyway**.

The app opens your browser once, then lives in the system tray. Right-click the
tray icon for **Open**, **Health Check**, **Start on Login**, and **Quit**.

Pushing a `v*` tag builds and drafts a release via
[`.github/workflows/release.yml`](.github/workflows/release.yml):

```bash
git tag v1.0.0 && git push origin v1.0.0
```

### Building the `.exe` yourself

```bash
make setup
make build                      # frontend/dist must exist first
pip install pyinstaller
python backend/scripts/make_icon.py   # generates backend/assets/icon.ico
make dist                       # writes dist/AppTracker.exe
```

Or manually:

```powershell
cd frontend; npm run build; cd ..
backend\.venv\Scripts\pip install pyinstaller
backend\.venv\Scripts\python backend\scripts\make_icon.py
backend\.venv\Scripts\pyinstaller --noconfirm backend\apptracker.spec
```

The first run may still need Visual C++ redistributables. Keep the *source
checkout* off OneDrive and other synced folders — `node_modules` and `.venv`
thrash sync clients.

## Roadmap / ideas

See [TODO.md](TODO.md) for the planned **research agent** (web scrape to fill job description / title / location when emails are thin).

- Interview calendar (.ics) export.
- Resume-tailoring suggestions per listing.
- Notifications to Slack/Discord/email in addition to desktop.
- Optional authentication for hosted deployments.
- Firefox build of the browser extension.

Done so far: browser extension, analytics, CSV/JSON import & export, drag-and-drop board, desktop notifications, follow-up reminders, merge UI, editable review queue, sync progress, tray health check.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and guidelines. This project is intentionally simple to fork and extend.

## License

MIT. See [LICENSE](LICENSE).
