(() => {
  const create = ({
    participantsList,
    participantsCount,
    taskAssignee,
    expensePayer,
    participantStatus,
    api,
    loadParticipants,
    formatDate,
    getParticipantsBusy,
    setParticipantsBusy,
    setEventParticipants
  }) => {
    const renderParticipants = (list, eventId) => {
    setEventParticipants(list);
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
        if (getParticipantsBusy() || !window.confirm("Удалить участника «" + participant.display_name + "»?")) return;
        setParticipantsBusy(true);
        remove.disabled = true;
        participantStatus.textContent = "";
        try {
          await api("/events/" + encodeURIComponent(eventId) + "/participants/" + encodeURIComponent(participant.id), {method:"DELETE"});
          await loadParticipants(eventId);
        } catch (error) {
          participantStatus.textContent = error.message;
          remove.disabled = false;
        } finally {
          setParticipantsBusy(false);
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
        if (getParticipantsBusy()) return;
        const targetEmail = email.value.trim();
        if (!targetEmail) {
          email.focus();
          return;
        }
        setParticipantsBusy(true);
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
          setParticipantsBusy(false);
          inviteButton.disabled = false;
          inviteButton.textContent = "Пригласить";
        }
      });
      inviteForm.append(email, inviteButton, inviteStatus);
      row.append(main, inviteForm);
      participantsList.append(row);
    });
  };
    return { renderParticipants };
  };
  window.GatherlyParticipantsUI = { create };
})();