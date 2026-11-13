/* CoIoED offline, same-origin officer autocomplete. No third-party calls. */
(() => {
  'use strict';
  for (const widget of document.querySelectorAll('[data-officer-autocomplete]')) {
    const area = widget.querySelector('textarea[name="attendees_text"]');
    const list = widget.querySelector('.officer-suggestion-list');
    if (!area || !list) continue;
    let options = [], index = -1, pending = null, delay = 0;
    const close = () => { options = []; index = -1; list.hidden = true; list.replaceChildren(); };
    function fragment() {
      const pos = area.selectionStart ?? 0;
      const start = area.value.lastIndexOf('\n', Math.max(0, pos - 1)) + 1;
      const endIndex = area.value.indexOf('\n', pos);
      const end = endIndex === -1 ? area.value.length : endIndex;
      return { start, end, text: area.value.slice(start, pos).trimStart() };
    }
    function choose(text) {
      const { start, end } = fragment();
      const before = area.value.slice(0,start), after = area.value.slice(end);
      area.value = before + text + after;
      const caret = before.length + text.length;
      area.focus(); area.setSelectionRange(caret,caret);
      area.dispatchEvent(new Event('input', { bubbles: true })); close();
    }
    function show(data) {
      options = data.slice(0,10); index = -1; list.replaceChildren();
      for (const [i,text] of options.entries()) {
        const item = document.createElement('button'); item.type = 'button';
        item.className = 'officer-option'; item.textContent = text; item.setAttribute('role','option');
        item.addEventListener('mousedown', event => {event.preventDefault(); choose(text);});
        item.addEventListener('click', () => choose(text));
        list.appendChild(item);
      }
      list.hidden = options.length === 0;
    }
    async function lookup() {
      const { text } = fragment();
      if (!text.trim()) { close(); return; }
      if (pending) pending.abort();
      pending = new AbortController(); const query = text.trim();
      try {
        const res = await fetch('/officers/suggest.json?q=' + encodeURIComponent(query), {
          method: 'GET', credentials: 'same-origin', signal: pending.signal,
          headers: { Accept: 'application/json' }
        });
        if (!res.ok) { close(); return; }
        const json = await res.json();
        if (fragment().text.trim() === query) show(Array.isArray(json.results) ? json.results : []);
      } catch(err) { if(err.name !== 'AbortError') close(); }
    }
    area.addEventListener('input', () => {window.clearTimeout(delay);delay = window.setTimeout(lookup, 160);});
    area.addEventListener('click', () => {window.clearTimeout(delay);delay=window.setTimeout(lookup,160);});
    area.addEventListener('keydown', ev => {
      if (list.hidden || !options.length) return;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault(); index = (index + (ev.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      } else if ((ev.key === 'Enter' || ev.key === 'Tab') && index !== -1) {
        ev.preventDefault(); choose(options[index]);return;
      } else if(ev.key === 'Escape') {close(); return;} else return;
      [...list.children].forEach((el,i) => el.classList.toggle('is-selected',i===index));
    });
    area.addEventListener('blur', () => window.setTimeout(close, 180));
  }
})();