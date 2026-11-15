(() => {
  const store = window.GatherlyOfflineStore;
  const panel = document.getElementById("offline-sync-panel");
  const count = document.getElementById("offline-sync-count");
  const status = document.getElementById("offline-sync-status");
  const list = document.getElementById("offline-sync-list");
  const syncButton = document.getElementById("offline-sync-now");
  const ownerId = crypto.randomUUID();
  let busy = false;

  const stateLabel = state => ({
    pending: "Ожидает отправки",
    sending: "Отправляется…",
    done: "Синхронизировано",
    conflict: "Конфликт версии",
    blocked: "Требует внимания"
  })[state] || state;
  const commandLabel = item => {
    const p = item.payload || {};
    if (item.kind.startsWith("note.")) return "Заметка: " + String(p.content || "удаление").slice(0, 72);
    if (item.kind.startsWith("task.")) return "Задача: " + String(p.title || p.task_id || "изменение").slice(0, 72);
    if (item.kind.startsWith("venue.")) return "Место: " + String(p.venue_name || "изменение места").slice(0, 72);
    return item.kind;
  };
  const setStatus = message => { if (status) status.textContent = message || ""; };
  const verifySession = async expectedUserId => {
    try {
      const response = await fetch("/auth/me", { credentials: "same-origin", headers: { "Accept": "application/json" } });
      if (!response.ok) return false;
      const identity = await response.json();
      return Boolean(identity && identity.user_id === expectedUserId && store.getUser() === expectedUserId);
    } catch (_) { return false; }
  };
  const fetchRevision = async eventId => {
    const response = await fetch("/events/" + encodeURIComponent(eventId) + "/sync-state", {
      credentials: "same-origin", headers: { "Accept": "application/json" }
    });
    if (!response.ok) throw new Error(response.status === 401
      ? "Войдите в тот же аккаунт, чтобы продолжить синхронизацию."
      : "Не удалось получить актуальную версию встречи.");
    const value = await response.json();
    if (!Number.isSafeInteger(value.revision) || value.revision < 1) throw new Error("Сервер вернул некорректную версию встречи.");
    await store.setEventRevision(eventId, value.revision);
    return value.revision;
  };
  const render = async () => {
    if (!panel || !store) return;
    if (!store.isEnabled() || !store.getUser()) {
      panel.hidden = true;
      if (list) list.replaceChildren();
      return;
    }
    panel.hidden = false;
    let items = [];
    try { items = await store.listOutbox(); }
    catch (_) { setStatus("Не удалось прочитать локальную очередь."); return; }
    const pending = items.filter(item => item.state !== "done").length;
    if (count) count.textContent = String(pending);
    if (syncButton) syncButton.disabled = busy || !navigator.onLine || pending === 0;
    if (list) {
      list.replaceChildren();
      items.forEach(item => {
        const row = document.createElement("div");
        row.className = "offline-sync-item";
        const description = document.createElement("div");
        description.className = "offline-sync-item-copy";
        const title = document.createElement("strong");
        title.textContent = commandLabel(item);
        const meta = document.createElement("span");
        meta.textContent = stateLabel(item.state) + (item.lastError ? " · " + item.lastError : "");
        description.append(title, meta);
        const actions = document.createElement("div");
        actions.className = "offline-sync-item-actions";
        if (item.state === "conflict") {
          const reapply = document.createElement("button");
          reapply.type = "button";
          reapply.className = "text-button";
          reapply.textContent = "Применить заново";
          reapply.addEventListener("click", () => reapplyItem(item));
          actions.append(reapply);
        }
        if (item.state !== "sending") {
          const discard = document.createElement("button");
          discard.type = "button";
          discard.className = "text-button";
          discard.textContent = "Удалить";
          discard.addEventListener("click", () => discardItem(item));
          actions.append(discard);
        }
        row.append(description, actions);
        list.append(row);
      });
    }
    if (!pending) setStatus("Все локальные изменения синхронизированы.");
    else if (!navigator.onLine) setStatus("Нет подключения. Изменения сохранены на этом устройстве.");
    else if (!busy && !status?.textContent) setStatus("Изменения сохранены локально. Нажмите «Синхронизировать», когда будете готовы.");
  };
  const withEventLock = async (eventId, callback) => {
    const name = "gatherly-offline-sync:" + eventId;
    if (navigator.locks?.request) {
      return navigator.locks.request(name, { mode: "exclusive", ifAvailable: true }, lock =>
        lock ? callback() : "locked"
      );
    }
    if (!await store.acquireLease(eventId, ownerId)) return "locked";
    try { return await callback(); }
    finally { await store.releaseLease(eventId, ownerId); }
  };
  const sendEventQueue = async (eventId, expectedUserId) => withEventLock(eventId, async () => {
    let items = (await store.listOutbox())
      .filter(item => item.eventId === eventId)
      .sort((a, b) => a.baseRevision - b.baseRevision || a.createdAt - b.createdAt);
    for (const item of items) {
      if (store.getUser() !== expectedUserId || !await verifySession(expectedUserId)) {
        setStatus("Синхронизация приостановлена: активный аккаунт изменился или не подтверждён.");
        return "auth";
      }
      if (item.state === "done") {
        await store.removeOutboxItem(item.operationId);
        continue;
      }
      if (item.state === "conflict" || item.state === "blocked") break;
      if (item.state !== "pending") break;
      if (item.retryAt && item.retryAt > Date.now()) break;
      if (!navigator.locks?.request && !await store.acquireLease(eventId, ownerId)) return false;

      let completed = false;
      let pauseReason = null;
      for (let attempt = 0; attempt < 3 && !completed; attempt += 1) {
        await store.updateOutboxState(item.operationId, "sending");
        await render();
        let response;
        try {
          response = await fetch("/offline-sync/commands", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify({
              operation_id: item.operationId,
              event_id: item.eventId,
              base_revision: item.baseRevision,
              kind: item.kind,
              payload: item.payload
            })
          });
        } catch (_) {
          const delay = Math.min(60000, 750 * (2 ** (item.attempts + attempt))) + Math.floor(Math.random() * 350);
          await store.updateOutboxState(item.operationId, "pending", {
            error: "Нет ответа от сервера. Повторим ту же операцию.",
            retryAt: Date.now() + delay
          });
          if (attempt < 2) await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }

        if (response.ok) {
          let body;
          try { body = await response.json(); } catch (_) { body = null; }
          if (!body || !Number.isSafeInteger(body.revision)) {
            await store.updateOutboxState(item.operationId, "pending", {
              error: "Ответ сервера неполный; операция будет безопасно повторена."
            });
            break;
          }
          await store.setEventRevision(eventId, body.revision);
          await store.removeOutboxItem(item.operationId);
          completed = true;
          setStatus("Изменение встречи синхронизировано.");
          continue;
        }

        if (response.status === 401) {
          await store.updateOutboxState(item.operationId, "pending", { error: "Нужно повторно войти в аккаунт." });
          setStatus("Синхронизация приостановлена: требуется вход в аккаунт.");
          return "auth";
        }
        if (response.status === 409) {
          let body = {};
          try { body = await response.json(); } catch (_) {}
          if (Number.isSafeInteger(body.current_revision)) {
            await store.setEventRevision(eventId, body.current_revision);
          }
          await store.updateOutboxState(item.operationId, "conflict", {
            error: "На сервере уже есть более новая версия.",
            serverRevision: body.current_revision
          });
          setStatus("Есть конфликт версии. Последующие изменения этой встречи приостановлены.");
          pauseReason = "conflict";
          break;
        }
        if (response.status === 429) {
          const header = response.headers.get("Retry-After");
          let retryAt = Date.now() + 30000;
          if (header) {
            const seconds = Number(header);
            const parsed = Number.isFinite(seconds) ? Date.now() + seconds * 1000 : Date.parse(header);
            if (Number.isFinite(parsed)) retryAt = parsed;
          }
          await store.updateOutboxState(item.operationId, "pending", {
            error: "Сервер ограничил частоту запросов; повторим позже.",
            retryAt
          });
          setStatus("Сервер попросил подождать перед следующей синхронизацией.");
          pauseReason = "rate";
          break;
        }
        if (response.status >= 500) {
          const delay = Math.min(60000, 750 * (2 ** (item.attempts + attempt))) + Math.floor(Math.random() * 350);
          await store.updateOutboxState(item.operationId, "pending", {
            error: "Сервер временно недоступен.",
            retryAt: Date.now() + delay
          });
          if (attempt < 2) await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }
        let detail = "Сервер отклонил изменение (" + response.status + ").";
        try {
          const body = await response.json();
          if (typeof body.detail === "string") detail = body.detail;
        } catch (_) {}
        await store.updateOutboxState(item.operationId, "blocked", { error: detail });
        setStatus("Одно изменение заблокировано. Проверьте очередь перед продолжением.");
        pauseReason = "blocked";
        break;
      }
      const current = (await store.listOutbox()).find(value => value.operationId === item.operationId);
      if (current) return pauseReason || "retry";
      items = items.filter(value => value.operationId !== item.operationId);
    }
    return true;
  });

  const syncNow = async () => {
    if (busy || !store?.isEnabled() || !store.getUser()) return;
    if (!navigator.onLine) { setStatus("Нет подключения к интернету."); return; }
    busy = true;
    if (syncButton) syncButton.disabled = true;
    setStatus("Синхронизируем изменения…");
    try {
      const expectedUserId = store.getUser();
      if (!await verifySession(expectedUserId)) {
        setStatus("Очередь заблокирована до подтверждения входа в исходный аккаунт.");
        return;
      }
      await store.recoverSending();
      const items = await store.listOutbox();
      const eventIds = [...new Set(items.map(item => item.eventId))];
      let authenticationPaused = false;
      let partial = false;
      for (const eventId of eventIds) {
        const result = await sendEventQueue(eventId, expectedUserId);
        if (result === "auth") {
          authenticationPaused = true;
          break;
        }
        if (typeof result === "string") partial = true;
      }
      if (authenticationPaused) {
        setStatus("Синхронизация приостановлена: требуется вход в исходный аккаунт.");
      } else if (partial) {
        setStatus("Синхронизация завершена частично. Проверьте отложенные, конфликтные или заблокированные изменения.");
      } else {
        setStatus("Проверка очереди завершена.");
      }
    } catch (_) {
      setStatus("Не удалось завершить синхронизацию. Очередь сохранена.");
    } finally {
      busy = false;
      await render();
    }
  };
  const discardItem = async item => {
    if (!window.confirm("Удалить это локальное изменение?")) return;
    try {
      await store.removeOutboxItem(item.operationId);
      if (navigator.onLine) {
        try {
          const revision = await fetchRevision(item.eventId);
          await store.rebasePendingEvent(item.eventId, revision);
        } catch (_) {}
      }
      setStatus("Локальное изменение удалено.");
      await render();
    } catch (_) { setStatus("Не удалось удалить изменение."); }
  };
  const reapplyItem = async item => {
    if (!window.confirm("Создать новую операцию на основе актуальной версии встречи? Содержимое изменения останется прежним.")) return;
    try {
      if (!navigator.onLine) throw new Error("Для повторного применения нужно подключение к интернету.");
      if (!await verifySession(store.getUser())) throw new Error("Сначала подтвердите вход в исходный аккаунт.");
      const revision = await fetchRevision(item.eventId);
      const replacement = await store.enqueue({
        eventId: item.eventId,
        baseRevision: revision,
        kind: item.kind,
        payload: item.payload,
        createdAt: item.createdAt - 0.5
      });
      await store.removeOutboxItem(item.operationId);
      await store.rebasePendingEvent(item.eventId, revision + 1, replacement.operationId);
      setStatus("Создана новая операция. Проверьте её перед синхронизацией.");
      await render();
    } catch (error) { setStatus(error.message || "Не удалось повторно применить изменение."); }
  };

  if (syncButton) syncButton.addEventListener("click", syncNow);
  window.addEventListener("online", render);
  window.addEventListener("offline", render);
  window.GatherlyOfflineSync = { refresh: render, syncNow };
  render();
})();


