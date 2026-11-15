(() => {
  if (!("serviceWorker" in navigator)) return;
  const secureContext = window.isSecureContext;
  if (!secureContext) return;

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js", { scope: "/" })
      .catch(() => {
        // PWA enhancement is optional; the web app remains usable without it.
      });
  });
})();
