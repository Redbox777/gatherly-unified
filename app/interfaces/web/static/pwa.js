(() => {
  const connectionStatus = document.getElementById("connection-status");
  const installButton = document.getElementById("pwa-install-button");

  const updateConnectionStatus = () => {
    if (!connectionStatus) return;
    connectionStatus.hidden = navigator.onLine;
    connectionStatus.textContent = navigator.onLine
      ? ""
      : "Нет подключения. Ранее сохранённые данные доступны локально; поддерживаемые заметки, задачи и место встречи можно поставить в очередь при включённом локальном хранении. Новые изменения не синхронизируются автоматически.";
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
        let discardPending = false;
        if (!offlineSetting.checked && offlineStore.getUser()) {
          const queued = await offlineStore.listOutbox();
          const pending = queued.filter(item => item.state !== "done");
          if (pending.length) {
            discardPending = window.confirm(
              "В очереди есть " + pending.length + " несинхронизированных изменений. " +
              "Если выключить локальное хранение, они будут безвозвратно удалены. Продолжить?"
            );
            if (!discardPending) {
              offlineSetting.checked = true;
              if (offlineSettingStatus) offlineSettingStatus.textContent = "Настройка не изменена. Очередь сохранена.";
              return;
            }
          }
        }
        await offlineStore.setEnabled(offlineSetting.checked, { discardPending });
        if (offlineSettingStatus) offlineSettingStatus.textContent = offlineSetting.checked
          ? "Локальное хранение включено. Поддерживаемые изменения можно сохранить без сети и отправить вручную."
          : "Локальное хранение выключено, локальные копии и очередь текущего аккаунта очищены.";
        window.GatherlyOfflineSync?.refresh();
      } catch (_) {
        offlineSetting.checked = offlineStore.isEnabled();
        if (offlineSettingStatus) offlineSettingStatus.textContent = "Не удалось изменить настройку. Очередь не удалена.";
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
