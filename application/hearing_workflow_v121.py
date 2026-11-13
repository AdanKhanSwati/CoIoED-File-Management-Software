"""CoIoED hearing approval and issuance workflow (local, auditable, no external calls).

A signed paper approval is entered by an authorized officer: the software does
NOT imply that the Secretary logged in. Issue creates *scheduled hearing* records
for every case, not fabricated verified actual proceedings.
"""
from __future__ import annotations
from datetime import date
import hashlib, html, re

STAGES=('Draft','Submitted for Secretary Review','Changes Requested',
        'Secretary Approved','Issued','Legacy Approved')
PATHS={'/notice/submit-secretary','/notice/secretary-decision',
       '/notice/issue','/notice/confirm-legacy-issue'}

def e(s):return html.escape(str(s if s is not None else ''),quote=True)

def schema(c):
    cols={x[1] for x in c.execute('PRAGMA table_info(hearing_notices_v2)')}
    for col,definition in (
        ('review_stage',"TEXT NOT NULL DEFAULT 'Draft'"),
        ('secretary_approval_date',"TEXT NOT NULL DEFAULT ''"),
        ('secretary_approval_reference',"TEXT NOT NULL DEFAULT ''"),
        ('secretary_approval_note',"TEXT NOT NULL DEFAULT ''"),
        ('issued_by','INTEGER'),('issued_at',"TEXT NOT NULL DEFAULT ''"),
        ('issue_reference',"TEXT NOT NULL DEFAULT ''")):
        if col not in cols:c.execute(f'ALTER TABLE hearing_notices_v2 ADD COLUMN {col} {definition}')
    c.executescript('''
        CREATE TABLE IF NOT EXISTS hearing_review_events_v121(
            id INTEGER PRIMARY KEY,notice_id INTEGER NOT NULL REFERENCES hearing_notices_v2(id),
            old_stage TEXT NOT NULL,new_stage TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',actor_id INTEGER NOT NULL REFERENCES users(id),
            recorded_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_hearing_review_notice ON hearing_review_events_v121(notice_id,id);
        CREATE TABLE IF NOT EXISTS case_hearing_schedule_v121(
            id INTEGER PRIMARY KEY,notice_id INTEGER NOT NULL REFERENCES hearing_notices_v2(id),
            day_id INTEGER NOT NULL REFERENCES hearing_days_v2(id),
            case_id INTEGER NOT NULL REFERENCES cases(id),
            hearing_date TEXT NOT NULL,
            record_type TEXT NOT NULL DEFAULT 'Scheduled - issued',
            issue_reference TEXT NOT NULL DEFAULT '',
            issued_by INTEGER NOT NULL REFERENCES users(id),issued_at TEXT NOT NULL,
            UNIQUE(notice_id,day_id,case_id));
        CREATE INDEX IF NOT EXISTS idx_case_hearing_schedule_case ON case_hearing_schedule_v121(case_id,hearing_date);
    ''')
    # A pre-upgrade 'Approved' is not proof of the Secretary's signed approval or
    # that an official notice was sent. Its status is retained, and marked legacy.
    c.execute("UPDATE hearing_notices_v2 SET review_stage='Legacy Approved' WHERE status='Approved' AND review_stage='Draft'")

def editable(n):
    return n['status']=='Draft' and n['review_stage'] in ('Draft','Changes Requested','Submitted for Secretary Review')

def review_notice(h,notice_id):
    """Validate the complete notice once; never require per-case approval checkboxes.

    Draft inputs remain editable until Secretary approval is recorded by an
    authorized operator. Every selected case is retained, including members
    of a 2–3 person combined row. Source reconciliation fields are not used.
    """
    import hearing_v2
    n,days=hearing_v2.get_notices(h,notice_id)
    if not days or any(not day['items'] for day in days):
        raise ValueError('Add at least one case to every hearing day before approval.')
    for day in days:
        grouped={}
        for it in day['items']:
            if not it['snapshot'].get('name','').strip():
                raise ValueError('Every scheduled person needs a name.')
            if it.get('group_id'):
                grouped.setdefault(it['group_id'],[]).append(it)
        for members in grouped.values():
            if len(members) not in (2,3) or not members[0].get('group'):
                raise ValueError('Combined hearing entries must contain two or three cases.')
    return n,days

