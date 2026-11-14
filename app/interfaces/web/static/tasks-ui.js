(() => {
  const create = ({
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
    getActiveEvent
  }) => {
    const resetTaskForm = () => {
    taskForm.reset();
    delete taskForm.dataset.editingTask;
    taskSave.textContent = "Добавить задачу";
    taskCancel.hidden = true;
    taskStatus.textContent = "";
  };
    const loadTasks = async eventId => {
    tasksList.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "tasks-empty";
    loading.textContent = "Загружаем задачи…";
    tasksList.append(loading);
    try {
      const tasks = await api("/events/" + encodeURIComponent(eventId) + "/tasks");
      if (!dialog.open || getActiveEvent()?.id !== eventId) return;
      renderTasks(tasks, eventId);
    } catch (error) {
      if (!dialog.open || getActiveEvent()?.id !== eventId) return;
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
    if (!getActiveEvent() || !taskTitle.value.trim()) return;
    const taskId = taskForm.dataset.editingTask;
    taskSave.disabled = true;
    taskStatus.textContent = taskId ? "Сохраняем изменения…" : "Добавляем задачу…";
    try {
      await api(
        "/events/" + encodeURIComponent(getActiveEvent().id) + "/tasks" +
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
      await loadTasks(getActiveEvent().id);
    } catch (error) {
      taskStatus.textContent = error.message;
    } finally {
      taskSave.disabled = false;
    }
  });
    return { resetTaskForm, loadTasks };
  };
  window.GatherlyTasksUI = { create };
})();