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
  const THEME_STORAGE_KEY = "wwa.aso.tools.theme";

  function storedTheme() {
    try {
      const value = window.localStorage.getItem(THEME_STORAGE_KEY);
      return value === "light" ? "light" : "dark";
    } catch (_) {
      return "dark";
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

    qsa(".theme-toggle").forEach((button) => {
      button.textContent = isLight ? "☾" : "☼";
      button.setAttribute("aria-label", isLight ? "Switch to dark theme" : "Switch to light theme");
      button.setAttribute("title", isLight ? "Dark theme" : "Light theme");
      button.setAttribute("aria-pressed", String(isLight));
    });
  }

  document.documentElement.dataset.theme = storedTheme();

  function installThemeToggle() {
    applyTheme(storedTheme());
    qsa(".theme-toggle").forEach((button) => {
      if (button.dataset.themeReady === "1") return;
      button.dataset.themeReady = "1";
      button.addEventListener("click", () => {
        const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
        saveTheme(nextTheme);
        applyTheme(nextTheme);
      });
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
      const saved = window.localStorage.getItem(key);
      if (saved !== null && !el.value) el.value = saved;
      el.addEventListener("input", () => window.localStorage.setItem(key, el.value));
    };

    ["url", "threshold", "country"].forEach(persistTextInput);

    ["showAll", "showOnlyClosed"].forEach((id) => {
      const el = byId(id);
      if (!el) return;
      const key = `${storagePrefix}.${page}.${id}`;
      const saved = window.localStorage.getItem(key);
      if (saved !== null) el.checked = saved === "1";
      el.addEventListener("change", () => window.localStorage.setItem(key, el.checked ? "1" : "0"));
    });

    const modeKey = `${storagePrefix}.${page}.mode`;
    const modeIds = [
      ["modeToolbox", "toolbox"],
      ["modeFull", "full"],
      ["modeAppMagic", "appmagic"],
    ];
    const savedMode = window.localStorage.getItem(modeKey);
    if (savedMode && typeof window.syncModeCheckboxes === "function") {
      window.syncModeCheckboxes(savedMode);
    }
    modeIds.forEach(([id, mode]) => {
      const el = byId(id);
      if (!el) return;
      el.addEventListener("change", () => {
        if (el.checked) window.localStorage.setItem(modeKey, mode);
        refreshModeVisualState();
      });
    });
    refreshModeVisualState();
  }

  function refreshModeVisualState() {
    qsa(".mode-item").forEach((item) => {
      const input = qs("input", item);
      item.classList.toggle("selected", Boolean(input && input.checked));
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
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");

      const activate = (event) => {
        const input = qs("input", item);
        if (!input || event.target === input) return;
        input.click();
        refreshModeVisualState();
      };

      item.addEventListener("click", activate);
      item.addEventListener("keydown", (event) => {
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

    const sync = () => {
      const isOpen = window.getComputedStyle(drawer).display !== "none";
      const wrap = qs(".wrap");
      document.body.classList.toggle("drawer-open", isOpen);
      hamburger.classList.toggle("is-open", isOpen);
      hamburger.setAttribute("aria-expanded", String(isOpen));

      if (wrap && window.matchMedia("(max-width: 680px)").matches) {
        wrap.style.paddingTop = isOpen ? `${Math.ceil(drawer.getBoundingClientRect().bottom + 16)}px` : "";
      } else if (wrap) {
        wrap.style.paddingTop = "";
      }
    };

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

  function enhancePointerFeedback() {
    const panelSelector = ".card, .appmagic-widget, .table-wrap, .result, .topnav-inner, .phone-frame";
    qsa(panelSelector).forEach((panel) => {
      panel.addEventListener("pointermove", (event) => {
        const rect = panel.getBoundingClientRect();
        panel.style.setProperty("--mx", `${event.clientX - rect.left}px`);
        panel.style.setProperty("--my", `${event.clientY - rect.top}px`);
      });
    });

    document.addEventListener("pointerdown", (event) => {
      const target = event.target.closest(".btn, .copy, .navbtn, .am-period, .am-geo, .am-seg, .export-btn");
      if (!target) return;

      const rect = target.getBoundingClientRect();
      target.style.setProperty("--rx", `${event.clientX - rect.left}px`);
      target.style.setProperty("--ry", `${event.clientY - rect.top}px`);
      target.classList.remove("is-pressing");
      window.requestAnimationFrame(() => target.classList.add("is-pressing"));
      window.setTimeout(() => target.classList.remove("is-pressing"), 420);
    });
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

    const cellText = (cell) => `"${String(cell.textContent || "").replace(/\s+/g, " ").trim().replaceAll('"', '""')}"`;
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

  ready(() => {
    installThemeToggle();
    installToast();
    wrapLoadingState();
    enhancePersistentInputs();
    enhanceModeSegments();
    enhanceRunOnEnter();
    enhanceDrawerState();
    enhanceTableToolbar();
    enhancePointerFeedback();
    enhanceExternalLinks();
    installTableExport();

    document.body.classList.add("ui-ready");
    document.addEventListener("change", refreshModeVisualState);
  });
})();
