/* The scan follows the real request lifecycle; its light trail is indeterminate. */
(() => {
  "use strict";
  const panel = document.getElementById("ratingScan");
  if (!panel) return;
  const clock = document.getElementById("ratingScanTime");
  const modeLabel = document.getElementById("ratingScanMode");
  const message = document.getElementById("ratingScanMessage");
  const hint = document.getElementById("ratingScanHint");
  const buttonLabel = document.querySelector("#run [data-run-label]");
  const labels = { appmagic: "App Magic", full: "Повне ASO", toolbox: "ASO Toolbox" };
  let startedAt = 0;
  let timer = 0;
  let scrollFrame = 0;
  let loading = false;

  function updateClock() {
    const seconds = Math.floor((performance.now() - startedAt) / 1000);
    clock.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    if (seconds >= 30 && !panel.dataset.longWait) {
      panel.dataset.longWait = "true";
      hint.textContent = "Перевірка триває. Очікуємо відповіді сервісів.";
    }
  }

  function syncClock() {
    clearInterval(timer);
    timer = 0;
    if (loading && !document.hidden) {
      updateClock();
      timer = window.setInterval(updateClock, 1000);
    }
  }

  window.wwaRatingScan = {
    setLoading(active, mode) {
      cancelAnimationFrame(scrollFrame);
      if (active && !loading) {
        startedAt = performance.now();
        delete panel.dataset.longWait;
        hint.textContent = "Результати з’являться автоматично.";
        modeLabel.textContent = labels[mode] || labels.toolbox;
        message.textContent = mode === "appmagic"
          ? "Шукаємо країни в App Magic та перевіряємо рейтинги."
          : "Збираємо рейтинги у різних країнах.";
        // The next request starts immediately; only the viewport moves to the scan.
        scrollFrame = requestAnimationFrame(() => {
          if (!loading) return;
          const bounds = panel.getBoundingClientRect();
          if (bounds.bottom > innerHeight - 24) {
            panel.scrollIntoView({ block: "nearest", behavior: document.documentElement.dataset.motion === "playing" ? "smooth" : "instant" });
          }
        });
      }
      loading = Boolean(active);
      panel.hidden = !loading;
      buttonLabel.textContent = loading ? "Перевіряємо" : "Перевірити";
      syncClock();
    }
  };

  if (window.IntersectionObserver) {
    new IntersectionObserver(entries => {
      panel.dataset.inView = String(entries[0].isIntersecting);
    }).observe(panel);
  }
  document.addEventListener("visibilitychange", syncClock);
  window.addEventListener("pagehide", () => { clearInterval(timer); cancelAnimationFrame(scrollFrame); });
  window.addEventListener("pageshow", syncClock);
})();
