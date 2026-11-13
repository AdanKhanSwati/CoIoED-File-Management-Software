/* CoIoED offline autocomplete: authorized local API; no external network or libraries. */
(function () {
  'use strict';
  function mount(input) {
    if (input.closest('form[action="/cases/save"]')) return; // Never mutate official case IDs in edit form.
    var action = input.getAttribute('data-case-action') || 'fill';
    var scope = input.getAttribute('data-case-lookup') || 'all';
    var wrap = document.createElement('div');
    wrap.className = 'case-lookup-wrap';
    var list = document.createElement('div');
    list.className = 'case-lookup-list';
    list.setAttribute('role', 'listbox');
    list.id = 'case-lookup-' + Math.random().toString(36).slice(2, 10);
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    wrap.appendChild(list);
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', list.id);
    var options = [], active = -1, timer = null, pending = null, requestId = 0;
    function close() {
      options = []; active = -1;
      list.classList.remove('show'); list.replaceChildren();
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }
    function activate(i) {
      active = i;
      Array.prototype.forEach.call(list.querySelectorAll('[role="option"]'), function(el, j) {
        el.setAttribute('aria-selected', j === active ? 'true' : 'false');
        if (j === active) { input.setAttribute('aria-activedescendant', el.id); el.scrollIntoView({block:'nearest'}); }
      });
    }
    var selectedCaseId = null;
    var noticeForm = input.closest('form[action="/notice/add-case"]');
    if (noticeForm) {
      selectedCaseId = noticeForm.querySelector('input[name="case_id"]');
      if (!selectedCaseId) { selectedCaseId = document.createElement('input'); selectedCaseId.type='hidden'; selectedCaseId.name='case_id'; noticeForm.appendChild(selectedCaseId); }
      input.addEventListener('input', function () { selectedCaseId.value = ''; });
    }
    function selectRow(row) {
      input.value = row.case_no; if (selectedCaseId) selectedCaseId.value=String(row.case_id); close();
      input.dispatchEvent(new Event('change', { bubbles: true }));
      if (action === 'navigate') window.location.assign('/case/' + encodeURIComponent(row.case_id));
      else input.focus();
    }
    function message(text) {
      list.replaceChildren(); var p=document.createElement('div');
      p.className='case-lookup-help'; p.textContent=text; list.appendChild(p);
      list.classList.add('show'); input.setAttribute('aria-expanded', 'true');
    }
    function render(rows) {
      close(); options = rows;
      if (!rows.length) { message('No matching indexed cases. Try the case number or another part of the name.'); return; }
      rows.forEach(function(row, i) {
        var el=document.createElement('button'); el.type='button'; el.className='case-lookup-option';
        el.id=list.id+'-'+i; el.setAttribute('role','option'); el.setAttribute('aria-selected','false');
        var headline=document.createElement('strong'); headline.textContent=row.case_no+'  —  '+row.name;
        var note=document.createElement('small');
        note.textContent=(row.parentage || 'No parentage recorded')+(row.source_row ? '  ·  Master row '+row.source_row : '');
        if (!row.verified) { var flag=document.createElement('span'); flag.className='case-lookup-status'; flag.textContent='Check source before approval'; headline.appendChild(flag); }
        el.appendChild(headline); el.appendChild(note);
        el.addEventListener('mousedown',function(e){e.preventDefault();});
        el.addEventListener('click',function(){selectRow(row);}); list.appendChild(el);
      });
      list.classList.add('show'); input.setAttribute('aria-expanded','true');
    }
    input.addEventListener('input',function(){
      clearTimeout(timer); if (pending) pending.abort();
      var term=input.value.trim(),seq=++requestId;
      if (term.length < 2) { close(); return; }
      timer=setTimeout(function(){
        pending=new AbortController();
        fetch('/cases/search.json?q='+encodeURIComponent(term)+'&scope='+encodeURIComponent(scope),{
          credentials:'same-origin',signal:pending.signal,headers:{'Accept':'application/json'}
        }).then(function(r){if(!r.ok)throw new Error('Search unavailable'); return r.json();})
          .then(function(rows){if(seq === requestId && document.activeElement === input)render(rows);})
          .catch(function(e){if(e.name!=='AbortError' && seq===requestId)message('Search could not be completed. Please try again.');});
      }, 170);
    });
    input.addEventListener('focus',function(){ if(input.value.trim().length>=2)input.dispatchEvent(new Event('input')); });
    input.addEventListener('keydown',function(e){
      if (e.key==='Escape') {close();return;}
      if (!options.length || !list.classList.contains('show'))return;
      if(e.key==='ArrowDown'){e.preventDefault();activate((active+1)%options.length);}
      else if(e.key==='ArrowUp'){e.preventDefault();activate((active-1+options.length)%options.length);}
      else if(e.key==='Enter' && active>=0){e.preventDefault();selectRow(options[active]);}
    });
    input.addEventListener('blur',function(){setTimeout(function(){if(!wrap.contains(document.activeElement))close();},180);});
    document.addEventListener('click',function(e){if(!wrap.contains(e.target))close();});
  }
  document.querySelectorAll('input[data-case-lookup], form input[name="case_no"]:not([type="hidden"])').forEach(mount);
})();