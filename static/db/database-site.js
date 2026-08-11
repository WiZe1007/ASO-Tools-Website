(() => {
  "use strict";

  const config = window.DATABASE_CONFIG || {};
  const databases = Array.isArray(config.databases) && config.databases.length
    ? config.databases
    : [{ key: "wwa", label: "WWA DB", title: "WWA Apps Database", configured: false }];
  const state = {
    apps: [],
    expandedRow: null,
    loading: false,
    editingApp: null,
    databaseKey: "wwa",
    loadSequence: 0,
  };

  const $ = (selector) => document.querySelector(selector);
  const elements = {
    body: document.body,
    themeButton: $("#themeButton"),
    addButton: $("#addAppButton"),
    refreshButton: $("#refreshButton"),
    searchInput: $("#searchInput"),
    statusFilter: $("#statusFilter"),
    enabledFilter: $("#enabledFilter"),
    tableBody: $("#appsTableBody"),
    loadingState: $("#loadingState"),
    emptyState: $("#emptyState"),
    resultCount: $("#resultCount"),
    lastUpdated: $("#lastUpdated"),
    modal: $("#appModal"),
    closeModalButton: $("#closeModalButton"),
    cancelModalButton: $("#cancelModalButton"),
    form: $("#appForm"),
    formError: $("#formError"),
    appInputField: $("#appInputField"),
    appInput: $("#appInput"),
    appName: $("#appName"),
    appOwner: $("#appOwner"),
    appStatus: $("#appStatus"),
    appTypeInputs: [...document.querySelectorAll('input[name="appType"]')],
    appEnabled: $("#appEnabled"),
    appNotes: $("#appNotes"),
    editRowIndex: $("#editRowIndex"),
    expectedAppId: $("#expectedAppId"),
    readonlyBundle: $("#readonlyBundle"),
    readonlyBundleValue: $("#readonlyBundleValue"),
    modalEyebrow: $("#modalEyebrow"),
    modalTitle: $("#modalTitle"),
    saveButton: $("#saveAppButton"),
    toastRegion: $("#toastRegion"),
    statTotal: $("#statTotal"),
    statLive: $("#statLive"),
    statWatch: $("#statWatch"),
    statErrors: $("#statErrors"),
    statDisabled: $("#statDisabled"),
    databaseTitle: $("#databaseTitle"),
    databaseDescription: $("#databaseDescription"),
    databaseSwitcher: $("#databaseSwitcher"),
    connectionState: $("#connectionState"),
    connectionStateLabel: $("#connectionStateLabel"),
  };

  const statusLabels = {
    live: "Live",
    watch: "Watch",
    banned: "Banned",
    paused: "Paused",
  };
  const appTypeLabels = {
    placeholder: "Заглушка",
    full: "Повноцінна",
  };

  function selectedAppType() {
    return elements.appTypeInputs.find((input) => input.checked)?.value || "full";
  }

  function setSelectedAppType(value) {
    const normalized = appTypeLabels[value] ? value : "full";
    elements.appTypeInputs.forEach((input) => {
      input.checked = input.value === normalized;
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function icon(name) {
    return `<svg aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
  }

  function formatDate(value) {
    if (!value) return { date: "Ще не перевірявся", time: "" };
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return { date: value, time: "" };
    return {
      date: new Intl.DateTimeFormat("uk-UA", { day: "2-digit", month: "short", year: "numeric" }).format(date),
      time: new Intl.DateTimeFormat("uk-UA", { hour: "2-digit", minute: "2-digit" }).format(date),
    };
  }

  function setTheme(theme) {
    const normalized = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = normalized;
    localStorage.setItem("wwa-db-theme", normalized);
  }

  function initTheme() {
    const saved = localStorage.getItem("wwa-db-theme") || localStorage.getItem("wwa-theme");
    setTheme(saved === "light" ? "light" : "dark");
    elements.themeButton.addEventListener("click", () => {
      setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
    });
  }

  function activeDatabase() {
    return databases.find((database) => database.key === state.databaseKey) || databases[0];
  }

  function databaseApiPath(rowIndex = null) {
    const base = `/api/databases/${encodeURIComponent(state.databaseKey)}/apps`;
    return rowIndex === null ? base : `${base}/${rowIndex}`;
  }

  function renderDatabaseContext() {
    const database = activeDatabase();
    state.databaseKey = database.key;
    elements.databaseTitle.textContent = database.title;
    elements.databaseDescription.textContent = database.key === "s"
      ? "Додавай Google Play застосунки, призначай відповідальних і контролюй стан перевірок другої команди."
      : "Додавай Google Play застосунки, призначай відповідальних і контролюй стан перевірок команди WWA.";
    elements.connectionState.classList.toggle("is-warning", !database.configured);
    elements.connectionStateLabel.textContent = database.configured ? "Google Sheets connected" : "Configuration required";
    document.title = database.title;
    document.querySelectorAll("[data-database-key]").forEach((button) => {
      const active = button.dataset.databaseKey === database.key;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function initDatabase() {
    const saved = localStorage.getItem("wwa-active-database");
    if (databases.some((database) => database.key === saved)) state.databaseKey = saved;
    else state.databaseKey = databases[0].key;
    renderDatabaseContext();
  }

  function selectDatabase(databaseKey) {
    if (databaseKey === state.databaseKey || !databases.some((database) => database.key === databaseKey)) return;
    closeModal(true);
    state.databaseKey = databaseKey;
    state.apps = [];
    state.expandedRow = null;
    elements.searchInput.value = "";
    elements.statusFilter.value = "all";
    elements.enabledFilter.value = "active";
    localStorage.setItem("wwa-active-database", databaseKey);
    renderDatabaseContext();
    render();
    loadApps();
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body) headers["Content-Type"] = "application/json";
    if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = config.csrfToken;
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      payload = { message: "Сервер повернув некоректну відповідь." };
    }
    if (!response.ok || payload.ok === false) {
      const error = new Error(payload.message || "Не вдалося виконати запит.");
      error.code = payload.error || `HTTP_${response.status}`;
      throw error;
    }
    return payload;
  }

  function showToast(message, type = "success") {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.innerHTML = `<i>${type === "error" ? "!" : "✓"}</i><strong>${escapeHtml(message)}</strong><button type="button" aria-label="Закрити">×</button>`;
    elements.toastRegion.append(toast);
    const remove = () => toast.remove();
    toast.querySelector("button").addEventListener("click", remove);
    window.setTimeout(remove, type === "error" ? 7000 : 4000);
  }

  function updateStats() {
    const enabledApps = state.apps.filter((app) => app.enabled);
    elements.statTotal.textContent = state.apps.length;
    elements.statLive.textContent = enabledApps.filter((app) => app.status === "live").length;
    elements.statWatch.textContent = enabledApps.filter((app) => app.status === "watch").length;
    elements.statErrors.textContent = enabledApps.filter((app) => app.last_error).length;
    elements.statDisabled.textContent = state.apps.filter((app) => !app.enabled).length;
  }

  function filteredApps() {
    const query = elements.searchInput.value.trim().toLowerCase();
    const status = elements.statusFilter.value;
    const enabled = elements.enabledFilter.value;
    return state.apps.filter((app) => {
      if (status !== "all" && app.status !== status) return false;
      if (enabled === "active" && !app.enabled) return false;
      if (enabled === "disabled" && app.enabled) return false;
      if (!query) return true;
      return [app.app_name, app.app_id, app.owner, app.notes, app.last_error]
        .some((value) => String(value || "").toLowerCase().includes(query));
    });
  }

  function renderCountryCodes(codes, kind) {
    if (!codes || !codes.length) return `<span class="country-code">Немає даних</span>`;
    return codes.map((code) => `<span class="country-code ${kind}">${escapeHtml(code)}</span>`).join("");
  }

  function renderDetailRow(app) {
    if (state.expandedRow !== app.row_index) return "";
    return `
      <tr class="detail-row">
        <td colspan="7">
          <div class="detail-panel">
            <div class="detail-group">
              <span>Закриті країни</span>
              <div class="country-list">${renderCountryCodes(app.closed_countries, "closed")}</div>
            </div>
            <div class="detail-group">
              <span>Відкриті країни</span>
              <div class="country-list">${renderCountryCodes(app.open_countries, "")}</div>
            </div>
            <div class="detail-group">
              <span>Нотатки / остання помилка</span>
              <p>${escapeHtml(app.notes || app.last_error || "Додаткової інформації немає.")}</p>
            </div>
          </div>
        </td>
      </tr>`;
  }

  function renderRow(app) {
    const checked = formatDate(app.last_checked_at);
    const ownerName = app.owner ? app.owner.split("@")[0] : "Не призначено";
    const firstLetter = (app.app_name || app.app_id || "A").trim().charAt(0) || "A";
    return `
      <tr class="${app.enabled ? "" : "is-disabled"}" data-row-index="${app.row_index}">
        <td data-label="Додаток">
          <div class="app-cell">
            <span class="app-monogram">${escapeHtml(firstLetter)}</span>
            <span class="app-copy">
              <strong title="${escapeHtml(app.app_name)}">${escapeHtml(app.app_name)}</strong>
              <code title="${escapeHtml(app.app_id)}">${escapeHtml(app.app_id)}</code>
              <span class="app-type-chip ${escapeHtml(app.app_type || "unknown")}">${escapeHtml(app.app_type_label || appTypeLabels[app.app_type] || "Не вказано")}</span>
            </span>
            <a class="external-link" href="${escapeHtml(app.app_url)}" target="_blank" rel="noopener" title="Відкрити Google Play">${icon("external")}</a>
          </div>
        </td>
        <td data-label="Статус"><span class="status-chip ${escapeHtml(app.status)}">${escapeHtml(statusLabels[app.status] || app.status)}</span></td>
        <td data-label="Власник"><div class="owner-cell"><strong title="${escapeHtml(app.owner)}">${escapeHtml(ownerName)}</strong><small>${escapeHtml(app.owner || "Без власника")}</small></div></td>
        <td data-label="Закриті GEO"><span class="geo-count ${app.closed_count ? "has-closed" : ""}">${app.closed_count || 0}</span></td>
        <td data-label="Остання перевірка"><span class="date-cell"><strong>${escapeHtml(checked.date)}</strong><small>${escapeHtml(checked.time)}</small></span></td>
        <td data-label="Моніторинг">
          <label class="table-toggle" title="${app.enabled ? "Вимкнути моніторинг" : "Увімкнути моніторинг"}">
            <input type="checkbox" data-action="toggle" ${app.enabled ? "checked" : ""}>
            <i></i>
          </label>
        </td>
        <td>
          <div class="row-actions">
            <button class="row-button" type="button" data-action="details" title="Деталі">${icon("chevron")}</button>
            <button class="row-button" type="button" data-action="edit" title="Редагувати">${icon("edit")}</button>
          </div>
        </td>
      </tr>${renderDetailRow(app)}`;
  }

  function render() {
    const apps = filteredApps();
    elements.tableBody.innerHTML = apps.map((app) => renderRow(app)).join("");
    elements.emptyState.hidden = state.loading || apps.length > 0;
    elements.resultCount.textContent = `Показано ${apps.length} із ${state.apps.length} записів`;
    updateStats();
  }

  function setLoading(loading) {
    state.loading = loading;
    elements.loadingState.hidden = !loading;
    elements.refreshButton.disabled = loading;
    elements.refreshButton.classList.toggle("is-loading", loading);
    if (loading) elements.resultCount.textContent = "Синхронізація з Google Sheets...";
  }

  async function loadApps({ quiet = false } = {}) {
    const requestId = ++state.loadSequence;
    const requestedDatabase = state.databaseKey;
    setLoading(true);
    try {
      const payload = await api(databaseApiPath());
      if (requestId !== state.loadSequence || requestedDatabase !== state.databaseKey) return;
      state.apps = Array.isArray(payload.apps) ? payload.apps : [];
      const updated = formatDate(payload.updated_at);
      elements.lastUpdated.textContent = `Оновлено: ${updated.date}, ${updated.time}`;
      render();
      if (quiet) showToast("Дані оновлено");
    } catch (error) {
      if (requestId !== state.loadSequence || requestedDatabase !== state.databaseKey) return;
      elements.emptyState.hidden = false;
      elements.emptyState.querySelector("strong").textContent = "Не вдалося завантажити базу";
      elements.emptyState.querySelector("small").textContent = error.message;
      showToast(error.message, "error");
    } finally {
      if (requestId === state.loadSequence && requestedDatabase === state.databaseKey) {
        setLoading(false);
        render();
      }
    }
  }

  function setFormBusy(busy) {
    elements.saveButton.disabled = busy;
    elements.saveButton.querySelector(".button-label").hidden = busy;
    elements.saveButton.querySelector(".button-loading").hidden = !busy;
  }

  function openModal(app = null) {
    state.editingApp = app;
    elements.form.reset();
    elements.formError.hidden = true;
    elements.appEnabled.checked = true;
    elements.appOwner.value = config.currentUserEmail || "";
    elements.appStatus.value = "watch";
    setSelectedAppType("full");
    elements.editRowIndex.value = "";
    elements.expectedAppId.value = "";

    if (app) {
      elements.modalEyebrow.textContent = "Редагування запису";
      elements.modalTitle.textContent = app.app_name || app.app_id;
      elements.appInputField.hidden = true;
      elements.readonlyBundle.hidden = false;
      elements.readonlyBundleValue.textContent = app.app_id;
      elements.editRowIndex.value = app.row_index;
      elements.expectedAppId.value = app.app_id;
      elements.appName.value = app.app_name || "";
      elements.appOwner.value = app.owner || "";
      elements.appStatus.value = statusLabels[app.status] ? app.status : "watch";
      setSelectedAppType(app.app_type);
      elements.appEnabled.checked = Boolean(app.enabled);
      elements.appNotes.value = app.notes || "";
    } else {
      elements.modalEyebrow.textContent = `Новий запис · ${activeDatabase().label}`;
      elements.modalTitle.textContent = `Додати до ${activeDatabase().label}`;
      elements.appInputField.hidden = false;
      elements.readonlyBundle.hidden = true;
    }
    elements.modal.hidden = false;
    document.body.style.overflow = "hidden";
    window.setTimeout(() => (app ? elements.appName : elements.appInput).focus(), 30);
  }

  function closeModal(force = false) {
    if (elements.saveButton.disabled && !force) return;
    elements.modal.hidden = true;
    document.body.style.overflow = "";
    state.editingApp = null;
  }

  async function saveApp(event) {
    event.preventDefault();
    elements.formError.hidden = true;
    const editing = Boolean(state.editingApp);
    if (!editing && !elements.appInput.value.trim()) {
      elements.formError.textContent = "Введи package name або Google Play URL.";
      elements.formError.hidden = false;
      elements.appInput.focus();
      return;
    }
    const payload = {
      app_name: elements.appName.value.trim(),
      owner: elements.appOwner.value.trim(),
      status: elements.appStatus.value,
      app_type: selectedAppType(),
      enabled: elements.appEnabled.checked,
      notes: elements.appNotes.value.trim(),
    };
    if (editing) payload.expected_app_id = elements.expectedAppId.value;
    else payload.app_input = elements.appInput.value.trim();

    setFormBusy(true);
    try {
      const path = editing ? databaseApiPath(state.editingApp.row_index) : databaseApiPath();
      const method = editing ? "PATCH" : "POST";
      const response = await api(path, { method, body: JSON.stringify(payload) });
      if (editing) {
        const index = state.apps.findIndex((app) => app.row_index === response.app.row_index);
        if (index >= 0) state.apps[index] = response.app;
      } else {
        state.apps.push(response.app);
      }
      setFormBusy(false);
      closeModal(true);
      render();
      showToast(editing ? "Зміни збережено" : "Додаток додано до бази");
    } catch (error) {
      elements.formError.textContent = error.message;
      elements.formError.hidden = false;
    } finally {
      setFormBusy(false);
    }
  }

  async function toggleApp(app, input) {
    input.disabled = true;
    try {
      const payload = await api(databaseApiPath(app.row_index), {
        method: "PATCH",
        body: JSON.stringify({ expected_app_id: app.app_id, enabled: input.checked }),
      });
      const index = state.apps.findIndex((item) => item.row_index === app.row_index);
      if (index >= 0) state.apps[index] = payload.app;
      render();
      showToast(input.checked ? "Моніторинг увімкнено" : "Додаток вимкнено без видалення історії");
    } catch (error) {
      input.checked = !input.checked;
      input.disabled = false;
      showToast(error.message, "error");
    }
  }

  function handleTableAction(event) {
    const actionElement = event.target.closest("[data-action]");
    if (!actionElement) return;
    const row = actionElement.closest("tr[data-row-index]");
    if (!row) return;
    const app = state.apps.find((item) => item.row_index === Number(row.dataset.rowIndex));
    if (!app) return;
    const action = actionElement.dataset.action;
    if (action === "edit") openModal(app);
    if (action === "details") {
      state.expandedRow = state.expandedRow === app.row_index ? null : app.row_index;
      render();
    }
    if (action === "toggle") toggleApp(app, actionElement);
  }

  function bindEvents() {
    elements.addButton.addEventListener("click", () => openModal());
    elements.refreshButton.addEventListener("click", () => loadApps({ quiet: true }));
    elements.searchInput.addEventListener("input", render);
    elements.statusFilter.addEventListener("change", render);
    elements.enabledFilter.addEventListener("change", render);
    elements.tableBody.addEventListener("click", handleTableAction);
    elements.tableBody.addEventListener("change", handleTableAction);
    elements.closeModalButton.addEventListener("click", closeModal);
    elements.cancelModalButton.addEventListener("click", closeModal);
    elements.modal.addEventListener("click", (event) => {
      if (event.target === elements.modal) closeModal();
    });
    elements.form.addEventListener("submit", saveApp);
    if (elements.databaseSwitcher) {
      elements.databaseSwitcher.addEventListener("click", (event) => {
        const button = event.target.closest("[data-database-key]");
        if (button) selectDatabase(button.dataset.databaseKey);
      });
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !elements.modal.hidden) closeModal();
    });
  }

  initTheme();
  initDatabase();
  bindEvents();
  loadApps();
})();
