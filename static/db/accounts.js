(() => {
  const root = document.documentElement;
  const storageKey = root.dataset.themeStorage || "wwa-theme";
  try {
    const saved = localStorage.getItem(storageKey) || localStorage.getItem("wwa-theme");
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  } catch {}
  const button = document.getElementById("accountsTheme");
  button?.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
    try { localStorage.setItem(storageKey, root.dataset.theme); } catch {}
  });
})();
