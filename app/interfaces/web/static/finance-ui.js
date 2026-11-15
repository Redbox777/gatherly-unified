(() => {
  const create = ({ api, formatDate, localInputValue, toIsoValue, dialog, getActiveEvent }) => {
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
    const balancesList = document.getElementById("balances-list");
    const financePending = document.getElementById("finance-pending");
    const settlementsList = document.getElementById("settlements-list");
    const settlementsCount = document.getElementById("settlements-count");
    const settlementForm = document.getElementById("settlement-form");
    const settlementCurrency = document.getElementById("settlement-currency");
    const settlementFrom = document.getElementById("settlement-from");
    const settlementTo = document.getElementById("settlement-to");
    const settlementAmount = document.getElementById("settlement-amount");
    const settlementDate = document.getElementById("settlement-date");
    const settlementNote = document.getElementById("settlement-note");
    const settlementStatus = document.getElementById("settlement-status");
    const settlementSave = document.getElementById("settlement-save");
    let currentFinanceBalances = [];
    let currentFinanceEventId = null;

  const resetExpenseForm = () => {
    expenseForm.reset();
    delete expenseForm.dataset.editingExpense;
    expenseCurrency.value = "EUR";
    expenseDate.value = localInputValue(new Date());
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
      if (!dialog.open || getActiveEvent()?.id !== eventId) return;
      renderExpenses(expenses, eventId);
    } catch (error) {
      if (!dialog.open || getActiveEvent()?.id !== eventId) return;
      expensesList.replaceChildren();
      const message = document.createElement("p");
      message.className = "expenses-empty";
      message.textContent = error.message;
      expensesList.append(message);
    }
  };
  const renderExpenseSplitEditor = async (container, expense, eventId) => {
    container.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "expenses-empty";
    loading.textContent = "Загружаем распределение…";
    container.append(loading);
    try {
      const current = await api(
        "/events/" + encodeURIComponent(eventId) + "/expenses/" +
          encodeURIComponent(expense.id) + "/split"
      );
      const participants = await api(
        "/events/" + encodeURIComponent(eventId) + "/participants"
      );
      if (!dialog.open || getActiveEvent()?.id !== eventId || !container.isConnected) return;
      container.replaceChildren();
      if ((current.allocations || []).some(item => !item.participant_id)) {
        const warning = document.createElement("p");
        warning.className = "split-help";
        warning.textContent = "В сохранённом распределении есть доля удалённого участника. Историческая запись сохранена; редактор не заменяет её автоматически, чтобы не потерять историю.";
        container.append(warning);
        (current.allocations || []).forEach(item => {
          const line = document.createElement("p");
          line.className = "split-summary";
          line.textContent = item.participant_name + ": " + item.amount + " " + current.currency;
          container.append(line);
        });
        return;
      }
      const form = document.createElement("form");
      form.className = "split-editor-form";
      const modeLabel = document.createElement("label");
      modeLabel.textContent = "Способ распределения";
      const mode = document.createElement("select");
      mode.innerHTML = '<option value="equal">Поровну</option><option value="fixed">Фиксированные суммы</option><option value="weighted">По весам</option>';
      mode.value = current.mode || "equal";
      modeLabel.append(mode);
      const hint = document.createElement("p");
      hint.className = "split-help";
      const rows = document.createElement("div");
      rows.className = "split-participants";
      const status = document.createElement("p");
      status.className = "participant-status";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      const actions = document.createElement("div");
      actions.className = "split-actions";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "text-button";
      cancel.textContent = "Закрыть";
      const save = document.createElement("button");
      save.type = "submit";
      save.className = "secondary-button";
      save.textContent = "Сохранить распределение";
      actions.append(cancel, save);
      const existing = new Map(
        (current.allocations || []).map(item => [item.participant_id, item])
      );
      const controls = new Map();
      const help = () => {
        hint.textContent = mode.value === "equal"
          ? "Отметьте участников, между которыми сумма делится поровну."
          : mode.value === "fixed"
            ? "Укажите сумму для каждого участника. В сумме они должны точно совпасть с расходом (" + expense.amount + " " + expense.currency + ")."
            : "Укажите положительный целый вес. Итоговая сумма распределится пропорционально.";
      };
      participants.forEach(participant => {
        const row = document.createElement("label");
        row.className = "split-participant";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = existing.size ? existing.has(participant.id) : true;
        const name = document.createElement("span");
        name.textContent = participant.display_name;
        const value = document.createElement("input");
        value.type = mode.value === "fixed" ? "number" : "number";
        value.min = mode.value === "fixed" ? "0.01" : "1";
        value.step = mode.value === "fixed" ? "any" : "1";
        value.inputMode = "decimal";
        value.className = "split-value";
        value.setAttribute("aria-label", mode.value === "fixed"
          ? "Сумма для " + participant.display_name
          : "Вес для " + participant.display_name);
        const previous = existing.get(participant.id);
        value.value = mode.value === "fixed"
          ? (previous?.amount || "")
          : mode.value === "weighted" ? (previous?.weight || "1") : "";
        value.hidden = mode.value === "equal";
        checkbox.addEventListener("change", () => {
          value.disabled = !checkbox.checked;
        });
        value.disabled = !checkbox.checked || mode.value === "equal";
        controls.set(participant.id, {checkbox, value});
        row.append(checkbox, name, value);
        rows.append(row);
      });
      const updateMode = () => {
        help();
        controls.forEach(({checkbox, value}) => {
          value.hidden = mode.value === "equal";
          value.min = mode.value === "fixed" ? "0.01" : "1";
          value.step = mode.value === "fixed" ? "any" : "1";
          value.setAttribute("aria-label", mode.value === "fixed" ? "Сумма" : "Вес");
          if (mode.value === "equal") {
            value.value = "";
            value.disabled = true;
          } else {
            value.disabled = !checkbox.checked;
            if (!value.value) value.value = mode.value === "weighted" ? "1" : "";
          }
        });
      };
      mode.addEventListener("change", updateMode);
      help();
      updateMode();
      form.append(modeLabel, hint, rows, status, actions);
      container.append(form);
      cancel.addEventListener("click", () => container.replaceChildren());
      form.addEventListener("submit", async event => {
        event.preventDefault();
        const allocations = [];
        for (const [participantId, control] of controls) {
          if (!control.checkbox.checked) continue;
          const item = {participant_id: participantId};
          if (mode.value === "fixed") {
            if (!control.value.value || Number(control.value.value) <= 0) {
              status.textContent = "Укажите положительную сумму для каждого выбранного участника.";
              control.value.focus();
              return;
            }
            item.amount = control.value.value;
          } else if (mode.value === "weighted") {
            if (!control.value.value || !Number.isInteger(Number(control.value.value)) ||
                Number(control.value.value) <= 0) {
              status.textContent = "Вес должен быть положительным целым числом.";
              control.value.focus();
              return;
            }
            item.weight = Number(control.value.value);
          }
          allocations.push(item);
        }
        if (!allocations.length) {
          status.textContent = "Выберите хотя бы одного участника.";
          return;
        }
        save.disabled = true;
        status.textContent = "Сохраняем распределение…";
        try {
          const result = await api(
            "/events/" + encodeURIComponent(eventId) + "/expenses/" +
              encodeURIComponent(expense.id) + "/split",
            {method:"PUT", body:JSON.stringify({mode:mode.value, allocations})}
          );
          status.textContent = "Распределение сохранено.";
          const summary = document.createElement("p");
          summary.className = "split-summary";
          summary.textContent = result.allocations.map(item =>
            (item.participant_name || "Участник") + ": " + item.amount + " " + result.currency
          ).join(" · ");
          const old = container.querySelector(".split-summary");
          old?.remove();
          form.insertBefore(summary, actions);
        } catch (error) {
          status.textContent = error.message;
        } finally {
          save.disabled = false;
        }
      });
    } catch (error) {
      container.replaceChildren();
      const message = document.createElement("p");
      message.className = "expenses-empty";
      message.textContent = error.message;
      container.append(message);
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
        expenseDate.value = localInputValue(new Date(expense.incurred_at));
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
      const split = document.createElement("button");
      split.type = "button";
      split.textContent = "Распределить";
      const splitPanel = document.createElement("div");
      splitPanel.className = "expense-split-panel";
      splitPanel.hidden = true;
      split.addEventListener("click", () => {
        splitPanel.hidden = !splitPanel.hidden;
        if (!splitPanel.dataset.loaded) {
          splitPanel.dataset.loaded = "true";
          renderExpenseSplitEditor(splitPanel, expense, eventId);
        }
      });
      actions.append(split, edit, remove);
      card.append(main, actions, splitPanel);
      expensesList.append(card);
    });
  };
  expenseCancel.addEventListener("click", () => {
    resetExpenseForm();
    expenseStatus.textContent = "";
  });
  expenseForm.addEventListener("submit", async event => {
    event.preventDefault();
    const activeEvent = getActiveEvent();
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

  const renderEventFinance = (result, settlements, eventId) => {
    currentFinanceBalances = result.balances || [];
    currentFinanceEventId = eventId;
    balancesList.replaceChildren();
    if (!currentFinanceBalances.length) {
      const empty = document.createElement("p");
      empty.className = "expenses-empty";
      empty.textContent = "Пока нет распределённых расходов.";
      balancesList.append(empty);
    } else {
      currentFinanceBalances.forEach(item => {
        const row = document.createElement("div");
        row.className = "balance-row";
        const name = document.createElement("strong");
        name.textContent = item.participant_name;
        const value = document.createElement("span");
        value.className = "balance-value " + (
          item.balance_minor_units > 0 ? "balance-positive" :
          item.balance_minor_units < 0 ? "balance-negative" : "balance-zero"
        );
        const status = item.balance_minor_units > 0 ? "к получению" :
          item.balance_minor_units < 0 ? "к оплате" : "расчёт закрыт";
        value.textContent = item.amount + " " + item.currency + " · " + status;
        row.append(name, value);
        balancesList.append(row);
      });
    }

    financePending.replaceChildren();
    (result.pending_expenses || []).forEach(item => {
      const note = document.createElement("p");
      note.className = "finance-notice";
      note.textContent = "Не распределено: " + item.count + " расход(а/ов) на " +
        item.amount + " " + item.currency + ". Эти суммы пока не входят в балансы.";
      financePending.append(note);
    });
    (result.unresolved || []).forEach(item => {
      const amounts = [
        ["плательщик удалён", item.missing_payer],
        ["доли удалённых участников", item.removed_participant_allocations],
        ["переводы с удалёнными участниками", item.settlements_with_removed_participant]
      ].filter(pair => Number(pair[1]) !== 0);
      if (!amounts.length) return;
      const note = document.createElement("p");
      note.className = "finance-notice";
      note.textContent = "Исторические записи " + item.currency + ": " +
        amounts.map(pair => pair[0] + " — " + pair[1]).join("; ") +
        ". Они сохранены отдельно и не распределены между текущими участниками.";
      financePending.append(note);
    });

    settlementsList.replaceChildren();
    settlementsCount.textContent = String(settlements.length);
    if (!settlements.length) {
      const empty = document.createElement("p");
      empty.className = "expenses-empty";
      empty.textContent = "Переводов пока нет.";
      settlementsList.append(empty);
    } else {
      settlements.forEach(item => {
        const row = document.createElement("article");
        row.className = "settlement-row";
        const parties = document.createElement("strong");
        parties.textContent = item.from_participant_name + " → " + item.to_participant_name;
        const amount = document.createElement("span");
        amount.textContent = item.amount + " " + item.currency;
        const meta = document.createElement("p");
        meta.textContent = (formatDate(item.settled_at) || "") +
          (item.note ? " · " + item.note : "");
        row.append(parties, amount, meta);
        settlementsList.append(row);
      });
    }

    settlementDate.value = localInputValue(new Date());
    const currencies = [...new Set(currentFinanceBalances
      .filter(item => item.balance_minor_units !== 0)
      .map(item => item.currency))].sort();
    settlementCurrency.replaceChildren();
    if (!currencies.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Нет открытых балансов";
      settlementCurrency.append(option);
      settlementForm.querySelectorAll("select, input, button").forEach(control => {
        control.disabled = true;
      });
      return;
    }
    settlementForm.querySelectorAll("select, input, button").forEach(control => {
      control.disabled = false;
    });
    currencies.forEach(currency => {
      const option = document.createElement("option");
      option.value = currency;
      option.textContent = currency;
      settlementCurrency.append(option);
    });

    const updateSettlementParties = () => {
      const currency = settlementCurrency.value;
      const rows = currentFinanceBalances.filter(item => item.currency === currency);
      const debtors = rows.filter(item => item.balance_minor_units < 0);
      const creditors = rows.filter(item => item.balance_minor_units > 0);
      const fill = (select, list, placeholder) => {
        const previous = select.value;
        select.replaceChildren();
        const first = document.createElement("option");
        first.value = "";
        first.textContent = placeholder;
        select.append(first);
        list.forEach(item => {
          const option = document.createElement("option");
          option.value = item.participant_id;
          option.textContent = item.participant_name + " (" + item.amount + " " + currency + ")";
          select.append(option);
        });
        if (list.some(item => item.participant_id === previous)) select.value = previous;
      };
      fill(settlementFrom, debtors, "Выберите должника");
      fill(settlementTo, creditors, "Выберите получателя");
      updateSettlementMaximum();
      settlementSave.disabled = !debtors.length || !creditors.length;
    };
    const updateSettlementMaximum = () => {
      const currency = settlementCurrency.value;
      const from = currentFinanceBalances.find(item =>
        item.currency === currency && item.participant_id === settlementFrom.value
      );
      const to = currentFinanceBalances.find(item =>
        item.currency === currency && item.participant_id === settlementTo.value
      );
      if (from && to) {
        const maxMinor = Math.min(-from.balance_minor_units, to.balance_minor_units);
        const precision = currency === "JPY" ? 0 : currency === "KWD" ? 3 : 2;
        settlementAmount.max = (maxMinor / (10 ** precision)).toFixed(precision);
      } else {
        settlementAmount.removeAttribute("max");
      }
    };
    settlementCurrency.onchange = updateSettlementParties;
    settlementFrom.onchange = updateSettlementMaximum;
    settlementTo.onchange = updateSettlementMaximum;
    updateSettlementParties();
  };

  const loadEventFinance = async eventId => {
    currentFinanceBalances = [];
    currentFinanceEventId = null;
    balancesList.replaceChildren();
    settlementsList.replaceChildren();
    settlementForm.querySelectorAll("select, input, button").forEach(control => {
      control.disabled = true;
    });
    settlementCurrency.replaceChildren();
    settlementFrom.replaceChildren();
    settlementTo.replaceChildren();
    settlementAmount.value = "";
    settlementNote.value = "";
    settlementStatus.textContent = "";
    try {
      const [balances, settlements] = await Promise.all([
        api("/events/" + encodeURIComponent(eventId) + "/balances"),
        api("/events/" + encodeURIComponent(eventId) + "/settlements")
      ]);
      if (!dialog.open || getActiveEvent()?.id !== eventId) return;
      renderEventFinance(balances, settlements, eventId);
    } catch (error) {
      if (!dialog.open || getActiveEvent()?.id !== eventId) return;
      balancesList.textContent = error.message;
      settlementsList.replaceChildren();
    }
  };

  settlementForm.addEventListener("submit", async event => {
    event.preventDefault();
    const eventId = currentFinanceEventId;
    if (
      !eventId ||
      getActiveEvent()?.id !== eventId ||
      !settlementFrom.value ||
      !settlementTo.value
    ) {
      settlementStatus.textContent = "Выберите должника и получателя.";
      return;
    }
    settlementSave.disabled = true;
    settlementStatus.textContent = "Сохраняем перевод…";
    try {
      await api(
        "/events/" + encodeURIComponent(eventId) + "/settlements",
        {
          method: "POST",
          body: JSON.stringify({
            from_participant_id: settlementFrom.value,
            to_participant_id: settlementTo.value,
            amount: settlementAmount.value,
            currency: settlementCurrency.value,
            settled_at: toIsoValue(settlementDate.value),
            note: settlementNote.value
          })
        }
      );
      if (getActiveEvent()?.id !== eventId) return;
      settlementAmount.value = "";
      settlementNote.value = "";
      settlementStatus.textContent = "Перевод записан.";
      await loadEventFinance(eventId);
    } catch (error) {
      if (getActiveEvent()?.id === eventId) {
        settlementStatus.textContent = error.message;
        settlementSave.disabled = false;
      }
    }
  });

    return { resetExpenseForm, loadExpenses, loadEventFinance };
  };

  window.GatherlyFinanceUI = { create };
})();
