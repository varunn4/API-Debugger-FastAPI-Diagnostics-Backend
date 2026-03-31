// script.js
// Keeps it simple: one submit handler, one render function.
// No framework, no build step, works offline after page load.

const form = document.getElementById("debugForm");
const resultBox = document.getElementById("result");
const submitBtn = document.getElementById("submitBtn");

// History/export UI
const historyBtn = document.getElementById("toggleHistoryBtn");
const exportHistoryBtn = document.getElementById("exportHistoryBtn");
const historyContainer = document.getElementById("historyContainer");
const historyBox = document.getElementById("historyBox");
let lastHistory = [];
let historyVisible = false;

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  await runDebug();
});

async function runDebug() {
  const payload = buildPayload();
  if (!payload) return;

  setLoading(true);
  resultBox.innerHTML = "";

  try {
    const res = await fetch("/debug", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await res.json();

    if (!res.ok) {
      showError(data.detail || "Validation error");
      return;
    }

    renderResult(data, payload);
  } catch (err) {
    showError("Could not reach the server. Is it running?");
  } finally {
    setLoading(false);
  }
}

function buildPayload() {
  const endpoint = document.getElementById("endpoint").value.trim();
  const method = document.getElementById("method").value;
  const statusCode = parseInt(document.getElementById("status_code").value, 10);
  const errorMessage = document.getElementById("error_message").value.trim();
  const logsText = document.getElementById("logs").value.trim();

  // parse headers JSON — show inline error if malformed
  let headers = {};
  const headersRaw = document.getElementById("headers").value.trim();
  if (headersRaw) {
    try {
      headers = JSON.parse(headersRaw);
    } catch {
      showError("Headers must be valid JSON. Example: {\"Authorization\": \"Bearer token\"}");
      return null;
    }
  }

  // same for payload
  let payloadObj = null;
  const payloadRaw = document.getElementById("payload").value.trim();
  if (payloadRaw) {
    try {
      payloadObj = JSON.parse(payloadRaw);
    } catch {
      showError("Payload must be valid JSON. Example: {\"name\": \"John\"}");
      return null;
    }
  }

  return {
    endpoint,
    method,
    headers: Object.keys(headers).length > 0 ? headers : null,
    payload: payloadObj,
    status_code: statusCode,
    error_message: errorMessage || null,
    logs: logsText || null,
  };
}

function renderResult(data, originalPayload) {
  const confidencePct = Math.round(data.confidence_score * 100);
  const confidenceClass = confidencePct >= 80 ? "high" : confidencePct >= 60 ? "medium" : "low";

  const logFlagsHtml = data.log_flags.length
    ? data.log_flags.map((f) => `<span class="flag">${f}</span>`).join(" ")
    : '<span class="muted">none detected</span>';

  const correctedJson = JSON.stringify(data.corrected_request, null, 2);

  resultBox.innerHTML = `
    <div class="result-card">
      <div class="result-header">
        <span class="issue-badge ${data.issue_type}">${formatIssueType(data.issue_type)}</span>
        <span class="confidence ${confidenceClass}">
          <span class="conf-label">Confidence</span>
          <span class="conf-value">${confidencePct}%</span>
        </span>
      </div>

      <section>
        <h3>Root Cause</h3>
        <p class="cause-text">${escapeHtml(data.root_cause)}</p>
      </section>

      <section>
        <h3>Suggested Fix</h3>
        <p class="fix-text">${formatFix(data.suggested_fix)}</p>
      </section>

      <section>
        <h3>AI Explanation</h3>
        <p class="cause-text">${escapeHtml(data.ai_explanation || "N/A")}</p>
      </section>

      <section>
        <h3>Additional Suggestions</h3>
        ${data.additional_suggestions?.length
          ? "<ul>" + data.additional_suggestions.map((s) => `<li>${escapeHtml(s)}</li>`).join("") + "</ul>"
          : '<p class="muted">N/A</p>'}
      </section>

      <section>
        <h3>AI Confidence Note</h3>
        <p class="cause-text">${escapeHtml(data.ai_confidence_note || "N/A")}</p>
      </section>

      <section>
        <h3>Log Signals</h3>
        <div class="flags">${logFlagsHtml}</div>
      </section>

      <section>
        <h3>Corrected Request <button class="copy-btn" onclick="copyCode()">copy</button></h3>
        <pre id="correctedCode">${escapeHtml(correctedJson)}</pre>
      </section>
    </div>
  `;

  resultBox.scrollIntoView({ behavior: "smooth" });
}

function formatFix(fix) {
  // turn numbered steps into a list if it looks like "1. ... 2. ..."
  if (/^\d\./.test(fix) || / \d\./.test(fix)) {
    const parts = fix.split(/(?=\d\.)/g).map((p) => p.trim()).filter(Boolean);
    if (parts.length > 1) {
      return "<ol>" + parts.map((p) => `<li>${escapeHtml(p.replace(/^\d\.\s*/, ""))}</li>`).join("") + "</ol>";
    }
  }
  return escapeHtml(fix);
}

function formatIssueType(type) {
  const labels = {
    auth_failure: "Auth Failure",
    permission_denied: "Permission Denied",
    not_found: "Not Found",
    bad_request: "Bad Request",
    server_error: "Server Error",
    timeout: "Timeout",
    rate_limited: "Rate Limited",
    unknown: "Unknown",
  };
  return labels[type] || type;
}

function copyCode() {
  const code = document.getElementById("correctedCode").innerText;
  navigator.clipboard.writeText(code).then(() => {
    const btn = document.querySelector(".copy-btn");
    btn.textContent = "copied!";
    setTimeout(() => (btn.textContent = "copy"), 2000);
  });
}

function showError(message) {
  resultBox.innerHTML = `<div class="error-box"><strong>Error:</strong> ${escapeHtml(message)}</div>`;
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  submitBtn.textContent = isLoading ? "Analyzing..." : "Debug Request";
}

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// prefill examples so new users aren't staring at a blank form
const EXAMPLES = [
  {
    label: "401 Expired Token",
    endpoint: "/api/v1/users",
    method: "GET",
    headers: { Authorization: "Bearer eyJhbGciOiJIUzI1NiJ9.expired" },
    payload: "",
    status_code: 401,
    error_message: "Unauthorized",
    logs: "JWT expired at 2024-01-15 10:45:00",
  },
  {
    label: "400 Missing Field",
    endpoint: "/api/v1/users",
    method: "POST",
    headers: { Authorization: "Bearer valid_token", "Content-Type": "application/json" },
    payload: { name: "John" },
    status_code: 400,
    error_message: "Missing required field: email",
    logs: "",
  },
  {
    label: "404 Wrong Path",
    endpoint: "/users/profile",
    method: "GET",
    headers: { Authorization: "Bearer valid_token" },
    payload: "",
    status_code: 404,
    error_message: "Not Found",
    logs: "No route matching /users/profile",
  },
  {
    label: "504 Timeout",
    endpoint: "/api/v1/reports/generate",
    method: "POST",
    headers: { Authorization: "Bearer valid_token" },
    payload: { from: "2024-01-01", to: "2024-12-31" },
    status_code: 504,
    error_message: "Gateway Timeout",
    logs: "upstream timed out (110: Connection timed out)",
  },
];

function loadExample(idx) {
  const ex = EXAMPLES[idx];
  document.getElementById("endpoint").value = ex.endpoint;
  document.getElementById("method").value = ex.method;
  document.getElementById("headers").value = JSON.stringify(ex.headers, null, 2);
  document.getElementById("payload").value = ex.payload ? JSON.stringify(ex.payload, null, 2) : "";
  document.getElementById("status_code").value = ex.status_code;
  document.getElementById("error_message").value = ex.error_message;
  document.getElementById("logs").value = ex.logs;
}

async function loadHistory() {
  if (!historyBox) return;

  try {
    const res = await fetch("/history");
    const data = await res.json();
    lastHistory = Array.isArray(data) ? data : [];
    exportHistoryBtn.disabled = lastHistory.length === 0;

    if (!lastHistory.length) {
      historyBox.innerHTML = '<p class="muted">No history available</p>';
      return;
    }

    historyBox.innerHTML = lastHistory
      .map((h) => {
        const meta = [
          `${h.method || ""} ${h.endpoint || ""}`.trim(),
          `status ${h.status_code ?? "N/A"}`,
          `${h.issue_type || "N/A"}`,
          `${h.created_at || ""}`.trim(),
        ].filter(Boolean);

        return `
          <div class="history-card">
            <div class="history-meta">
              ${meta.map((m) => `<span>${escapeHtml(m)}</span>`).join("")}
            </div>
            <pre class="history-json">${escapeHtml(JSON.stringify(h, null, 2))}</pre>
          </div>
        `;
      })
      .join("");
  } catch (err) {
    historyBox.innerHTML = '<p class="muted">Failed to load history</p>';
    exportHistoryBtn.disabled = true;
  }
}

function exportHistory() {
  if (!lastHistory || lastHistory.length === 0) return;
  const blob = new Blob([JSON.stringify(lastHistory, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);

  const a = document.createElement("a");
  a.href = url;
  a.download = "api-debugger-history.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// build example buttons dynamically
window.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("exampleButtons");
  EXAMPLES.forEach((ex, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "example-btn";
    btn.textContent = ex.label;
    btn.onclick = () => loadExample(i);
    container.appendChild(btn);
  });

  if (historyBtn && historyContainer) {
    historyBtn.addEventListener("click", async () => {
      if (!historyVisible) {
        await loadHistory();
        historyContainer.style.display = "block";
        historyBtn.innerText = "Hide History";
      } else {
        historyContainer.style.display = "none";
        historyBtn.innerText = "Show History";
      }
      historyVisible = !historyVisible;
    });
  }
  if (exportHistoryBtn) {
    exportHistoryBtn.addEventListener("click", exportHistory);
  }
});
