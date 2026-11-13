// CoIoED local workspace interactions. Runs entirely on the offline host.
(() => {
  'use strict';
  const tabs = document.querySelector('.section-tabs');
  const names = ['case-profile','case-documents','case-hearings','case-proceedings','case-original'];
  const sections = names.map(id => document.getElementById(id)).filter(Boolean);
  if (tabs && sections.length) {
    document.body.classList.add('case-tabbed');
    tabs.setAttribute('role','tablist');
    const links = [...tabs.querySelectorAll('a[href^="#case-"]')];
    const show = (id) => {
      const target = names.includes(id) && document.getElementById(id) ? id : 'case-profile';
      for (const section of sections) {
        const current = section.id === target;
        section.hidden = !current;
        section.setAttribute('role','tabpanel');
      }
      const retention = document.getElementById('case-retention');
      if (retention) retention.hidden = target !== 'case-profile';
      for (const link of links) {
        const current = link.hash === '#'+target;
        link.classList.toggle('selected',current);
        link.setAttribute('role','tab');
        link.setAttribute('aria-selected',current ? 'true':'false');
      }
    };
    tabs.addEventListener('click', (e) => {
      const link = e.target.closest('a[href^="#case-"]');
      if (!link) return;
      e.preventDefault();
      const id=link.hash.slice(1);
      show(id);
      history.replaceState(null, '', '#'+id);
    });
    window.addEventListener('hashchange', () => show(location.hash.slice(1)));
    show(location.hash.slice(1));
  }
  document.addEventListener('submit', e => {
    const form=e.target.closest('form[data-confirm]');
    if (form && !window.confirm(form.dataset.confirm || 'Are you sure?')) e.preventDefault();
  }, true);
  document.addEventListener('keydown',e=>{
    if ((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==='k') {
      const target=document.querySelector('.quick-case input');
      if(target){e.preventDefault();target.focus();target.select();}
    }
    if(e.key==='Escape'){
      for(const d of document.querySelectorAll('.actions-menu[open]'))d.open=false;
    }
  });
})();