def _transition(c,a,actor,notice_id,old,new,details,changes):
    names=list(changes)
    clauses=[k+'=?' for k in names]+['review_stage=?','updated_at=?']
    result=c.execute('UPDATE hearing_notices_v2 SET '+','.join(clauses)+' WHERE id=? AND review_stage=?',
              (*[changes[k] for k in names],new,a.now(),notice_id,old))
    if result.rowcount!=1:raise ValueError('The hearing review status changed. Refresh before trying again.')
    c.execute('INSERT INTO hearing_review_events_v121(notice_id,old_stage,new_stage,details,actor_id,recorded_at) VALUES(?,?,?,?,?,?)',
              (notice_id,old,new,details[:1500],actor,a.now()))
    a.audit(c,actor,'HEARING_WORKFLOW','hearing_notice',notice_id,old+' -> '+new+'; '+details[:240])

def post(h,path,form):
    import hearing_v2
    a=hearing_v2.appmod(h)
    try:notice_id=int(form.get('notice_id','0'))
    except (ValueError,TypeError):raise ValueError('Invalid hearing notice ID.')
    if notice_id<1:raise ValueError('Invalid notice ID.')
    is_authorizer=h.allowed('hearing.approve')
    # Backward-compatible endpoint name; previous multi-stage review is retired.
    if path=='/notice/submit-secretary':
        raise ValueError('The submission step was removed. Open the notice and use Approved by Secretary, CoIoED.')
    if not is_authorizer:
        raise ValueError('Hearing approval permission is required to record the Secretary decision or issue.')
    with a.db() as c:
        n=c.execute('SELECT * FROM hearing_notices_v2 WHERE id=?',(notice_id,)).fetchone()
    if not n or n['status']=='Deleted Draft':raise ValueError('Notice not found.')
    if path=='/notice/secretary-decision':
        decision=form.get('decision','')
        if decision not in ('approved','changes'):
            raise ValueError('Select a valid approval action.')
        if n['status']!='Draft':
            raise ValueError('Issued schedules are locked. Create a linked revision.')
        if decision=='approved':
            if not editable(n):
                raise ValueError('Notice is already approved. Refresh the page.')
            if form.get('secretary_confirmed')!='yes':
                raise ValueError('Confirm that the Secretary has approved the printed notice.')
            review_notice(h,notice_id)
            new='Secretary Approved'
            # The operator records the date they confirmed the signed approval,
            # not an invented past signature date or Secretary login.
            changes={'secretary_approval_date':date.today().isoformat(),
                     'secretary_approval_reference':'',
                     'secretary_approval_note':'Physical approval confirmed by authorized staff',
                     'approved_by':h.user['id'],'approved_at':a.now()}
            details='Secretary paper approval confirmed by staff (no digital Secretary signature).'
        else:
            if n['review_stage']!='Secretary Approved':
                raise ValueError('Only an approved, unissued notice can be reopened.')
            new='Draft'
            changes={'secretary_approval_date':'','secretary_approval_reference':'',
                     'secretary_approval_note':'', 'approved_by':None,'approved_at':None}
            details='Secretary approval flag cleared to permit draft corrections.'
        with a.db() as c:
            fresh=c.execute('SELECT status,review_stage FROM hearing_notices_v2 WHERE id=?',(notice_id,)).fetchone()
            if not fresh or fresh['status']!='Draft' or fresh['review_stage']!=n['review_stage']:
                raise ValueError('Notice state changed. Refresh before retrying.')
            _transition(c,a,h.user['id'],notice_id,n['review_stage'],new,details,changes)
        return h.redirect('/notice/'+str(notice_id))
    if path in ('/notice/issue','/notice/confirm-legacy-issue'):
        legacy=path.endswith('confirm-legacy-issue')
        if legacy and (n['status']!='Approved' or n['review_stage']!='Legacy Approved'):
            raise ValueError('Only previously approved legacy notices can be confirmed this way.')
        if not legacy and (n['status']!='Draft' or n['review_stage']!='Secretary Approved'):
            raise ValueError('Record the Secretary\'s signed approval before issuing a notice.')
        ref=str(form.get('issue_reference','')).strip()
        if len(ref)<3 or len(ref)>200:raise ValueError('Enter the official dispatch or issue reference.')
        if legacy:
            day=str(form.get('approval_date','')).strip()
            approval_ref=str(form.get('approval_reference','')).strip()
            try:date.fromisoformat(day)
            except ValueError:raise ValueError('Enter the historic written approval date.')
            if len(approval_ref)<3:raise ValueError('Enter a reference proving physical Secretary approval.')
        _,days=review_notice(h,notice_id)
        _,_,sections=hearing_v2.notice_sections(h,notice_id)
        pdf=hearing_v2.notice_export.pdf_bytes(sections)
        xlsx=hearing_v2.notice_export.excel_bytes(sections)
        timestamp=a.now()
        with a.db() as c:
            fresh=c.execute('SELECT status,review_stage FROM hearing_notices_v2 WHERE id=?',(notice_id,)).fetchone()
            if not fresh or fresh['review_stage']!=n['review_stage']:
                raise ValueError('Notice changed while issuing. Refresh and retry.')
            changes={'status':'Approved','issued_by':h.user['id'],'issued_at':timestamp,'issue_reference':ref,
                     'pdf_sha256':hashlib.sha256(pdf).hexdigest(),
                     'xlsx_sha256':hashlib.sha256(xlsx).hexdigest()}
            if legacy:
                # Do not replace historical output hashes with today's new exporter;
                # the previous approved PDF/XLSX may be the official archived copy.
                changes.pop('pdf_sha256',None);changes.pop('xlsx_sha256',None)
                changes.update({'secretary_approval_date':day,'secretary_approval_reference':approval_ref,
                                'approved_by':h.user['id'],'approved_at':timestamp})
            # A new issued revision supersedes the earlier planned appearances,
            # while retaining their history. Do not delete or rewrite proceedings.
            previous=c.execute('SELECT supersedes_id FROM hearing_notices_v2 WHERE id=?',(notice_id,)).fetchone()
            if previous and previous['supersedes_id']:
                c.execute("UPDATE case_hearing_schedule_v121 SET record_type=? WHERE notice_id=? AND record_type='Scheduled - issued'",
                          ('Superseded by notice #'+str(notice_id),previous['supersedes_id']))
            # Case-by-case scheduled dates, NOT a verified proceeding that has already happened.
            for d in days:
                for it in d['items']:
                    c.execute('''INSERT OR IGNORE INTO case_hearing_schedule_v121
                        (notice_id,day_id,case_id,hearing_date,issue_reference,issued_by,issued_at)
                        VALUES(?,?,?,?,?,?,?)''',
                        (notice_id,d['day']['id'],it['case_id'],d['day']['hearing_date'],ref,h.user['id'],timestamp))
            _transition(c,a,h.user['id'],notice_id,n['review_stage'],'Issued',
                        'Dispatch reference '+ref+'; '+str(sum(len(d['items']) for d in days))+' case schedule entries recorded.',changes)
        return h.redirect('/notice/'+str(notice_id))
    raise ValueError('Unsupported workflow action.')

