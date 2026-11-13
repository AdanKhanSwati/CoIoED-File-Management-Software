/* CoIoED 13 — client-side layout orchestration only; no data access/telemetry. */
(()=>{
'use strict';
const $=(selector,parent=document)=>parent.querySelector(selector);
const $$=(selector,parent=document)=>Array.from(parent.querySelectorAll(selector));
const body=document.body;
if(!body.classList.contains('v13-root')) return;

// Navigation is a real accessible drawer on small windows.
const navToggle=$('.v13-nav-toggle');
const nav=$('#v13-sidebar');
const overlay=$('.v13-screen-overlay');
const setNav=(open)=>{
  body.classList.toggle('v13-nav-open',Boolean(open));
  if(navToggle){navToggle.setAttribute('aria-expanded',String(Boolean(open)));
    navToggle.setAttribute('aria-label',open?'Close navigation':'Open navigation');}
};
if(navToggle){navToggle.addEventListener('click',()=>setNav(!body.classList.contains('v13-nav-open')));}
if(overlay){overlay.addEventListener('click',()=>setNav(false));}
if(nav){$$('a',nav).forEach(a=>a.addEventListener('click',()=>setNav(false)));}
const search=$('.v13-global-search input');
document.addEventListener('keydown',event=>{
  const element=document.activeElement;
  const typing=element&&['INPUT','TEXTAREA','SELECT'].includes(element.tagName);
  if(event.key==='Escape' && body.classList.contains('v13-nav-open')){setNav(false);navToggle?.focus();}
  if((event.key==='/'&&!typing&&!event.ctrlKey&&!event.altKey)||((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k')){
    if(search){event.preventDefault();search.focus();search.select();}
  }
});

// A context-aware screen hierarchy; existing backend/forms stay intact.
const view=$('.v13-view');
const route=body.dataset.v13Route||'';
if(!view)return;
const screen=body.dataset.v13Screen;

function insertTOC(root,panels,titleText){
  if(!panels.length)return;
  const layout=document.createElement('div');layout.className='v13-hearing-workspace';
  const index=document.createElement('nav');index.className='v13-day-index';index.setAttribute('aria-label',titleText||'On this page');
  const heading=document.createElement('div');heading.className='v13-day-index-title';heading.textContent=titleText||'Contents';index.append(heading);
  const content=document.createElement('div');content.className='v13-hearing-main';
  panels[0].before(layout);layout.append(index,content);
  const links=[];
  panels.forEach((panel,i)=>{
    const h=$('h2',panel);if(!h) {content.append(panel);return;}
    panel.id='v13-section-'+(i+1);
    const link=document.createElement('a');link.href='#'+panel.id;
    const text=document.createElement('strong'); text.textContent=h.textContent.trim().replace(/\s+/g,' ').slice(0,56);
    link.append(text);
    if(panel.classList.contains('v13-day-panel')){
      const count=document.createElement('span');
      const match=(panel.textContent||'').match(/Cases for this hearing day:\s*(\d+)/i);
      count.textContent=match?(match[1]+' scheduled case'+(match[1]==='1'?'':'s')):'Open hearing day';
      link.append(count);
    }
    index.append(link);links.push(link);content.append(panel);
  });
  if(links.length){links[0].classList.add('is-active');links.forEach(l=>l.addEventListener('click',()=>{
    links.forEach(x=>x.classList.remove('is-active'));l.classList.add('is-active');
  }));}
}
if(screen==='hearing' && /^\/notice\/\d+\/?$/.test(route)){
  const panels=$$('.v13-view > .panel');
  panels.forEach((panel)=>{
    const h=$('h2',panel);if(!h)return;
    const label=h.textContent.trim();
    if(/^Hearing Day\s+\d+/i.test(label))panel.classList.add('v13-day-panel');
    else if(/approval|revision|secretary/i.test(label))panel.classList.add('v13-approval-panel');
    else if(/ANNEX|notice metadata/i.test(label))panel.classList.add('v13-notice-meta');
  });
  // Only build the navigation for notices with real day sections.
  if(panels.some(p=>p.classList.contains('v13-day-panel')))insertTOC(view,panels,'Notice sections');
  const controls=$('.v13-view > .actions');if(controls)controls.classList.add('v13-notice-export');
}
if(screen==='users' && route==='/users'){
  const panels=$$('.v13-view > .panel');
  if(panels.length>=2){
    const grid=document.createElement('div');grid.className='v13-users-grid';panels[0].before(grid);panels.forEach(p=>grid.append(p));
  }
}
if(screen==='case' && /^\/case\/\d+\/?$/.test(route) && !$('.section-tabs',view)){
  const panels=$$('.v13-view > .panel');
  if(panels.length>=3){panels.forEach((p,i)=>p.id=p.id||('v13-case-section-'+i));
    const strip=document.createElement('nav');strip.className='v13-case-jumps';strip.setAttribute('aria-label','Case file sections');
    panels.slice(0,8).forEach((p,i)=>{const h=$('h2,h3',p);if(!h)return;const a=document.createElement('a');a.href='#'+p.id;
      a.textContent=h.textContent.trim().replace(/\s+/g,' ').slice(0,38);strip.append(a);});
    if(strip.childElementCount)panels[0].before(strip);
  }
}

// Consistent table operations. Density is a view preference, not a case edit.
const containers=$$('.v13-view .table-container');
containers.forEach((container,i)=>{
  const tools=document.createElement('div');tools.className='v13-table-tools';
  const scroll=document.createElement('span');scroll.className='v13-tablescroll-indicator';
  scroll.textContent='Scroll within the table for additional columns →';
  const density=document.createElement('button');density.type='button';density.className='v13-density-toggle';density.textContent='Compact rows';density.setAttribute('aria-pressed','false');
  density.addEventListener('click',()=>{
    const active=container.classList.toggle('v13-compact');
    density.textContent=active?'Comfortable rows':'Compact rows';density.setAttribute('aria-pressed',String(active));
  });
  tools.append(scroll,density);container.before(tools);
  const check=()=>scroll.classList.toggle('is-visible',container.scrollWidth>container.clientWidth+4);
  check();if(typeof ResizeObserver!=='undefined')new ResizeObserver(check).observe(container);
});

// Screen-specific labels in a consistent typographic structure.
if(screen==='dashboard'){
  const metrics=$('.an-hero-metrics',view);
  if(metrics){metrics.setAttribute('aria-label','Current operational indicators');}
  // Move nothing from underlying analytics: all recorded metrics remain visible.
}

// Mark forms with clear sections and ensure buttons don't submit unintentionally.
$$('.v13-view form').forEach(form=>{
  if(form.action&&/\/notice\//.test(form.action))form.classList.add('v13-hearing-form');
});
})();