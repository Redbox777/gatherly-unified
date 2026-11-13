(() => {
  const sidebar = document.getElementById("sidebar");
  const toggle = document.getElementById("menu-toggle");
  const scrim = document.getElementById("mobile-scrim");
  const grid = document.getElementById("meeting-grid");
  const count = document.getElementById("event-count");
  const summaryCount = document.getElementById("summary-event-count");
  const logout = document.getElementById("logout-button");
  const dialog = document.getElementById("event-dialog");
  const form = document.getElementById("event-form");
  const detail = document.getElementById("event-detail");
  const titleInput = document.getElementById("event-title");
  const dateInput = document.getElementById("event-starts-at");
  const formStatus = document.getElementById("event-form-status");
  const saveButton = document.getElementById("event-save");
  let events = [];
  let activeEvent = null;
  let busy = false;

  const closeMenu = () => {
    sidebar?.classList.remove("open");
    if (scrim) scrim.hidden = true;
    toggle?.setAttribute("aria-expanded", "false");
    toggle?.setAttribute("aria-label", "Открыть меню");
    toggle?.querySelector("use")?.setAttribute("href", "#icon-menu");
  };
  if (sidebar && toggle && scrim) {
    toggle.addEventListener("click", () => {
      if (sidebar.classList.contains("open")) closeMenu();
      else {
        sidebar.classList.add("open");
        scrim.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
        toggle.setAttribute("aria-label", "Закрыть меню");
        toggle.querySelector("use")?.setAttribute("href", "#icon-close");
      }
    });
    scrim.addEventListener("click", closeMenu);
    sidebar.querySelectorAll("a").forEach(link => link.addEventListener("click", closeMenu));
    document.addEventListener("keydown", event => { if (event.key === "Escape") closeMenu(); });
    window.addEventListener("resize", () => { if (window.matchMedia("(min-width: 761px)").matches) closeMenu(); });
  }

  const showState = (heading, message) => {
    grid.replaceChildren();
    const panel = document.createElement("div");
    panel.className = "dashboard-state";
    const title = document.createElement("strong");
    title.textContent = heading;
    const text = document.createElement("span");
    text.className = "state-detail";
    text.textContent = message;
    panel.append(title, text);
    grid.append(panel);
  };
  const dateValue = value => {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const formatDate = value => {
    const date = dateValue(value);
    return date ? new Intl.DateTimeFormat("ru-RU", {day:"numeric",month:"long",year:"numeric"}).format(date) : null;
  };
  const formatTime = value => {
    const date = dateValue(value);
    return date ? new Intl.DateTimeFormat("ru-RU", {hour:"2-digit",minute:"2-digit"}).format(date) : "";
  };
  const localInputValue = value => {
    const date = dateValue(value);
    if (!date) return "";
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0,16);
  };
  const toIsoValue = value => value ? new Date(value).toISOString() : null;
  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: {"Content-Type":"application/json", ...(options.headers || {})}
    });
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
      throw new Error(message);
    }
    if (response.status === 204) return null;
    return response.json();
  };
  const setBusy = value => {
    busy = value;
    saveButton.disabled = value;
    saveButton.textContent = value ? "Сохраняем…" : (activeEvent ? "Сохранить изменения" : "Сохранить");
  };
  const openForm = event => {
    activeEvent = event || null;
    form.hidden = false;
    detail.hidden = true;
    form.reset();
    titleInput.value = event?.title || "";
    dateInput.value = localInputValue(event?.starts_at);
    document.getElementById("event-dialog-eyebrow").textContent = event ? "ИЗМЕНЕНИЕ СОБЫТИЯ" : "НОВОЕ СОБЫТИЕ";
    document.getElementById("event-dialog-title").textContent = event ? "Изменить встречу" : "Создать встречу";
    formStatus.textContent = "";
    setBusy(false);
    if (!dialog.open) dialog.showModal();
    titleInput.focus();
  };
  const openDetail = async event => {
    activeEvent = event;
    form.hidden = true;
    detail.hidden = false;
    document.getElementById("detail-title").textContent = event.title;
    document.getElementById("detail-date").textContent = event.starts_at
      ? (formatDate(event.starts_at) + " · " + (formatTime(event.starts_at) || ""))
      : "Дата и время пока не назначены";
    document.getElementById("detail-created").textContent = formatDate(event.created_at)
      ? "Создано " + formatDate(event.created_at) : "";
    if (!dialog.open) dialog.showModal();
    try {
      const fresh = await api("/events/" + encodeURIComponent(event.id));
      activeEvent = fresh;
      document.getElementById("detail-title").textContent = fresh.title;
      document.getElementById("detail-date").textContent = fresh.starts_at
        ? (formatDate(fresh.starts_at) + " · " + formatTime(fresh.starts_at))
        : "Дата и время пока не назначены";
    } catch (error) {
      if (dialog.open) document.getElementById("detail-created").textContent = error.message;
    }
  };
  const renderEvents = list => {
    events = list;
    grid.replaceChildren();
    count.textContent = list.length ? list.length + " событий" : "Пока пусто";
    summaryCount.textContent = String(list.length);
    if (!list.length) {
      showState("Пока нет встреч", "Создайте первую встречу — она появится здесь.");
      return;
    }
    list.forEach(event => {
      const card = document.createElement("article");
      card.className = "meeting-card event-card";
      const top = document.createElement("div");
      top.className = "card-top";
      const date = document.createElement("span");
      const formatted = formatDate(event.starts_at);
      date.className = "event-date" + (formatted ? "" : " unscheduled");
      date.textContent = formatted || "ДАТА НЕ УКАЗАНА";
      top.append(date);
      const symbol = document.createElement("div");
      symbol.className = "meeting-symbol symbol-teal";
      symbol.setAttribute("aria-hidden", "true");
      symbol.textContent = "✦";
      const heading = document.createElement("h3");
      heading.textContent = event.title || "Без названия";
      const meta = document.createElement("p");
      meta.className = "event-meta";
      meta.textContent = event.starts_at ? "Начало в " + formatTime(event.starts_at) : "Время не назначено";
      const actions = document.createElement("div");
      actions.className = "event-card-actions";
      const view = document.createElement("button");
      view.type = "button";
      view.className = "text-button";
      view.textContent = "Подробнее";
      view.addEventListener("click", () => openDetail(event));
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "text-button";
      edit.textContent = "Изменить";
      edit.addEventListener("click", () => openForm(event));
      actions.append(view, edit);
      card.append(top, symbol, heading, meta, actions);
      grid.append(card);
    });
  };
  const loadDashboard = async () => {
    showState("Загружаем встречи", "Получаем список событий вашего аккаунта…");
    try {
      const session = await fetch("/auth/me", {credentials:"same-origin"});
      if (session.status === 401) {
        window.location.replace("/auth.html?next=%2F");
        return;
      }
      if (!session.ok) throw new Error("Не удалось проверить вход. Обновите страницу.");
      const list = await api("/events");
      if (!Array.isArray(list)) throw new Error("Сервер вернул неожиданный ответ.");
      list.sort((a,b) => !a.starts_at ? 1 : !b.starts_at ? -1 : new Date(a.starts_at)-new Date(b.starts_at));
      renderEvents(list);
    } catch (error) {
      showState("Не удалось загрузить встречи", error.message || "Проверьте подключение и обновите страницу.");
      count.textContent = "Ошибка загрузки";
    }
  };

  document.getElementById("create-event-button").addEventListener("click", () => openForm(null));
  document.getElementById("dialog-close").addEventListener("click", () => dialog.close());
  document.getElementById("detail-close").addEventListener("click", () => dialog.close());
  document.getElementById("event-cancel").addEventListener("click", () => dialog.close());
  document.getElementById("edit-event-button").addEventListener("click", () => openForm(activeEvent));
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    const title = titleInput.value.trim();
    if (!title || title.length > 200) {
      formStatus.textContent = "Введите название длиной от 1 до 200 символов.";
      titleInput.focus();
      return;
    }
    const payload = {title, starts_at:toIsoValue(dateInput.value)};
    const editing = Boolean(activeEvent);
    setBusy(true);
    formStatus.textContent = "";
    try {
      const saved = await api(editing ? "/events/" + encodeURIComponent(activeEvent.id) : "/events", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(payload)
      });
      dialog.close();
      await loadDashboard();
      if (!editing && saved?.id) {
        const created = events.find(item => item.id === saved.id) || saved;
        openDetail(created);
      }
    } catch (error) {
      formStatus.textContent = error.message;
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("delete-event-button").addEventListener("click", async () => {
    if (!activeEvent || busy) return;
    const confirmed = window.confirm("Удалить встречу «" + activeEvent.title + "»? Это действие нельзя отменить.");
    if (!confirmed) return;
    const button = document.getElementById("delete-event-button");
    button.disabled = true;
    try {
      await api("/events/" + encodeURIComponent(activeEvent.id), {method:"DELETE"});
      dialog.close();
      await loadDashboard();
    } catch (error) {
      document.getElementById("detail-created").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  if (logout) logout.addEventListener("click", async () => {
    logout.disabled = true;
    try {
      await api("/auth/logout", {method:"POST",body:"{}"});
      window.location.replace("/auth.html");
    } catch (_) {
      logout.disabled = false;
      showState("Не удалось выйти", "Сессия не завершена. Попробуйте ещё раз.");
    }
  });
  if (grid) loadDashboard();
})();