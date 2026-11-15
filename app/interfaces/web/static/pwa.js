(() => {
  const connectionStatus = document.getElementById("connection-status");
  const installButton = document.getElementById("pwa-install-button");

  const updateConnectionStatus = () => {
    if (!connectionStatus) return;
    connectionStatus.hidden = navigator.onLine;
    connectionStatus.textContent = navigator.onLine
      ? ""
      : "Нет подключения к интернету. Сейчас доступна только оболочка приложения; данные встреч и изменения не синхронизируются.";
  };

  updateConnectionStatus();
  window.addEventListener("online", updateConnectionStatus);
  window.addEventListener("offline", updateConnectionStatus);

  let deferredInstallPrompt = null;
  if (installButton) {
    window.addEventListener("beforeinstallprompt", event => {
      event.preventDefault();
      deferredInstallPrompt = event;
      installButton.hidden = false;
    });

    installButton.addEventListener("click", async () => {
      if (!deferredInstallPrompt) return;
      const prompt = deferredInstallPrompt;
      deferredInstallPrompt = null;
      installButton.disabled = true;
      try {
        await prompt.prompt();
        await prompt.userChoice;
      } catch (_) {
        // Installation is optional; the browser menu remains available.
      } finally {
        installButton.hidden = true;
        installButton.disabled = false;
      }
    });

    window.addEventListener("appinstalled", () => {
      deferredInstallPrompt = null;
      installButton.hidden = true;
    });
  }

  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js", { scope: "/" })
      .catch(() => {
        // PWA enhancement is optional; the web app remains usable without it.
      });
  });
})();
