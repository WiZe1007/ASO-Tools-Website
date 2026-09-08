(() => {
  'use strict';

  const initialize = () => {
    const stage = document.querySelector('.stellar-stage');
    if (!stage) return;

    const hero = stage.closest('.wwa-home-hero') || document.querySelector('.wwa-home-hero');
    const search = document.getElementById('stellarSearch');
    const sort = document.getElementById('stellarSort');
    const container = document.getElementById('stellarCards');
    const cards = container ? Array.from(container.querySelectorAll('[data-tool-card]')) : [];
    const filters = Array.from(document.querySelectorAll('[data-tool-filter]'));
    const noResults = document.getElementById('stellarNoResults');
    const status = document.getElementById('stellarSearchStatus');
    const originalOrder = new Map(cards.map((card, index) => [card, index]));
    const collator = new Intl.Collator('uk', { sensitivity: 'base', numeric: true });
    let selectedFilter = filters.find((button) => button.getAttribute('aria-pressed') === 'true')?.dataset.toolFilter || 'all';

    const beginExploring = () => {
      stage.dataset.exploring = 'true';
    };

    const normalize = (text) => text.normalize('NFKC').toLocaleLowerCase('uk').trim();

    const updateCards = () => {
      const terms = normalize(search?.value || '').split(/\s+/).filter(Boolean);
      let visible = 0;

      cards.forEach((card) => {
        const name = normalize(card.dataset.name || card.textContent || '');
        const matchesQuery = terms.every((term) => name.includes(term));
        const matchesCategory = selectedFilter === 'all' || card.dataset.category === selectedFilter;
        card.hidden = !(matchesQuery && matchesCategory);
        if (!card.hidden) visible += 1;
      });

      filters.forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.toolFilter === selectedFilter));
      });

      if (noResults) noResults.hidden = visible > 0;
      if (status) status.textContent = `Показано інструментів: ${visible} із ${cards.length}.`;
    };

    search?.addEventListener('input', () => {
      beginExploring();
      updateCards();
    });

    // Native search fields may dispatch "search" when their clear control is used.
    search?.addEventListener('search', () => {
      beginExploring();
      updateCards();
    });

    filters.forEach((button) => {
      button.addEventListener('click', () => {
        selectedFilter = button.dataset.toolFilter || 'all';
        beginExploring();
        updateCards();
      });
    });

    container?.addEventListener('focusin', (event) => {
      if (event.target instanceof Element && event.target.closest('[data-tool-card]')) {
        beginExploring();
      }
    });

    document.querySelectorAll('.stellar-side-active').forEach((button) => {
      button.addEventListener('click', () => {
        if (search) search.value = '';
        selectedFilter = 'all';
        if (sort) sort.value = 'default';
        if (container) cards.forEach((card) => container.append(card));
        beginExploring();
        updateCards();
      });
    });

    sort?.addEventListener('change', () => {
      if (!container) return;
      beginExploring();
      const ordered = [...cards].sort((first, second) => {
        if (sort.value === 'name') {
          return collator.compare(first.dataset.name || '', second.dataset.name || '') ||
            originalOrder.get(first) - originalOrder.get(second);
        }
        return originalOrder.get(first) - originalOrder.get(second);
      });
      ordered.forEach((card) => container.append(card));
      updateCards();
    });

    document.getElementById('stellarCloseFeature')?.addEventListener('click', () => {
      beginExploring();
      search?.focus({ preventScroll: true });
    });

    const connectDial = (inputId, outputId, target, property, suffix = '', angleFactor = 3.6) => {
      const input = document.getElementById(inputId);
      const output = document.getElementById(outputId);
      if (!input || !target) return;

      const update = () => {
        const value = Number(input.value);
        if (!Number.isFinite(value)) return;
        if (output) output.textContent = `${value}${suffix}`;
        input.setAttribute('aria-valuetext', `${value}%`);
        target.style.setProperty(property, String(value / 100));
        input.closest('.stellar-dial')?.style.setProperty('--dial-angle', `${value * angleFactor}deg`);
      };

      input.addEventListener('input', update);
      update();
    };

    connectDial('stellarGlass', 'stellarGlassValue', stage, '--stellar-glass-opacity', '%');
    connectDial('stellarLight', 'stellarLightValue', hero, '--stellar-light-level', '', 1.8);

    document.querySelectorAll('[data-open-settings]').forEach((button) => {
      button.addEventListener('click', () => {
        document.querySelector('#wwaTopUtilities button')?.focus({ preventScroll: true });
      });
    });

    updateCards();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, { once: true });
  } else {
    initialize();
  }
})();
