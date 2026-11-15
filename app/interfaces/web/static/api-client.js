(() => {
  const offlineStore = window.GatherlyOfflineStore;
  const cacheKeyFor = (url, method) => {
    if (method !== "GET") return null;
    let parsed;
    try { parsed = new URL(url, window.location.origin); } catch (_) { return null; }
    if (parsed.origin !== window.location.origin || parsed.search || parsed.hash) return null;
    if (!/^\/events(?:\/[A-Za-z0-9_-]+(?:\/(?:participants|notes|tasks|venue))?)?$/.test(parsed.pathname)) return null;
    return "GET:" + parsed.pathname;
  };
  const api = async (url, options = {}) => {
    const method = String(options.method || "GET").toUpperCase();
    const cacheKey = cacheKeyFor(url, method);
    let response;
    try {
      response = await fetch(url, {
        credentials: "same-origin",
        ...options,
        headers: {"Content-Type":"application/json", ...(options.headers || {})}
      });
    } catch (networkError) {
      if (cacheKey && offlineStore?.isEnabled() && offlineStore.getUser()) {
        try {
          const cached = await offlineStore.get(cacheKey);
          if (cached !== null) return cached;
        } catch (_) {}
      }
      throw networkError;
    }
    if (response.status === 401) {
      window.location.replace("/auth.html?next=%2F");
      throw new Error("Требуется войти в аккаунт.");
    }
    if (!response.ok) {
      let message = "Не удалось выполнить запрос. Попробуйте ещё раз.";
      try {
        const body = await response.json();
        if (response.status === 422) message = "Проверьте название и дату встречи.";
        else if (response.status === 404) message = "Эта встреча уже недоступна. Обновите список.";
        else if (typeof body.detail === "string" && response.status >= 500) message = "Сервер временно недоступен.";
      } catch (_) {}
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    const data = await response.json();
    if (cacheKey && offlineStore?.isEnabled() && offlineStore.getUser()) {
      offlineStore.put(cacheKey, data).catch(() => {});
    }
    return data;
  };
  window.GatherlyApiClient = { request: api };
})();