(() => {
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
  window.GatherlyDate = { dateValue, formatDate, formatTime, localInputValue, toIsoValue };
})();