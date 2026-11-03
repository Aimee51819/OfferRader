function setStatus(msg, kind) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
  el.classList.toggle("hidden", !msg);
}

// Runs in the page context to extract the listing text.
function extractPageContent() {
  const selection = window.getSelection ? String(window.getSelection()) : "";
  const body = document.body ? document.body.innerText : "";
  const text = (selection && selection.trim().length > 40 ? selection : body) || "";
  return {
    title: document.title || "",
    url: window.location.href,
    text: text.slice(0, 12000),
  };
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

    const backend = await resolveBackend();
    setStatus("Parsing with AI...", null);
    const res = await fetch(`${backend}/api/parse`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: `${result.title}\n${result.url}\n\n${result.text}`,
      }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Parse failed (${res.status})`);
    }
    parsed = await res.json();
    parsed.url = parsed.url || result.url;

    document.getElementById("company").value = parsed.company || "";
    document.getElementById("title").value = parsed.title || "";
    document.getElementById("location").value = parsed.location || "";
    document.getElementById("salary").value = parsed.salary || "";
    document.getElementById("preview").classList.remove("hidden");
    setStatus("Review and save.", "success");
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
    const payload = {
      company: document.getElementById("company").value || "Unknown",
      title: document.getElementById("title").value || "Unknown",
      location: document.getElementById("location").value || null,
      salary: document.getElementById("salary").value || null,
      url: (parsed && parsed.url) || null,
      source: (parsed && parsed.source) || "Browser extension",
      description: (parsed && parsed.description) || null,
      skills: parsed && parsed.skills ? parsed.skills.join(", ") : null,
      status: "saved",
    };
    const res = await fetch(`${backend}/api/applications`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Save failed (${res.status})`);
    }
    setStatus("Saved to your tracker!", "success");
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