test("reapplying a conflicted command uses the current server revision before rebasing later commands", async () => {
  const conflict = (await store.listOutbox()).find(item => item.eventId === eventTwo && item.state === "conflict");
  const later = (await store.listOutbox()).find(item => item.eventId === eventTwo && item.state === "pending");
  assert.ok(conflict);
  assert.ok(later);
  global.fetch = async url => {
    if (url === "/auth/me") {
      return new Response(JSON.stringify({ user_id: "sender-account" }), { status: 200 });
    }
    if (url === "/events/" + eventTwo + "/sync-state") {
      return new Response(JSON.stringify({ event_id: eventTwo, revision: 4 }), {
        status: 200, headers: { "Content-Type": "application/json" }
      });
    }
    throw new Error("Unexpected fetch URL: " + url);
  };
  const previousConfirm = global.window.confirm;
  global.window.confirm = () => true;
  try {
    await global.GatherlyOfflineSync.reapplyItem(conflict);
  } finally {
    global.window.confirm = previousConfirm;
  }
  const remaining = (await store.listOutbox()).filter(item => item.eventId === eventTwo);
  const replacement = remaining.find(item => item.operationId !== later.operationId);
  const rebasedLater = remaining.find(item => item.operationId === later.operationId);
  assert.ok(replacement);
  assert.notEqual(replacement.operationId, conflict.operationId);
  assert.equal(replacement.baseRevision, 4);
  assert.equal(rebasedLater.baseRevision, 5);
  assert.equal(replacement.state, "pending");
});
