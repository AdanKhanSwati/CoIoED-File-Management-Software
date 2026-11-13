/* CoIoED v12: offline, no dependencies. Keyboard-accessible mobile sidebar and table navigation. */
(() => {
  'use strict';
  const body=document.body;
  const button=document.getElementById('nav-toggle');
  const sidebar=document.getElementById('app-sidebar');
  const backdrop=document.getElementById('sidebar-backdrop');
  if(button && sidebar && backdrop){
    const mq=window.matchMedia('(max-width: 1199px)');
    let previousFocus=null;
    function setOpen(open){
      const yes=!!open && mq.matches;
      body.classList.toggle('nav-open',yes);
      button.setAttribute('aria-expanded',String(yes));
      backdrop.hidden=!yes;
      backdrop.setAttribute('aria-hidden',String(!yes));
      if(yes){previousFocus=document.activeElement; sidebar.querySelector('a[href]')?.focus({preventScroll:true});}
      else if(previousFocus && document.contains(previousFocus)){previousFocus.focus({preventScroll:true});previousFocus=null;}
    }
    button.addEventListener('click',()=>setOpen(!body.classList.contains('nav-open')));
    backdrop.addEventListener('click',()=>setOpen(false));
    sidebar.addEventListener('click',ev=>{if(ev.target.closest('a[href]'))setOpen(false);});
    document.addEventListener('keydown',ev=>{
      if(!body.classList.contains('nav-open'))return;
      if(ev.key==='Escape'){ev.preventDefault();setOpen(false);return;}
      if(ev.key!=='Tab')return;
      const nodes=[...sidebar.querySelectorAll('a[href],button:not([disabled]),input:not([disabled])')]
        .filter(el=>el.getClientRects().length>0);
      if(!nodes.length)return;
      const first=nodes[0],last=nodes[nodes.length-1];
      if(ev.shiftKey && document.activeElement===first){ev.preventDefault();last.focus();}
      else if(!ev.shiftKey && document.activeElement===last){ev.preventDefault();first.focus();}
    });
    mq.addEventListener?.('change',()=>{if(!mq.matches)setOpen(false);});
  }
  // Wide registers retain their full table data; users can scroll with keyboard too.
  const tables=[...document.querySelectorAll('.table-container')];
  for(const container of tables){
    if(!container.hasAttribute('tabindex'))container.tabIndex=0;
    if(!container.hasAttribute('role'))container.setAttribute('role','region');
    if(!container.hasAttribute('aria-label'))container.setAttribute('aria-label','Data table, scroll horizontally to see remaining columns');
    const hint=document.createElement('div');
    hint.className='table-scroll-hint';
    hint.textContent='Scroll table horizontally to view all columns  →';
    hint.hidden=true;
    container.insertAdjacentElement('beforebegin',hint);
    container._scrollHint=hint;
  }
  function updateTableHints(){
    for(const container of tables){
      if(container._scrollHint)container._scrollHint.hidden=!(innerWidth<=900 && container.scrollWidth>container.clientWidth+6);
    }
  }
  window.addEventListener('resize',updateTableHints,{passive:true});
  requestAnimationFrame(updateTableHints);
})();