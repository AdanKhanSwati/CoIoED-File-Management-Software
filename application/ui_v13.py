"""CoIoED 13.0: server-rendered application shell, no external dependencies.

Changes presentation ONLY: existing handlers, forms, CSRF, permissions and data
access remain controlled by the main application. No new data storage.
"""
from __future__ import annotations
from html import escape as esc

NAV = (
    ('Workspace', (('/', 'Overview', 'layout-dashboard', None),)),
    ('Casework', (
        ('/cases', 'Comprehensive Register', 'files', 'cases.view'),
        ('/notices', 'Hearing Schedule', 'calendar-days', 'hearing.view'),
        ('/rotation', 'Hearing Rotation', 'clock-3', 'hearing.view'),
        ('/production', 'Production Orders', 'clipboard-check', 'production.view'),
        ('/movements', 'File Movement', 'arrow-left-right', 'movement.view'),
        ('/fsp', 'Family Support', 'heart-handshake', 'fsp.view'),
    )),
    ('Administration', (
        ('/procurement', 'Procurement', 'shopping-bag', 'procurement.view'),
        ('/hr', 'HR & Attendance', 'users-round', 'hr.view'),
        ('/users', 'Employees & Access', 'shield-check', 'users.manage'),
        ('/reports/monthly', 'Reports', 'chart-column', 'reports.view'),
        ('/audit', 'Audit Trail', 'history', 'audit.view'),
    )),
)
PATHS = {
    '/': ('Command overview', 'A consolidated operational picture across authorized Commission records.', ('/notices', 'Prepare hearing notice', 'hearing.edit')),
    '/cases': ('Comprehensive Register', 'Find, review, and maintain the indexed Commission case files.', ('/case/new', 'Create case', 'cases.edit')),
    '/notices': ('Hearing Schedule', 'Prepare consolidated notices, manage hearing dates and issue approved schedules.', None),
    '/rotation': ('Hearing Rotation', 'Track cases approaching the next scheduling window.', None),
    '/production': ('Production Orders', 'Monitor issuance, execution and pending follow-up.', None),
    '/movements': ('File Movement', 'Trace physical case-file custody and receipt.', None),
    '/fsp': ('Family Support', 'Record referrals, package status and supporting records.', None),
    '/procurement': ('Procurement', 'Track requisitions, approvals, orders and receipts.', ('/procurement/new', 'New requisition', 'procurement.edit')),
    '/hr': ('HR & Attendance', 'Personnel records, leave and attendance in one place.', None),
    '/users': ('Employees & Access', 'Employee profiles, user credentials and individual permissions.', ('/users/employee/new', 'Add employee', 'users.manage')),
    '/reports/monthly': ('Reports', 'Generate and review office reporting.', None),
    '/audit': ('Audit Trail', 'Recorded changes and access activity.', None),
}
# Static inline SVG path data, not sourced from records.
ICONS = {
 'layout-dashboard':'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
 'files':'<path d="M6 4h9l4 4v13H6z"/><path d="M15 4v5h4M9 13h7M9 17h7"/>',
 'calendar-days':'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18M7 14h3m4 0h3M7 18h3"/>',
 'clock-3':'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
 'clipboard-check':'<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4.5v-2h6v2M8 13l2 2 5-5"/>',
 'arrow-left-right':'<path d="M4 7h16l-4-4m4 4-4 4M20 17H4l4-4m-4 4 4 4"/>',
 'heart-handshake':'<path d="M20.8 4.5a5.5 5.5 0 0 0-7.8 0L12 5.6 10.9 4.5a5.5 5.5 0 0 0-7.7 7.8L12 21l8.8-8.7a5.5 5.5 0 0 0 0-7.8Z"/>',
 'shopping-bag':'<rect x="4" y="7" width="16" height="14" rx="2"/><path d="M9 9V6a3 3 0 0 1 6 0v3"/>',
 'users-round':'<circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2M17 9a3 3 0 0 1 0 6m2 6v-2a5 5 0 0 0-3-4.6"/>',
 'shield-check':'<path d="m12 2 9 4v6c0 5-3.8 8.5-9 10-5.2-1.5-9-5-9-10V6z"/><path d="m8 12 2.5 2.5L16 9"/>',
 'chart-column':'<path d="M4 20V12h4v8m3 0V4h4v16m3 0v-7h3v7M2 20h20"/>',
 'history':'<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>',
 'search':'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
 'menu':'<path d="M4 6h16M4 12h16M4 18h16"/>',
 'x':'<path d="M5 5l14 14M19 5 5 19"/>',
 'chevron-right':'<path d="m9 18 6-6-6-6"/>',
 'arrow-up-right':'<path d="M7 17 17 7M7 7h10v10"/>',
 'lock':'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
 'log-out':'<path d="M10 17l5-5-5-5m5 5H3M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7"/>',
 'plus':'<path d="M12 5v14M5 12h14"/>',
}

def icon(name):
    return f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">{ICONS.get(name, ICONS["files"])}</svg>'

def resolve(path, title):
    if path.startswith('/notice/'):
        return ('Hearing Notice', 'Manage hearing days, combined case rows and schedule documents.', None, 'hearing')
    if path.startswith('/case/'):
        if path.endswith('/new') or title.lower().startswith(('new case','add case')):
            return ('New Case', 'Register a new case file.', None, 'case')
        return ('Case File', 'Case details, proceedings, documents and movement history.', None, 'case')
    if path.startswith('/users/'):
        return (title if title not in ('Employee','Users') else 'Employee Account', 'Manage individual employee information and authorized access.', None, 'users')
    for p, data in PATHS.items():
        if path == p: return (*data, 'dashboard' if p=='/' else ('register' if p=='/cases' else 'hearing' if p=='/notices' else 'users' if p=='/users' else 'module'))
    return (title,'Commission records and office operations.',None,'module')


