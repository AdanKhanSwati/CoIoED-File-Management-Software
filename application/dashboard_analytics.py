"""Permission-aware, read-only operational dashboard for the CoIoED LAN pilot.

Totals labelled 'current' are not affected by historical date filters. Period
measures use the actual domain event date, never the file-import timestamp.
No network requests, persistent queries, or external charting dependencies.
"""
from __future__ import annotations

import html
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode


def esc(v):
    return html.escape(str(v if v is not None else ''), quote=True)


def bounds(query):
    start = str(query.get('from', '') or '').strip()
    end = str(query.get('to', '') or '').strip()
    for value in (start,end):
        if value and date.fromisoformat(value).isoformat() != value:
            raise ValueError('The dashboard needs dates in YYYY-MM-DD format.')
    if start and end and start > end:
        raise ValueError('The starting date cannot be after the ending date.')
    return start,end


def scope(expression, start, end, base='1=1'):
    """Only predefined SQL expressions, never user-input SQL identifiers."""
    sql=base
    params=[]
    if start:
        sql+=f' AND ({expression}) >= ?'
        params.append(start)
    if end:
        sql+=f' AND ({expression}) <= ?'
        params.append(end)
    return sql, params


def case_date_sql(col):
    """Normalize valid complete legacy dates; partial dates are never guessed.

    Source imports may contain DD/MM/YYYY as well as YYYY-MM-DD.  Pattern alone
    does not ensure valid calendar dates, so SQLite date() verifies the result.
    """
    raw=f'trim({col})'
    maybe=(f"CASE WHEN {raw} GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]' "
           f'THEN {raw} '
           f"WHEN {raw} GLOB '[0-3][0-9]/[01][0-9]/[12][0-9][0-9][0-9]' "
           f'THEN substr({raw},7,4)||\'-\'||substr({raw},4,2)||\'-\'||substr({raw},1,2) '
           'ELSE NULL END')
    # SQLite date() normalizes some invalid day values differently between builds;
    # reject dates where parsed day/month/year no longer match the source.
    return f'(CASE WHEN date({maybe}) = ({maybe}) THEN {maybe} ELSE NULL END)'


def count(c, sql, args=()):
    value=c.execute(sql, args).fetchone()[0]
    return 0 if value is None else value


def groups(c, sql, args=()):
    return [(str(r[0] or 'Unspecified'),int(r[1] or 0)) for r in c.execute(sql,args).fetchall()]


def kpi(name, value, hint='', href=None, kind='normal'):
    display=f'{value:,}' if isinstance(value,int) else str(value)
    tag='a' if href else 'div'
    attr=f' href="{esc(href)}"' if href else ''
    return (f'<{tag}{attr} class="an-kpi {esc(kind)}"><span class="an-label">{esc(name)}</span>'
            f'<strong>{esc(display)}</strong><small>{esc(hint)}</small></{tag}>')


def bar_chart(title, items, description='', max_items=7):
    if not items:return '<div class="an-empty">No records available for this breakdown.</div>'
    items=list(items[:max_items]);peak=max((v for _,v in items),default=0)
    contents=''.join(
        '<div class="an-bar-row"><div class="an-bar-caption"><span title="'+esc(name)+'">'+esc(name)+
        '</span><strong>'+f'{value:,}'+'</strong></div><div class="an-track"><div class="an-fill" style="width:'+str(round(value/peak*100,1) if peak else 0)+'%"></div></div></div>'
        for name,value in items)
    return f'<div class="an-chart"><h3>{esc(title)}</h3><p class="an-chart-note">{esc(description)}</p>{contents}</div>'


def section(title, url, subtitle, metrics, charts=(), notes=''):
    content=(f'<section class="an-section"><div class="an-section-head"><div><h2>{esc(title)}</h2>'
             f'<p>{esc(subtitle)}</p></div><a href="{esc(url)}">Open section →</a></div>'
             f'<div class="an-metrics">{"".join(metrics)}</div>')
    if charts:content+='<div class="an-charts">'+''.join(charts)+'</div>'
    if notes:content+=f'<div class="an-method">{esc(notes)}</div>'
    return content+'</section>'


