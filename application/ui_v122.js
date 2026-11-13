/* CoIoED 12.2 — progressive interface checks. No external services. */
(() => {
  'use strict';
  if (!document.body.classList.contains('ui-v122')) return;
  // Lightweight, self-contained loaded-build confirmation for installation diagnostics.
  document.documentElement.dataset.coioedUi='12.2';
  // Plain office terminology replaces promotional language in the legacy overview.
  const heading=document.querySelector('.an-hero h1');
  if (heading && heading.textContent.trim()==='Command dashboard') heading.textContent='Head Office overview';
  const eyebrow=document.querySelector('.an-hero .an-overline');
  if (eyebrow && eyebrow.textContent.trim()==='HEAD OFFICE · OPERATIONAL INTELLIGENCE') eyebrow.textContent='OFFICE OPERATIONS · CURRENT RECORDS';
  // Accessibility: avoid trapping focus when a user chooses a row action after moving between modules.
  document.querySelectorAll('main .table-container').forEach(el => {
    if (!el.hasAttribute('tabindex')) el.tabIndex=0;
    if (!el.hasAttribute('aria-label')) el.setAttribute('aria-label','Data table; use horizontal scrolling when needed');
  });
})();