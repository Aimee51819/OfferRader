// Finds the AppTracker backend. The launcher uses port 8000 unless it is busy,
// in which case it takes the next free port up to 8100, so a fixed URL is not
// enough. Order: the URL set in Settings, the last URL that worked, then a
// probe of 127.0.0.1:8000-8100.
const APP_ID = "apptracker";
const PROBE_START = 8000;
const PROBE_END = 8100;
const PROBE_TIMEOUT_MS = 2500;

function normalizeUrl(url) {
  return (url || "").trim().replace(/\/+$/, "");
}

function storageGet(area, defaults) {
  return new Promise((resolve) => area.get(defaults, resolve));
}

// True only when the server answers as AppTracker, so another app that
// happens to hold one of these ports is never mistaken for it.
async function isAppTracker(base) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch(`${base}/api/identity`, { signal: controller.signal });
    if (!res.ok) return false;
    const body = await res.json();
    return body && body.app === APP_ID;
  } catch (e) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function probeLocalPorts() {
  // The backend binds 127.0.0.1 only; probing "localhost" can try ::1 first.
  const bases = [];
  for (let port = PROBE_START; port <= PROBE_END; port++) {
    bases.push(`http://127.0.0.1:${port}`);
  }
  const hits = await Promise.all(bases.map(isAppTracker));
  const index = hits.indexOf(true);
  return index === -1 ? null : bases[index];
}

async function resolveBackend() {
  const { backendUrl } = await storageGet(chrome.storage.sync, { backendUrl: "" });
  const { detectedBackendUrl } = await storageGet(chrome.storage.local, {
    detectedBackendUrl: "",
  });
  const candidates = [...new Set([normalizeUrl(backendUrl), normalizeUrl(detectedBackendUrl)])];
  for (const candidate of candidates) {
    if (candidate && (await isAppTracker(candidate))) return candidate;
  }

  const found = await probeLocalPorts();
  if (!found) {
    throw new Error(
      `Can't find AppTracker on ports ${PROBE_START}-${PROBE_END}. Is it running? ` +
        "You can set its address in Settings."
    );
  }
  await new Promise((resolve) => chrome.storage.local.set({ detectedBackendUrl: found }, resolve));
  return found;
}