def panel(h,notice,days):
    """One notice-level Secretary approval action; issue remains a separate step."""
    stage=notice['review_stage']
    n_id=int(notice['id'])
    base=f'<input type="hidden" name="notice_id" value="{n_id}">'
    csrf=h.csrf()
    body='<div class="panel"><h2>Secretary approval</h2>'
    body+='<p class="muted">Print the complete notice and obtain the Secretary’s approval on paper. Confirm approval once for the ENTIRE notice; individual case entries do not require separate approval.</p>'
    if stage=='Issued':
        body+='<p><strong>Status: Issued</strong></p>'
        body+=f'<p><strong>Approval recorded:</strong> {e(notice["secretary_approval_date"])} &nbsp; <strong>Dispatch:</strong> {e(notice["issue_reference"])}</p>'
        body+='<p class="muted small">All included cases have their scheduled hearing date in history. This is not proof that actual proceedings took place.</p>'
    elif stage=='Secretary Approved':
        body+='<p><strong>Status: Approved by Secretary, CoIoED</strong></p>'
        body+=f'<p class="muted small">Approval confirmed by an authorized employee on {e(notice["secretary_approval_date"])}. This is a record of paper approval, not a digital Secretary signature.</p>'
        if h.allowed('hearing.approve'):
            body+=f'<form method="post" action="/notice/issue">{csrf}{base}'
            body+='<label>Issue / dispatch reference<input name="issue_reference" maxlength="200" required placeholder="Official notice reference"></label>'
            body+='<button>Issue schedule and update case hearing histories</button></form>'
            body+=f'<form method="post" action="/notice/secretary-decision">{csrf}{base}<input type="hidden" name="decision" value="changes">'
            body+='<button class="secondary">Clear approval and return to draft for changes</button></form>'
    elif notice['status']=='Draft':
        body+='<p><strong>Status: Not approved</strong> — the notice remains editable.</p>'
        if h.allowed('hearing.approve'):
            body+=f'<form method="post" action="/notice/secretary-decision">{csrf}{base}<input type="hidden" name="decision" value="approved">'
            body+='<label><input type="checkbox" name="secretary_confirmed" value="yes" required style="width:auto"> Approved by Secretary, CoIoED (signed printed notice confirmed)</label>'
            body+='<p class="muted small">This approval applies to all hearing days and cases in this notice. The system records your account and confirmation time.</p>'
            body+='<button type="submit">Save Secretary approval</button></form>'
        else:
            body+='<p class="muted small">An employee with hearing approval permission can record the Secretary’s signed decision.</p>'
    elif stage=='Legacy Approved':
        body+=f'<p class="notice">This notice was approved in an older software version; signed approval and issuance are not automatically assumed.</p>'
        if h.allowed('hearing.approve'):
            body+=f'<form method="post" action="/notice/confirm-legacy-issue">{csrf}{base}'
            body+='<label>Historic approval date<input name="approval_date" type="date" required></label>'
            body+='<label>Signed paper approval reference<input name="approval_reference" required></label>'
            body+='<label>Official issue reference<input name="issue_reference" required></label><button>Confirm historic issuance</button></form>'
    with __import__('hearing_v2').appmod(h).db() as c:
        rows=c.execute("SELECT ev.new_stage,ev.details,ev.recorded_at,COALESCE(u.fullname,'Unknown') AS actor FROM hearing_review_events_v121 ev LEFT JOIN users u ON u.id=ev.actor_id WHERE ev.notice_id=? ORDER BY ev.id DESC LIMIT 12",(n_id,)).fetchall()
    if rows:
        body+='<h3>Decision history</h3><div class="table-container"><table><thead><tr><th>When recorded</th><th>Action</th><th>Recorded by</th><th>Details</th></tr></thead><tbody>'
        for row in rows:
            body+='<tr><td>'+e(row['recorded_at'])+'</td><td>'+e(row['new_stage'])+'</td><td>'+e(row['actor'])+'</td><td>'+e(row['details'])+'</td></tr>'
        body+='</tbody></table></div>'
    return body+'</div>'

