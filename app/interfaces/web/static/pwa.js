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

  const offlineSetting = document.getElementById("offline-storage-enabled");
  const offlineSettingStatus = document.getElementById("offline-storage-status");
  const offlineStore = window.GatherlyOfflineStore;
  if (offlineSetting && offlineStore) {
    offlineSetting.checked = offlineStore.isEnabled();
    offlineSetting.addEventListener("change", async () => {
      offlineSetting.disabled = true;
      if (offlineSettingStatus) offlineSettingStatus.textContent = "Сохраняем настройку…";
      try {
        await offlineStore.setEnabled(offlineSetting.checked);
        if (offlineSettingStatus) offlineSettingStatus.textContent = offlineSetting.checked
          ? "Локальное хранение включено. Данные начнут сохраняться после следующего обновления встреч."
          : "Локальное хранение выключено, локальные копии текущего аккаунта очищены.";
      } catch (_) {
        offlineSetting.checked = false;
        if (offlineSettingStatus) offlineSettingStatus.textContent = "Не удалось изменить настройку. Попробуйте ещё раз.";
      } finally { offlineSetting.disabled = false; }
    });
  }

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
