/* One adaptive, hardware-decodable background; the poster always renders first. */
(() => {
  "use strict";
  const install = () => {
    const video = document.querySelector("[data-background-video]");
    if (!video) return;
    const media = video.closest("[data-background-media]");
    const root = document.documentElement;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const compact = matchMedia("(max-width: 600px) and (orientation: portrait)");
    const connection = navigator.connection;
    const source = compact.matches ? video.dataset.mobileSrc : video.dataset.desktopSrc;
    const positionKey = 'wwa.tools.background-position';
    let resume = null;
    try {
      const saved = JSON.parse(sessionStorage.getItem(positionKey) || 'null');
      if (saved?.src === source && Date.now() - saved.at < 15000 && Number.isFinite(saved.time)) resume = saved;
    } catch (_) {}
    let ready = false;
    let inView = true;
    let leaving = false;
    let pending = false;
    let blocked = false;
    let failed = false;

    video.muted = true;
    video.defaultMuted = true;
    const economical = () => connection?.saveData || /^(slow-)?2g$/.test(connection?.effectiveType || "");
    const allowed = () => ready && inView && !leaving && !failed && !blocked &&
      !document.hidden && !reduced.matches && !economical() && root.dataset.motion === "playing";

    const sync = () => {
      if (!allowed()) { video.pause(); return; }
      // Choose once per page visit: resizing never downloads a second video.
      if (!video.hasAttribute("src")) {
        video.preload = 'auto';
        video.src = source;
        video.load();
      }
      if (!video.readyState) return;
      if (pending || !video.paused) return;
      pending = true;
      const play = video.play();
      if (!play) { pending = false; return; }
      play.catch((error) => {
        // Autoplay restrictions keep the poster visible without retrying in a loop.
        if (error.name !== "AbortError") blocked = true;
      }).finally(() => {
        pending = false;
        if (!allowed()) video.pause();
        else if (video.paused) sync();
      });
    };

    video.addEventListener('loadedmetadata', () => {
      if (resume && Number.isFinite(video.duration) && video.duration > 0) {
        try { video.currentTime = Math.max(0, resume.time % video.duration); } catch (_) {}
        media.dataset.videoResumed = 'true';
        resume = null;
      }
      sync();
    });
    video.addEventListener("playing", () => {
      if (allowed()) media.dataset.videoReady = "true";
      else video.pause();
    });
    video.addEventListener("error", () => {
      failed = true;
      delete media.dataset.videoReady;
      video.pause();
    });
    document.addEventListener("visibilitychange", sync);
    reduced.addEventListener("change", sync);
    connection?.addEventListener("change", sync);
    new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((entries) => {
        inView = entries.some((entry) => entry.isIntersecting);
        sync();
      }).observe(media);
    }
    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-motion-toggle]")) { blocked = false; sync(); }
    });
    const remember = () => {
      if (video.readyState < 2) return;
      try { sessionStorage.setItem(positionKey, JSON.stringify({ src: source, time: video.currentTime, at: Date.now() })); } catch (_) {}
    };
    // Pause before the browser captures the outgoing page. Waiting for pagehide
    // can discard the playing video's compositor frame and flash a pale snapshot.
    window.addEventListener('pageswap', () => { remember(); leaving = true; video.pause(); });
    window.addEventListener("pagehide", () => { remember(); leaving = true; video.pause(); });
    window.addEventListener("pageshow", () => { leaving = false; sync(); });

    const start = () => {
      const activate = () => { ready = true; sync(); };
      if ("requestIdleCallback" in window) requestIdleCallback(activate, { timeout: 1200 });
      else setTimeout(activate, 250);
    };
    // A cached background resumes during navigation instead of flashing its opening frame.
    if (resume) { ready = true; sync(); }
    else if (document.readyState === "complete") start();
    else window.addEventListener("load", start, { once: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install, { once: true });
  else install();
})();
