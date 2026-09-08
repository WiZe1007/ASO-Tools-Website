(function () {
  "use strict";

  const ready = (fn) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
    } else {
      fn();
    }
  };

  const byId = (id) => document.getElementById(id);
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const safeStorage = {
    get(key) { try { return window.localStorage.getItem(key); } catch (_) { return null; } },
    set(key, value) { try { window.localStorage.setItem(key, value); } catch (_) {} },
  };
  const THEME_STORAGE_KEY = "wwa.aso.tools.theme";

  function storedTheme() {
    try {
      const value = window.localStorage.getItem(THEME_STORAGE_KEY);
      return value === "dark" ? "dark" : "light";
    } catch (_) {
      return "light";
    }
  }

  function saveTheme(theme) {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch (_) {
      // Ignore storage failures; the current page still switches theme.
    }
  }

  function applyTheme(theme) {
    const nextTheme = theme === "light" ? "light" : "dark";
    const isLight = nextTheme === "light";

    document.documentElement.dataset.theme = nextTheme;
    if (document.body) {
      document.body.classList.toggle("theme-light", isLight);
      document.body.classList.toggle("theme-dark", !isLight);
    }

    qsa("[data-theme-toggle]").forEach((button) => {
      const label = button.querySelector("[data-theme-label]");
      if (label) label.textContent = isLight ? "Темна тема" : "Світла тема";
      else button.textContent = isLight ? "☾" : "☼";
      button.setAttribute("aria-label", isLight ? "Switch to dark theme" : "Switch to light theme");
      button.setAttribute("title", isLight ? "Dark theme" : "Light theme");
      button.setAttribute("aria-pressed", String(isLight));
    });
  }

  document.documentElement.dataset.theme = storedTheme();

  function installThemeToggle() {
    applyTheme(storedTheme());
    qsa("[data-theme-toggle]").forEach((button) => {
      if (button.dataset.themeReady === "1") return;
      button.dataset.themeReady = "1";
      button.addEventListener("click", () => {
        const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
        saveTheme(nextTheme);
        const update = () => applyTheme(nextTheme);
        if (window.wwaMotion) window.wwaMotion.changeTheme(update, button);
        else update();
      });
    });
  }

  function installContactModal() {
    const helpButtons = qsa('.nav-utility[aria-label="Help"]');
    if (!helpButtons.length || window.__wwaContactModalInstalled) return;
    window.__wwaContactModalInstalled = true;

    const modal = document.createElement("div");
    modal.className = "contact-modal";
    modal.hidden = true;
    modal.innerHTML = `
      <div class="contact-modal__backdrop" data-contact-close></div>
      <section class="contact-modal__panel" role="dialog" aria-modal="true" aria-labelledby="contactModalTitle">
        <button class="contact-modal__close" type="button" aria-label="Закрити" data-contact-close>×</button>
        <p class="contact-modal__eyebrow">WWA Tools</p>
        <h2 id="contactModalTitle">Контакти</h2>
        <p class="contact-modal__text">Пропозиції та допомогу можна дізнатися за цими контактами</p>
        <div class="contact-modal__list">
          <a class="contact-modal__item" href="https://t.me/Nemofresh_publisher" target="_blank" rel="noopener noreferrer">
            <span>Телеграм</span>
            <b>@Nemofresh_publisher</b>
          </a>
          <a class="contact-modal__item" href="mailto:bohdan.m.publish@wildwildgroup.com">
            <span>Пошта</span>
            <b>bohdan.m.publish@wildwildgroup.com</b>
          </a>
        </div>
      </section>
    `;
    document.body.appendChild(modal);

    const closeButtons = qsa("[data-contact-close]", modal);
    let lastActiveElement = null;

    const openModal = () => {
      lastActiveElement = document.activeElement;
      const drawer = byId("drawer");
      if (drawer?.contains(lastActiveElement)) {
        drawer.style.display = "none";
        lastActiveElement = byId("hamb");
      }
      modal.hidden = false;
      document.body.classList.add("contact-modal-open");
      qs(".contact-modal__close", modal)?.focus({ preventScroll: true });
    };

    const closeModal = () => {
      modal.hidden = true;
      document.body.classList.remove("contact-modal-open");
      if (lastActiveElement && typeof lastActiveElement.focus === "function") {
        lastActiveElement.focus({ preventScroll: true });
      }
    };

    helpButtons.forEach((button) => {
      button.dataset.contactReady = "1";
      button.setAttribute("title", "Контакти");
      button.setAttribute("aria-haspopup", "dialog");
      button.addEventListener("click", openModal);
    });

    closeButtons.forEach((button) => button.addEventListener("click", closeModal));
    document.addEventListener("keydown", (event) => {
      if (modal.hidden) return;
      if (event.key === "Escape") closeModal();
      if (event.key === "Tab") {
        const focusable = qsa('button, a[href]', modal);
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
  }

  function installToast() {
    if (window.__wwaAsoToastInstalled) return;
    window.__wwaAsoToastInstalled = true;

    const toast = document.createElement("div");
    toast.id = "asoToast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    toast.innerHTML = '<span class="toast-dot"></span><span class="toast-text"></span>';
    document.body.appendChild(toast);

    let timer = 0;
    const nativeAlert = window.alert.bind(window);
    window.showAsoToast = (message) => {
      const text = String(message || "").trim();
      if (!text) return;
      qs(".toast-text", toast).textContent = text;
      toast.classList.add("show");
      window.clearTimeout(timer);
      timer = window.setTimeout(() => toast.classList.remove("show"), 3600);
    };

    window.alert = (message) => {
      if (!document.body) {
        nativeAlert(message);
        return;
      }
      window.showAsoToast(message);
    };
  }

  function wrapLoadingState() {
    if (typeof window.setLoading !== "function" || window.setLoading.__wwaEnhanced) return;

    const original = window.setLoading;
    const enhanced = function (isLoading, text) {
      original.call(window, isLoading, text);

      document.body.classList.toggle("is-loading", Boolean(isLoading));

      const run = byId("run");
      if (run) {
        run.disabled = Boolean(isLoading);
        run.setAttribute("aria-busy", String(Boolean(isLoading)));
      }

      const status = byId("statusText");
      if (status) status.setAttribute("aria-live", "polite");

      if (!isLoading) {
        window.requestAnimationFrame(scrollResultsIntoView);
      }
    };

    enhanced.__wwaEnhanced = true;
    window.setLoading = enhanced;
  }

  function scrollResultsIntoView() {
    const candidates = ["appmagicWidget", "tableWrap", "result"]
      .map(byId)
      .filter(Boolean)
      .filter((el) => {
        const style = window.getComputedStyle(el);
        return style.display !== "none" && el.textContent.trim().length > 0;
      });

    const target = candidates[0];
    if (!target) return;

    const rect = target.getBoundingClientRect();
    if (rect.top > window.innerHeight - 80) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function enhancePersistentInputs() {
    const page = document.body.dataset.page || "site";
    const storagePrefix = "wwa.aso.tools";

    const persistTextInput = (id) => {
      const el = byId(id);
      if (!el) return;
      const key = `${storagePrefix}.${page}.${id}`;
      const saved = safeStorage.get(key);
      if (saved !== null && !el.value) {
        el.value = saved;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
      el.addEventListener("input", () => safeStorage.set(key, el.value));
    };

    ["url", "threshold", "country"].forEach(persistTextInput);

    ["showAll", "showOnlyClosed"].forEach((id) => {
      const el = byId(id);
      if (!el) return;
      const key = `${storagePrefix}.${page}.${id}`;
      const saved = safeStorage.get(key);
      if (saved !== null) el.checked = saved === "1";
      el.addEventListener("change", () => safeStorage.set(key, el.checked ? "1" : "0"));
    });

    const modeKey = `${storagePrefix}.${page}.mode`;
    const modeIds = [
      ["modeToolbox", "toolbox"],
      ["modeFull", "full"],
      ["modeAppMagic", "appmagic"],
    ];
    const savedMode = safeStorage.get(modeKey);
    if (savedMode && typeof window.syncModeCheckboxes === "function") {
      window.syncModeCheckboxes(savedMode);
    }
    modeIds.forEach(([id, mode]) => {
      const el = byId(id);
      if (!el) return;
      el.addEventListener("change", () => {
        if (el.checked) safeStorage.set(modeKey, mode);
        refreshModeVisualState();
      });
    });
    refreshModeVisualState();
  }

  function refreshModeVisualState() {
    qsa(".mode-item").forEach((item) => {
      const input = qs("input", item);
      item.classList.toggle("selected", Boolean(input && input.checked));
      item.setAttribute("aria-checked", String(Boolean(input && input.checked)));
      item.tabIndex = input?.checked ? 0 : -1;
    });
  }

  function enhanceRunOnEnter() {
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.metaKey || event.ctrlKey) return;
      const target = event.target;
      if (!(target instanceof HTMLInputElement) || !target.classList.contains("input")) return;
      const run = byId("run");
      if (!run || run.disabled) return;
      event.preventDefault();
      run.click();
    });
  }

  function enhanceModeSegments() {
    qsa(".mode-item").forEach((item) => {
      item.setAttribute("role", "radio");
      const checkbox = qs("input", item);
      if (checkbox) { checkbox.tabIndex = -1; checkbox.setAttribute("aria-hidden", "true"); }
      item.closest(".mode-box")?.setAttribute("role", "radiogroup");
      item.closest(".mode-box")?.setAttribute("aria-label", "Режим перевірки");

      const activate = (event) => {
        const input = qs("input", item);
        if (!input || event.target === input) return;
        input.click();
        refreshModeVisualState();
      };

      item.addEventListener("click", activate);
      item.addEventListener("keydown", (event) => {
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
          event.preventDefault();
          const items = qsa(".mode-item", item.parentElement);
          const direction = ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1;
          const next = items[(items.indexOf(item) + direction + items.length) % items.length];
          next.focus();
          next.click();
          return;
        }
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        activate(event);
      });
    });
  }

  function enhanceDrawerState() {
    const drawer = byId("drawer");
    const hamburger = byId("hamb");
    if (!drawer || !hamburger) return;
    const drawerManagedPage = ["overview", "live-db", "s-live-db"].includes(document.body?.dataset?.page || "");

    const sync = () => {
      if (window.getComputedStyle(hamburger).display === "none" && drawer.style.display !== "none") {
        drawer.style.display = "none";
      }
      // A discrete exit transition remains painted after logical closure.
      const isOpen = drawer.style.display ? drawer.style.display !== "none" : window.getComputedStyle(drawer).display !== "none";
      drawer.inert = !isOpen;
      drawer.setAttribute("aria-hidden", String(!isOpen));
      if (!isOpen && drawer.contains(document.activeElement)) hamburger.focus({ preventScroll: true });
      const wrap = qs(".wrap");
      document.body.classList.toggle("drawer-open", isOpen);
      hamburger.classList.toggle("is-open", isOpen);
      hamburger.setAttribute("aria-expanded", String(isOpen));
      hamburger.setAttribute("aria-label", isOpen ? "Close menu" : "Open menu");

      if (wrap) wrap.style.paddingTop = "";
    };

    const setDrawerOpen = (open) => {
      drawer.style.display = open ? "block" : "none";
      sync();
    };

    if (drawerManagedPage && hamburger.dataset.drawerClickReady !== "1") {
      hamburger.dataset.drawerClickReady = "1";
      hamburger.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const isOpen = drawer.style.display === "block";
        setDrawerOpen(!isOpen);
      });

      drawer.addEventListener("click", (event) => {
        if (event.target.closest("a")) setDrawerOpen(false);
      });

      document.addEventListener("click", (event) => {
        if (window.getComputedStyle(drawer).display === "none") return;
        if (drawer.contains(event.target) || hamburger.contains(event.target)) return;
        setDrawerOpen(false);
      });

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") setDrawerOpen(false);
      });
    }

    hamburger.setAttribute("aria-controls", "drawer");
    hamburger.setAttribute("aria-expanded", "false");
    new MutationObserver(sync).observe(drawer, { attributes: true, attributeFilter: ["style", "class"] });
    window.addEventListener("resize", sync);
    sync();
  }

  function enhanceTableToolbar() {
    const wrap = byId("tableWrap");
    const table = byId("tbl");
    if (!wrap || !table || qs(".table-toolbar", wrap)) return;

    const toolbar = document.createElement("div");
    toolbar.className = "table-toolbar";
    toolbar.innerHTML = [
      '<div class="table-toolbar-title">Results</div>',
      '<label class="quick-filter-label">',
      '<span>Quick filter</span>',
      '<input class="quick-filter" type="search" autocomplete="off" placeholder="Country, GEO, status..." />',
      "</label>",
      '<div class="table-count" aria-live="polite">0 rows</div>',
    ].join("");

    wrap.insertBefore(toolbar, wrap.firstElementChild);

    const input = qs(".quick-filter", toolbar);
    const count = qs(".table-count", toolbar);
    const tbody = qs("tbody", table);

    const applyFilter = () => {
      const query = input.value.trim().toLowerCase();
      let total = 0;
      let visible = 0;

      qsa("tr", tbody).forEach((row) => {
        total += 1;
        const match = !query || row.textContent.toLowerCase().includes(query);
        row.hidden = !match;
        if (match) visible += 1;
      });

      count.textContent = query ? `${visible} / ${total} rows` : `${total} rows`;
    };

    input.addEventListener("input", applyFilter);
    new MutationObserver(applyFilter).observe(tbody, { childList: true });
    applyFilter();
  }

  function enhanceExternalLinks() {
    qsa('a[target="_blank"]').forEach((link) => {
      if (!link.rel.includes("noopener")) link.rel = `${link.rel} noopener noreferrer`.trim();
    });
  }

  function installTableExport() {
    const button = byId("exportBtn");
    const table = byId("tbl");
    if (!button || !table) return;

    const cellText = (cell) => {
      let value = String(cell.textContent || "").replace(/\s+/g, " ").trim();
      if (/^[=+\-@]/.test(value)) value = `'${value}`;
      return `"${value.replaceAll('"', '""')}"`;
    };
    button.addEventListener("click", () => {
      const rows = qsa("tr", table).filter((row) => !row.hidden);
      if (rows.length <= 1) {
        window.showAsoToast?.("Немає результатів для експорту.");
        return;
      }

      const csv = rows
        .map((row) => qsa("th,td", row).map(cellText).join(","))
        .join("\n");
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `wwa-aso-results-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      window.showAsoToast?.("CSV експортовано.");
    });
  }

  function enhanceWorkspace() {
    const lineIcon = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m10 13 4-4m-5 7-1 1a4.2 4.2 0 0 1-6-6l4-4a4.2 4.2 0 0 1 6 0m1 1 1-1a4.2 4.2 0 0 1 6 6l-4 4a4.2 4.2 0 0 1-6 0"/></svg>';
    qsa(".url-ico").forEach(el => { el.innerHTML = lineIcon; });
    qsa(".run-ico").forEach(el => {
      el.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h16m-6-6 6 6-6 6"/></svg>';
    });
    const empty = byId("wwaEmpty");
    if (empty) {
      const update = () => {
        const hasResults = ["summary", "appmagicWidget", "tableWrap"].map(byId).some(el => el && el.textContent.trim() && getComputedStyle(el).display !== "none");
        empty.hidden = hasResults;
        const waiting = document.body.classList.contains("is-loading");
        qs(".wwa-empty-heading > span", empty).textContent = waiting ? "Перевіряємо країни…" : "Очікуємо на посилання";
        empty.classList.toggle("wwa-empty--loading", waiting);
      };
      ["summary", "appmagicWidget", "tableWrap"].map(byId).filter(Boolean).forEach(el => {
        new MutationObserver(update).observe(el, { childList: true, subtree: true, attributes: true, attributeFilter: ["style", "hidden"] });
      });
      new MutationObserver(update).observe(document.body, { attributes: true, attributeFilter: ["class"] });
      update();
    }
  }

  ready(() => {
    installThemeToggle();
    installContactModal();
    installToast();
    wrapLoadingState();
    enhancePersistentInputs();
    enhanceModeSegments();
    enhanceRunOnEnter();
    enhanceDrawerState();
    enhanceTableToolbar();
    enhanceExternalLinks();
    installTableExport();
    enhanceWorkspace();

    document.body.classList.add("ui-ready");
    document.addEventListener("change", refreshModeVisualState);
  });
})();
