chrome.storage.sync.get({ backendUrl: "" }, (items) => {
  document.getElementById("backendUrl").value = items.backendUrl;
});

function showStatus(msg, kind) {
  const status = document.getElementById("status");
  status.textContent = msg;
  status.className = "status " + kind;
}

document.getElementById("save").addEventListener("click", () => {
  // Blank means auto-detect; drop the cached URL so detection starts fresh.
  const backendUrl = normalizeUrl(document.getElementById("backendUrl").value);
  chrome.storage.sync.set({ backendUrl }, () => {
    chrome.storage.local.remove("detectedBackendUrl", async () => {
      showStatus("Saved. Looking for AppTracker...", "");
      try {
        const found = await resolveBackend();
        showStatus(`Saved. Connected to ${found}.`, "success");
      } catch (e) {
        showStatus(`Saved. ${e.message}`, "error");
      }
    });
  });
});
