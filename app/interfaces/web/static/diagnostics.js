(() => {
  const openButton = document.getElementById("bug-report-open");
  const dialog = document.getElementById("bug-report-dialog");
  const form = document.getElementById("bug-report-form");
  const preview = document.getElementById("bug-report-preview");
  const consent = document.getElementById("bug-report-consent");
  const cancel = document.getElementById("bug-report-cancel");
  const submit = document.getElementById("bug-report-submit");
  const status = document.getElementById("bug-report-status");
  if (!openButton || !dialog || !form || !preview || !consent || !cancel || !submit || !status) return;

  let latestError = null;
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
  const safeFrames = (stack) => String(stack || "").split("\n").slice(0, 12).map((line) => {
    const match = line.match(/:(\d+):(\d+)\)?$/);
    return match ? "at line " + match[1] + ", column " + match[2] : "";
  }).filter(Boolean).slice(0, 8).join("\n");
  const capture = (code, error) => {
    latestError = {
      app_version: "0.1.0",
      browser: browserName(),
      operating_system: operatingSystem(),
      module: "frontend",
      error_code: code,
      stack: safeFrames(error && error.stack)
    };
  };

  window.addEventListener("error", (event) => capture("window_error", event.error));
  window.addEventListener("unhandledrejection", (event) => capture("unhandled_rejection", event.reason));

  const currentReport = () => latestError || {
    app_version: "0.1.0",
    browser: browserName(),
    operating_system: operatingSystem(),
    module: "manual",
    error_code: "user_reported_issue",
    stack: ""
  };
  openButton.addEventListener("click", () => {
    const report = currentReport();
    preview.textContent = [
      "Версия: " + report.app_version,
      "Браузер: " + report.browser,
      "ОС: " + report.operating_system,
      "Модуль: " + report.module,
      "Код: " + report.error_code,
      "Стек (только номера строк и столбцов):",
      report.stack || "Не записан"
    ].join("\n");
    consent.checked = false;
    status.textContent = "";
    submit.disabled = false;
    submit.hidden = false;
    dialog.showModal();
  });
  cancel.addEventListener("click", () => dialog.close());
  form.addEventListener("submit", async (event) => {
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
      submit.hidden = true;
    } catch (_) {
      status.textContent = "Не удалось отправить отчёт. Попробуйте позже.";
      submit.disabled = false;
    }
  });
})();