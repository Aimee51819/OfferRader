# Setup on an Apple Silicon Mac (M4)

Everything below runs locally. Steps marked **(you)** need your own Google/Microsoft login or a decision only you can make. The tool never performs them for you.

## 1. Dependencies

```bash
xcode-select --install
```

```bash
brew install python@3.12 node ollama
```

```bash
git clone <your fork of application-tracker> && cd application-tracker
make setup        # backend/.venv (requirements-dev.txt) + frontend/node_modules
```

## 2. Local model (Ollama + Qwen3-4B-Instruct)

```bash
ollama serve      # or launch the Ollama app; it listens on 127.0.0.1:11434
```

```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

```bash
ollama list       # confirm the exact tag
```

Set the tag you actually have in `backend/.env` (`OLLAMA_MODEL=...`) or in **Settings → Ollama model tag**. Check it:

```bash
make check-ollama
```

If the tag is missing, the health check lists the Qwen3 tags you do have. Larger quantizations (`q8_0`, `fp16`) also work if you set `OLLAMA_MODEL` accordingly. No fine-tuning or LoRA is used, and there is no cloud fallback.

## 3. Configuration and database

```bash
cp backend/.env.example backend/.env
make migrate      # takes a SQLite backup in DATA_DIR/backups first, then upgrades
```

Data lives in `~/Library/Application Support/AppTracker` unless you set `DATA_DIR`. If you used the original tracker, its tables are preserved as `legacy_*`, and your applications and status history are copied into the new schema. IMAP accounts come over disabled so you can switch to OAuth.

Manual backup at any time:

```bash
cd backend && .venv/bin/python -m app.cli backup
```

Settings → Backup can also download a zip of the database and key.

## 4. OAuth app registration — consent checklist **(you)**

You need your own OAuth client. They are free, and you stay in control of the consent screen.

### Gmail (`gmail.readonly`)

1. Go to https://console.cloud.google.com/ → create a project (for example "AppTracker local").
2. **APIs & Services → Library → Gmail API → Enable.**
3. **OAuth consent screen**: user type *External*, publishing status *Testing*, and add your own Gmail address under **Test users**.
4. **Scopes**: add `.../auth/gmail.readonly` only. Do **not** add modify, send, compose or full `mail.google.com` scopes.
5. **Credentials → Create credentials → OAuth client ID → Desktop app.**
6. Put the values in `backend/.env`: `GOOGLE_CLIENT_ID=...` and `GOOGLE_CLIENT_SECRET=...`. Google calls the desktop secret a secret, but it isn't confidential. It still stays out of git.
7. Loopback redirect used: `http://127.0.0.1:8000/api/oauth/google/callback` (desktop clients accept loopback redirects). If you run on another port, set `OAUTH_REDIRECT_BASE`.
8. In *Testing* mode Google expires refresh tokens after 7 days. You'll then see "authorization expired — reconnect" on the dashboard; click **Connect Gmail** again. Publishing the app removes that limit but requires Google verification for restricted scopes.

### Outlook / Microsoft 365 (`Mail.Read`)

1. Go to https://entra.microsoft.com → **App registrations → New registration**.
2. Supported account types: choose *personal Microsoft accounts* for outlook.com, or include work/school accounts (for example a Rutgers Microsoft 365 account) as needed.
3. **Authentication → Add a platform → Mobile and desktop applications**, with redirect URI `http://localhost:8000/api/oauth/microsoft/callback`. Then set **Allow public client flows = Yes**. No client secret is used (PKCE).
4. **API permissions → Microsoft Graph → Delegated → `Mail.Read`** (plus the default `openid`, `email`, `offline_access`). Do not add `Mail.ReadWrite` or `Mail.Send`.
5. `backend/.env`: `MICROSOFT_CLIENT_ID=<Application (client) ID>`, and `MICROSOFT_TENANT=common` (or `consumers`, or your tenant ID).
6. A university tenant may require admin consent for `Mail.Read`. If sign-in says approval is required, that has to come from the tenant admin; the app cannot work around it.

Do **not** use an Outlook app password with IMAP: Microsoft has disabled basic auth for IMAP on outlook.com and Microsoft 365.

## 5. Run

```bash
make dev                 # API on 127.0.0.1:8000, UI on http://localhost:5173
```

Or as a menu-bar app serving the built UI on http://127.0.0.1:8000:

```bash
make app
```

## 6. First sync (dry run first)

1. **Settings → Connect Gmail / Connect Outlook** **(you)**. Your browser shows the provider's consent screen; approve the read-only access.
2. Choose scope per account: Gmail *Inbox only* or all mail (default, which includes the Promotions/Updates tabs where ATS mail often lands), plus optionally *Include spam*. Outlook can add the *Junk* folder. The look-back defaults to 60 days.
3. Click **Fetch only**. This reads recent mail into the local store without parsing.
4. Click **Dry run on fetched mail**. It shows what the agent *would* do and saves nothing.
5. Click **Sync now**, then work through the **Review queue**.
6. Once you're satisfied, turn on **Automatic background sync**. Leave the policy on *Always ask me first*, or switch to *Apply routine updates automatically*. Even then, terminal and ambiguous items still go to review.

After the Mac sleeps, the scheduler runs once on wake and resumes from the last saved cursor. The dashboard warns if no sync has succeeded for two hours (configurable).

## 7. Reminders

The defaults are 72 h, 24 h and 3 h before a deadline (**Settings → Reminders**). Reminders appear on the dashboard, and optionally as macOS notifications via `osascript` or browser notifications. Each threshold fires once. No email is ever sent. **Import / export → Deadlines (.ics)** gives you a calendar file to import yourself.

## 8. Integration with an application tool (optional)

```bash
cd backend && .venv/bin/python -m app.cli integration-token
```

Call `POST /api/integrations/preflight` before asking for approval to apply, and `POST /api/integrations/submissions` only after a confirmed submission. See [ARCHITECTURE.md](ARCHITECTURE.md#integration-endpoint).

## Docker (optional)

```bash
docker compose up --build      # UI/API on http://127.0.0.1:8080 only
```

Ollama keeps running natively on the Mac for Metal acceleration; the container reaches it via `host.docker.internal`. Register the OAuth redirect URIs on port 8080 if you use Docker.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "Model not ready" | Run `ollama serve`, then `ollama pull <tag>`, and make `OLLAMA_MODEL` match `ollama list` |
| "Authorization expired — reconnect" | Click Connect again (Google test-mode tokens expire after 7 days) |
| `412` on Connect | `GOOGLE_CLIENT_ID` / `MICROSOFT_CLIENT_ID` missing in `backend/.env`; restart the backend |
| `redirect_uri_mismatch` | The registered redirect must match the port the backend runs on (`OAUTH_REDIRECT_BASE`) |
| Browser extension can't save | Add `EXTENSION_ORIGINS=chrome-extension://<id>` to `backend/.env` |
