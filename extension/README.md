# Browser Extension - One-Click Job Capture

A small Chrome/Edge (Manifest V3) extension that captures the job listing on the page you're viewing and saves it straight into your Job Application Tracker - no copy/paste.

## How it works

1. On any job listing page, click the extension icon.
2. Click **Capture this page**. The extension reads only the page title and URL (page text never leaves the browser) and pre-fills the role and company for you to correct.
3. Review the parsed fields and click **Save to Tracker**. It's created via `/api/applications`.

## Install (load unpacked)

1. Make sure the tracker backend is running (usually `http://127.0.0.1:8000`).
2. Open `chrome://extensions` (or `edge://extensions`).
3. Enable **Developer mode** (top right).
4. Click **Load unpacked** and select this `extension/` folder.
5. Pin the extension for easy access.

## Configuration

You usually don't need to configure anything. The launcher uses port 8000, or the next free port up to 8100 if 8000 is busy. The extension finds the backend in this order:

1. The backend URL set in **Settings** (right-click the icon -> Options, or the "Settings" link in the popup), if one is set.
2. The last URL that worked.
3. A probe of `127.0.0.1:8000`-`8100`, looking for the `/api/identity` endpoint that reports `"app": "apptracker"`. The extension caches the first match.

Set a URL in Settings only if your backend runs outside that range or on another host.

## Notes

- Saving runs the tracker's duplicate preflight first (BLOCK / WARN / ALLOW) and stores the listing as **Tracked**, not as applied.
- The backend only accepts writes from origins it knows. Add your extension's origin to `backend/.env`, e.g. `EXTENSION_ORIGINS=chrome-extension://<extension id>` (shown on chrome://extensions), and restart.
- The extension only talks to your local AppTracker backend. `host_permissions` covers `localhost` and `127.0.0.1` on any port so that detection works. It sends page text there for parsing and nothing else.
- Firefox support: the manifest is standard MV3; minor tweaks may be needed for `about:debugging` loading.