def _money(number):
    d=Decimal(str(number or 0))
    if d >= 10000000:return f'PKR {d/Decimal(10000000):,.2f} crore'
    if d >= 100000:return f'PKR {d/Decimal(100000):,.2f} lakh'
    return f'PKR {d:,.0f}'


def build(c, allowed, query, today=None):
    """Return fully escaped HTML. allowed(permission) must be user-scoped."""
    today=today or date.today()
    start,end=bounds(query)
    current=f'{start or "Earliest available"} — {end or "Latest available"}' if (start or end) else 'All recorded dates'
    this_month=today.replace(day=1)
    period_links=[('All time','/'),('Last 30 days','/?'+urlencode({'from':(today-timedelta(days=29)).isoformat(),'to':today.isoformat()})),
                  ('This month','/?'+urlencode({'from':this_month.isoformat(),'to':today.isoformat()})),
                  ('This year','/?'+urlencode({'from':date(today.year,1,1).isoformat(),'to':today.isoformat()}))]
    content='<div class="an-dashboard"><div class="an-hero"><div><span class="an-overline">HEAD OFFICE · OPERATIONAL INTELLIGENCE</span><h1>Command dashboard</h1><p>Casework, hearings, custody, family support and administration — live figures from this computer’s records.</p></div><div class="an-sync"><span class="an-live-dot"></span>Local database<br><small>Calculated on page load</small></div></div>'
    content+='<form method="get" action="/" class="an-filter" aria-label="Dashboard reporting period"><div><strong>Reporting period</strong><small>Filters event activity, not current outstanding balances</small></div><label>From <input aria-label="Starting date" type="date" name="from" value="'+esc(start)+'"></label><label>To <input aria-label="Ending date" type="date" name="to" value="'+esc(end)+'"></label><button type="submit">Apply dates</button><a class="an-reset" href="/">Clear</a></form>'
    content+='<div class="an-presets">'+''.join(f'<a href="{esc(url)}">{esc(label)}</a>' for label,url in period_links)+'</div>'
    content+=f'<div class="an-scope"><span>PERIOD SELECTED</span> <b>{esc(current)}</b> <small>All date ranges are inclusive. Metrics marked “Current” are always the latest recorded status.</small></div>'
    lead=[];panels=[];warnings=[];follow=[]
    if allowed('cases.view'):
        all_files=count(c,'SELECT COUNT(*) FROM cases');active=count(c,'SELECT COUNT(*) FROM cases WHERE archived=0')
        under=count(c,"SELECT COUNT(*) FROM cases WHERE archived=0 AND status='Under Investigation'")
        unverified=count(c,'SELECT COUNT(*) FROM cases WHERE archived=0 AND COALESCE(case_verified,0)=0')
        archived=all_files-active
        rdate=case_date_sql('registered_date');mdate=case_date_sql('missing_date')
        w,p=scope(rdate,start,end,'archived=0')
        registered=count(c,f'SELECT COUNT(*) FROM cases WHERE {w}',p)
        w2,p2=scope(mdate,start,end,'archived=0')
        incidents=count(c,f'SELECT COUNT(*) FROM cases WHERE {w2}',p2)
        missing_registered=count(c,f'SELECT COUNT(*) FROM cases WHERE archived=0 AND {rdate} IS NULL')
        missing_occ=count(c,f'SELECT COUNT(*) FROM cases WHERE archived=0 AND {mdate} IS NULL')
        lead.extend([kpi('Indexed case files',all_files,'Current • includes archived','/cases'),kpi('Active case files',active,'Current • excludes archived','/cases'),kpi('Under investigation',under,'Current status','/cases'),kpi('Registered in period',registered,'Official registration date','/cases')])
        states=groups(c,'SELECT COALESCE(NULLIF(trim(status),\'\'),\'Unspecified\'),COUNT(*) n FROM cases WHERE archived=0 GROUP BY 1 ORDER BY n DESC')
        regions=groups(c,'SELECT COALESCE(NULLIF(trim(region),\'\'),\'Unspecified\'),COUNT(*) n FROM cases WHERE archived=0 GROUP BY 1 ORDER BY n DESC')
        districts=groups(c,'SELECT COALESCE(NULLIF(trim(district),\'\'),\'Unspecified\'),COUNT(*) n FROM cases WHERE archived=0 GROUP BY 1 ORDER BY n DESC LIMIT 8')
        monthly_cases=groups(c,f'SELECT substr({rdate},1,7),COUNT(*) FROM cases WHERE {w} GROUP BY 1 ORDER BY 1 DESC LIMIT 12',p)[::-1]
        missing_years=groups(c,f'SELECT substr({mdate},1,4),COUNT(*) FROM cases WHERE {w2} GROUP BY 1 ORDER BY 1 DESC LIMIT 10',p2)[::-1]
        panels.append(section('Cases & comprehensive register','/cases','Indexed physical/electronic case files, not a verified count of individual missing persons.',
             [kpi('Recorded registrations',registered,'Selected period • registration date'),kpi('Missing incidents',incidents,'Selected period • complete missing date'),kpi('Archived files',archived,'Current'),kpi('Awaiting verification',unverified,'Current • import status')],
             [bar_chart('Current case status',states),bar_chart('Regional office distribution',regions),bar_chart('Registrations by month • period',monthly_cases),bar_chart('Missing incidents by year • period',missing_years),bar_chart('District distribution',districts)],
             f'Data coverage: {missing_registered:,} active case files lack a valid complete registration date; {missing_occ:,} lack a valid complete missing date. Partial or unknown dates are excluded from precise day-range totals.'))
        if unverified:follow.append(('Unverified case records',unverified,'/cases'))
    if allowed('hearing.view'):
        w,p=scope('d.hearing_date',start,end,"n.status!='Deleted Draft'")
        days=count(c,f'SELECT COUNT(*) FROM hearing_days_v2 d JOIN hearing_notices_v2 n ON n.id=d.notice_id WHERE {w}',p)
        scheduled_people=count(c,f'SELECT COUNT(i.id) FROM hearing_items_v2 i JOIN hearing_days_v2 d ON d.id=i.day_id JOIN hearing_notices_v2 n ON n.id=d.notice_id WHERE {w}',p)
        hearing_rows=count(c,f"SELECT COUNT(DISTINCT CASE WHEN i.group_id IS NULL THEN 'i:'||i.id ELSE 'g:'||i.group_id END) FROM hearing_items_v2 i JOIN hearing_days_v2 d ON d.id=i.day_id JOIN hearing_notices_v2 n ON n.id=d.notice_id WHERE {w}",p)
        drafts=count(c,"SELECT COUNT(*) FROM hearing_notices_v2 WHERE status='Draft'")
        issued=count(c,"SELECT COUNT(*) FROM hearing_notices_v2 WHERE status='Approved'")
        future=count(c,"SELECT COUNT(*) FROM hearing_days_v2 d JOIN hearing_notices_v2 n ON n.id=d.notice_id WHERE n.status!='Deleted Draft' AND d.hearing_date>=?",(today.isoformat(),))
        grouped=count(c,'SELECT COUNT(*) FROM hearing_groups_v2 g JOIN hearing_days_v2 d ON d.id=g.day_id JOIN hearing_notices_v2 n ON n.id=d.notice_id WHERE '+w,p)
        if not lead:lead.append(kpi('Hearing dates in period',days,'Scheduled days','/notices'))
        else:lead.append(kpi('Upcoming hearing days',future,'Current • upcoming','/notices'))
        monthly=groups(c,f'SELECT substr(d.hearing_date,1,7) ym,COUNT(d.id) n FROM hearing_days_v2 d JOIN hearing_notices_v2 n ON n.id=d.notice_id WHERE {w} GROUP BY ym ORDER BY ym DESC LIMIT 10',p)[::-1]
        notices=groups(c,"SELECT status,COUNT(*) FROM hearing_notices_v2 WHERE status!='Deleted Draft' GROUP BY status ORDER BY COUNT(*) DESC")
        panels.append(section('Hearing schedules','/notices','Tracks date sections, scheduled people, combined hearing rows and notice approval.',
             [kpi('Days in period',days,'Hearing date'),kpi('Scheduled people',scheduled_people,'Individual persons in period'),kpi('Hearing rows',hearing_rows,'Combined files count as one row'),kpi('Grouped entries',grouped,'Selected period'),kpi('Draft notices',drafts,'Current'),kpi('Issued notices',issued,'Current')],
             [bar_chart('Hearing dates by month',monthly),bar_chart('Notice approvals • current',notices)],
             'One case/person may appear more than once across dates. An issued notice is not proof that an appearance took place.'))
        if allowed('cases.view'):
            # Aggregate two recognized sources of hearing dates. No assumed history:
            # cases with no recorded prior hearing are NOT called 'never heard'.
            cutoff=(today-timedelta(days=90)).isoformat()
            rotation_sql='''WITH dated AS (
                  SELECT case_id,hearing_date AS dt FROM hearings WHERE hearing_date<=?
                  UNION ALL
                  SELECT i.case_id,d.hearing_date AS dt FROM hearing_items_v2 i
                    JOIN hearing_days_v2 d ON d.id=i.day_id
                    JOIN hearing_notices_v2 n ON n.id=d.notice_id
                  WHERE n.status='Approved' AND d.hearing_date<=?
                ), previous AS (SELECT case_id,MAX(dt) last_dt FROM dated GROUP BY case_id),
                future AS (SELECT DISTINCT i.case_id FROM hearing_items_v2 i
                    JOIN hearing_days_v2 d ON d.id=i.day_id
                    JOIN hearing_notices_v2 n ON n.id=d.notice_id
                    WHERE n.status='Approved' AND d.hearing_date>?)
                SELECT
                  SUM(CASE WHEN p.last_dt IS NOT NULL AND p.last_dt<=? AND f.case_id IS NULL THEN 1 ELSE 0 END),
                  SUM(CASE WHEN p.last_dt IS NULL AND f.case_id IS NULL THEN 1 ELSE 0 END),
                  SUM(CASE WHEN f.case_id IS NOT NULL THEN 1 ELSE 0 END)
                FROM cases c LEFT JOIN previous p ON p.case_id=c.id LEFT JOIN future f ON f.case_id=c.id
                WHERE c.archived=0 AND lower(coalesce(c.status,'')) NOT IN ('disposed','traced','closed')'''
            rotation_row=c.execute(rotation_sql,(today.isoformat(),today.isoformat(),today.isoformat(),cutoff)).fetchone()
            due,unknown,upcoming=[int(v or 0) for v in rotation_row]
            panels.append(section('Hearing rotation & scheduling coverage','/rotation',
                'Monitors unscheduled case files, using only locally recorded hearing history.',
                [kpi('Rotation due (90+ days)',due,'Current • recorded last hearing'),
                 kpi('No recorded past hearing',unknown,'Unverified history • not proof of no hearing'),
                 kpi('Future approved hearing',upcoming,'Current')],
                notes='The 90-day metric uses the most recent hearing recorded in legacy hearing entries or approved notices, and excludes cases with a future approved hearing. It does not establish whether an unrecorded historical hearing occurred.'))
            if due:follow.append(('Hearing rotation due (90+ days)',due,'/rotation'))
        if drafts:follow.append(('Hearing notices in draft',drafts,'/notices'))
    if allowed('production.view'):
        w,p=scope('issued_date',start,end)
        period_orders=count(c,f'SELECT COUNT(*) FROM production_orders WHERE {w}',p)
        pending=count(c,"SELECT COUNT(*) FROM production_orders WHERE order_status='Pending execution'")
        partial=count(c,"SELECT COUNT(*) FROM production_orders WHERE order_status='Partially executed'")
        executed=count(c,"SELECT COUNT(*) FROM production_orders WHERE order_status='Executed'")
        overdue=count(c,"SELECT COUNT(*) FROM production_orders WHERE order_status IN ('Pending execution','Partially executed') AND follow_up_date<>'' AND follow_up_date<?",(today.isoformat(),))
        states=groups(c,'SELECT order_status,COUNT(*) FROM production_orders GROUP BY order_status ORDER BY COUNT(*) DESC')
        panels.append(section('Production orders','/production','Issued orders, execution states and recorded follow-up dates.',
                [kpi('Issued in period',period_orders,'Issuance date'),kpi('Pending execution',pending,'Current'),kpi('Partially executed',partial,'Current'),kpi('Executed',executed,'Current'),kpi('Past follow-up date',overdue,'Current • unresolved')],
                [bar_chart('Execution status • current',states)]))
        if pending+partial:follow.append(('Unresolved production orders',pending+partial,'/production'))
        if overdue:follow.append(('Orders past follow-up date',overdue,'/production'))
    if allowed('fsp.view'):
        w,p=scope('referral_date',start,end,"referral_date<>''")
        referrals=count(c,f'SELECT COUNT(*) FROM fsp WHERE {w}',p)
        received_period_where,received_period_params=scope('receipt_date',start,end,"status='Received' AND receipt_date<>''")
        receipts=count(c,f'SELECT COUNT(*) FROM fsp WHERE {received_period_where}',received_period_params)
        support_total=count(c,'SELECT COUNT(*) FROM fsp')
        open_support=count(c,"SELECT COUNT(*) FROM fsp WHERE status IN ('Referred','Pending Review','Under Review','In Progress','Pending')")
        value=count(c,'SELECT COALESCE(SUM(amount_pkr),0) FROM fsp')
        states=groups(c,'SELECT status,COUNT(*) FROM fsp GROUP BY status ORDER BY COUNT(*) DESC')
        panels.append(section('Family support packages','/fsp','Referral and receipt tracking; declared package amounts are not verified payments.',
                [kpi('Files with FSP',support_total,'Current'),kpi('Referrals in period',referrals,'Referral date'),kpi('Received in period',receipts,'Receipt date'),kpi('Open / in progress',open_support,'Current'),kpi('Recorded package value',_money(value),'Current • not funds disbursed')],
                [bar_chart('Package status • current',states)]))
        if open_support:follow.append(('Family support follow-ups',open_support,'/fsp'))
    if allowed('movement.view'):
        w,p=scope('substr(sent_at,1,10)',start,end)
        dispatched=count(c,f'SELECT COUNT(*) FROM file_movements WHERE {w}',p)
        wr,pr=scope('substr(received_at,1,10)',start,end,"received_at<>''")
        received=count(c,f'SELECT COUNT(*) FROM file_movements WHERE {wr}',pr)
        pending=count(c,"SELECT COUNT(*) FROM file_movements WHERE status='Pending Receipt'")
        overdue=count(c,"SELECT COUNT(*) FROM file_movements WHERE status='Pending Receipt' AND expected_return<>'' AND expected_return<?",(today.isoformat(),))
        states=groups(c,'SELECT status,COUNT(*) FROM file_movements GROUP BY status ORDER BY COUNT(*) DESC')
        panels.append(section('Physical file movement','/movements','Dispatch, acknowledgement and outstanding custody movements.',
                [kpi('Dispatched in period',dispatched,'Dispatch date'),kpi('Receipts in period',received,'Receipt date'),kpi('Awaiting receipt',pending,'Current'),kpi('Past expected return',overdue,'Current • pending')],
                [bar_chart('Custody workflow • current',states)]))
        if pending:follow.append(('Pending file acknowledgements',pending,'/movements'))
    if allowed('hr.view'):
        active=count(c,"SELECT COUNT(*) FROM staff WHERE status='Active'")
        total=count(c,'SELECT COUNT(*) FROM staff')
        w,p=scope('date',start,end)
        attendance=count(c,f'SELECT COUNT(*) FROM attendance WHERE {w}',p)
        w2,p2=scope('start_date',start,end)
        leaves=count(c,f'SELECT COUNT(*) FROM leaves WHERE {w2}',p2)
        pending=count(c,"SELECT COUNT(*) FROM leaves WHERE status='Pending'")
        states=groups(c,'SELECT kind,COUNT(*) FROM attendance WHERE '+w+' GROUP BY kind ORDER BY COUNT(*) DESC',p)
        leave_states=groups(c,'SELECT status,COUNT(*) FROM leaves GROUP BY status ORDER BY COUNT(*) DESC')
        contracts=count(c,"SELECT COUNT(*) FROM documents WHERE employee_id IS NOT NULL AND (lower(category) LIKE '%contract%' OR lower(section) LIKE '%contract%')")
        panels.append(section('HR, attendance & contracts','/hr','Employee directory, recorded daily attendance, leave requests and stored contract documents.',
                [kpi('Active staff',active,'Current'),kpi('Total employees',total,'Current'),kpi('Attendance records',attendance,'Selected period • per employee per day'),kpi('Leave starts',leaves,'Selected period'),kpi('Pending leave decisions',pending,'Current'),kpi('Contract attachments',contracts,'Stored documents')],
                [bar_chart('Attendance types • period',states),bar_chart('Leave decisions • current',leave_states)],
                'Attendance records are not unique people. Leave activity uses the leave start date, not each calendar day of leave.'))
        if pending:follow.append(('Pending leave approvals',pending,'/hr'))
    if allowed('procurement.view'):
        w,p=scope('request_date',start,end)
        count_period=count(c,f'SELECT COUNT(*) FROM procurement_requests WHERE {w}',p)
        period_total=count(c,f'SELECT COALESCE(SUM(CAST(estimated_total AS REAL)),0) FROM procurement_requests WHERE {w}',p)
        total_req=count(c,'SELECT COUNT(*) FROM procurement_requests')
        active_req=count(c,"SELECT COUNT(*) FROM procurement_requests WHERE status IN ('Submitted','Returned','Approved','PO Issued','Partially Received')")
        pending_approval=count(c,"SELECT COUNT(*) FROM procurement_requests WHERE status='Submitted'")
        po_count=count(c,"SELECT COUNT(*) FROM procurement_requests WHERE po_number<>''")
        received_count=count(c,"SELECT COUNT(*) FROM procurement_requests WHERE status='Received'")
        status=groups(c,'SELECT status,COUNT(*) FROM procurement_requests GROUP BY status ORDER BY COUNT(*) DESC')
        months=groups(c,'SELECT substr(request_date,1,7),COUNT(*) FROM procurement_requests WHERE '+w+' GROUP BY 1 ORDER BY 1 DESC LIMIT 10',p)[::-1]
        panels.append(section('Procurement & purchases','/procurement','Requisitions, authorizations, purchase orders and receipt tracking.',
                [kpi('Requisitions in period',count_period,'Request date'),kpi('Estimated value',_money(period_total),'Selected period • not expenditure'),kpi('Recorded requisitions',total_req,'Current total'),kpi('Awaiting approval',pending_approval,'Current'),kpi('Active workflow',active_req,'Current'),kpi('PO reference recorded',po_count,'Current'),kpi('Received',received_count,'Current')],
                [bar_chart('Procurement stages • current',status),bar_chart('Requests by month',months)],
                'Estimated requisition value is a planning estimate; it is not cash spent, procurement budget, or booked payment.'))
        if pending_approval:follow.append(('Procurement approvals awaited',pending_approval,'/procurement'))
    if allowed('proceedings.view'):
        w,p=scope('proceeding_date',start,end)
        n=count(c,f'SELECT COUNT(*) FROM case_proceedings WHERE {w}',p)
        verified=count(c,f'SELECT COUNT(*) FROM case_proceedings WHERE {w} AND verified=1',p)
        total=count(c,'SELECT COUNT(*) FROM case_proceedings')
        kinds=groups(c,'SELECT proceeding_type,COUNT(*) FROM case_proceedings WHERE '+w+' GROUP BY proceeding_type ORDER BY COUNT(*) DESC',p)
        panels.append(section('Proceedings history','/cases','Historical proceedings entered against individual case records.',
                [kpi('Recorded in period',n,'Proceeding date'),kpi('Verified in period',verified,'Verified history'),kpi('History entries',total,'Current database')],
                [bar_chart('Proceeding types • period',kinds)]))
    if allowed('documents.view'):
        w,p=scope('substr(created_at,1,10)',start,end)
        n=count(c,f'SELECT COUNT(*) FROM documents WHERE {w}',p)
        total=count(c,'SELECT COUNT(*) FROM documents')
        cats=groups(c,'SELECT category,COUNT(*) FROM documents WHERE '+w+' GROUP BY category ORDER BY COUNT(*) DESC LIMIT 8',p)
        panels.append(section('Documents & scanned records','/cases','Scanned attachments indexed in the local document repository.',
                [kpi('Uploaded in period',n,'Upload date'),kpi('Stored documents',total,'Current')],
                [bar_chart('Document categories • period',cats)]))
    if allowed('users.manage'):
        enabled=count(c,'SELECT COUNT(*) FROM users WHERE active=1')
        disabled=count(c,'SELECT COUNT(*) FROM users WHERE active=0')
        assigned=count(c,'SELECT COUNT(*) FROM users WHERE staff_id IS NOT NULL')
        panels.append(section('User accounts & permissions','/users','Login accounts and assigned employee access.',
                [kpi('Enabled logins',enabled,'Current'),kpi('Disabled logins',disabled,'Current'),kpi('Employee-linked logins',assigned,'Current')]))
    if allowed('audit.view'):
        w,p=scope('substr(at,1,10)',start,end)
        entries=count(c,f'SELECT COUNT(*) FROM audit WHERE {w}',p)
        actions=groups(c,'SELECT action,COUNT(*) FROM audit WHERE '+w+' GROUP BY action ORDER BY COUNT(*) DESC LIMIT 8',p)
        panels.append(section('Audit & application activity','/audit','Recorded system actions, filtered by the audit timestamp.',
                [kpi('Audit entries in period',entries,'Action date')],
                [bar_chart('Recorded actions • period',actions)]))
    if lead:content+='<div class="an-intro-kicker">CURRENT OPERATIONS AT A GLANCE</div><div class="an-hero-metrics">'+''.join(lead)+'</div>'
    if follow:
        content+='<section class="an-follow"><div class="an-section-head"><div><h2>Priority action queues</h2><p>Current outstanding work, regardless of reporting period.</p></div></div><div class="an-queue">'
        content+=''.join(f'<a href="{esc(url)}"><span>{esc(label)}</span><strong>{value:,}</strong><span aria-hidden="true">↗</span></a>' for label,value,url in sorted(follow,key=lambda x:x[1],reverse=True))+'</div></section>'
    content+=''.join(panels)
    if not panels:content+='<section class="an-section"><h2>No operational analytics available</h2><p>Your account has not been granted access to any reporting modules. Ask your administrator for the appropriate viewing permissions.</p></section>'
    content+='<div class="an-footer"><strong>Reporting definitions & limitations</strong><p>Each module uses its own event date: official case registration/missing date; hearing date; order issue date; FSP referral/receipt date; dispatch/receipt date; attendance/leave start date; procurement request date; and record timestamps for uploads/audit logs. Current statuses and outstanding queues are snapshots and are not reconstructed for a past reporting date. Missing/partial dates are not assigned invented dates. Data appears only for modules you are allowed to view. These are operational indicators, not certified government statistics.</p></div></div>'
    return content


def render(handler, query):
    import sys
    a=sys.modules[handler.__class__.__module__]
    start,end=bounds(query)
    # Single connection produces one consistent read snapshot; permission checks
    # must be evaluated before queries execute, and must never be cached globally.
    with a.db() as c:
        import employee_access
        def permitted(name):return employee_access.allowed(c,handler.user['id'],name)
        content=build(c,permitted,query)
    return handler.write(handler.wrap('Dashboard',content))