def render(handler, title, body):
    """New structural shell. Does not modify content handlers or record data."""
    if not getattr(handler,'user',None):
        # Login page adopts v13 typography but retains normal credential form.
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>{esc(title)} · CoIoED</title><link rel="stylesheet" href="/style.css">'
                f'<link rel="stylesheet" href="/ui-v13.css"></head><body class="v13-login"><main>{body}</main></body></html>')
    path=handler.parse()[0]
    page_title, description, action, section=resolve(path,title)
    user=handler.user
    fullname=esc(user['fullname'])
    initials=''.join(chunk[0] for chunk in str(user['fullname']).split()[:2]).upper() or 'CO'
    nav=[]
    for group, entries in NAV:
        items=[]
        for href,label,glyph,permission in entries:
            if permission is not None and not handler.allowed(permission): continue
            active=(path==href or (href=='/cases' and path.startswith('/case/'))
                    or (href=='/notices' and path.startswith('/notice/'))
                    or (href=='/users' and path.startswith('/users/')))
            items.append(f'<a href="{href}" class="v13-nav-link{" is-active" if active else ""}" {"aria-current=page" if active else ""}>'
                         f'{icon(glyph)}<span>{esc(label)}</span>{"<i></i>" if active else ""}</a>')
        if items:nav.append(f'<div class="v13-nav-group"><div class="v13-nav-category">{esc(group)}</div>{"".join(items)}</div>')
    cta=''
    if action:
        href,label,permission=action
        if handler.allowed(permission): cta=f'<a class="v13-primary-action" href="{href}">{icon("plus")}{esc(label)}</a>'
    role='Employee account' if user['staff_id'] else 'Administrator'
    office='Head Office / Islamabad'
    breadcrumbs='<a href="/">Head Office</a>'+('' if path=='/' else f'<span>{icon("chevron-right")}</span><b>{esc(page_title)}</b>')
    search=''
    if handler.allowed('cases.view'):
        search=(f'<form action="/cases" method="get" class="v13-global-search" role="search">{icon("search")}'
                '<input name="q" data-case-lookup="all" data-case-action="navigate" placeholder="Search case number or missing person" '
                'aria-label="Search cases" autocomplete="off"><kbd>/</kbd></form>')
    return f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(page_title)} · CoIoED</title>
<link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/dashboard.css"><link rel="stylesheet" href="/ui-v13.css">
<script src="/case_lookup.js" defer></script><script src="/workspace.js" defer></script><script src="/officer_lookup.js" defer></script><script src="/instruction_lookup.js" defer></script><script src="/ui-v13.js" defer></script>
</head><body class="authenticated v13-root" data-v13-screen="{esc(section)}" data-v13-route="{esc(path)}">
<a href="#v13-main" class="v13-skip">Skip to content</a>
<div class="v13-layout">
<aside id="v13-sidebar" class="v13-sidebar" aria-label="Primary navigation">
    <div class="v13-identity"><img src="/logo.png" alt="Commission emblem"><div><strong>CoIoED</strong><span>HEAD OFFICE · ISLAMABAD</span></div></div>
    <div class="v13-system-label"><span class="v13-offline-dot"></span>INTERNAL RECORDS WORKSPACE</div>
    <nav class="v13-navigation" aria-label="Commission modules">{''.join(nav)}</nav>
    <div class="v13-sidebar-bottom"><div class="v13-network"><span class="v13-offline-dot"></span> LOCAL / OFFLINE SYSTEM</div>
    <div class="v13-side-footer"><span>Commission records platform</span><strong>VERSION 13.0</strong></div></div>
</aside>
<div class="v13-screen-overlay" data-close-nav="" aria-hidden="true"></div>
<div class="v13-shell">
<header class="v13-topbar"><button type="button" class="v13-nav-toggle" aria-label="Open navigation" aria-controls="v13-sidebar" aria-expanded="false">{icon('menu')}</button>
<div class="v13-topbar-location"><span class="v13-location-dot"></span><b>{esc(office)}</b><small>Case Management & Operations</small></div>
{search}
<div class="v13-account"><span class="v13-account-avatar">{esc(initials)}</span><div class="v13-account-text"><strong>{fullname}</strong><span>{esc(role)}</span></div>
<a href="/account/security" class="v13-account-link" title="Account settings" aria-label="Account settings">{icon('lock')}</a>
<form method="post" action="/logout" class="v13-logout">{handler.csrf()}<button type="submit" title="Sign out" aria-label="Sign out">{icon('log-out')}</button></form></div>
</header>
<main class="v13-main" id="v13-main"><div class="v13-breadcrumbs">{breadcrumbs}</div>
<div class="v13-page-header"><div class="v13-page-header-copy"><div class="v13-page-overline">COMMISSION WORKSPACE <span>/</span> {esc(page_title.upper())}</div>
<h1>{esc(page_title)}</h1><p>{esc(description)}</p></div><div class="v13-page-header-actions">{cta}</div></div>
<div class="v13-view v13-view-{esc(section)}">{body}</div>
<footer class="v13-footer"><span>CoIoED Head Office — Internal records system</span><span>Operational data is subject to verification · UI v13.0</span></footer>
</main></div></div></body></html>'''