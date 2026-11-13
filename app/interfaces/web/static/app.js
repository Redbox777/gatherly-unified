(() => {
  const sidebar = document.getElementById("sidebar");
  const toggle = document.getElementById("menu-toggle");
  const scrim = document.getElementById("mobile-scrim");
  const grid = document.getElementById("meeting-grid");
  const count = document.getElementById("event-count");
  const summaryCount = document.getElementById("summary-event-count");
  const logout = document.getElementById("logout-button");

  const closeMenu = () => {
    sidebar?.classList.remove("open");
    if (scrim) scrim.hidden = true;
    toggle?.setAttribute("aria-expanded", "false");
    toggle?.setAttribute("aria-label", "Открыть меню");
    toggle?.querySelector("use")?.setAttribute("href", "#icon-menu");
  };

  const openMenu = () => {
    sidebar?.classList.add("open");
    if (scrim) scrim.hidden = false;
    toggle?.setAttribute("aria-expanded", "true");
    toggle?.setAttribute("aria-label", "Закрыть меню");
    toggle?.querySelector("use")?.setAttribute("href", "#icon-close");
  };

  if (sidebar && toggle && scrim) {
    toggle.addEventListener("click", () => {
      if (sidebar.classList.contains("open")) closeMenu();
      else openMenu();
    });
    scrim.addEventListener("click", closeMenu);
    sidebar.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenu();
    });
    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 761px)").matches) closeMenu();
    });
  }

  const showState = (heading, detail) => {
    if (!grid) return;
    grid.replaceChildren();
    const panel = document.createElement("div");
    panel.className = "dashboard-state";
    const title = document.createElement("strong");
    title.textContent = heading;
    const description = document.createElement("span");
    description.className = "state-detail";
    description.textContent = detail;
    panel.append(title, description);
    grid.append(panel);
  };

  const formatDate = (value) => {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return new Intl.DateTimeFormat("ru-RU", {
      day: "numeric", month: "long", year: "numeric"
    }).format(date);
  };

  const formatTime = (value) => {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit", minute: "2-digit"
    }).format(date);
  };

  const renderEvents = (events) => {
    grid.replaceChildren();
    if (count) count.textContent = events.length ? events.length + " событий" : "Пока пусто";
    if (summaryCount) summaryCount.textContent = String(events.length);
    if (!events.length) {
      showState("Пока нет встреч", "Когда вы создадите первое событие, оно появится здесь. Интерфейс создания встреч будет следующим этапом.");
      return;
    }
    events.forEach((event) => {
      const card = document.createElement("article");
      card.className = "meeting-card event-card";
      const top = document.createElement("div");
      top.className = "card-top";
      const date = document.createElement("span");
      const formattedDate = formatDate(event.starts_at);
      date.className = "event-date" + (formattedDate ? "" : " unscheduled");
      date.textContent = formattedDate || "ДАТА НЕ УКАЗАНА";
      top.append(date);
      const symbol = document.createElement("div");
      symbol.className = "meeting-symbol symbol-teal";
      symbol.setAttribute("aria-hidden", "true");
      symbol.textContent = "✦";
      const title = document.createElement("h3");
      title.textContent = event.title || "Без названия";
      const meta = document.createElement("p");
      meta.className = "event-meta";
      meta.textContent = formattedTime(event.starts_at);
      const created = document.createElement("p");
      created.className = "event-created";
      const createdDate = formatDate(event.created_at);
      created.textContent = createdDate ? "Создано " + createdDate : "";
      card.append(top, symbol, title, meta, created);
      grid.append(card);
    });
  };

  const formattedTime = (value) => {
    const time = formatTime(value);
    return time ? "Начало в " + time : "Время не назначено";
  };

  const loadDashboard = async () => {
    try {
      const session = await fetch("/auth/me", { credentials: "same-origin" });
      if (session.status === 401) {
        window.location.replace("/auth.html?next=%2F");
        return;
      }
      if (!session.ok) {
        showState("Не удалось проверить вход", "Обновите страницу. Если проблема повторится, попробуйте войти снова.");
        return;
      }
      const response = await fetch("/events", { credentials: "same-origin" });
      if (response.status === 401) {
        window.location.replace("/auth.html?next=%2F");
        return;
      }
      if (!response.ok) {
        showState("Не удалось загрузить встречи", "Сервер временно не отвечает. Попробуйте обновить страницу.");
        if (count) count.textContent = "Ошибка загрузки";
        return;
      }
      const events = await response.json();
      if (!Array.isArray(events)) throw new Error("Unexpected events response");
      events.sort((a, b) => {
        if (!a.starts_at) return 1;
        if (!b.starts_at) return -1;
        return new Date(a.starts_at) - new Date(b.starts_at);
      });
      renderEvents(events);
    } catch (_) {
      showState("Нет соединения с сервером", "Проверьте подключение и обновите страницу.");
      if (count) count.textContent = "Нет соединения";
    }
  };

  if (logout) {
    logout.addEventListener("click", async () => {
      logout.disabled = true;
      try {
        const response = await fetch("/auth/logout", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        });
        if (response.ok) {
          window.location.replace("/auth.html");
          return;
        }
        logout.disabled = false;
        showState("Не удалось выйти", "Сессия не завершена. Попробуйте ещё раз.");
      } catch (_) {
        logout.disabled = false;
        showState("Нет соединения с сервером", "Не удалось завершить сессию. Проверьте подключение.");
      }
    });
  }

  if (grid) loadDashboard();
})();
