function setStatus(msg, kind) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
  el.classList.toggle("hidden", !msg);
}

// Runs in the page context. Only the title and URL are used; page text stays in the browser.
function extractPageContent() {
  return { title: document.title || "", url: window.location.href };
}

let parsed = null;

async function capture() {
  setStatus("Reading page...", null);
  const captureBtn = document.getElementById("capture");
  captureBtn.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractPageContent,
    });
    parsed = { url: result.url };
    // Common title shapes: "Role - Company | Board" / "Role at Company".
    const parts = (result.title || "").split(/\s[|–—-]\s|\sat\s/);
    document.getElementById("title").value = (parts[0] || "").trim();
    document.getElementById("company").value = (parts[1] || "").trim();
    document.getElementById("location").value = "";
    document.getElementById("preview").classList.remove("hidden");
    setStatus("Check the company and exact role title, then save.", "success");
  } catch (e) {
    setStatus(e.message || "Something went wrong.", "error");
  } finally {
    captureBtn.disabled = false;
  }
}

async function save() {
  const saveBtn = document.getElementById("save");
  saveBtn.disabled = true;
  try {
    const backend = await resolveBackend();
    const company = document.getElementById("company").value.trim();
    const role = document.getElementById("title").value.trim();
    if (!company || !role) throw new Error("Company and role are required.");
    const url = (parsed && parsed.url) || null;
    const check = await fetch(`${backend}/api/duplicates/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company, role_title: role, posting_url: url, recruiting_cycle: "Summer 2027" }),
    }).then((r) => r.json());
    if (check.decision === "BLOCK") {
      throw new Error("Already tracked or applied: " + (check.reasons[0] || "same opening"));
    }
    if (check.decision === "WARN" && !confirm("Heads up:\n" + check.reasons.join("\n") + "\n\nSave anyway?")) {
      setStatus("Not saved.", null);
      return;
    }
    // Saved as "Tracked" (not applied). Mark it submitted in the app after you really apply.
    const res = await fetch(`${backend}/api/applications`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company,
        role_title: role,
        location: document.getElementById("location").value || null,
        posting_url: url,
        recruiting_cycle: "Summer 2027",
      }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      const msg = (detail.detail && detail.detail.message) || detail.detail || `Save failed (${res.status})`;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    setStatus("Saved as Tracked.", "success");
    document.getElementById("preview").classList.add("hidden");
  } catch (e) {
    setStatus(e.message || "Could not save.", "error");
  } finally {
    saveBtn.disabled = false;
  }
}

document.getElementById("capture").addEventListener("click", capture);
document.getElementById("save").addEventListener("click", save);
document.getElementById("openOptions").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});
resolveBackend()
  .then((backend) => {
    document.getElementById("openApp").href = backend.replace(":8000", ":5173");
  })
  .catch((e) => setStatus(e.message, "error"));
