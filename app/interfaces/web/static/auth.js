(() => {
  const $ = (selector) => document.querySelector(selector);
  const forms = {
    login: $("#login-form"),
    register: $("#register-form"),
    reset: $("#reset-form"),
    verification: $("#verification-form")
  };
  const tabs = $("#auth-tabs");
  const footnote = $("#auth-footnote");
  const signedIn = $("#signed-in-panel");
  const status = $("#auth-status");
  const title = $("#panel-title");
  const description = $("#panel-description");

  const messages = {
    login: ["С возвращением", "Войдите, чтобы продолжить в Gatherly."],
    register: ["Создать аккаунт", "Зарегистрируйтесь, чтобы собирать людей и планы в одном месте."],
    reset: ["Восстановление доступа", "Укажите почту, и мы отправим инструкции, если аккаунт существует."],
    verification: ["Подтвердить почту", "Запросите новое письмо, если ещё не подтвердили адрес."]
  };

  function setStatus(message, kind = "") {
    status.textContent = message;
    status.className = "auth-status" + (kind ? " " + kind : "");
  }

  function showPanel(name) {
    Object.entries(forms).forEach(([key, form]) => { form.hidden = key !== name; });
    tabs.hidden = name === "reset" || name === "verification";
    footnote.hidden = name === "reset" || name === "verification";
    signedIn.hidden = true;
    title.textContent = messages[name][0];
    description.textContent = messages[name][1];
    document.querySelectorAll(".auth-tab").forEach((tab) => {
      const active = tab.dataset.mode === name;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    setStatus("");
  }

  function showSignedIn() {
    Object.values(forms).forEach((form) => { form.hidden = true; });
    tabs.hidden = true;
    footnote.hidden = true;
    signedIn.hidden = false;
    title.textContent = "Добро пожаловать";
    description.textContent = "Ваш аккаунт Gatherly готов к работе.";
    setStatus("");
  }

  async function request(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return response;
  }

  function friendlyFailure(response, fallback) {
    if (response.status === 429) return "Слишком много попыток. Подождите немного и попробуйте снова.";
    if (response.status === 422) return "Проверьте введённые данные и требования к паролю.";
    if (response.status >= 500) return "Сервис временно недоступен. Попробуйте позже.";
    return fallback;
  }

  function setBusy(form, busy, label) {
    const button = form.querySelector('button[type="submit"]');
    if (!button) return;
    if (busy) {
      button.dataset.originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = label;
    } else {
      button.disabled = false;
      if (button.dataset.originalLabel) button.textContent = button.dataset.originalLabel;
      delete button.dataset.originalLabel;
    }
  }

  document.querySelectorAll("[data-mode]").forEach((tab) => {
    tab.addEventListener("click", () => showPanel(tab.dataset.mode));
  });
  document.querySelectorAll("[data-panel]").forEach((button) => {
    button.addEventListener("click", () => showPanel(button.dataset.panel));
  });

  forms.login.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = $("#login-email").value.trim();
    const password = $("#login-password").value;
    setBusy(forms.login, true, "Входим…");
    setStatus("");
    try {
      const response = await request("/auth/login", { email, password });
      if (!response.ok) {
        setStatus(response.status === 401
          ? "Не удалось войти. Проверьте почту и пароль."
          : friendlyFailure(response, "Не удалось выполнить вход. Попробуйте ещё раз."), "error");
        return;
      }
      forms.login.reset();
      window.location.replace("/");
    } catch (_) {
      setStatus("Нет соединения с сервером. Проверьте интернет и повторите попытку.", "error");
    } finally {
      setBusy(forms.login, false);
    }
  });

  forms.register.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = $("#register-email").value.trim();
    const password = $("#register-password").value;
    const confirm = $("#register-confirm").value;
    if (password !== confirm) {
      setStatus("Пароли не совпадают.", "error");
      $("#register-confirm").focus();
      return;
    }
    if (password.length < 12) {
      setStatus("Пароль должен содержать не менее 12 символов.", "error");
      return;
    }
    setBusy(forms.register, true, "Создаём аккаунт…");
    setStatus("");
    try {
      const response = await request("/auth/register", { email, password });
      if (!response.ok) {
        setStatus(friendlyFailure(response, "Не удалось начать регистрацию. Попробуйте позже."), "error");
        return;
      }
      forms.register.reset();
      setStatus("Если регистрацию можно завершить, на указанную почту придёт письмо для подтверждения. Проверьте также папку «Спам».", "success");
    } catch (_) {
      setStatus("Нет соединения с сервером. Проверьте интернет и повторите попытку.", "error");
    } finally {
      setBusy(forms.register, false);
    }
  });

  forms.reset.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = $("#reset-email").value.trim();
    setBusy(forms.reset, true, "Отправляем…");
    setStatus("");
    try {
      const response = await request("/auth/password-reset/request", { email });
      if (!response.ok) {
        setStatus(friendlyFailure(response, "Не удалось отправить запрос. Попробуйте позже."), "error");
        return;
      }
      forms.reset.reset();
      setStatus("Если для этой почты есть подходящий аккаунт, инструкции по восстановлению уже отправлены.", "success");
    } catch (_) {
      setStatus("Нет соединения с сервером. Проверьте интернет и повторите попытку.", "error");
    } finally {
      setBusy(forms.reset, false);
    }
  });

  forms.verification.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = $("#verification-email").value.trim();
    setBusy(forms.verification, true, "Отправляем…");
    setStatus("");
    try {
      const response = await request("/auth/verification/request", { email });
      if (!response.ok) {
        setStatus(friendlyFailure(response, "Не удалось отправить запрос. Попробуйте позже."), "error");
        return;
      }
      forms.verification.reset();
      setStatus("Если аккаунт с таким адресом ещё не подтверждён, письмо будет отправлено.", "success");
    } catch (_) {
      setStatus("Нет соединения с сервером. Проверьте интернет и повторите попытку.", "error");
    } finally {
      setBusy(forms.verification, false);
    }
  });

  $("#logout-button").addEventListener("click", async () => {
    const button = $("#logout-button");
    button.disabled = true;
    try {
      const response = await request("/auth/logout", {});
      if (response.ok) {
        showPanel("login");
        setStatus("Вы вышли из аккаунта.", "success");
      } else {
        setStatus("Не удалось завершить сессию. Попробуйте ещё раз.", "error");
      }
    } catch (_) {
      setStatus("Нет соединения с сервером. Попробуйте ещё раз.", "error");
    } finally {
      button.disabled = false;
    }
  });

  fetch("/auth/me", { credentials: "same-origin" })
    .then((response) => {
      if (response.ok) window.location.replace("/");
    })
    .catch(() => {});
})();