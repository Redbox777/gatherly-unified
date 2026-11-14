(() => {
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
  window.GatherlyApiClient = { request: api };
})();