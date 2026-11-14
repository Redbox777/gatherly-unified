(() => {
  const create = ({
    notesCount,
    notesList,
    noteContent,
    noteVisibility,
    noteForm,
    noteSave,
    noteStatus,
    api,
    formatDate,
    getActiveEvent,
    loadNotes
  }) => {
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
    if (!getActiveEvent() || !noteContent.value.trim()) return;
    const noteId = noteForm.dataset.editingNote;
    noteSave.disabled = true;
    noteStatus.textContent = noteId ? "Сохраняем изменения…" : "Сохраняем…";
    try {
      await api(
        "/events/" + encodeURIComponent(getActiveEvent().id) + "/notes" +
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
      await loadNotes(getActiveEvent().id);
    } catch (error) {
      noteStatus.textContent = error.message;
    } finally {
      noteSave.disabled = false;
    }
  });
    return { renderNotes };
  };
  window.GatherlyNotesUI = { create };
})();