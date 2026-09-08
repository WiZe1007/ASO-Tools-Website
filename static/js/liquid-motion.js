/* Progressive motion: native navigation, real controls, no delayed submissions. */
(() => {
  "use strict";
  const root = document.documentElement;
  const storageKey = "wwa.tools.liquid-motion";
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  const controls = [...document.querySelectorAll("[data-motion-toggle]")];
  const read = (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const save = (value) => { try { localStorage.setItem(storageKey, value); } catch (_) {} };
  const finiteAnimations = new Set();
  const spring = "cubic-bezier(.16,1,.3,1)";
  let paused = read(storageKey) === "paused";
  let themeTransition = null;
  const incomingTransition = () => window.wwaPageTransitions?.entering;
  const canAnimate = () => !paused && !reducedMotion.matches && !document.hidden;

  if (document.body.dataset.page === "auth") {
    root.dataset.theme = read("wwa.aso.tools.theme") === "dark" ? "dark" : "light";
  }

  function animate(element, keyframes, options) {
    if (!canAnimate() || !element?.animate) return null;
    const animation = element.animate(keyframes, options);
    finiteAnimations.add(animation);
    animation.finished.catch(() => {}).finally(() => finiteAnimations.delete(animation));
    return animation;
  }

  function syncMotion() {
    const stopped = paused || reducedMotion.matches;
    root.dataset.motion = stopped ? "paused" : document.hidden ? "suspended" : "playing";
    controls.forEach((button) => {
      const label = reducedMotion.matches ? "Анімацію вимкнено в системних налаштуваннях" : paused ? "Увімкнути анімацію" : "Призупинити анімацію";
      button.disabled = reducedMotion.matches;
      button.setAttribute("aria-pressed", String(stopped));
      button.setAttribute("aria-label", label);
      button.title = label;
      const text = button.querySelector("[data-motion-label]");
      if (text) text.textContent = label;
    });
    if (stopped) window.wwaPageTransitions?.active?.skipTransition();
    if (stopped || document.hidden) {
      finiteAnimations.forEach((animation) => animation.finish());
      themeTransition?.skipTransition();
    }
  }

  controls.forEach((button) => button.addEventListener("click", () => {
    if (reducedMotion.matches) return;
    paused = !paused;
    save(paused ? "paused" : "playing");
    syncMotion();
  }));

  document.addEventListener("visibilitychange", syncMotion);
  reducedMotion.addEventListener("change", syncMotion);
  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey) return;
    paused = event.newValue === "paused";
    syncMotion();
  });

  // A single floating material moves underneath the labels, including keyboard selection.
  function installIndicator(container, itemSelector, selectedSelector, followHover) {
    if (!container) return;
    const items = [...container.querySelectorAll(itemSelector)];
    if (!items.length || (!followHover && !container.querySelector(selectedSelector))) return;
    const indicator = container.querySelector("[data-nav-indicator]") || document.createElement("span");
    indicator.className = "liquid-indicator";
    indicator.setAttribute("aria-hidden", "true");
    if (!indicator.parentElement) container.prepend(indicator);
    container.classList.add("has-liquid-indicator");
    let hovered = null;
    let initialized = indicator.dataset.positioned === "true";
    const update = () => {
      const focused = container.contains(document.activeElement) ? document.activeElement.closest(itemSelector) : null;
      const item = hovered || focused || container.querySelector(selectedSelector);
      if (!item || !container.offsetWidth) { indicator.style.opacity = "0"; indicator.dataset.visible = "false"; return; }
      indicator.dataset.visible = "true";
      indicator.style.opacity = "1";
      if (!initialized) indicator.style.transition = "none";
      const x = `${item.offsetLeft}px`;
      const width = `${item.offsetWidth}px`;
      indicator.style.setProperty("--indicator-y", `${item.offsetTop}px`);
      if (indicator.style.width !== width) indicator.style.width = width;
      if (indicator.style.getPropertyValue("--indicator-x") !== x) indicator.style.setProperty("--indicator-x", x);
      if (!initialized) {
        initialized = true;
        requestAnimationFrame(() => requestAnimationFrame(() => indicator.style.removeProperty("transition")));
      }
    };
    if (followHover) {
      container.addEventListener('click', (event) => {
        const link = event.target.closest(itemSelector);
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (link.href === location.href) event.preventDefault();
      });
      items.forEach((item) => item.addEventListener("pointerenter", () => {
        if (!finePointer.matches) return;
        hovered = item;
        update();
      }));
      container.addEventListener("pointerleave", () => { hovered = null; update(); });
      container.addEventListener("focusin", (event) => { hovered = event.target.closest(itemSelector); update(); });
      container.addEventListener("focusout", (event) => {
        if (!container.contains(event.relatedTarget)) { hovered = null; update(); }
      });
    }
    new MutationObserver(update).observe(container, { subtree: true, attributes: true, attributeFilter: ["class", "aria-checked", "aria-current"] });
    if (window.ResizeObserver) new ResizeObserver(update).observe(container);
    else window.addEventListener("resize", update);
    update();
  }

  const actionSelector = ".stellar-feature-cta,.stellar-eyebrow,.stellar-segment button,.wwa-hero-cta,.wwa-hero-secondary,.wwa-tool-tile,.wwa-nav-cta,.run-btn,.indexing-btn,.search-panel .btn,.refresh-btn,.copy,.copy-btn,.export-btn,.wwa-utility,.wwa-menu";
  function ripple(button, x, y) {
    if (!canAnimate() || button.disabled) return;
    const rect = button.getBoundingClientRect();
    const size = Math.hypot(rect.width, rect.height) * 2;
    const wave = document.createElement("span");
    wave.className = "liquid-ripple";
    wave.setAttribute("aria-hidden", "true");
    wave.style.cssText = `width:${size}px;height:${size}px;left:${x - rect.left}px;top:${y - rect.top}px`;
    button.classList.add("liquid-action");
    button.append(wave);
    const animation = animate(wave, [{ transform: "translate(-50%,-50%) scale(0)", opacity: .2 }, { transform: "translate(-50%,-50%) scale(1)", opacity: 0 }], { duration: 400, easing: "cubic-bezier(.2,.7,.2,1)" });
    if (animation) animation.finished.catch(() => {}).finally(() => wave.remove());
    else wave.remove();
  }
  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const button = event.target.closest(actionSelector);
    if (button) ripple(button, event.clientX, event.clientY);
  }, { passive: true });
  document.addEventListener("click", (event) => {
    if (event.detail !== 0) return;
    const button = event.target.closest(actionSelector);
    if (button) { const rect = button.getBoundingClientRect(); ripple(button, rect.left + rect.width / 2, rect.top + rect.height / 2); }
  });

  // Content stays visible without JavaScript and is never held behind an animation.
  function installReveals() {
    if (!window.IntersectionObserver) return;
    const selector = "#workspace>.header,#workspace>.hero,#workspace>.indexing-header,#workspace>.card,#workspace>.wwa-empty,#workspace>.search-panel,#workspace>.indexing-panel,.wwa-auth-panel,#summary,#tableWrap,#appmagicWidget,#result";
    const seen = new WeakSet();
    const observer = new IntersectionObserver((entries) => {
      let index = 0;
      entries.forEach((entry) => {
        if (!entry.isIntersecting || seen.has(entry.target)) return;
        seen.add(entry.target);
        observer.unobserve(entry.target);
        if (incomingTransition() || root.dataset.navigationMode === "native") return;
        animate(entry.target, [{ opacity: 0, translate: "0 10px" }, { opacity: 1, translate: "0 0" }], { duration: 400, delay: Math.min(index++ * 40, 120), easing: spring, fill: "backwards" });
      });
    }, { threshold: .06 });
    document.querySelectorAll(selector).forEach((element) => observer.observe(element));
  }

  // Do not stack per-panel reveals over the browser's shared page transition.
  window.addEventListener("wwa:navigation-start", () => {
    finiteAnimations.forEach((animation) => animation.finish());
  });

  // Expand the new theme from its trigger. Unsupported browsers switch immediately.
  window.wwaMotion = {
    changeTheme(update, button) {
      if (themeTransition) {
        themeTransition.skipTransition();
        update();
        return;
      }
      if (!canAnimate() || !document.startViewTransition) { update(); return; }
      finiteAnimations.forEach((animation) => animation.finish());
      const rect = button.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      // Percentages keep snapshot coordinates correct on high-density displays.
      const origin = `${(x / innerWidth * 100).toFixed(3)}% ${(y / innerHeight * 100).toFixed(3)}%`;
      root.dataset.themeTransition = "true";
      let transition;
      try { transition = document.startViewTransition(update); }
      catch (_) { delete root.dataset.themeTransition; update(); return; }
      themeTransition = transition;
      transition.ready.then(() => {
        if (!canAnimate()) { transition.skipTransition(); return; }
        animate(root, [{ clipPath: `circle(0% at ${origin})` }, { clipPath: `circle(145% at ${origin})` }], { duration: 500, easing: "cubic-bezier(.4,0,.2,1)", pseudoElement: "::view-transition-new(root)" });
      }).catch(() => {});
      transition.finished.catch(() => {}).finally(() => {
        if (themeTransition === transition) { themeTransition = null; delete root.dataset.themeTransition; }
      });
    }
  };

  syncMotion();
  const ready = () => {
    window.wwaPageTransitions?.prepare();
    const topbar = document.querySelector('.wwa-full-nav');
    if (topbar) {
      const syncHeight = () => root.style.setProperty('--wwa-header-height', `${topbar.offsetHeight}px`);
      syncHeight();
      if (window.ResizeObserver) new ResizeObserver(syncHeight).observe(topbar);
      else window.addEventListener('resize', syncHeight, { passive: true });
    }
    installIndicator(document.querySelector(".wwa-navigation"), ".wwa-nav-link", '[aria-current="page"]', true);
    document.querySelectorAll(".mode-box").forEach((box) => installIndicator(box, ".mode-item", '.mode-item.selected', false));
    installReveals();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, { once: true });
  else ready();
})();
