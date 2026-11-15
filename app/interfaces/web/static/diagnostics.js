(() => {
  const MAX_EVENTS = 40;
  const events = [];
  const browserName = () => {
    const ua = navigator.userAgent || "";
    if (/Edg\//.test(ua)) return "Edge";
    if (/Firefox\//.test(ua)) return "Firefox";
    if (/Chrome\//.test(ua)) return "Chrome";
    if (/Safari\//.test(ua)) return "Safari";
    return "Other";
  };
  const operatingSystem = () => {
    const ua = navigator.userAgent || "";
    if (/Android/.test(ua)) return "Android";
    if (/iPhone|iPad|iPod/.test(ua)) return "iOS";
    if (/Windows/.test(ua)) return "Windows";
    if (/Mac OS|Macintosh/.test(ua)) return "macOS";
    if (/Linux/.test(ua)) return "Linux";
    return "Other";
  };
  const safeFrames = stack => String(stack || "").split("\n").slice(0, 12).map(line => {
    const match = line.match(/:(\d+):(\d+)\)?$/);
    return match ? "at line " + match[1] + ", column " + match[2] : "";
  }).filter(Boolean).slice(0, 8).join("\n");
  const routeCategory = path => {
    const value = String(path || "");
    if (/^\/auth(?:\/|$)/.test(value)) return "auth";
    if (value === "/account" || value.startsWith("/account/")) return "account";
    if (value.startsWith("/offline-sync/")) return "offline_sync";
    if (/^\/events\/[^/]+/.test(value)) return "event_detail";
    if (value === "/events" || value.startsWith("/events/")) return "events";
    return "other";
  };
  const record = candidate => {
    try {
      if (!candidate || typeof candidate !== "object" || events.length >= MAX_EVENTS) {
        if (events.length >= MAX_EVENTS) events.shift();
      }
      const allowedSubsystems = ["ui", "api", "network", "offline_sync"];
      const allowedCodes = [
        "window_error", "unhandled_rejection", "api_network_error",
        "api_http_error", "network_offline", "network_online",
        "offline_sync_state"
      ];
      if (!candidate || !allowedSubsystems.includes(candidate.subsystem) ||
          !allowedCodes.includes(candidate.event_code)) return;
      const item = {
        occurred_at: new Date().toISOString(),
        subsystem: candidate.subsystem,
        event_code: candidate.event_code
      };
      if (candidate.route_category && ["events", "event_detail", "auth", "account", "offline_sync", "other"].includes(candidate.route_category)) {
        item.route_category = candidate.route_category;
      }
      if (candidate.method && ["GET", "POST", "PUT", "PATCH", "DELETE"].includes(candidate.method)) {
        item.method = candidate.method;
      }
      if (Number.isInteger(candidate.status_code) && candidate.status_code >= 100 && candidate.status_code <= 599) {
        item.status_code = candidate.status_code;
      }
      if (Number.isFinite(candidate.duration_ms)) {
        item.duration_ms = Math.max(0, Math.min(60000, Math.round(candidate.duration_ms)));
      }
      if (typeof candidate.request_id === "string" && /^[A-Fa-f0-9-]{8,64}$/.test(candidate.request_id)) {
        item.request_id = candidate.request_id;
      }
      if (candidate.state && ["pending", "sending", "done", "conflict", "blocked"].includes(candidate.state)) {
        item.state = candidate.state;
      }
      events.push(item);
      if (events.length > MAX_EVENTS) events.shift();
    } catch (_) {
      // Diagnostics must never interrupt product behavior.
    }
  };

  let latestError = null;
  const captureError = (code, error) => {
    try {
      latestError = {
        app_version: "0.1.0",
        browser: browserName(),
        operating_system: operatingSystem(),
        module: "frontend",
        error_code: code,
        stack: safeFrames(error && error.stack)
      };
      record({ subsystem: "ui", event_code: code });
    } catch (_) {}
  };

  window.addEventListener("error", event => captureError("window_error", event.error));
  window.addEventListener("unhandledrejection", event => captureError("unhandled_rejection", event.reason));
  window.addEventListener("offline", () => record({ subsystem: "network", event_code: "network_offline" }));
  window.addEventListener("online", () => record({ subsystem: "network", event_code: "network_online" }));

  window.GatherlyDiagnostics = {
    recordApiEvent(details) {
      try {
        const code = details && details.network_error ? "api_network_error" : "api_http_error";
        record({
          subsystem: "api",
          event_code: code,
          route_category: routeCategory(details && details.path),
          method: details && details.method,
          status_code: details && details.status_code,
          duration_ms: details && details.duration_ms,
          request_id: details && details.request_id
        });
      } catch (_) {}
    },
    recordSyncState(state) {
      record({ subsystem: "offline_sync", event_code: "offline_sync_state", state });
    },
    getEvents() {
      return events.map(event => ({ ...event }));
    }
  };

  const openButton = document.getElementById("bug-report-open");
  const dialog = document.getElementById("bug-report-dialog");
  const form = document.getElementById("bug-report-form");
  const preview = document.getElementById("bug-report-preview");
  const consent = document.getElementById("bug-report-consent");
  const cancel = document.getElementById("bug-report-cancel");
  const submit = document.getElementById("bug-report-submit");
  const status = document.getElementById("bug-report-status");
  if (!openButton || !dialog || !form || !preview || !consent || !cancel || !submit || !status) return;

  const currentReport = () => ({
    ...(latestError || {
      app_version: "0.1.0",
      browser: browserName(),
      operating_system: operatingSystem(),
      module: "manual",
      error_code: "user_reported_issue",
      stack: ""
    }),
    diagnostics: window.GatherlyDiagnostics.getEvents()
  });
  openButton.addEventListener("click", () => {
    const report = currentReport();
    preview.textContent = [
      "Версия: " + report.app_version,
      "Браузер: " + report.browser,
      "ОС: " + report.operating_system,
      "Модуль: " + report.module,
      "Код: " + report.error_code,
      "Стек (только номера строк и столбцов):",
      report.stack || "Не записан",
      "Технические события (" + report.diagnostics.length + "):",
      JSON.stringify(report.diagnostics, null, 2)
    ].join("\n");
    consent.checked = false;
    status.textContent = "";
    submit.disabled = false;
    submit.hidden = false;
    dialog.showModal();
  });
  cancel.addEventListener("click", () => dialog.close());
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!consent.checked) return;
    submit.disabled = true;
    status.textContent = "Отправка…";
    try {
      const response = await fetch("/diagnostics/bug-reports", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...currentReport(), consent: true })
      });
      if (!response.ok) throw new Error("report_rejected");
      status.textContent = "Спасибо. Технический отчёт отправлен.";
      latestError = null;
      events.length = 0;
      submit.hidden = true;
    } catch (_) {
      status.textContent = "Не удалось отправить отчёт. Попробуйте позже.";
      submit.disabled = false;
    }
  });
})();