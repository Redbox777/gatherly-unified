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
  const notificationToggle = document.getElementById("notification-toggle");
  const notificationPanel = document.getElementById("notification-panel");
  const notificationList = document.getElementById("notification-list");
  const notificationCount = document.getElementById("notification-count");
  const reminderSave = document.getElementById("reminder-save");
  const reminderStatus = document.getElementById("reminder-status");
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
  const expensesList = document.getElementById("expenses-list");
  const expensesCount = document.getElementById("expenses-count");
  const expenseForm = document.getElementById("expense-form");
  const expenseTitle = document.getElementById("expense-title");
  const expenseDescription = document.getElementById("expense-description");
  const expenseAmount = document.getElementById("expense-amount");
  const expenseCurrency = document.getElementById("expense-currency");
  const expensePayer = document.getElementById("expense-payer");
  const expenseDate = document.getElementById("expense-date");
  const expenseStatus = document.getElementById("expense-status");
  const expenseSave = document.getElementById("expense-save");
  const expenseCancel = document.getElementById("expense-cancel");
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
      const error = new Error(message);
      error.status = response.status;
      throw error;
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
    resetTaskForm();
    resetExpenseForm();
    form.hidden = true;
    detail.hidden = false;
    document.getElementById("detail-title").textContent = event.title;
    document.getElementById("detail-date").textContent = event.starts_at
      ? (formatDate(event.starts_at) + " · " + (formatTime(event.starts_at) || ""))
      : "Дата и время пока не назначены";
    document.getElementById("detail-created").textContent = formatDate(event.created_at)
      ? "Создано " + formatDate(event.created_at) : "";
    if (!dialog.open) dialog.showModal();
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
    loadExpenses(event.id);
    loadVenue(event.id);
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
  const renderParticipants = (list, eventId) => {
    participantsCount.textContent = String(list.length);
    if (taskAssignee) {
      const selected = taskAssignee.value;
      taskAssignee.replaceChildren();
      const unassigned = document.createElement("option");
      unassigned.value = "";
      unassigned.textContent = "Не назначен";
      taskAssignee.append(unassigned);
      list.forEach(participant => {
        const option = document.createElement("option");
        option.value = participant.id;
        option.textContent = participant.display_name;
        taskAssignee.append(option);
      });
      taskAssignee.value = selected;
    }
    if (expensePayer) {
      const selectedPayer = expensePayer.value;
      expensePayer.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = list.length ? "Выберите плательщика" : "Сначала добавьте участника";
      expensePayer.append(placeholder);
      list.forEach(participant => {
        const option = document.createElement("option");
        option.value = participant.id;
        option.textContent = participant.display_name;
        expensePayer.append(option);
      });
      expensePayer.value = selectedPayer;
    }
    participantsList.replaceChildren();
    if (!list.length) {
      const empty = document.createElement("p");
      empty.className = "participants-empty";
      empty.textContent = "Пока никого нет. Добавьте первого участника.";
      participantsList.append(empty);
      return;
    }
    list.forEach(participant => {
      const row = document.createElement("div");
      row.className = "participant-row";
      const main = document.createElement("div");
      main.className = "participant-main";
      const name = document.createElement("span");
      name.className = "participant-name";
      name.textContent = participant.display_name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "participant-remove";
      remove.textContent = "Удалить";
      remove.setAttribute("aria-label", "Удалить участника " + participant.display_name);
      remove.addEventListener("click", async () => {
        if (participantsBusy || !window.confirm("Удалить участника «" + participant.display_name + "»?")) return;
        participantsBusy = true;
        remove.disabled = true;
        participantStatus.textContent = "";
        try {
          await api("/events/" + encodeURIComponent(eventId) + "/participants/" + encodeURIComponent(participant.id), {method:"DELETE"});
          await loadParticipants(eventId);
        } catch (error) {
          participantStatus.textContent = error.message;
          remove.disabled = false;
        } finally {
          participantsBusy = false;
        }
      });
      main.append(name, remove);

      const inviteForm = document.createElement("form");
      inviteForm.className = "participant-invite-form";
      const email = document.createElement("input");
      email.type = "email";
      email.name = "email";
      email.required = true;
      email.maxLength = 254;
      email.autocomplete = "email";
      email.placeholder = "Email участника";
      email.setAttribute("aria-label", "Email для приглашения: " + participant.display_name);
      const inviteButton = document.createElement("button");
      inviteButton.type = "submit";
      inviteButton.className = "participant-invite-button";
      inviteButton.textContent = "Пригласить";
      const inviteStatus = document.createElement("span");
      inviteStatus.className = "participant-invite-status";
      inviteStatus.setAttribute("role", "status");
      inviteStatus.setAttribute("aria-live", "polite");
      inviteForm.addEventListener("submit", async event => {
        event.preventDefault();
        if (participantsBusy) return;
        const targetEmail = email.value.trim();
        if (!targetEmail) {
          email.focus();
          return;
        }
        participantsBusy = true;
        inviteButton.disabled = true;
        inviteButton.textContent = "Отправляем…";
        inviteStatus.textContent = "";
        try {
          const invitation = await api(
            "/events/" + encodeURIComponent(eventId) + "/participants/" +
              encodeURIComponent(participant.id) + "/invitation",
            {method:"POST", body:JSON.stringify({email:targetEmail})}
          );
          inviteStatus.textContent = "Приглашение отправлено · до " + formatDate(invitation.expires_at);
          inviteForm.reset();
        } catch (error) {
          if (error.status === 409) {
            inviteStatus.textContent = "Этот участник уже связан с аккаунтом.";
          } else if (error.status === 503) {
            inviteStatus.textContent = "Почта временно недоступна. Попробуйте позже.";
          } else if (error.status === 429) {
            inviteStatus.textContent = "Слишком много писем. Подождите и попробуйте позже.";
          } else {
            inviteStatus.textContent = error.message;
          }
        } finally {
          participantsBusy = false;
          inviteButton.disabled = false;
          inviteButton.textContent = "Пригласить";
        }
      });
      inviteForm.append(email, inviteButton, inviteStatus);
      row.append(main, inviteForm);
      participantsList.append(row);
    });
  };


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
  const renderNotes = (notes, eventId) => {
    notesCount.textContent = String(notes.length);
    notesList.replaceChildren();
    if (!notes.length) {
      const empty = document.createElement("p");
      empty.className = "notes-empty";
      empty.textContent = "Заметок пока нет. Добавьте первую запись.";
      notesList.append(empty);
      return;
    }
    notes.forEach(note => {
      const card = document.createElement("article");
      card.className = "note-card";
      const meta = document.createElement("div");
      meta.className = "note-meta";
      const visibility = document.createElement("span");
      visibility.textContent = note.visibility === "shared" ? "Общая заметка" : "Личная заметка";
      const date = document.createElement("time");
      date.dateTime = note.updated_at;
      date.textContent = formatDate(note.updated_at) || "";
      meta.append(visibility, date);
      const content = document.createElement("p");
      content.className = "note-text";
      content.textContent = note.content;
      card.append(meta, content);
      const actions = document.createElement("div");
      actions.className = "note-actions";
      if (note.can_edit) {
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "text-button";
        edit.textContent = "Изменить";
        edit.addEventListener("click", () => {
          noteContent.value = note.content;
          noteVisibility.value = note.visibility;
          noteForm.dataset.editingNote = note.id;
          noteSave.textContent = "Сохранить изменения";
          noteStatus.textContent = "Редактирование заметки";
          noteContent.focus();
        });
        actions.append(edit);
      }
      if (note.can_delete) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-button note-delete";
      remove.textContent = "Удалить";
      remove.addEventListener("click", async () => {
        if (!window.confirm("Удалить эту заметку?")) return;
        remove.disabled = true;
        try {
          await api("/events/" + encodeURIComponent(eventId) + "/notes/" + encodeURIComponent(note.id), {method:"DELETE"});
          await loadNotes(eventId);
        } catch (error) {
          noteStatus.textContent = error.message;
          remove.disabled = false;
        }
      });
      actions.append(remove);
      }
      if (actions.childElementCount) card.append(actions);
      notesList.append(card);
    });
  };
  if (noteForm) noteForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!activeEvent || !noteContent.value.trim()) return;
    const noteId = noteForm.dataset.editingNote;
    noteSave.disabled = true;
    noteStatus.textContent = noteId ? "Сохраняем изменения…" : "Сохраняем…";
    try {
      await api(
        "/events/" + encodeURIComponent(activeEvent.id) + "/notes" +
          (noteId ? "/" + encodeURIComponent(noteId) : ""),
        {
          method: noteId ? "PUT" : "POST",
          body: JSON.stringify({content:noteContent.value, visibility:noteVisibility.value})
        }
      );
      noteForm.reset();
      delete noteForm.dataset.editingNote;
      noteSave.textContent = "Сохранить заметку";
      noteStatus.textContent = "Заметка сохранена.";
      await loadNotes(activeEvent.id);
    } catch (error) {
      noteStatus.textContent = error.message;
    } finally {
      noteSave.disabled = false;
    }
  });

  const resetTaskForm = () => {
    taskForm.reset();
    delete taskForm.dataset.editingTask;
    taskSave.textContent = "Добавить задачу";
    taskCancel.hidden = true;
    taskStatus.textContent = "";
  };
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
    const latitudeText = venueLatitude.value.trim();
    const longitudeText = venueLongitude.value.trim();
    if (Boolean(latitudeText) !== Boolean(longitudeText)) {
      venueStatus.textContent = "Укажите обе координаты или оставьте оба поля пустыми.";
      return;
    }
    venueSave.disabled = true;
    venueStatus.textContent = "Сохраняем место…";
    try {
      const venue = await api("/events/" + encodeURIComponent(activeEvent.id) + "/venue", {
        method: "PUT",
        body: JSON.stringify({
          venue_name: venueName.value.trim(),
          address: venueAddress.value.trim(),
          latitude: latitudeText ? Number(latitudeText) : null,
          longitude: longitudeText ? Number(longitudeText) : null
        })
      });
      renderVenue(venue);
      venueStatus.textContent = "Место сохранено.";
    } catch (error) {
      venueStatus.textContent = error.message;
    } finally {
      venueSave.disabled = false;
    }
  });
  venueDelete.addEventListener("click", async () => {
    if (!activeEvent || venueDelete.hidden) return;
    if (!window.confirm("Удалить место проведения этой встречи?")) return;
    venueDelete.disabled = true;
    try {
      await api("/events/" + encodeURIComponent(activeEvent.id) + "/venue", {method:"DELETE"});
      renderVenue(null);
      venueStatus.textContent = "Место удалено.";
    } catch (error) {
      venueStatus.textContent = error.message;
    } finally {
      venueDelete.disabled = false;
    }
  });

  const loadTasks = async eventId => {
    tasksList.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "tasks-empty";
    loading.textContent = "Загружаем задачи…";
    tasksList.append(loading);
    try {
      const tasks = await api("/events/" + encodeURIComponent(eventId) + "/tasks");
      if (!dialog.open || activeEvent?.id !== eventId) return;
      renderTasks(tasks, eventId);
    } catch (error) {
      if (!dialog.open || activeEvent?.id !== eventId) return;
      tasksList.replaceChildren();
      const message = document.createElement("p");
      message.className = "tasks-empty";
      message.textContent = error.message;
      tasksList.append(message);
    }
  };
  const renderTasks = (tasks, eventId) => {
    tasksCount.textContent = tasks.length + (tasks.length === 1 ? " задача" : " задач");
    tasksList.replaceChildren();
    if (!tasks.length) {
      const empty = document.createElement("p");
      empty.className = "tasks-empty";
      empty.textContent = "Пока нет задач. Добавьте первую.";
      tasksList.append(empty);
      return;
    }
    tasks.forEach(task => {
      const card = document.createElement("article");
      card.className = "task-card" + (task.completed ? " completed" : "");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "task-check";
      check.checked = Boolean(task.completed);
      check.setAttribute("aria-label", (task.completed ? "Вернуть задачу в работу: " : "Отметить выполненной: ") + task.title);
      check.addEventListener("change", async () => {
        check.disabled = true;
        try {
          await api("/events/" + encodeURIComponent(eventId) + "/tasks/" + encodeURIComponent(task.id) + "/completion", {
            method:"PATCH", body:JSON.stringify({completed:check.checked})
          });
          await loadTasks(eventId);
        } catch (error) {
          check.checked = !check.checked;
          taskStatus.textContent = error.message;
          check.disabled = false;
        }
      });
      const main = document.createElement("div");
      main.className = "task-main";
      const title = document.createElement("strong");
      title.className = "task-title";
      title.textContent = task.title;
      main.append(title);
      if (task.description) {
        const description = document.createElement("p");
        description.className = "task-description";
        description.textContent = task.description;
        main.append(description);
      }
      const meta = document.createElement("div");
      meta.className = "task-meta";
      const due = document.createElement("span");
      due.textContent = task.due_at ? "Срок: " + (formatDate(task.due_at) || "") + " · " + (formatTime(task.due_at) || "") : "Без срока";
      const assignee = document.createElement("span");
      assignee.textContent = task.display_name ? "Ответственный: " + task.display_name : "Не назначен";
      meta.append(due, assignee);
      main.append(meta);
      const actions = document.createElement("div");
      actions.className = "task-actions";
      const edit = document.createElement("button");
      edit.type = "button";
      edit.textContent = "Изменить";
      edit.addEventListener("click", () => {
        taskTitle.value = task.title;
        taskDescription.value = task.description || "";
        taskDueAt.value = localInputValue(task.due_at);
        taskAssignee.value = task.assignee_participant_id || "";
        taskForm.dataset.editingTask = task.id;
        taskSave.textContent = "Сохранить изменения";
        taskCancel.hidden = false;
        taskStatus.textContent = "Редактирование задачи";
        taskTitle.focus();
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "task-delete";
      remove.textContent = "Удалить";
      remove.addEventListener("click", async () => {
        if (!window.confirm("Удалить задачу «" + task.title + "»?")) return;
        remove.disabled = true;
        try {
          await api("/events/" + encodeURIComponent(eventId) + "/tasks/" + encodeURIComponent(task.id), {method:"DELETE"});
          if (taskForm.dataset.editingTask === task.id) resetTaskForm();
          await loadTasks(eventId);
        } catch (error) {
          taskStatus.textContent = error.message;
          remove.disabled = false;
        }
      });
      actions.append(edit, remove);
      card.append(check, main, actions);
      tasksList.append(card);
    });
  };
  taskCancel.addEventListener("click", resetTaskForm);
  taskForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!activeEvent || !taskTitle.value.trim()) return;
    const taskId = taskForm.dataset.editingTask;
    taskSave.disabled = true;
    taskStatus.textContent = taskId ? "Сохраняем изменения…" : "Добавляем задачу…";
    try {
      await api(
        "/events/" + encodeURIComponent(activeEvent.id) + "/tasks" +
          (taskId ? "/" + encodeURIComponent(taskId) : ""),
        {
          method: taskId ? "PUT" : "POST",
          body:JSON.stringify({
            title:taskTitle.value,
            description:taskDescription.value,
            due_at:toIsoValue(taskDueAt.value),
            assignee_participant_id:taskAssignee.value || null
          })
        }
      );
      resetTaskForm();
      taskStatus.textContent = "Задача сохранена.";
      await loadTasks(activeEvent.id);
    } catch (error) {
      taskStatus.textContent = error.message;
    } finally {
      taskSave.disabled = false;
    }
  });

  const resetExpenseForm = () => {
    expenseForm.reset();
    delete expenseForm.dataset.editingExpense;
    expenseCurrency.value = "EUR";
    expenseDate.value = toLocalDateTimeValue(new Date());
    expenseSave.textContent = "Добавить расход";
    expenseCancel.hidden = true;
  };
  const loadExpenses = async eventId => {
    expensesList.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "expenses-empty";
    loading.textContent = "Загружаем расходы…";
    expensesList.append(loading);
    try {
      const expenses = await api("/events/" + encodeURIComponent(eventId) + "/expenses");
      if (!dialog.open || activeEvent?.id !== eventId) return;
      renderExpenses(expenses, eventId);
    } catch (error) {
      if (!dialog.open || activeEvent?.id !== eventId) return;
      expensesList.replaceChildren();
      const message = document.createElement("p");
      message.className = "expenses-empty";
      message.textContent = error.message;
      expensesList.append(message);
    }
  };
  const renderExpenses = (expenses, eventId) => {
    expensesCount.textContent = String(expenses.length);
    expensesList.replaceChildren();
    if (!expenses.length) {
      const empty = document.createElement("p");
      empty.className = "expenses-empty";
      empty.textContent = "Пока нет расходов. Добавьте первую запись.";
      expensesList.append(empty);
      return;
    }
    expenses.forEach(expense => {
      const card = document.createElement("article");
      card.className = "expense-card";
      const main = document.createElement("div");
      main.className = "expense-main";
      const title = document.createElement("strong");
      title.className = "expense-title";
      title.textContent = expense.title;
      const amount = document.createElement("strong");
      amount.className = "expense-amount";
      amount.textContent = expense.amount + " " + expense.currency;
      const details = document.createElement("p");
      details.className = "expense-meta";
      details.textContent = (expense.paid_by_name || "Плательщик не указан") +
        " · " + (formatDate(expense.incurred_at) || "");
      main.append(title, amount, details);
      if (expense.description) {
        const description = document.createElement("p");
        description.className = "expense-description";
        description.textContent = expense.description;
        main.append(description);
      }
      const actions = document.createElement("div");
      actions.className = "expense-actions";
      const edit = document.createElement("button");
      edit.type = "button";
      edit.textContent = "Изменить";
      edit.addEventListener("click", () => {
        expenseForm.dataset.editingExpense = expense.id;
        expenseTitle.value = expense.title;
        expenseDescription.value = expense.description || "";
        expenseAmount.value = expense.amount;
        expenseCurrency.value = expense.currency;
        expensePayer.value = expense.paid_by_participant_id || "";
        expenseDate.value = toLocalDateTimeValue(new Date(expense.incurred_at));
        expenseSave.textContent = "Сохранить изменения";
        expenseCancel.hidden = false;
        expenseStatus.textContent = "";
        expenseTitle.focus();
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "expense-delete";
      remove.textContent = "Удалить";
      remove.addEventListener("click", async () => {
        if (!window.confirm("Удалить этот расход?")) return;
        remove.disabled = true;
        try {
          await api("/events/" + encodeURIComponent(eventId) + "/expenses/" + encodeURIComponent(expense.id), {method:"DELETE"});
          await loadExpenses(eventId);
        } catch (error) {
          expenseStatus.textContent = error.message;
          remove.disabled = false;
        }
      });
      actions.append(edit, remove);
      card.append(main, actions);
      expensesList.append(card);
    });
  };
  expenseCancel.addEventListener("click", () => {
    resetExpenseForm();
    expenseStatus.textContent = "";
  });
  expenseForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!activeEvent || !expensePayer.value) {
      expenseStatus.textContent = "Выберите участника, который оплатил расход.";
      return;
    }
    const expenseId = expenseForm.dataset.editingExpense;
    expenseSave.disabled = true;
    expenseStatus.textContent = expenseId ? "Сохраняем изменения…" : "Добавляем расход…";
    try {
      await api(
        "/events/" + encodeURIComponent(activeEvent.id) + "/expenses" +
          (expenseId ? "/" + encodeURIComponent(expenseId) : ""),
        {
          method: expenseId ? "PUT" : "POST",
          body: JSON.stringify({
            title: expenseTitle.value,
            description: expenseDescription.value,
            amount: expenseAmount.value,
            currency: expenseCurrency.value,
            paid_by_participant_id: expensePayer.value,
            incurred_at: toIsoValue(expenseDate.value)
          })
        }
      );
      resetExpenseForm();
      expenseStatus.textContent = "Расход сохранён.";
      await loadExpenses(activeEvent.id);
    } catch (error) {
      expenseStatus.textContent = error.message;
    } finally {
      expenseSave.disabled = false;
    }
  });

  const renderNotifications = result => {
    const items = Array.isArray(result.items) ? result.items : [];
    notificationCount.textContent = String(result.unread_count || 0);
    notificationList.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "notification-empty";
      empty.textContent = "Пока нет уведомлений.";
      notificationList.append(empty);
      return;
    }
    items.forEach(item => {
      const card = document.createElement("article");
      card.className = "notification-item" + (item.read_at ? "" : " unread");
      const heading = document.createElement("strong");
      heading.textContent = item.title;
      const body = document.createElement("p");
      body.textContent = item.body;
      const time = document.createElement("time");
      time.dateTime = item.created_at;
      time.textContent = formatDate(item.created_at) || "";
      card.append(heading, body, time);
      if (!item.read_at) {
        const mark = document.createElement("button");
        mark.type = "button";
        mark.className = "text-button";
        mark.textContent = "Отметить прочитанным";
        mark.addEventListener("click", async () => {
          mark.disabled = true;
          try {
            await api("/notifications/" + encodeURIComponent(item.id) + "/read", {method:"POST",body:"{}"});
            await loadNotifications();
          } catch (error) {
            mark.disabled = false;
            body.textContent = error.message;
          }
        });
        card.append(mark);
      }
      notificationList.append(card);
    });
  };
  const loadNotificationPreferences = async () => {
    const status = document.getElementById("preferences-status");
    try {
      const preferences = await api("/notification-preferences");
      document.getElementById("pref-email").checked = preferences.email_enabled;
      document.getElementById("pref-updates").checked = preferences.event_updates_enabled;
      document.getElementById("pref-participants").checked = preferences.participant_added_enabled;
      document.getElementById("pref-reminders").checked = preferences.reminders_enabled;
    } catch (error) {
      status.textContent = error.message;
    }
  };
  document.getElementById("preferences-save").addEventListener("click", async () => {
    const button = document.getElementById("preferences-save");
    const status = document.getElementById("preferences-status");
    button.disabled = true;
    status.textContent = "Сохраняем…";
    try {
      await api("/notification-preferences", {
        method:"PUT",
        body:JSON.stringify({
          email_enabled:document.getElementById("pref-email").checked,
          event_updates_enabled:document.getElementById("pref-updates").checked,
          participant_added_enabled:document.getElementById("pref-participants").checked,
          reminders_enabled:document.getElementById("pref-reminders").checked
        })
      });
      status.textContent = "Настройки сохранены.";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  const loadNotifications = async () => {
    try {
      renderNotifications(await api("/notifications"));
      await loadNotificationPreferences();
    } catch (error) {
      notificationList.replaceChildren();
      const message = document.createElement("p");
      message.className = "notification-empty";
      message.textContent = error.message;
      notificationList.append(message);
    }
  };
  const loadEventReminders = async eventId => {
    reminderStatus.textContent = "";
    document.querySelectorAll('input[name="reminder-offset"]').forEach(input => {
      input.checked = false;
      input.disabled = true;
    });
    reminderSave.disabled = true;
    try {
      const result = await api("/events/" + encodeURIComponent(eventId) + "/reminders");
      document.querySelectorAll('input[name="reminder-offset"]').forEach(input => {
        input.checked = result.offsets_minutes.includes(Number(input.value));
        input.disabled = false;
      });
    } catch (error) {
      reminderStatus.textContent = error.message;
    } finally {
      reminderSave.disabled = false;
    }
  };
  reminderSave.addEventListener("click", async () => {
    if (!activeEvent) return;
    const offsets = Array.from(document.querySelectorAll('input[name="reminder-offset"]:checked'))
      .map(input => Number(input.value));
    reminderSave.disabled = true;
    reminderStatus.textContent = "Сохраняем…";
    try {
      await api("/events/" + encodeURIComponent(activeEvent.id) + "/reminders", {
        method:"PUT", body:JSON.stringify({offsets_minutes:offsets})
      });
      reminderStatus.textContent = "Настройки напоминаний сохранены.";
    } catch (error) {
      reminderStatus.textContent = error.message;
    } finally {
      reminderSave.disabled = false;
    }
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
  notificationToggle.addEventListener("click", async () => {
    const opening = notificationPanel.hidden;
    notificationPanel.hidden = !opening;
    notificationToggle.setAttribute("aria-expanded", String(opening));
    if (opening) await loadNotifications();
  });
  document.getElementById("notification-close").addEventListener("click", () => {
    notificationPanel.hidden = true;
    notificationToggle.setAttribute("aria-expanded", "false");
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