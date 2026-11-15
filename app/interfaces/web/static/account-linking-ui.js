(() => {
  const openButton = document.getElementById("account-settings-open");
  const dialog = document.getElementById("account-link-dialog");
  if (!openButton || !dialog) return;

  const closeButton = document.getElementById("account-link-close");
  const identitiesBox = document.getElementById("account-identities");
  const emailForm = document.getElementById("account-link-email-form");
  const emailInput = document.getElementById("link-source-email");
  const passwordInput = document.getElementById("link-source-password");
  const emailSubmit = document.getElementById("account-link-email-submit");
  const emailStatus = document.getElementById("account-link-email-status");
  const telegramWidget = document.getElementById("account-link-telegram-widget");
  const telegramStatus = document.getElementById("account-link-telegram-status");
  let widgetUsername = "";
  let widgetLoaded = false;

  const setStatus = (node, message) => { node.textContent = message; };
  const request = async (url, payload) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    return { response, body };
  };

  const friendlyError = (status) => {
    if (status === 401) return "Не удалось подтвердить второй аккаунт. Проверьте данные и повторите попытку.";
    if (status === 403) return "Сессия текущего аккаунта устарела. Выйдите и войдите снова, затем повторите объединение.";
    if (status === 409) return "Объединение невозможно: оба аккаунта уже используют один и тот же способ входа.";
    if (status === 429) return "Слишком много попыток. Подождите немного и повторите.";
    if (status === 422) return "Проверьте введённые данные.";
    return "Не удалось объединить аккаунты. Попробуйте позже.";
  };

  const renderIdentities = (identities) => {
    identitiesBox.replaceChildren();
    if (!Array.isArray(identities) || !identities.length) {
      const empty = document.createElement("p");
      empty.textContent = "Не удалось определить способы входа.";
      identitiesBox.append(empty);
      return;
    }
    identities.forEach((identity) => {
      const row = document.createElement("div");
      row.className = "account-identity-row";
      const title = document.createElement("strong");
      title.textContent = identity.provider === "email" ? "Электронная почта" : "Telegram";
      const detail = document.createElement("span");
      const state = identity.verified ? "Подтверждён" : "Не подтверждён";
      detail.textContent = (identity.identifier ? identity.identifier + " · " : "") + state;
      row.append(title, detail);
      identitiesBox.append(row);
    });
  };

  const loadIdentities = async () => {
    identitiesBox.textContent = "Загружаем способы входа…";
    const response = await fetch("/account/identities", { credentials: "same-origin" });
    if (response.status === 401) {
      window.location.replace("/auth.html?next=%2F");
      return;
    }
    if (!response.ok) throw new Error("Не удалось загрузить способы входа.");
    renderIdentities(await response.json());
  };

  const loadTelegramWidget = async () => {
    if (widgetLoaded) return;
    widgetLoaded = true;
    telegramWidget.textContent = "Загружаем официальный виджет Telegram…";
    try {
      const response = await fetch("/account/link/config", { credentials: "same-origin" });
      if (!response.ok) throw new Error("config");
      const config = await response.json();
      widgetUsername = String(config.telegram_bot_username || "");
      if (!widgetUsername) {
        telegramWidget.textContent = "Связывание через Telegram пока не настроено на сервере.";
        return;
      }
      const script = document.createElement("script");
      script.async = true;
      script.src = "https://telegram.org/js/telegram-widget.js?22";
      script.dataset.telegramLogin = widgetUsername;
      script.dataset.size = "large";
      script.dataset.userpic = "false";
      script.dataset.requestAccess = "write";
      script.dataset.lang = "ru";
      script.dataset.onauth = "window.GatherlyAccountLink.onTelegramAuth(user)";
      telegramWidget.replaceChildren(script);
    } catch (_) {
      telegramWidget.textContent = "Не удалось загрузить настройки Telegram. Попробуйте обновить страницу.";
    }
  };

  const onTelegramAuth = async (user) => {
    if (!user || !user.id || !user.auth_date || !user.hash) {
      setStatus(telegramStatus, "Telegram не вернул необходимые данные подтверждения.");
      return;
    }
    setStatus(telegramStatus, "Проверяем Telegram-аккаунт и переносим данные…");
    try {
      const signedPayload = Object.fromEntries(
        Object.entries(user)
          .filter(([key, value]) => key !== "hash" && value !== undefined && value !== null)
          .map(([key, value]) => [key, String(value)])
      );
      signedPayload.hash = String(user.hash);
      const { response } = await request("/account/link/telegram", signedPayload);
      if (!response.ok) {
        setStatus(telegramStatus, friendlyError(response.status));
        return;
      }
      setStatus(telegramStatus, "Telegram-аккаунт успешно объединён с текущим.");
      await loadIdentities();
    } catch (_) {
      setStatus(telegramStatus, "Ошибка соединения. Проверьте интернет и повторите попытку.");
    }
  };

  window.GatherlyAccountLink = { onTelegramAuth };

  emailForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (emailSubmit.disabled) return;
    emailSubmit.disabled = true;
    emailSubmit.textContent = "Проверяем…";
    setStatus(emailStatus, "");
    try {
      const { response } = await request("/account/link/email", {
        email: emailInput.value.trim(),
        password: passwordInput.value
      });
      if (!response.ok) {
        setStatus(emailStatus, friendlyError(response.status));
        return;
      }
      emailForm.reset();
      setStatus(emailStatus, "Email-аккаунт успешно объединён с текущим.");
      await loadIdentities();
    } catch (_) {
      setStatus(emailStatus, "Ошибка соединения. Проверьте интернет и повторите попытку.");
    } finally {
      emailSubmit.disabled = false;
      emailSubmit.textContent = "Проверить и объединить";
    }
  });

  openButton.addEventListener("click", async () => {
    dialog.showModal();
    setStatus(emailStatus, "");
    setStatus(telegramStatus, "");
    try { await loadIdentities(); } catch (_) {
      identitiesBox.textContent = "Не удалось загрузить способы входа. Обновите страницу.";
    }
    await loadTelegramWidget();
  });
  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
})();