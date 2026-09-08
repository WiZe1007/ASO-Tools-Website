/* Original, event-driven motion for the mountain workspace. No animation loop. */
(() => {
  "use strict";

  const install = () => {
    const hero = document.querySelector(".wwa-home-hero[data-alpine-motion]");
    if (!hero) return;

    const root = document.documentElement;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
    const buttons = [...hero.querySelectorAll(".wwa-hero-cta")];
    const pending = new Map();
    let frame = 0;
    let inView = true;

    const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
    const active = () => inView && !document.hidden && !reduced.matches &&
      root.dataset.motion !== "paused" && root.dataset.motion !== "suspended";
    const pointerActive = (event) => active() && fine.matches && event.pointerType !== "touch";

    // Bounds are read by pointer handlers; this frame only writes styles.
    const queue = (element, properties, hovered) => {
      const patch = pending.get(element) || { properties: {} };
      Object.assign(patch.properties, properties);
      if (hovered !== undefined) patch.hovered = hovered;
      pending.set(element, patch);
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (active()) {
          pending.forEach((entry, target) => {
            Object.entries(entry.properties).forEach(([name, value]) => target.style.setProperty(name, value));
            if (entry.hovered !== undefined) target.dataset.hovered = String(entry.hovered);
          });
        }
        pending.clear();
      });
    };

    const position = (event, element) => {
      const bounds = element.getBoundingClientRect();
      return {
        x: clamp((event.clientX - bounds.left) / (bounds.width || 1), 0, 1),
        y: clamp((event.clientY - bounds.top) / (bounds.height || 1), 0, 1)
      };
    };

    const resetPointers = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      pending.clear();
      buttons.forEach((button) => {
        button.style.setProperty("--magnet-x", "0px");
        button.style.setProperty("--magnet-y", "0px");
      });
    };

    const syncScroll = () => {
      const scroll = Math.max(0, window.scrollY || 0);
      document.body.dataset.alpineScrolled = String(scroll > 60);
    };

    hero.addEventListener("animationend", (event) => {
      if (event.target.matches(".wwa-hero-cta")) hero.dataset.introComplete = "true";
    });

    const syncPolicy = () => {
      if (document.hidden || reduced.matches || root.dataset.motion === "paused") hero.dataset.introComplete = "true";
      if (!active() || !fine.matches) resetPointers();
      syncScroll();
    };

    buttons.forEach((button) => {
      button.addEventListener("pointermove", (event) => {
        if (!pointerActive(event)) return;
        const point = position(event, button);
        queue(button, {
          "--magnet-x": `${((point.x - 0.5) * 8).toFixed(2)}px`,
          "--magnet-y": `${((point.y - 0.5) * 8).toFixed(2)}px`
        });
      }, { passive: true });
      button.addEventListener("pointerleave", () => {
        if (active()) queue(button, { "--magnet-x": "0px", "--magnet-y": "0px" });
      });
    });

    if ("IntersectionObserver" in window) {
      new IntersectionObserver((entries) => {
        inView = entries.some((entry) => entry.isIntersecting);
        syncPolicy();
      }, { threshold: 0 }).observe(hero);
    }
    new MutationObserver(syncPolicy).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
    reduced.addEventListener("change", syncPolicy);
    fine.addEventListener("change", syncPolicy);
    document.addEventListener("visibilitychange", syncPolicy);
    window.addEventListener("scroll", syncScroll, { passive: true });
    window.addEventListener("resize", syncPolicy, { passive: true });
    window.addEventListener("pagehide", resetPointers);
    window.addEventListener("pageshow", syncPolicy);
    resetPointers();
    syncScroll();
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install, { once: true });
  else install();
})();
