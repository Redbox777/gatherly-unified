(() => {
  const offlineStore = window.GatherlyOfflineStore;
  const cacheKeyFor = (url, method) => {
    if (method !== "GET") return null;
    let parsed;
    try { parsed = new URL(url, window.location.origin); } catch (_) { return null; }
    if (parsed.origin !== window.location.origin || parsed.search || parsed.hash) return null;
    if (/^\/events\/[0-9a-f-]+\/sync-state$/i.test(parsed.pathname)) {
      return "GET:" + parsed.pathname;
    }
    if (!/^\/events(?:\/[A-Za-z0-9_-]+(?:\/(?:participants|notes|tasks|venue))?)?$/.test(parsed.pathname)) return null;
    return "GET:" + parsed.pathname;
  };
  const eventIdFromPath = pathname => {
    const match = pathname.match(/^\/events\/([0-9a-f-]+)(?:\/|$)/i);
    return match ? match[1] : null;
  };
  const decodeBody = options => {
    if (!options.body) return {};
    try {
      const value = JSON.parse(options.body);
      return value && typeof value === "object" && !Array.isArray(value) ? value : null;
    } catch (_) { return null; }
  };
  const commandFromRequest = (url, options) => {
    let parsed;
    try { parsed = new URL(url, window.location.origin); } catch (_) { return null; }
    if (parsed.origin !== window.location.origin) return null;
    const parts = parsed.pathname.match(/^\/events\/([0-9a-f-]+)\/(notes|tasks|venue)(?:\/([0-9a-f-]+)(?:\/completion)?)?$/i);
    if (!parts) return null;
    const [, eventId, resource, resourceId] = parts;
    const method = String(options.method || "GET").toUpperCase();
    const body = decodeBody(options);
    if (body === null) return null;
    if (resource === "notes") {
      if (method === "POST" && !resourceId) return { eventId, kind: "note.create", payload: body };
      if (method === "PUT" && resourceId) return { eventId, kind: "note.update", payload: { ...body, note_id: resourceId } };
      if (method === "DELETE" && resourceId) return { eventId, kind: "note.delete", payload: { note_id: resourceId } };
    }
    if (resource === "tasks") {
      if (method === "POST" && !resourceId) return { eventId, kind: "task.create", payload: body };
      if (method === "PUT" && resourceId && !parsed.pathname.endsWith("/completion")) {
        return { eventId, kind: "task.update", payload: { ...body, task_id: resourceId } };
      }
      if (method === "PATCH" && resourceId && parsed.pathname.endsWith("/completion")) {
        return { eventId, kind: "task.complete", payload: { task_id: resourceId, ...body } };
      }
      if (method === "DELETE" && resourceId) return { eventId, kind: "task.delete", payload: { task_id: resourceId } };
    }
    if (resource === "venue") {
      if (method === "PUT" && !resourceId) return { eventId, kind: "venue.set", payload: body };
      if (method === "DELETE" && !resourceId) return { eventId, kind: "venue.delete", payload: {} };
    }
    return null;
  };
  const refreshRevision = async eventId => {
    if (!eventId || !offlineStore?.isEnabled() || !offlineStore.getUser()) return;
    try {
      const response = await fetch("/events/" + encodeURIComponent(eventId) + "/sync-state", {
        credentials: "same-origin",
        headers: { "Accept": "application/json" }
      });
      if (!response.ok) return;
      const state = await response.json();
      if (Number.isSafeInteger(state.revision)) await offlineStore.setEventRevision(eventId, state.revision);
    } catch (_) {}
  };
  const enqueueOffline = async command => {
    if (!command || !offlineStore?.isEnabled() || !offlineStore.getUser()) return null;
    const currentRevision = await offlineStore.getEventRevision(command.eventId);
    if (!Number.isSafeInteger(currentRevision)) {
      throw new Error("Нельзя безопасно поставить изменение в очередь: сначала откройте эту встречу при подключённом интернете.");
    }
    const existing = await offlineStore.listOutbox();
    const eventItems = existing.filter(item => item.eventId === command.eventId);
    if (eventItems.some(item => item.state === "conflict" || item.state === "blocked")) {
      throw new Error("Для этой встречи есть конфликт или заблокированное изменение. Сначала разберите очередь синхронизации.");
    }
    const latestRevision = eventItems.reduce(
      (revision, item) => Math.max(revision, item.baseRevision + 1),
      currentRevision
    );
    const record = await offlineStore.enqueue({
      eventId: command.eventId,
      baseRevision: latestRevision,
      kind: command.kind,
      payload: command.payload
    });
    window.GatherlyOfflineSync?.refresh();
    return { offline_queued: true, operation_id: record.operationId };
  };
  const api = async (url, options = {}) => {
    const method = String(options.method || "GET").toUpperCase();
    const cacheKey = cacheKeyFor(url, method);
    let parsed;
    try { parsed = new URL(url, window.location.origin); } catch (_) {}
    const eventId = parsed ? eventIdFromPath(parsed.pathname) : null;
    const eventMutation = Boolean(eventId && method !== "GET" &&
      /^\/events\/[0-9a-f-]+\/(?:participants|notes|tasks|venue)?(?:\/|$)/i.test(parsed.pathname));
    if (eventMutation && offlineStore?.isEnabled() && offlineStore.getUser()) {
      try { await offlineStore.clearCurrentCache(); } catch (_) {}
    }
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
      const command = commandFromRequest(url, options);
      if (command) {
        const queued = await enqueueOffline(command);
        if (queued) return queued;
      }
      throw networkError;
    }
    if (response.status === 401) {
      window.location.replace("/auth.html?next=%2F");
      throw new Error("Требуется войти в аккаунт.");
    }
    if (!response.ok) {
      let message = "Не удалось выполнить запрос. Попробуйте ещё раз.";
      let body = null;
      try { body = await response.json(); } catch (_) {}
      if (response.status === 422) message = "Проверьте название и дату встречи.";
      else if (response.status === 404) message = "Эта встреча уже недоступна. Обновите список.";
      else if (typeof body?.detail === "string" && response.status >= 500) message = "Сервер временно недоступен.";
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) {
      if (eventMutation) await refreshRevision(eventId);
      return null;
    }
    const data = await response.json();
    if (cacheKey && offlineStore?.isEnabled() && offlineStore.getUser()) {
      try { await offlineStore.put(cacheKey, data); } catch (_) {}
    }
    if (eventMutation) await refreshRevision(eventId);
    return data;
  };
  window.GatherlyApiClient = { request: api };
})();
