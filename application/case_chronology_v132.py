"""CoIoED case chronology, v13.2.

Approved HEARING SCHEDULES are shown automatically alongside verified/unverified
ACTUAL PROCEEDINGS. No scheduled hearing is inserted into case_proceedings: an
approved future hearing is NOT evidence that a proceeding was conducted.

Read-only: derives approved schedule appearances directly from immutable notice
items, including each member of a combined two/three-person hearing row. This
avoids duplicate event creation, enables immediate consistency on approval,
and retains superseded notice history.
"""
from __future__ import annotations
from html import escape
from datetime import date
from urllib.parse import quote


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(c, table):
    # Never use untrusted values as SQL identifiers.
    assert table in {'hearing_notices_v2', 'case_hearing_schedule_v121'}
    return {r[1] for r in c.execute('PRAGMA table_info('+table+')')}


def _safe(value):
    return escape(str(value if value is not None else ''), quote=True)


def _notice_status(r, columns, superseded):
    stage = str(r['review_stage']) if 'review_stage' in columns and r['review_stage'] else ''
    if superseded:
        return 'Superseded schedule', 'superseded'
    if stage == 'Issued':
        return 'Issued / scheduled', 'scheduled'
    if stage == 'Secretary Approved':
        return 'Secretary approved / awaiting issuance', 'scheduled'
    if stage == 'Legacy Approved':
        return 'Legacy approved / paper approval unconfirmed', 'legacy'
    if r['status'] == 'Approved':
        # Original (pre-workflow) approval is not necessarily Secretary's signature.
        return 'Approved notice / issuance unconfirmed', 'legacy'
    return 'Scheduled', 'scheduled'


def event_rows(c, case_id, actual_records=None, *, include_schedule=True, include_actual=True):
    """List one entry per case/notified day and actual proceeding, newest first.

    No migrations or updates. Only official recorded approvals/issued notices
    can yield schedule rows. Unapproved drafts are intentionally excluded.
    """
    case_id=int(case_id)
    events=[]
    if include_schedule:
        tables=_tables(c)
        required={'hearing_notices_v2','hearing_days_v2','hearing_items_v2'}
        if required.issubset(tables):
            cols=_columns(c,'hearing_notices_v2')
            # Selecting n.* supports older and newer notice workflows, without
            # inventing absent columns (and without modifying the database).
            rows=c.execute('''SELECT n.*, d.hearing_date, d.hearing_time,
                    d.area_caption, d.id AS timeline_day_id, i.id AS timeline_item_id,
                    i.group_id AS timeline_group_id
                FROM hearing_items_v2 i
                JOIN hearing_days_v2 d ON d.id=i.day_id
                JOIN hearing_notices_v2 n ON n.id=d.notice_id
                WHERE i.case_id=? AND n.status!='Deleted Draft'
                ORDER BY d.hearing_date DESC,n.id DESC,i.id DESC''',(case_id,)).fetchall()
            all_revisions=c.execute('SELECT id,supersedes_id,status'+(',review_stage' if 'review_stage' in cols else '')+' FROM hearing_notices_v2 WHERE supersedes_id IS NOT NULL').fetchall()
            children={}
            for r in all_revisions:children.setdefault(r['supersedes_id'],[]).append(r)
            for r in rows:
                stage=r['review_stage'] if 'review_stage' in cols else ''
                if r['status']!='Approved' and stage not in ('Secretary Approved','Issued'):
                    continue
                # An older notice is superseded only when its replacement is
                # itself approved or issued, never by an unfinished draft.
                nexts=children.get(r['id'],[])
                superseded=any(n['status']=='Approved' or ('review_stage' in cols and n['review_stage'] in ('Secretary Approved','Issued')) for n in nexts)
                status,tag=_notice_status(r,cols,superseded)
                ref=str(r['issue_reference'] or '') if 'issue_reference' in cols else ''
                approval_date=str(r['secretary_approval_date'] or '') if 'secretary_approval_date' in cols else ''
                events.append({
                    'date':str(r['hearing_date']), 'kind':'Scheduled hearing',
                    'status':status,'tag':tag, 'notice_id':r['id'],
                    'notice_version':r['version'],'issue_reference':ref,
                    'approval_date':approval_date,
                    'description':str(r['area_caption'] or ''),
                    'time':str(r['hearing_time'] or ''),
                    'verified':False, 'source':'schedule',
                    'sortid':int(r['timeline_item_id'])})
    if include_actual:
        if actual_records is None:
            import proceedings
            actual_records=proceedings.history(c, case_id) if 'case_proceedings' in _tables(c) else []
        for r in actual_records:
            verified=bool(r['verified'])
            events.append({
                'date':str(r['proceeding_date']), 'kind':'Actual proceeding',
                'status':'Verified proceeding' if verified else 'Awaiting verification',
                'tag':'actual' if verified else 'unverified',
                'notice_id':None,'notice_version':None,'issue_reference':str(r['source_reference'] or ''),
                'approval_date':'', 'description':str(r['summary'] or ''),
                'time':'','verified':verified,'source':'proceeding','sortid':int(r['id'])})
    events.sort(key=lambda r:(r['date'], r['source']=='proceeding',r['sortid']),reverse=True)
    return events


def render(c, case_id, records=None, *, include_schedule=True, include_actual=True):
    entries=event_rows(c,case_id,records,include_schedule=include_schedule,include_actual=include_actual)
    body=['<section class="panel" id="combined-hearing-chronology" aria-labelledby="chronology-heading">',
          '<h2 id="chronology-heading">Combined hearing &amp; proceedings history</h2>',
          '<p class="muted small">Secretary-approved or issued hearing notices appear here automatically for every included person. '
          'An approved or scheduled hearing is <strong>not</strong> proof that proceedings took place; '
          'only a separately recorded, verified actual proceeding updates the Last Verified Proceeding Date.</p>']
    if not entries:
        body.append('<p class="muted">No approved hearing schedules or actual proceedings recorded for this case.</p></section>')
        return ''.join(body)
    body.append('<div class="table-scroll" style="overflow-x:auto"><table><thead><tr>'
                '<th scope="col">Date</th><th scope="col">Event</th><th scope="col">Status</th>'
                '<th scope="col">Notice / source</th><th scope="col">Details</th>'
                '</tr></thead><tbody>')
    for x in entries:
        if x['notice_id'] is not None:
            ref=(f'<a href="/notice/{int(x["notice_id"])}">Notice #{int(x["notice_id"])}'
                 f' v{int(x["notice_version"] or 1)}</a>')
            if x['issue_reference']:ref+='<div class="muted small">'+_safe(x['issue_reference'])+'</div>'
        else:
            ref=_safe(x['issue_reference'] or 'Proceedings record')
        details=_safe(x['description'])
        if x['time']:details+=(' · ' if details else '')+_safe(x['time'])
        if x['approval_date']:details+=f'<div class="muted small">Secretary approval recorded: {_safe(x["approval_date"])}</div>'
        body.append('<tr><td><strong>'+_safe(x['date'])+'</strong></td>'
                    '<td>'+_safe(x['kind'])+'</td>'
                    '<td><span class="pill">'+_safe(x['status'])+'</span></td>'
                    '<td>'+ref+'</td><td>'+details+'</td></tr>')
    body.append('</tbody></table></div></section>')
    return ''.join(body)