/* Shared appearance only; database requests and permissions stay in their owners. */
(() => {
  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const read = key => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  let paused = read('wwa.tools.liquid-motion') === 'paused';
  const animate = (element, frames, options) => {
    if (!element || reduced.matches || root.dataset.motion !== 'playing') return;
    element.getAnimations().forEach(animation => animation.cancel());
    return element.animate(frames, options);
  };
  const syncMotion = () => {
    const stopped = reduced.matches || paused;
    root.dataset.motion = stopped ? 'paused' : 'playing';
    document.querySelectorAll('[data-motion-toggle]').forEach(button => {
      const label = reduced.matches ? 'Анімацію вимкнено в системних налаштуваннях' : stopped ? 'Увімкнути анімацію' : 'Призупинити анімацію';
      button.disabled = reduced.matches;
      button.title = label;
      button.setAttribute('aria-label', label);
      button.setAttribute('aria-pressed', String(stopped));
    });
  };
  const syncTheme = () => {
    const label = root.dataset.theme === 'light' ? 'Увімкнути темну тему' : 'Увімкнути світлу тему';
    document.querySelectorAll('[data-db-theme]').forEach(button => {
      button.title = label; button.setAttribute('aria-label', label);
    });
  };
  document.querySelectorAll('[data-db-theme]').forEach(button => button.addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
    write('wwa-db-theme', root.dataset.theme); syncTheme();
  }));
  document.querySelectorAll('[data-motion-toggle]').forEach(button => button.addEventListener('click', () => {
    paused = !paused; write('wwa.tools.liquid-motion', paused ? 'paused' : 'playing'); syncMotion();
  }));
  reduced.addEventListener('change', syncMotion);
  syncTheme(); syncMotion();

  const nav = document.querySelector('#databaseSwitcher');
  const indicator = nav?.querySelector('.db-nav-indicator');
  if (indicator) {
    let hover = null, initialized = false;
    const update = () => {
      const selected = hover || nav.querySelector('.is-active');
      if (!selected) return;
      if (!initialized) indicator.style.transition = 'none';
      indicator.style.width = `${selected.offsetWidth}px`;
      indicator.style.setProperty('--db-nav-x', `${selected.offsetLeft}px`);
      indicator.style.setProperty('--db-nav-y', `${selected.offsetTop}px`);
      indicator.style.opacity = '1'; nav.classList.add('has-indicator');
      if (!initialized) { initialized = true; requestAnimationFrame(() => requestAnimationFrame(() => indicator.style.removeProperty('transition'))); }
    };
    nav.querySelectorAll('[data-database-key]').forEach(button => {
      button.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') { hover = button; update(); } });
      button.addEventListener('focus', () => { hover = button; update(); });
    });
    nav.addEventListener('pointerleave', () => { hover = null; update(); });
    nav.addEventListener('focusout', event => { if (!nav.contains(event.relatedTarget)) { hover = null; update(); } });
    nav.addEventListener('click', () => { hover = null; update(); });
    new MutationObserver(update).observe(nav, {subtree: true, attributes: true, attributeFilter: ['aria-pressed']});
    new ResizeObserver(update).observe(nav); update();
  }
  window.wwaDatabaseAppearance = {
    reveal(element) { animate(element, [{opacity: .45,transform: 'translateY(5px)'},{opacity: 1,transform: 'translateY(0)'}], {duration: 240,easing: 'cubic-bezier(.22,1,.36,1)'}); }
  };
})();