def case_panel(c,case_id,allowed=True):
    if not allowed:return ''
    rows=c.execute('''SELECT s.*,n.version,n.review_stage,d.hearing_time,d.area_caption
        FROM case_hearing_schedule_v121 s
        JOIN hearing_notices_v2 n ON n.id=s.notice_id
        JOIN hearing_days_v2 d ON d.id=s.day_id
        WHERE s.case_id=? ORDER BY s.hearing_date DESC,s.id DESC LIMIT 100''',(case_id,)).fetchall()
    body='<div class="panel"><h2>Scheduled hearings from issued notices</h2><p class="muted small">Dates below were automatically recorded upon official issuance of signed schedules. They do not prove the hearing took place and do not change the Last Verified Proceeding Date.</p>'
    if rows:
        body+='<div class="table-container"><table><thead><tr><th>Hearing date</th><th>Notice</th><th>Time</th><th>Districts</th><th>Issue ref</th><th>Record</th></tr></thead><tbody>'
        for r in rows:
            body+=f'<tr><td>{e(r["hearing_date"])}</td><td><a href="/notice/{int(r["notice_id"])}">#{int(r["notice_id"])} v{int(r["version"])}</a></td><td>{e(r["hearing_time"])}</td><td>{e(r["area_caption"])}</td><td>{e(r["issue_reference"])}</td><td>{e(r["record_type"])}</td></tr>'
        body+='</tbody></table></div>'
    else:body+='<p>No issued hearing schedules have been recorded for this person.</p>'
    if rows:body+=f'<p class="muted small">Once the hearing actually occurs, record the verified proceeding against its signed order in the <a href="/case/{int(case_id)}#case-proceedings">Proceedings</a> section.</p>'
    return body+'</div>'