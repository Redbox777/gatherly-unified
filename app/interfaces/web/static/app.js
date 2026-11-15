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
  const participantsList = document.getElementById("participants-list");
  const participantsCount = document.getElementById("participants-count");
  const participantForm = document.getElementById("participant-add-form");
  const participantName = document.getElementById("participant-name");
  const participantStatus = document.getElementById("participant-status");
  const participantAddButton = document.getElementById("participant-add-button");
  const notesList = document.getElementById("notes-list");
  const notesCount = document.getElementById("notes-count");
  const noteForm = document.getElementById("note-form");
  const noteContent = document.getElementById("note-content");
  const noteVisibility = document.getElementById("note-visibility");
  const noteStatus = document.getElementById("note-status");
  const noteSave = document.getElementById("note-save");
  const tasksList = document.getElementById("tasks-list");
  const tasksCount = document.getElementById("tasks-count");
  const taskForm = document.getElementById("task-form");
  const taskTitle = document.getElementById("task-title");
  const taskDescription = document.getElementById("task-description");
  const taskDueAt = document.getElementById("task-due-at");
  const taskAssignee = document.getElementById("task-assignee");
  const taskStatus = document.getElementById("task-status");
  const taskSave = document.getElementById("task-save");
  const taskCancel = document.getElementById("task-cancel");
  const expensePayer = document.getElementById("expense-payer");
  const venueForm = document.getElementById("venue-form");
  const venueDisplay = document.getElementById("venue-display");
  const venueName = document.getElementById("venue-name");
  const venueAddress = document.getElementById("venue-address");
  const venueLatitude = document.getElementById("venue-latitude");
  const venueLongitude = document.getElementById("venue-longitude");
  const venueStatus = document.getElementById("venue-status");
  const venueSave = document.getElementById("venue-save");
  const venueDelete = document.getElementById("venue-delete");
  let participantsBusy = false;
  let eventParticipants = [];
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
  const { dateValue, formatDate, formatTime, localInputValue, toIsoValue } = window.GatherlyDate;
  const api = window.GatherlyApiClient.request;
  const offlineStore = window.GatherlyOfflineStore;
  const financeUI = window.GatherlyFinanceUI.create({
    api,
    formatDate,
    localInputValue,
    toIsoValue,
    dialog,
    getActiveEvent: () => activeEvent
  });
  const { loadNotifications, loadEventReminders } = window.GatherlyNotificationsUI.create({
    api,
    formatDate,
    getActiveEvent: () => activeEvent
  });
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
    resetTaskForm();
    financeUI.resetExpenseForm();
    form.hidden = true;
    detail.hidden = false;
    document.getElementById("detail-title").textContent = event.title;
    document.getElementById("detail-date").textContent = event.starts_at
      ? (formatDate(event.starts_at) + " · " + (formatTime(event.starts_at) || ""))
      : "Дата и время пока не назначены";
    document.getElementById("detail-created").textContent = formatDate(event.created_at)
      ? "Создано " + formatDate(event.created_at) : "";
    if (!dialog.open) dialog.showModal();
    try { await api("/events/" + encodeURIComponent(event.id) + "/sync-state"); } catch (_) {}
    window.GatherlyOfflineSync?.refresh();
    participantStatus.textContent = "";
    participantsCount.textContent = "Загрузка…";
    participantsList.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "participants-empty";
    loading.textContent = "Загружаем список участников…";
    participantsList.append(loading);
    loadParticipants(event.id);
    loadEventReminders(event.id);
    loadNotes(event.id);
    loadTasks(event.id);
    financeUI.loadExpenses(event.id);
    financeUI.loadEventFinance(event.id);
    loadVenue(event.id);
    const requestedEventId = event.id;
    try {
      const fresh = await api("/events/" + encodeURIComponent(requestedEventId));
      if (!dialog.open || activeEvent?.id !== requestedEventId) return;
      activeEvent = fresh;
      document.getElementById("detail-title").textContent = fresh.title;
      document.getElementById("detail-date").textContent = fresh.starts_at
        ? (formatDate(fresh.starts_at) + " · " + formatTime(fresh.starts_at))
        : "Дата и время пока не назначены";
    } catch (error) {
      if (dialog.open && activeEvent?.id === requestedEventId) {
        document.getElementById("detail-created").textContent = error.message;
      }
    }
  };
  const loadParticipants = async eventId => {
    try {
      const list = await api("/events/" + encodeURIComponent(eventId) + "/participants");
      if (!dialog.open || activeEvent?.id !== eventId) return;
      renderParticipants(list, eventId);
    } catch (error) {
      if (!dialog.open || activeEvent?.id !== eventId) return;
      participantsCount.textContent = "Недоступно";
      participantsList.replaceChildren();
      const message = document.createElement("p");
      message.className = "participants-empty";
      message.textContent = error.message;
      participantsList.append(message);
    }
  };
  const { renderParticipants } = window.GatherlyParticipantsUI.create({
    participantsList,
    participantsCount,
    taskAssignee,
    expensePayer,
    participantStatus,
    api,
    loadParticipants: eventId => loadParticipants(eventId),
    formatDate,
    getParticipantsBusy: () => participantsBusy,
    setParticipantsBusy: value => { participantsBusy = value; },
    setEventParticipants: list => { eventParticipants = list; }
  });


  const loadNotes = async eventId => {
    notesList.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "notes-empty";
    loading.textContent = "Загружаем заметки…";
    notesList.append(loading);
    try {
      const notes = await api("/events/" + encodeURIComponent(eventId) + "/notes");
      if (!dialog.open || activeEvent?.id !== eventId) return;
      renderNotes(notes, eventId);
    } catch (error) {
      if (!dialog.open || activeEvent?.id !== eventId) return;
      notesList.replaceChildren();
      const message = document.createElement("p");
      message.className = "notes-empty";
      message.textContent = error.message;
      notesList.append(message);
    }
  };
  const { renderNotes } = window.GatherlyNotesUI.create({
    notesCount,
    notesList,
    noteContent,
    noteVisibility,
    noteForm,
    noteSave,
    noteStatus,
    api,
    formatDate,
    getActiveEvent: () => activeEvent,
    loadNotes: eventId => loadNotes(eventId)
  });
  const renderVenue = venue => {
    venueDisplay.replaceChildren();
    venueDelete.hidden = !venue;
    if (!venue) {
      const empty = document.createElement("p");
      empty.className = "venue-empty";
      empty.textContent = "Место пока не указано.";
      venueDisplay.append(empty);
      venueForm.reset();
      return;
    }
    venueName.value = venue.venue_name;
    venueAddress.value = venue.address;
    venueLatitude.value = venue.latitude ?? "";
    venueLongitude.value = venue.longitude ?? "";
    const summary = document.createElement("div");
    summary.className = "venue-summary";
    const title = document.createElement("strong");
    title.textContent = venue.venue_name;
    const address = document.createElement("p");
    address.textContent = venue.address;
    const link = document.createElement("a");
    const query = venue.latitude != null && venue.longitude != null
      ? venue.latitude + "," + venue.longitude
      : venue.address;
    link.href = "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(query);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Посмотреть на карте ↗";
    summary.append(title, address, link);
    venueDisplay.append(summary);
  };
  const loadVenue = async eventId => {
    venueStatus.textContent = "";
    try {
      const venue = await api("/events/" + encodeURIComponent(eventId) + "/venue");
      if (!dialog.open || activeEvent?.id !== eventId) return;
      renderVenue(venue);
    } catch (error) {
      if (dialog.open && activeEvent?.id === eventId) venueStatus.textContent = error.message;
    }
  };
  venueForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!activeEvent) return;
    const eventId = activeEvent.id;
    const latitudeText = venueLatitude.value.trim();
    const longitudeText = venueLongitude.value.trim();
    if (Boolean(latitudeText) !== Boolean(longitudeText)) {
      venueStatus.textContent = "Укажите обе координаты или оставьте оба поля пустыми.";
      return;
    }
    venueSave.disabled = true;
    venueStatus.textContent = "Сохраняем место…";
    try {
      const venue = await api("/events/" + encodeURIComponent(eventId) + "/venue", {
        method: "PUT",
        body: JSON.stringify({
          venue_name: venueName.value.trim(),
          address: venueAddress.value.trim(),
          latitude: latitudeText ? Number(latitudeText) : null,
          longitude: longitudeText ? Number(longitudeText) : null
        })
      });
      if (!dialog.open || activeEvent?.id !== eventId) return;
      if (venue?.offline_queued) {
        venueStatus.textContent = "Изменение места сохранено на устройстве и ждёт синхронизации.";
        window.GatherlyOfflineSync?.refresh();
      } else {
        renderVenue(venue);
        venueStatus.textContent = "Место сохранено.";
      }
    } catch (error) {
      if (dialog.open && activeEvent?.id === eventId) venueStatus.textContent = error.message;
    } finally {
      venueSave.disabled = false;
    }
  });
  venueDelete.addEventListener("click", async () => {
    if (!activeEvent || venueDelete.hidden) return;
    const eventId = activeEvent.id;
    if (!window.confirm("Удалить место проведения этой встречи?")) return;
    venueDelete.disabled = true;
    try {
      const result = await api("/events/" + encodeURIComponent(eventId) + "/venue", {method:"DELETE"});
      if (!dialog.open || activeEvent?.id !== eventId) return;
      if (result?.offline_queued) {
        venueStatus.textContent = "Удаление места сохранено в локальной очереди.";
        window.GatherlyOfflineSync?.refresh();
      } else {
        renderVenue(null);
        venueStatus.textContent = "Место удалено.";
      }
    } catch (error) {
      if (dialog.open && activeEvent?.id === eventId) venueStatus.textContent = error.message;
    } finally {
      venueDelete.disabled = false;
    }
  });

  const { resetTaskForm, loadTasks } = window.GatherlyTasksUI.create({
    tasksList,
    tasksCount,
    taskForm,
    taskTitle,
    taskDescription,
    taskDueAt,
    taskAssignee,
    taskStatus,
    taskSave,
    taskCancel,
    dialog,
    api,
    formatDate,
    formatTime,
    localInputValue,
    toIsoValue,
    getActiveEvent: () => activeEvent
  });
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
      let session = null;
      try {
        session = await fetch("/auth/me", {credentials:"same-origin"});
      } catch (networkError) {
        const rememberedUserId = offlineStore?.getRememberedUser();
        if (!rememberedUserId) throw networkError;
        await offlineStore.setUser(rememberedUserId);
        window.GatherlyOfflineSync?.refresh();
      }
      if (session) {
        if (session.status === 401) {
          window.location.replace("/auth.html?next=%2F");
          return;
        }
        if (!session.ok) throw new Error("Не удалось проверить вход. Обновите страницу.");
        const identity = await session.json();
        if (!identity || typeof identity.user_id !== "string" || !identity.user_id) {
          throw new Error("Не удалось подтвердить аккаунт. Обновите страницу.");
        }
        await offlineStore?.setUser(identity.user_id);
        window.GatherlyOfflineSync?.refresh();
      }
      const list = await api("/events");
      if (!Array.isArray(list)) throw new Error("Сервер вернул неожиданный ответ.");
      list.sort((a,b) => !a.starts_at ? 1 : !b.starts_at ? -1 : new Date(a.starts_at)-new Date(b.starts_at));
      renderEvents(list);
    } catch (error) {
      showState("Не удалось загрузить встречи", error.message || "Проверьте подключение и обновите страницу.");
      count.textContent = "Ошибка загрузки";
    }
  };

  participantForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (participantsBusy || !activeEvent) return;
    const displayName = participantName.value.trim();
    if (!displayName || displayName.length > 120) {
      participantStatus.textContent = "Введите имя длиной от 1 до 120 символов.";
      participantName.focus();
      return;
    }
    participantsBusy = true;
    participantAddButton.disabled = true;
    participantAddButton.textContent = "Добавляем…";
    participantStatus.textContent = "";
    try {
      await api("/events/" + encodeURIComponent(activeEvent.id) + "/participants", {
        method:"POST", body:JSON.stringify({display_name:displayName})
      });
      participantForm.reset();
      await loadParticipants(activeEvent.id);
    } catch (error) {
      participantStatus.textContent = error.message === "Не удалось выполнить запрос. Попробуйте ещё раз."
        ? "Не удалось добавить участника. Возможно, такое имя уже есть в этой встрече." : error.message;
    } finally {
      participantsBusy = false;
      participantAddButton.disabled = false;
      participantAddButton.textContent = "Добавить";
    }
  });
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
      try { await offlineStore?.clearCurrentUser(); } catch (_) {}
      window.GatherlyOfflineSync?.refresh();
      window.location.replace("/auth.html");
    } catch (_) {
      logout.disabled = false;
      showState("Не удалось выйти", "Сессия не завершена. Попробуйте ещё раз.");
    }
  });
  if (grid) {
    loadDashboard();
    loadNotifications();
  }
})();