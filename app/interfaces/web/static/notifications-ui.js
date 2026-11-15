(() => {
  const create = ({ api, formatDate, getActiveEvent }) => {
    const notificationToggle = document.getElementById("notification-toggle");
    const notificationPanel = document.getElementById("notification-panel");
    const notificationList = document.getElementById("notification-list");
    const notificationCount = document.getElementById("notification-count");
    const reminderSave = document.getElementById("reminder-save");
    const reminderStatus = document.getElementById("reminder-status");

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
      const telegramToggle = document.getElementById("pref-telegram");
      const telegramLink = document.getElementById("telegram-bot-link");
      const telegramStatus = document.getElementById("telegram-preference-status");
      telegramToggle.checked = preferences.telegram_enabled;
      telegramToggle.disabled = !preferences.telegram_available;
      telegramLink.hidden = !preferences.telegram_available;
      if (preferences.telegram_available) {
        telegramLink.href = preferences.telegram_bot_url;
        telegramStatus.textContent = "Откройте бота, нажмите Start, затем включите уведомления.";
      } else if (!preferences.telegram_linked) {
        telegramStatus.textContent = "Сначала подключите Telegram к аккаунту Gatherly.";
      } else {
        telegramStatus.textContent = "Telegram-уведомления пока не настроены на сервере.";
      }
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
          reminders_enabled:document.getElementById("pref-reminders").checked,
          telegram_enabled:document.getElementById("pref-telegram").checked
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
    const activeEvent = getActiveEvent();
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

    return { loadNotifications, loadEventReminders };
  };

  window.GatherlyNotificationsUI = { create };
})();
