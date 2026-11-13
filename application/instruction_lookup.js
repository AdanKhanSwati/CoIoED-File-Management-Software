/* CoIoED hearing instruction suggestions. Local authenticated endpoints only. */
(() => {
  'use strict';
  for (const widget of document.querySelectorAll('[data-instruction-autocomplete]')) {
    const area = widget.querySelector('textarea[name="instructions"]');
    const list = widget.querySelector('.instruction-suggestion-list');
    if (!area || !list) continue;
    let options = [], active = -1, timer = 0, controller = null;
    const close = () => {
      options = []; active = -1;
      list.hidden = true; list.replaceChildren();
      area.setAttribute('aria-expanded', 'false');
    };
    function choose(text) {
      // Selecting reuses the complete historic instruction, not just one line.
      area.value = text;
      area.focus();
      area.setSelectionRange(text.length, text.length);
      area.dispatchEvent(new Event('input', { bubbles: true }));
      if (controller) controller.abort();
      window.clearTimeout(timer); close();
    }
    function show(items) {
      options = items.slice(0, 10); active = -1;
      list.replaceChildren();
      for (const text of options) {
        const item = document.createElement('button');
        item.type = 'button'; item.className = 'instruction-option';
        item.textContent = text; item.title = text;
        item.setAttribute('role', 'option');
        item.addEventListener('mousedown', event => { event.preventDefault(); choose(text); });
        item.addEventListener('click', () => choose(text));
        list.appendChild(item);
      }
      list.hidden = options.length === 0;
      area.setAttribute('aria-expanded', String(!list.hidden));
    }
    async function lookup() {
      const query = area.value.trim();
      if (query.length < 2 || query.length > 160) { close(); return; }
      if (controller) controller.abort();
      const current = new AbortController(); controller = current;
      try {
        const response = await fetch('/instructions/suggest.json?q=' + encodeURIComponent(query), {
          credentials: 'same-origin', signal: current.signal,
          headers: { 'Accept': 'application/json' }
        });
        if (!response.ok) { close(); return; }
        const result = await response.json();
        if (!current.signal.aborted && area.value.trim() === query) {
          show(Array.isArray(result.results) ? result.results.filter(v => typeof v === 'string') : []);
        }
      } catch(err) { if (err.name !== 'AbortError') close(); }
    }
    function schedule() {
      window.clearTimeout(timer); timer = window.setTimeout(lookup, 180);
    }
    area.setAttribute('aria-autocomplete', 'list');
    area.setAttribute('aria-expanded', 'false');
    area.addEventListener('input', schedule);
    area.addEventListener('keydown', event => {
      if (list.hidden || !options.length) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        active = (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
        [...list.children].forEach((el,i) => el.classList.toggle('is-selected', i === active));
      } else if ((event.key === 'Enter' || event.key === 'Tab') && active >= 0) {
        event.preventDefault(); choose(options[active]);
      } else if (event.key === 'Escape') close();
    });
    area.addEventListener('blur', () => window.setTimeout(close, 170));
  }
})();