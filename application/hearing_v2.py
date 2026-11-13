"""CoIoED Hearing Notices v0.2 (pilot). Stores per-notice snapshots, not links to mutable live data."""
import html, json, re, sys
from datetime import date
from urllib.parse import quote
import notice_export

HEARING_EDITOR={'admin','registrar','inquiry','records'}
APPROVERS={'admin','registrar'}


def db_schema(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS hearing_notices_v2 (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT 'SCHEDULE - INQUIRY HEARING',
            annex_label TEXT NOT NULL DEFAULT 'ANNEX-A', annex_prefix TEXT NOT NULL DEFAULT 'To:',
            letter_reference TEXT NOT NULL DEFAULT 'CoIoED Letter No.2/2/2011(NFH)',
            letter_date TEXT NOT NULL DEFAULT '28th October, 2026',
            status TEXT NOT NULL DEFAULT 'Draft', version INTEGER NOT NULL DEFAULT 1,
            supersedes_id INTEGER REFERENCES hearing_notices_v2(id),
            approved_by INTEGER REFERENCES users(id), approved_at TEXT,
            pdf_sha256 TEXT, xlsx_sha256 TEXT, created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hearing_days_v2 (
            id INTEGER PRIMARY KEY,notice_id INTEGER NOT NULL REFERENCES hearing_notices_v2(id),
            seq INTEGER NOT NULL,hearing_date TEXT NOT NULL, hearing_time TEXT NOT NULL DEFAULT '10:30 AM onwards',
            area_caption TEXT NOT NULL DEFAULT '', source_case_order TEXT NOT NULL DEFAULT '', reconciliation_note TEXT NOT NULL DEFAULT '', reconciled INTEGER NOT NULL DEFAULT 0, annex_label TEXT DEFAULT '',
            annex_prefix TEXT DEFAULT '',letter_reference TEXT DEFAULT '', letter_date TEXT DEFAULT '',title TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS hearing_officer_directory (
            officer_name TEXT PRIMARY KEY COLLATE NOCASE,
            used_count INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hearing_instruction_directory (
            instruction_text TEXT PRIMARY KEY COLLATE NOCASE,
            used_count INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hearing_items_v2 (
            id INTEGER PRIMARY KEY, day_id INTEGER NOT NULL REFERENCES hearing_days_v2(id),
            case_id INTEGER NOT NULL REFERENCES cases(id),seq INTEGER NOT NULL,
            source_snapshot_json TEXT NOT NULL,snapshot_json TEXT NOT NULL,
            attendees_json TEXT NOT NULL DEFAULT '[]',
            special_instructions TEXT NOT NULL DEFAULT '', reviewed INTEGER NOT NULL DEFAULT 0,
            updated_by INTEGER REFERENCES users(id),updated_at TEXT NOT NULL,
            UNIQUE(day_id,case_id));
        CREATE TABLE IF NOT EXISTS hearing_groups_v2 (
            id INTEGER PRIMARY KEY, day_id INTEGER NOT NULL REFERENCES hearing_days_v2(id),
            shared_address TEXT NOT NULL DEFAULT '',
            attendees_json TEXT NOT NULL DEFAULT '[]',
            special_instructions TEXT NOT NULL DEFAULT '', reviewed INTEGER NOT NULL DEFAULT 0,
            updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_hearing_group_day ON hearing_groups_v2(day_id);
        CREATE INDEX IF NOT EXISTS idx_notice_day ON hearing_days_v2(notice_id,seq);
        CREATE INDEX IF NOT EXISTS idx_notice_item ON hearing_items_v2(day_id,seq);
    ''')
    item_cols={x[1] for x in c.execute('PRAGMA table_info(hearing_items_v2)')}
    if 'group_id' not in item_cols:
        c.execute('ALTER TABLE hearing_items_v2 ADD COLUMN group_id INTEGER DEFAULT NULL')
    c.execute('CREATE INDEX IF NOT EXISTS idx_hearing_items_group ON hearing_items_v2(group_id)')
    import hearing_workflow_v121
    hearing_workflow_v121.schema(c)
    existing={x[1] for x in c.execute('PRAGMA table_info(cases)')}
    for col,typ in [('complainant_name','TEXT NOT NULL DEFAULT \'\''),('cnic_owner','TEXT NOT NULL DEFAULT \'Missing person\''),('cnic_relation',"TEXT NOT NULL DEFAULT ''"),('case_verified','INTEGER NOT NULL DEFAULT 0')]:
        if col not in existing:c.execute(f'ALTER TABLE cases ADD COLUMN {col} {typ}')


def appmod(h):return sys.modules[h.__class__.__module__]
def e(value):return html.escape(str(value if value is not None else ''),quote=True)
def get_notices(h,notice_id):
    a=appmod(h)
    with a.db() as c:
        n=c.execute('SELECT * FROM hearing_notices_v2 WHERE id=?',(notice_id,)).fetchone()
        if not n or n['status']=='Deleted Draft':raise ValueError('Notice not found')
        days=[]
        order='seq,id' if n['status']=='Approved' else 'hearing_date ASC,seq ASC,id ASC'
        for day in c.execute(f'SELECT * FROM hearing_days_v2 WHERE notice_id=? ORDER BY {order}',(notice_id,)).fetchall():
            items=[]
            groups={g['id']:dict(g) for g in c.execute('SELECT * FROM hearing_groups_v2 WHERE day_id=?',(day['id'],)).fetchall()}
            for x in c.execute('SELECT * FROM hearing_items_v2 WHERE day_id=? ORDER BY seq,id',(day['id'],)).fetchall():
                z=dict(x);z['snapshot']=json.loads(x['snapshot_json']);z['source_snapshot']=json.loads(x['source_snapshot_json']);z['attendees']=json.loads(x['attendees_json'])
                z['group']=groups.get(z.get('group_id')) if z.get('group_id') else None
                items.append(z)
            days.append({'day':dict(day),'items':items})
    return dict(n),days

def normalize_hearing_day_sequence(c,notice_id):
    """Reassign draft-day display sequence after inserting/changing a date.

    Same-date days retain their previous relative order. Issued notices must
    retain their previously approved sequence and output hashes.
    """
    notice=c.execute('SELECT status FROM hearing_notices_v2 WHERE id=?',(notice_id,)).fetchone()
    if not notice or notice['status']!='Draft':return
    days=c.execute('SELECT id FROM hearing_days_v2 WHERE notice_id=? ORDER BY hearing_date ASC,seq ASC,id ASC',(notice_id,)).fetchall()
    for idx,row in enumerate(days,1):
        c.execute('UPDATE hearing_days_v2 SET seq=? WHERE id=?',(idx,row['id']))


def notice_sections(h,notice_id):
    n,days=get_notices(h,notice_id)
    return n,days,notice_export.notice_data(n,days)

def suggest_officers(h,q):
    """Private authenticated lookup of saved officer names. Never includes case details."""
    a=appmod(h)
    term=str(q.get('q') or '').strip()
    if not term or len(term)>120:
        return h.write(json.dumps({'results':[]}),typ='application/json; charset=utf-8')
    matches={}
    with a.db() as c:
        for r in c.execute('SELECT officer_name,used_count FROM hearing_officer_directory ORDER BY used_count DESC LIMIT 1200'):
            name=str(r['officer_name']).strip()
            if name.casefold().startswith(term.casefold()):matches[name.casefold()]=(name,r['used_count'])
        # Pre-upgrade notices are included even when the directory was created after they were saved.
        for r in c.execute('SELECT attendees_json FROM hearing_items_v2 ORDER BY updated_at DESC LIMIT 15000'):
            try:values=json.loads(r['attendees_json'] or '[]')
            except (TypeError,ValueError):continue
            for name in values:
                name=str(name).strip()
                if name and len(name)<=300 and name.casefold().startswith(term.casefold()) and name.casefold() not in matches:
                    matches[name.casefold()]=(name,0)
        for r in c.execute("SELECT officers FROM hearings WHERE officers!='' ORDER BY created_at DESC LIMIT 3000"):
            for name in (r['officers'] or '').splitlines():
                name=name.strip()
                if name and len(name)<=300 and name.casefold().startswith(term.casefold()) and name.casefold() not in matches:
                    matches[name.casefold()]=(name,0)
    values=sorted(matches.values(),key=lambda t:(-t[1],t[0].casefold()))[:12]
    return h.write(json.dumps({'results':[v[0] for v in values]},ensure_ascii=False),typ='application/json; charset=utf-8')

def suggest_instructions(h,q):
    """Private, read-only lookup of saved historical instructions for hearing editors.

    Search existing saved notice texts as well as the directory so that legacy
    records become available without a migration or a manual re-save.
    """
    a=appmod(h)
    query=str(q.get('q') or '').strip()
    if len(query)<2 or len(query)>160:
        return h.write(json.dumps({'results':[]}),typ='application/json; charset=utf-8')
    query_fold=' '.join(query.casefold().split())
    matches={}
    def add(value,usage=0):
        text=str(value or '').strip()
        if not text or len(text)>4000:return
        folded=' '.join(text.casefold().split())
        if query_fold not in folded:return
        key=folded
        previous=matches.get(key)
        if previous is None or usage>previous[1]:matches[key]=(text,usage,0 if folded.startswith(query_fold) else 1)
    with a.db() as c:
        for r in c.execute('SELECT instruction_text,used_count FROM hearing_instruction_directory ORDER BY used_count DESC,last_used_at DESC LIMIT 4000'):
            add(r['instruction_text'],r['used_count'])
        for r in c.execute("SELECT special_instructions FROM hearing_items_v2 WHERE TRIM(special_instructions)!='' ORDER BY updated_at DESC LIMIT 15000"):
            add(r['special_instructions'])
        for r in c.execute("SELECT instructions FROM hearings WHERE TRIM(instructions)!='' ORDER BY created_at DESC LIMIT 4000"):
            add(r['instructions'])
    ordered=sorted(matches.values(),key=lambda value:(value[2],-value[1],value[0].casefold()))[:10]
    return h.write(json.dumps({'results':[v[0] for v in ordered]},ensure_ascii=False),typ='application/json; charset=utf-8')

def notice_range_label(start, end):
    """Show actual hearing dates without weekdays; preserve IDs internally."""
    if not start:
        return 'No hearing dates assigned'
    def short_label(value):
        dt=date.fromisoformat(value)
        suffix='th' if 11 <= dt.day % 100 <= 13 else {1:'st',2:'nd',3:'rd'}.get(dt.day%10,'th')
        return f'{dt.day}{suffix} {dt.strftime("%B %Y")}'
    return short_label(start) if start == end else short_label(start)+' to '+short_label(end)


def notice_range_for_days(days):
    dates = sorted(d['day']['hearing_date'] for d in days)
    return notice_range_label(dates[0], dates[-1]) if dates else 'No hearing dates assigned'


def url(path,i):return f'{path}?id={i}'


def get(h,path,q):
    a=appmod(h)
    if path=='/notices':
        import procurement
        start,end=procurement.range_params(q)
        with a.db() as c:
            rows=c.execute('''SELECT n.*, (SELECT MIN(d.hearing_date) FROM hearing_days_v2 d WHERE d.notice_id=n.id) AS first_hearing_date,
            (SELECT MAX(d.hearing_date) FROM hearing_days_v2 d WHERE d.notice_id=n.id) AS last_hearing_date,
            (SELECT COUNT(*) FROM hearing_days_v2 d WHERE d.notice_id=n.id) as days,
            (SELECT COUNT(*) FROM hearing_items_v2 i JOIN hearing_days_v2 d ON i.day_id=d.id WHERE d.notice_id=n.id) as cases
            FROM hearing_notices_v2 n WHERE n.status!='Deleted Draft'
            AND ((?='' AND ?='') OR EXISTS(SELECT 1 FROM hearing_days_v2 dd WHERE dd.notice_id=n.id
              AND (?='' OR dd.hearing_date>=?) AND (?='' OR dd.hearing_date<=?)))
            ORDER BY n.id DESC LIMIT 200''',(start,end,start,start,end,end)).fetchall()
        links=[]
        for row in rows:
            cells=(f'<a href="/notice/{row["id"]}"><strong>{e(notice_range_label(row["first_hearing_date"],row["last_hearing_date"]))}</strong></a>',f'v{row["version"]}',e(row['days']),e(row['cases']),e(row['review_stage']),e(row['created_at'][:10]))
            if row['status']=='Draft' and row['review_stage'] in ('Draft','Changes Requested','Submitted for Secretary Review') and h.allowed('hearing.edit'):
                action=f'<form method="post" action="/notice/delete-draft" data-confirm="Delete draft?">{h.csrf()}<input type="hidden" name="notice_id" value="{row["id"]}"><button class="secondary">Delete draft</button></form>'
            else:action='—'
            links.append(cells+(action,))
        form='<div class="panel"><h2>Create a new consolidated hearing schedule</h2><form method="post" action="/notice/create">'+h.csrf()+'<button>Create blank notice</button></form></div>' if h.allowed('hearing.edit') else ''
        date_filters='<div class="panel"><h2>Filter by hearing date</h2><form method="get" class="bar">'+a.field('from','From date',start,typ='date')+a.field('to','To date',end,typ='date')+'<button>Filter notices</button><a class="btn secondary" href="/notices">Clear</a></form></div>'
        return h.write(h.wrap('Hearing Notices','<h1>Hearing Schedule Builder</h1><p class="sub">ANNEX-A notice format | Multiple hearing days | Any number of cases per day | Editable exports</p>'+form+date_filters+'<div class="panel">'+a.table(['Notice','Version','Days','Cases','Status','Created','Actions'],links)+'</div>'))
    if path=='/notice/delete-day':
        # Two-step confirmation page: only a draft editor may delete a day.
        if not h.allowed('hearing.edit'):
            return h.abort(403,'Hearing edit permission required')
        try:day_id=int(q.get('day_id') or '0')
        except (TypeError,ValueError):raise ValueError('Invalid hearing day')
        with a.db() as c:
            day=c.execute("""SELECT d.*,n.status,n.review_stage, (SELECT COUNT(*) FROM hearing_items_v2 i WHERE i.day_id=d.id) AS case_count,
                  (SELECT COUNT(*) FROM hearing_days_v2 x WHERE x.notice_id=d.notice_id) AS day_count
                  FROM hearing_days_v2 d JOIN hearing_notices_v2 n ON n.id=d.notice_id WHERE d.id=?""",(day_id,)).fetchone()
        if not day:raise ValueError('Hearing day not found')
        if day['status']!='Draft' or day['review_stage'] not in ('Draft','Changes Requested','Submitted for Secretary Review'):raise ValueError('Only an editable draft hearing day can be deleted. Secretary review locks the notice.')
        if day['day_count']<=1:raise ValueError('The last hearing day cannot be removed. Delete the whole draft notice, or add another day first.')
        body=(f'<h1>Confirm deletion of hearing day</h1>'
              f'<div class="notice danger"><strong>Delete {e(notice_export.date_label(day["hearing_date"]))}?</strong>'
              f'<p>Area: {e(day["area_caption"] or "Not entered")}</p>'
              f'<p>Only this day will be removed from the draft notice. It contains <strong>{day["case_count"]} scheduled case entry(ies)</strong>, with their notice-specific officer lists, special instructions and edits. This action cannot be undone through the editor.</p>'
              f'<p>The Comprehensive Case Register, proceedings, other hearing days and issued notices will not be deleted.</p></div>'
              f'<form method="post" action="/notice/delete-day">{h.csrf()}'
              f'<input type="hidden" name="day_id" value="{day_id}">'
              f'<input type="hidden" name="expected_notice_id" value="{day["notice_id"]}">'
              f'<button class="secondary" type="submit">Yes, delete this hearing day</button> '
              f'<a class="btn" href="/notice/{day["notice_id"]}">Cancel</a></form>')
        return h.write(h.wrap('Confirm hearing day deletion',body))
    m=re.fullmatch(r'/notice/group/(\d+)',path)
    if m:return edit_group(h,int(m.group(1)))
    m=re.fullmatch(r'/notice/(\d+)',path)
    if m:return detail(h,int(m.group(1)))
    m=re.fullmatch(r'/notice/item/(\d+)',path)
    if m:return edit_item(h,int(m.group(1)))
    if path=='/notice/download/day-pdf':
        # Download one physical hearing day from a multi-day notice. This is
        # read-only: both draft and approved notices may be exported, but the
        # selected day must be a member of the requested notice.
        if not h.allowed('hearing.export'):
            return h.abort(403,'Hearing export permission required')
        try:
            notice_id=int(q.get('id') or '0')
            day_id=int(q.get('day_id') or '0')
        except (TypeError,ValueError):
            raise ValueError('Invalid hearing day or notice identifier')
        if notice_id<1 or day_id<1:
            raise ValueError('Invalid hearing day or notice identifier')
        n,days,sections=notice_sections(h,notice_id)
        match=next((index for index,d in enumerate(days)
                    if int(d['day']['id'])==day_id),None)
        if match is None:
            raise ValueError('This hearing day does not belong to the selected notice')
        # The selected day becomes page one of this standalone file, so its
        # ANNEX-A block appears once and the day heading is never repeated on
        # overflow pages. Do not change the consolidated weekly export.
        pdf=notice_export.pdf_bytes([sections[match]])
        with a.db() as c:
            a.audit(c,h.user['id'],'EXPORT','hearing_day',day_id,
                    f'pdf; notice={notice_id}; day={sections[match]["date"]}')
        stamp=sections[match]['date']
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',stamp):
            stamp='unknown-date'
        filename=f'CoIoED_Hearing_{stamp}_Notice_{notice_id}_Day_{day_id}.pdf'
        disposition='inline' if q.get('print')=='1' else 'attachment'
        return h.write(pdf,typ='application/pdf',extra={
            'Content-Disposition':f'{disposition}; filename="{filename}"',
            'Cache-Control':'no-store'})
    if path in ('/notice/preview','/notice/download/xlsx','/notice/download/pdf'):
        notice_id=int(q.get('id') or '0')
        n,days,sections=notice_sections(h,notice_id)
        if not days:raise ValueError('Add hearing day before exporting')
        if path=='/notice/preview':
            clean_pdf_link=f'<div class="controls" style="background:#e8f2ec;padding:12px;font:13px Arial,sans-serif"><a style="display:inline-block;background:#087b50;color:white;padding:9px 13px;text-decoration:none;border-radius:4px" href="/notice/download/pdf?id={notice_id}">Download CLEAN PDF (no browser headers or footers)</a><span style="margin-left:12px">For browser printing, uncheck More settings > Headers and footers.</span></div>'
            return h.write(notice_export.render_html(sections).replace('<body>', '<body>'+clean_pdf_link,1))
        if not h.allowed('hearing.export'):raise ValueError('You are not permitted to export hearing notices')
        with a.db() as c:a.audit(c,h.user['id'],'EXPORT','hearing_notice',notice_id,path.rsplit('/',1)[-1])
        if path.endswith('/xlsx'):
            data=notice_export.excel_bytes(sections)
            return h.write(data,typ='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',extra={'Content-Disposition':f'attachment; filename="CoIoED_Notice_{notice_id}_v{n["version"]}.xlsx"'})
        data=notice_export.pdf_bytes(sections)
        return h.write(data,typ='application/pdf',extra={'Content-Disposition':f'attachment; filename="CoIoED_Notice_{notice_id}_v{n["version"]}.pdf"'})
    return h.abort(404,'Notice page not found')


def detail(h,notice_id):
    a=appmod(h);n,days=get_notices(h,notice_id)
    import hearing_workflow_v121
    readonly=not hearing_workflow_v121.editable(n) or not h.allowed('hearing.edit')
    desc='<h1>Hearing Schedule: '+e(notice_range_for_days(days))+'</h1><p class="sub">Version '+str(n['version'])+' | '+e(n['status'])+' | Move editable days between notices, print individual days, and preserve master case records.</p>'
    controls=f'<div class="actions"><a class="btn secondary" href="/notices">All notices</a><a class="btn" href="/notice/download/pdf?id={notice_id}">Download CLEAN PDF for printing</a><a class="btn secondary" href="/notice/download/xlsx?id={notice_id}">Download formatted Excel</a><a class="btn secondary" href="/notice/preview?id={notice_id}" target="_blank">Browser preview</a></div>'
    warning='<div class="notice">PDF generation in this PILOT supports Latin characters only. For non-Latin names, use browser print to PDF; the production release needs an approved Unicode renderer. The final PDF and XLSX must be visually checked by the Inquiry Branch.</div>'
    if not readonly:
        form=f'''<div class="panel"><h2>Notice metadata - ANNEX-A (first printed page only)</h2><form action="/notice/save" method="post">{h.csrf()}<input type="hidden" name="notice_id" value="{notice_id}"><div class="two">'''
        for k,label in [('annex_label','ANNEX label'),('annex_prefix','Reference prefix'),('letter_reference','Letter number / reference'),('letter_date','dated'),('title','Centered schedule title')]:form+=a.field(k,label,n[k])
        form+='</div><button>Save header</button></form></div>'
    else:form=f'<div class="panel"><b>{e(n["annex_label"])}</b><p>{e(n["annex_prefix"])} {e(n["letter_reference"])}</p><p>dated {e(n["letter_date"])}</p></div>'
    blocks=[]
    for day_pos,d in enumerate(days,1):
        day=d['day'];id=day['id']
        if readonly:edit=''
        else:
            edit=f'''<form method="post" action="/notice/save-day">{h.csrf()}<input type="hidden" name="day_id" value="{id}"><div class="two">'''
            for k,label,typ in [('hearing_date','Date','date'),('hearing_time','Time','text'),('area_caption','District(s) - separate with commas; final & added automatically','text'),('title','Day title override (optional)','text')]:edit+=a.field(k,label,day[k],typ=typ)
            edit+='</div><button>Save hearing day</button></form>'
        warning=''
        cases=[]
        for it in d['items']:
            s=it['snapshot'];link=f'<a href="/notice/item/{it["id"]}">{e(s["case_no"])}</a>'
            if it.get('group'):
                link+=f' <a class="pill" href="/notice/group/{it["group_id"]}">Combined row</a>'
            buttons=''
            if not readonly:
                buttons=f'''<form method="post" action="/notice/move" class="inline">{h.csrf()}<input type="hidden" name="item_id" value="{it['id']}"><input type="hidden" name="direction" value="up"><button class="secondary">↑</button></form> <form method="post" action="/notice/move" class="inline">{h.csrf()}<input type="hidden" name="item_id" value="{it['id']}"><input type="hidden" name="direction" value="down"><button class="secondary">↓</button></form> <form method="post" action="/notice/remove" class="inline">{h.csrf()}<input type="hidden" name="item_id" value="{it['id']}"><button class="secondary">Remove</button></form>'''
            cases.append((link,e(s['name']),e(s['missing_date']),buttons))
        add=''
        if not readonly:
            add=f'''<form method="post" action="/notice/add-case" class="bar hearing-case-picker">{h.csrf()}<input type="hidden" name="day_id" value="{id}"><input type="hidden" name="case_id" value=""><div class="field" style="flex:1;min-width:240px"><label>Find a case by number or missing person's name</label><input type="text" name="case_no" data-case-lookup="all" data-case-action="fill" placeholder="Type ID or name, then choose a match" autocomplete="off" required></div><button>Add case to this day</button></form>
                <p class="muted small">All active indexed records appear here, including unverified, suffix-less, duplicate, and clubbed IDs. Select a matching result; each source row has a distinct internal ID. Unverified fields must be checked on the notice case entry before approval. CNICs and addresses are never included in search suggestions.</p>'''
        # Single-day download is available beside every hearing date, even
        # after the consolidated notice has been approved and locked.
        download_day_link=(
            f'<a class="btn" href="/notice/download/day-pdf?id={notice_id}&amp;day_id={id}">'
            f'Download PDF for {e(notice_export.date_label(day["hearing_date"]))}</a>'
            if h.allowed('hearing.export') else '')
        print_day_link=(
            f'<a class="btn secondary" href="/notice/download/day-pdf?id={notice_id}&amp;day_id={id}&amp;print=1" target="_blank" rel="noopener">Print Day</a>'
            if h.allowed('hearing.export') else '')
        # Draft-only move retains this day, its group memberships, cases and notes.
        move_day_control=''
        if not readonly:
            with a.db() as c:
                targets=c.execute("""SELECT id FROM hearing_notices_v2
                    WHERE id!=? AND status='Draft' AND COALESCE(review_stage,'Draft') IN ('Draft','Changes Requested','Submitted for Secretary Review')
                    ORDER BY id DESC LIMIT 200""",(notice_id,)).fetchall()
                candidates=[]
                for target in targets:
                    bounds=c.execute('SELECT MIN(hearing_date),MAX(hearing_date) FROM hearing_days_v2 WHERE notice_id=?',(target['id'],)).fetchone()
                    candidates.append((target['id'],notice_range_label(bounds[0],bounds[1])))
            if candidates:
                options=''.join(f'<option value="{other_id}">{e(label)} (ID {other_id})</option>' for other_id,label in candidates)
                move_day_control=(f'<form class="inline" method="post" action="/notice/move-day" data-confirm="Move this entire day to another draft notice?">'
                    f'{h.csrf()}<input type="hidden" name="day_id" value="{id}">'
                    f'<input type="hidden" name="source_notice_id" value="{notice_id}">'
                    f'<label>Move day to <select name="destination_notice_id" required><option value="">Select destination</option>{options}</select></label>'
                    f'<label>New hearing date (optional) <input type="date" name="new_hearing_date" value="{e(day["hearing_date"])}" required></label>'
                    f'<button class="secondary" type="submit">Move Day</button></form>')
        # Put the delete action next to the day-specific PDF rather than in a
        # separate low-visibility panel. The confirmation is server-rendered:
        # no inline JavaScript is required under the restrictive CSP.
        delete_day_link=''
        if not readonly and len(days)>1:
            delete_day_link=(f'<a class="btn secondary" data-day-delete="{id}" '
                             f'href="/notice/delete-day?day_id={id}">Delete this day</a>')
        elif not readonly:
            delete_day_link='<span class="muted small">Only hearing day: delete the entire draft notice to remove it.</span>'
        group_control=''
        ungrouped=[it for it in d['items'] if not it.get('group_id')]
        if not readonly and len(ungrouped)>=2:
            choices=''.join(f'<option value="{it["id"]}">{e(it["snapshot"]["case_no"])} — {e(it["snapshot"]["name"][:80])}</option>' for it in ungrouped)
            group_control=(f'<div class="panel" style="margin-top:14px"><h3>Combine 2 or 3 cases in one hearing row</h3>'
                f'<p class="muted small">Select cases in their intended order. The complainant and address will automatically be copied from the FIRST selected case. Individual master records remain separate.</p>'
                f'<form method="post" action="/notice/group-create">{h.csrf()}<input type="hidden" name="day_id" value="{id}">'
                f'<div class="three"><label>1. Primary case (source for address)<select name="first_item_id" required><option value="">Select case</option>{choices}</select></label>'
                f'<label>2. Second case<select name="second_item_id" required><option value="">Select case</option>{choices}</select></label>'
                f'<label>3. Third case (optional)<select name="third_item_id"><option value="">None</option>{choices}</select></label></div>'
                f'<button type="submit">Combine selected cases</button></form></div>')
        blocks.append(f'<div class="panel"><h2>Hearing Day {day_pos}: {e(notice_export.date_label(day["hearing_date"]))} - {e(day["area_caption"])}</h2><p class="muted small">Cases for this hearing day: {len(d["items"])}. There is no fixed number of cases per day. Add or remove any number of cases; the printed schedule continues onto additional pages as necessary.</p><div class="actions" style="margin:12px 0 18px">{print_day_link}{download_day_link}{move_day_control}{delete_day_link}</div>{edit}{warning}{a.table(["Case ID","Missing person","Missing date","Actions"],cases)}{group_control}{add}</div>')
    addday=''
    if not readonly:
        addday=f'''<div class="panel"><h2>Add another hearing day</h2><form method="post" action="/notice/add-day">{h.csrf()}<input type="hidden" name="notice_id" value="{notice_id}"><div class="three">{a.field('hearing_date','Hearing date',date.today().isoformat(),typ='date',required=True)}{a.field('hearing_time','Time','10:30 AM onwards')}{a.field('area_caption','District(s) (e.g. Mardan, Charsadda, Swat)')}</div><button>Add day</button></form></div>'''
    footer='<div class="panel"><h2>Notice status and revisions</h2><p class="muted">Secretary approval is recorded once for the complete notice. Issued notices are locked; corrections require a linked revision.</p>'
    if hearing_workflow_v121.editable(n) and h.allowed('hearing.edit'):
        footer+=f'<form method="post" action="/notice/delete-draft" data-confirm="Delete draft?">{h.csrf()}<input type="hidden" name="notice_id" value="{notice_id}"><button class="secondary">Delete this draft</button></form>'
    footer+=hearing_workflow_v121.panel(h,n,days)
    if n['status']=='Approved' and h.allowed('hearing.edit'):
        footer+=f'<form method="post" action="/notice/revise">{h.csrf()}<input type="hidden" name="notice_id" value="{notice_id}"><button>Create linked revision</button></form>'
    footer+='</div>'
    return h.write(h.wrap('Notice',desc+controls+warning+form+''.join(blocks)+addday+footer))


def edit_item(h,item_id):
    a=appmod(h)
    with a.db() as c:
        row=c.execute('''SELECT i.*,d.notice_id,n.status,n.review_stage FROM hearing_items_v2 i JOIN hearing_days_v2 d ON i.day_id=d.id JOIN hearing_notices_v2 n ON n.id=d.notice_id WHERE i.id=?''',(item_id,)).fetchone()
    if not row:raise ValueError('Notice case entry not found')
    s=json.loads(row['snapshot_json']);source=json.loads(row['source_snapshot_json']);att=json.loads(row['attendees_json'])
    readonly=row['status']!='Draft' or row['review_stage'] not in ('Draft','Changes Requested','Submitted for Secretary Review') or not h.allowed('hearing.edit')
    info='<h1>Notice Case Entry | '+e(s['case_no'])+'</h1><p class="sub">Case fields below affect this notice only. They never overwrite the master case record.</p>'
    if row['group_id']:
        info+=f'<div class="notice">This person is part of a combined hearing row. <a href="/notice/group/{row["group_id"]}">Edit shared complainant, officers and special instructions here</a>. Individual ID, name, CNIC and missing date remain editable on this page.</div>'
    changed=[k for k in s if str(s[k])!=str(source.get(k,''))]
    info+=f'<div class="notice">Original case snapshot preserved. Notice field overrides: {e(", ".join(changed) if changed else "None")}. Check the ID, name, CNIC, missing date and complainant address against the register before issuing the complete notice. If the source case number is blank, correct it here when possible.</div>'
    if not readonly:
        form=f'<form method="post" action="/notice/save-item">{h.csrf()}<input type="hidden" name="item_id" value="{item_id}"><div class="panel"><h2>Notice-visible case fields</h2><div class="two">'
        for k,label in [('case_no','CoIoED No.'),('name','Name of missing person'),('parentage','Parentage'),('cnic','CNIC — include any relationship exactly as recorded in Comprehensive List'),('missing_date','Missing date (YYYY-MM-DD)'),('address','Complainant and complete address')]:form+=a.field(k,label,s.get(k,''))
        form+='</div></div><div class="panel"><h2>Officers to Attend</h2><p class="muted">Enter one officer or focal person per line. Add as many as needed; remove an officer by deleting their line. The printed and Excel schedule automatically numbers non-empty names vertically, without blank numbered slots.</p>'
        form+='<div class="officer-entry-widget" data-officer-autocomplete="yes">'
        form+='<label for="attendees_text">Officers / names / designations — one per line</label>'
        form+='<p class="muted small">Start typing D, DP, DPO, etc. Select a previous officer with the mouse, Tab or Enter; each selected name fills only the current line. Use as many lines as needed.</p>'
        form+='<textarea id="attendees_text" name="attendees_text" rows="8" spellcheck="false" autocomplete="off" style="width:100%">'+e('\n'.join(str(p).strip() for p in att if str(p).strip()))+'</textarea>'
        form+='<div class="officer-suggestion-list" role="listbox" hidden></div></div>'
        form+='<div class="instruction-entry-widget" data-instruction-autocomplete="yes">'
        form+='<label for="instructions">Special Instructions — printed and exported</label>'
        form+='<p class="muted small">Type at least two characters to see previously saved instructions. Select one to reuse its full wording, then review and edit it for this case.</p>'
        form+='<textarea id="instructions" name="instructions" rows="4" spellcheck="true" autocomplete="off" aria-controls="instruction-suggestion-list">'+e(row['special_instructions'])+'</textarea>'
        form+='<div class="instruction-suggestion-list" id="instruction-suggestion-list" role="listbox" aria-label="Previously saved special instructions" hidden></div></div>'
        form+='<button>Save notice case entry</button></div></form>'
    else:
        form='<div class="panel">'+a.table(['Field','Value'],[(e(k),e(s.get(k,''))) for k in s if k not in ('cnic_owner','cnic_relation')])+'<h2>Officers to Attend</h2>'+a.table(['Position','Attendee'],[(str(i+1),e(x)) for i,x in enumerate(p for p in att if str(p).strip())])+'<h2>Special Instructions</h2><p>'+e(row['special_instructions'])+'</p></div>'
    return h.write(h.wrap('Notice case',info+form+f'<a class="btn secondary" href="/notice/{row["notice_id"]}">Back to notice</a>'))



def edit_group(h,group_id):
    # Edit only the jointly printed fields; never modify either master case.
    a=appmod(h)
    with a.db() as c:
        g=c.execute('''SELECT g.*,d.notice_id,n.status,n.review_stage FROM hearing_groups_v2 g
            JOIN hearing_days_v2 d ON d.id=g.day_id JOIN hearing_notices_v2 n ON n.id=d.notice_id
            WHERE g.id=?''',(group_id,)).fetchone()
        if not g:raise ValueError('Combined hearing row not found')
        members=c.execute('SELECT * FROM hearing_items_v2 WHERE group_id=? AND day_id=? ORDER BY seq,id',(group_id,g['day_id'])).fetchall()
    if len(members) not in (2,3):raise ValueError('Combined hearing row must contain 2 or 3 cases; contact the administrator before issuing.')
    with a.db() as c:
        available=c.execute('SELECT id,snapshot_json FROM hearing_items_v2 WHERE day_id=? AND group_id IS NULL ORDER BY seq,id',(g['day_id'],)).fetchall()
    readonly=g['status']!='Draft' or g['review_stage'] not in ('Draft','Changes Requested','Submitted for Secretary Review') or not h.allowed('hearing.edit')
    names=[]
    for m in members:
        snap=json.loads(m['snapshot_json']);names.append(e(snap.get('case_no',''))+' — '+e(snap.get('name','')))
    heading=f'<h1>Combined hearing entry</h1><p class="sub">'+ ' &amp; '.join(names)+'</p>'
    hint=('<p class="muted">These cases print under one S. #, with individual case IDs, names, CNICs and missing dates. Shared address, officers and instructions appear once. The original shared address was taken from the first selected case and remains editable.</p>')
    if readonly:
        body=f'<div class="panel"><h2>Shared complainant & address</h2><p>{e(g["shared_address"])}</p><h2>Officers to Attend</h2><p style="white-space:pre-wrap">{e(chr(10).join(json.loads(g["attendees_json"])))}</p><h2>Special Instructions</h2><p>{e(g["special_instructions"])}</p></div>'
    else:
        body=(f'<form action="/notice/group-save" method="post">{h.csrf()}<input type="hidden" name="group_id" value="{group_id}">'
            f'<div class="panel">{a.field("shared_address","Shared complainant & address",g["shared_address"],typ="textarea",required=True)}'
            f'<div class="officer-entry-widget" data-officer-autocomplete="yes"><label>Shared Officers to Attend (one per line)</label>'
            f'<textarea name="attendees_text" rows="7">{e(chr(10).join(json.loads(g["attendees_json"])))}</textarea><div class="officer-suggestion-list" role="listbox" hidden></div></div>'
            f'<div class="instruction-entry-widget" data-instruction-autocomplete="yes"><label>Shared Special Instructions</label>'
            f'<textarea name="instructions" rows="5">{e(g["special_instructions"])}</textarea><div class="instruction-suggestion-list" role="listbox" hidden></div></div>'
            f'<button>Save combined row</button></div></form>'
            f'<form method="post" action="/notice/group-split">{h.csrf()}<input type="hidden" name="group_id" value="{group_id}">'
            f'<button class="secondary" type="submit">Separate these cases into individual rows</button></form>')
    if not readonly and len(members)==2 and available:
        options=''.join(f'<option value="{r["id"]}">{e(json.loads(r["snapshot_json"]).get("case_no",""))} — {e(json.loads(r["snapshot_json"]).get("name",""))}</option>' for r in available)
        body+=(f'<div class="panel"><h3>Add a third person to this row</h3><form method="post" action="/notice/group-add-member">{h.csrf()}'
               f'<input type="hidden" name="group_id" value="{group_id}"><label>Select the third case<select name="item_id" required><option value="">Select an individual case</option>{options}</select></label><br>'
               f'<button>Add as third case</button></form><p class="muted small">The shared address and directions stay unchanged. Reconfirm the combined details before approving the notice.</p></div>')
    return h.write(h.wrap('Combined hearing row',heading+hint+body+f'<p><a class="btn secondary" href="/notice/{g["notice_id"]}">Back to notice</a></p>'))


def post(h,path,f):
    import hearing_workflow_v121
    if path in hearing_workflow_v121.PATHS:return hearing_workflow_v121.post(h,path,f)
    if path=='/notice/approve':raise ValueError('Use the Secretary review and recorded issuance workflow instead of direct approval.')
    a=appmod(h);action=path.rsplit('/',1)[-1]
    if action=='approve' and not h.allowed('hearing.approve'):raise ValueError('Employee does not have notice approval permission')
    if action!='approve' and not h.allowed('hearing.edit'):raise ValueError('Employee does not have notice edit permission')
    def getid(k):
        try:return int(f.get(k,'0'))
        except (TypeError,ValueError):raise ValueError('Invalid numeric identifier')
    def notice_open(c,notice_id):
        n=c.execute('SELECT * FROM hearing_notices_v2 WHERE id=?',(notice_id,)).fetchone()
        if not n or n['status']=='Deleted Draft':raise ValueError('Notice not found')
        if not hearing_workflow_v121.editable(n):raise ValueError('Notice is locked for Secretary review, approved or issued. Return for corrections or create a revision.')
        return n
    if path=='/notice/group-create':
        day_id=getid('day_id')
        first=getid('first_item_id');second=getid('second_item_id')
        third=getid('third_item_id') if f.get('third_item_id') else 0
        selected=[first,second]+([third] if third else [])
        if min(selected)<1 or len(set(selected))!=len(selected):
            raise ValueError('Select 2 or 3 different cases in the desired order.')
        with a.db() as c:
            day=c.execute('SELECT notice_id FROM hearing_days_v2 WHERE id=?',(day_id,)).fetchone()
            if not day:raise ValueError('Hearing day not found')
            notice_open(c,day['notice_id'])
            placeholders=','.join('?' for _ in selected)
            members=c.execute(f'SELECT * FROM hearing_items_v2 WHERE id IN ({placeholders}) AND day_id=?',(*selected,day_id)).fetchall()
            if len(members)!=len(selected) or any(m['group_id'] for m in members):
                raise ValueError('All cases must be separate entries on the same hearing day.')
            by_id={m['id']:m for m in members}
            # Position group at its earliest old position but display members in
            # exactly the order selected. This fixes the meaning of FIRST file.
            original=[r['id'] for r in c.execute('SELECT id FROM hearing_items_v2 WHERE day_id=? ORDER BY seq,id',(day_id,)).fetchall()]
            insert_at=min(original.index(v) for v in selected)
            order=[v for v in original if v not in set(selected)]
            order[insert_at:insert_at]=selected
            primary=json.loads(by_id[first]['snapshot_json'])
            # Use original per-case snapshot, not the Comprehensive List after
            # subsequent modifications or a blank equality-based fallback.
            shared_address=primary.get('address','')
            officers=json.loads(by_id[first]['attendees_json'] or '[]')
            instructions=by_id[first]['special_instructions'] or ''
            gid=c.execute('''INSERT INTO hearing_groups_v2(day_id,shared_address,attendees_json,special_instructions,updated_by,updated_at)
                VALUES(?,?,?,?,?,?)''',(day_id,shared_address,json.dumps(officers),instructions,h.user['id'],a.now())).lastrowid
            c.execute(f'UPDATE hearing_items_v2 SET group_id=? WHERE day_id=? AND id IN ({placeholders})',(gid,day_id,*selected))
            for seq,item_id in enumerate(order,1):c.execute('UPDATE hearing_items_v2 SET seq=? WHERE id=?',(seq,item_id))
            a.audit(c,h.user['id'],'COMBINE_HEARING_CASES','hearing_group',gid,'day='+str(day_id)+'; items='+','.join(map(str,selected))+'; primary='+str(first))
        return h.redirect('/notice/group/'+str(gid))
    if path=='/notice/group-add-member':
        gid=getid('group_id');item_id=getid('item_id')
        with a.db() as c:
            grp=c.execute('SELECT g.*,d.notice_id FROM hearing_groups_v2 g JOIN hearing_days_v2 d ON d.id=g.day_id WHERE g.id=?',(gid,)).fetchone()
            if not grp:raise ValueError('Combined row not found')
            notice_open(c,grp['notice_id'])
            members=c.execute('SELECT id FROM hearing_items_v2 WHERE day_id=? AND group_id=? ORDER BY seq,id',(grp['day_id'],gid)).fetchall()
            if len(members)!=2:raise ValueError('Only existing two-person rows can receive a third case.')
            item=c.execute('SELECT * FROM hearing_items_v2 WHERE id=? AND day_id=?',(item_id,grp['day_id'])).fetchone()
            if not item or item['group_id'] is not None:raise ValueError('Choose an ungrouped case from this hearing day.')
            current=[r['id'] for r in c.execute('SELECT id FROM hearing_items_v2 WHERE day_id=? ORDER BY seq,id',(grp['day_id'],)).fetchall()]
            current.remove(item_id)
            group_member_ids=[r['id'] for r in members]
            insert_after=max(current.index(x) for x in group_member_ids)
            current.insert(insert_after+1,item_id)
            c.execute('UPDATE hearing_items_v2 SET group_id=? WHERE id=?',(gid,item_id))
            c.execute('UPDATE hearing_groups_v2 SET reviewed=0,updated_by=?,updated_at=? WHERE id=?',(h.user['id'],a.now(),gid))
            for seq,iid in enumerate(current,1):c.execute('UPDATE hearing_items_v2 SET seq=? WHERE id=?',(seq,iid))
            a.audit(c,h.user['id'],'ADD_TO_HEARING_GROUP','hearing_group',gid,'third item='+str(item_id))
        return h.redirect('/notice/group/'+str(gid))
    if path=='/notice/group-save':
        gid=getid('group_id')
        with a.db() as c:
            grp=c.execute('SELECT g.*,d.notice_id FROM hearing_groups_v2 g JOIN hearing_days_v2 d ON d.id=g.day_id WHERE g.id=?',(gid,)).fetchone()
            if not grp:raise ValueError('Combined row not found')
            notice_open(c,grp['notice_id'])
            if c.execute('SELECT COUNT(*) FROM hearing_items_v2 WHERE group_id=? AND day_id=?',(gid,grp['day_id'])).fetchone()[0] not in (2,3):
                raise ValueError('Combined row has an invalid number of cases.')
            address=f.get('shared_address','').strip()[:5000]
            if not address:raise ValueError('Shared complainant and address are required.')
            officers=[line.strip()[:300] for line in f.get('attendees_text','').splitlines() if line.strip()]
            if sum(map(len,officers))>25000:raise ValueError('Officer list is too long.')
            instructions=f.get('instructions','').strip()[:4000]
            checked=int(grp['reviewed'])
            c.execute('UPDATE hearing_groups_v2 SET shared_address=?,attendees_json=?,special_instructions=?,reviewed=?,updated_by=?,updated_at=? WHERE id=?',
                (address,json.dumps(officers),instructions,checked,h.user['id'],a.now(),gid))
            for officer in set(officers):
                c.execute('''INSERT INTO hearing_officer_directory(officer_name,used_count,last_used_at) VALUES(?,1,?)
                    ON CONFLICT(officer_name) DO UPDATE SET used_count=used_count+1,last_used_at=excluded.last_used_at''',(officer,a.now()))
            if instructions:
                c.execute('''INSERT INTO hearing_instruction_directory(instruction_text,used_count,last_used_at) VALUES(?,1,?)
                    ON CONFLICT(instruction_text) DO UPDATE SET used_count=used_count+1,last_used_at=excluded.last_used_at''',(instructions,a.now()))
            a.audit(c,h.user['id'],'EDIT_HEARING_GROUP','hearing_group',gid,'shared fields reviewed='+str(checked))
        return h.redirect('/notice/'+str(grp['notice_id']))
    if path=='/notice/group-split':
        gid=getid('group_id')
        with a.db() as c:
            grp=c.execute('SELECT g.*,d.notice_id FROM hearing_groups_v2 g JOIN hearing_days_v2 d ON d.id=g.day_id WHERE g.id=?',(gid,)).fetchone()
            if not grp:raise ValueError('Combined row not found')
            notice_open(c,grp['notice_id'])
            c.execute('UPDATE hearing_items_v2 SET group_id=NULL WHERE group_id=? AND day_id=?',(gid,grp['day_id']))
            c.execute('DELETE FROM hearing_groups_v2 WHERE id=?',(gid,))
            a.audit(c,h.user['id'],'SPLIT_HEARING_GROUP','hearing_group',gid,'restored 2 or 3 independent hearing entries')
        return h.redirect('/notice/'+str(grp['notice_id']))
    if path=='/notice/delete-draft':
        id=getid('notice_id')
        with a.db() as c:
            n=notice_open(c,id)
            if c.execute('SELECT 1 FROM hearing_notices_v2 WHERE supersedes_id=? AND status!=\'Deleted Draft\' LIMIT 1',(id,)).fetchone():
                raise ValueError('This draft has a linked later version; deletion must be reviewed separately')
            # Soft delete preserves the original rows, historical references, and audit evidence.
            c.execute('UPDATE hearing_notices_v2 SET status=?, updated_at=? WHERE id=? AND status=\'Draft\'',('Deleted Draft',a.now(),id))
            a.audit(c,h.user['id'],'DELETE_DRAFT','hearing_notice',id,'Hidden from active list; retained for audit')
        return h.redirect('/notices')
    if path=='/notice/create':
        with a.db() as c:
            cur=c.execute('INSERT INTO hearing_notices_v2(created_by,created_at,updated_at) VALUES(?,?,?)',(h.user['id'],a.now(),a.now()))
            a.audit(c,h.user['id'],'CREATE','hearing_notice',cur.lastrowid)
        return h.redirect('/notice/'+str(cur.lastrowid))
    if path=='/notice/save':
        id=getid('notice_id')
        keys=['annex_label','annex_prefix','letter_reference','letter_date','title']
        with a.db() as c:
            notice_open(c,id)
            c.execute('UPDATE hearing_notices_v2 SET '+','.join(f'{k}=?' for k in keys)+',updated_at=? WHERE id=?',tuple(f.get(k,'')[:350] for k in keys)+(a.now(),id))
            a.audit(c,h.user['id'],'EDIT','hearing_notice',id,'notice metadata')
        return h.redirect('/notice/'+str(id))
    if path=='/notice/move-day':
        day_id=getid('day_id')
        source_id=getid('source_notice_id')
        destination_id=getid('destination_notice_id')
        if not day_id or not source_id or not destination_id or source_id==destination_id:
            raise ValueError('Select two different notices and a valid hearing day')
        new_date=str(f.get('new_hearing_date','')).strip()
        if new_date and not a.valid_iso_date(new_date):
            raise ValueError('A valid ISO hearing date is required')
        with a.db() as c:
            # Lock for an all-or-nothing move. Existing group/item rows reference
            # the day ID, so no copying or destructive reassignment is needed.
            c.execute('BEGIN IMMEDIATE')
            source=notice_open(c,source_id)
            destination=notice_open(c,destination_id)
            day=c.execute('SELECT * FROM hearing_days_v2 WHERE id=? AND notice_id=?',(day_id,source_id)).fetchone()
            if not day:
                raise ValueError('This day is no longer in the source notice. No records were moved.')
            target_date=new_date or day['hearing_date']
            if c.execute('SELECT 1 FROM hearing_days_v2 WHERE notice_id=? AND hearing_date=?',(destination_id,target_date)).fetchone():
                raise ValueError('Destination already contains this hearing date. Nothing moved.')
            c.execute('UPDATE hearing_days_v2 SET notice_id=?,hearing_date=? WHERE id=? AND notice_id=?',(destination_id,target_date,day_id,source_id))
            normalize_hearing_day_sequence(c,source_id)
            normalize_hearing_day_sequence(c,destination_id)
            c.execute('UPDATE hearing_notices_v2 SET updated_at=? WHERE id IN (?,?)',(a.now(),source_id,destination_id))
            a.audit(c,h.user['id'],'MOVE_HEARING_DAY','hearing_day',day_id,
                    f'From draft {source_id} to draft {destination_id}; {day["hearing_date"]} to {target_date}; cases/groups preserved')
        return h.redirect('/notice/'+str(destination_id))
    if path=='/notice/delete-day':
        day_id=getid('day_id')
        expected_notice_id=getid('expected_notice_id')
        with a.db() as c:
            day=c.execute('SELECT * FROM hearing_days_v2 WHERE id=?',(day_id,)).fetchone()
            if not day:raise ValueError('Hearing day not found')
            if day['notice_id']!=expected_notice_id:raise ValueError('Hearing notice mismatch. No records changed.')
            notice_open(c,day['notice_id'])
            count=c.execute('SELECT COUNT(*) FROM hearing_days_v2 WHERE notice_id=?',(day['notice_id'],)).fetchone()[0]
            if count<=1:raise ValueError('Cannot delete the last hearing day. Delete the draft notice instead.')
            item_count=c.execute('SELECT COUNT(*) FROM hearing_items_v2 WHERE day_id=?',(day_id,)).fetchone()[0]
            # Delete only notice-specific combined-row records; case register unaffected.
            c.execute('DELETE FROM hearing_groups_v2 WHERE day_id=?',(day_id,))
            c.execute('DELETE FROM hearing_items_v2 WHERE day_id=?',(day_id,))
            c.execute('DELETE FROM hearing_days_v2 WHERE id=? AND notice_id=?',(day_id,day['notice_id']))
            normalize_hearing_day_sequence(c,day['notice_id'])
            c.execute('UPDATE hearing_notices_v2 SET updated_at=? WHERE id=?',(a.now(),day['notice_id']))
            a.audit(c,h.user['id'],'DELETE_HEARING_DAY','hearing_day',day_id,
                    'Draft notice '+str(day['notice_id'])+'; '+str(day['hearing_date'])+'; removed '+str(item_count)+' notice case entries')
        return h.redirect('/notice/'+str(day['notice_id']))
    if path=='/notice/add-day':
        id=getid('notice_id');d=f.get('hearing_date','')
        if not d or not a.valid_iso_date(d):raise ValueError('Valid hearing date required')
        with a.db() as c:
            notice_open(c,id)
            seq=c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM hearing_days_v2 WHERE notice_id=?',(id,)).fetchone()[0]
            c.execute('INSERT INTO hearing_days_v2(notice_id,seq,hearing_date,hearing_time,area_caption) VALUES(?,?,?,?,?)',(id,seq,d,f.get('hearing_time','10:30 AM onwards')[:120],notice_export.district_caption(f.get('area_caption',''))[:180]))
            normalize_hearing_day_sequence(c,id)
            a.audit(c,h.user['id'],'CREATE','hearing_day',id,d)
        return h.redirect('/notice/'+str(id))
    if path=='/notice/save-day':
        id=getid('day_id');d=f.get('hearing_date','')
        if not a.valid_iso_date(d) or not d:raise ValueError('Valid date required')
        with a.db() as c:
            day=c.execute('SELECT * FROM hearing_days_v2 WHERE id=?',(id,)).fetchone()
            if not day:raise ValueError('Day not found')
            notice_open(c,day['notice_id'])
            keys=['hearing_date','hearing_time','area_caption','title']
            c.execute('UPDATE hearing_days_v2 SET '+','.join(k+'=?' for k in keys)+' WHERE id=?',tuple((notice_export.district_caption(f.get(k,'')) if k=='area_caption' else f.get(k,''))[:200] for k in keys)+(id,))
            normalize_hearing_day_sequence(c,day['notice_id'])
            a.audit(c,h.user['id'],'EDIT','hearing_day',id,'time/region/reference')
        return h.redirect('/notice/'+str(day['notice_id']))
    if path=='/notice/add-case':
        dayid=getid('day_id')
        selected=str(f.get('case_id','')).strip()
        typed=str(f.get('case_no','')).strip()
        with a.db() as c:
            day=c.execute('SELECT * FROM hearing_days_v2 WHERE id=?',(dayid,)).fetchone()
            if not day:raise ValueError('Hearing day not found')
            notice_open(c,day['notice_id'])
            if selected:
                if not selected.isdecimal():raise ValueError('Invalid selected case. Search again and choose a dropdown result.')
                record=c.execute('SELECT * FROM cases WHERE id=? AND archived=0',(int(selected),)).fetchone()
                if not record:raise ValueError('Selected case was not found or was archived. Search again.')
                # Prevent changing the case number while retaining a stale selected internal ID.
                if typed and typed != a.display_case_no(record):
                    raise ValueError('Case selection changed. Search again and select a matching case.')
            else:
                # Keyboard/manual fallback: an exact official or original ID is safe only when unique.
                number=a.normalized_case_no(typed)
                matches=c.execute('SELECT * FROM cases WHERE archived=0 AND (case_no=? OR source_case_no=?) ORDER BY id LIMIT 3',(number,typed)).fetchall() if typed else []
                if len(matches)>1:raise ValueError('More than one case uses this ID. Choose the correct person from the search dropdown.')
                if not matches:raise ValueError('Case not found. Type the case ID or missing person name and select a dropdown result.')
                record=matches[0]
            no=a.display_case_no(record)
            if c.execute('SELECT 1 FROM hearing_items_v2 WHERE day_id=? AND case_id=?',(dayid,record['id'])).fetchone():raise ValueError('This case is already scheduled on this hearing day.')
            snapshot={k:str(record[k] or '') for k in ['name','parentage','cnic','cnic_owner','cnic_relation','missing_date','address']}
            # Display exactly what the original register says, never a database surrogate key.
            snapshot['case_no']=str(record['source_case_no'] or '') if record['source_row'] is not None else str(record['case_no'])
            # Keep the actual imported CNIC even if its owner is a relative or unknown.
            # Ownership is displayed separately; it must never cause data loss.
            if record['complainant_name'] and record['complainant_name'].casefold() not in snapshot['address'].casefold():snapshot['address']=record['complainant_name']+', '+snapshot['address']
            seq=c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM hearing_items_v2 WHERE day_id=?',(dayid,)).fetchone()[0]
            cur=c.execute('''INSERT INTO hearing_items_v2(day_id,case_id,seq,source_snapshot_json,snapshot_json,updated_by,updated_at) VALUES(?,?,?,?,?,?,?)''',(dayid,record['id'],seq,json.dumps(snapshot),json.dumps(snapshot),h.user['id'],a.now()))
            a.audit(c,h.user['id'],'ADD','hearing_notice_case',cur.lastrowid,no)
        return h.redirect('/notice/item/'+str(cur.lastrowid))
    if path=='/notice/save-item':
        item_id=getid('item_id')
        with a.db() as c:
            item=c.execute('''SELECT i.*,d.notice_id FROM hearing_items_v2 i JOIN hearing_days_v2 d ON i.day_id=d.id WHERE i.id=?''',(item_id,)).fetchone()
            if not item:raise ValueError('Notice case not found')
            notice_open(c,item['notice_id'])
            source=json.loads(item['source_snapshot_json'])
            keys=['case_no','name','parentage','cnic','missing_date','address']
            current=json.loads(item['snapshot_json'])
            s={k:(f.get(k,current.get(k,'')) or '').strip()[:5000] for k in keys}
            # Preserve older notice metadata in storage but never require a separate
            # CNIC ownership/status question on the hearing entry form.
            for legacy_key in ('cnic_owner','cnic_relation'):
                if legacy_key in current:s[legacy_key]=current[legacy_key]
            if not s['name']:raise ValueError('Missing-person name is required.')
            # Source IDs can legitimately be suffix-less or clubbed. Do not silently rewrite them.
            # A blank original ID must be corrected before marking the notice entry reviewed.
            # Historical sources may have incomplete/irregular missing dates. Retain the original
            # value unchanged; only validate newly entered/replaced dates.
            if s['missing_date'] and s['missing_date']!=source.get('missing_date','') and not a.valid_missing_date(s['missing_date']):
                raise ValueError('New or corrected missing date must be a valid full or partial date.')
            # New UI: any number of one-per-line attendee entries. Blank lines never print.
            # Legacy compatibility: older clients still submit attendee1...attendee8.
            if 'attendees_text' in f:
                attendees=[p.strip()[:300] for p in f.get('attendees_text','').splitlines() if p.strip()]
            else:
                attendees=[f.get(f'attendee{x}','').strip()[:300] for x in range(1,9) if f.get(f'attendee{x}','').strip()]
            if sum(len(x) for x in attendees)>25000:
                raise ValueError('Attendee list is too long for one hearing case entry.')
            reviewed=int(item['reviewed'])  # Legacy flag preserved, no individual approval action
            instructions=f.get('instructions','')[:4000].strip()
            c.execute('UPDATE hearing_items_v2 SET snapshot_json=?,attendees_json=?,special_instructions=?,reviewed=?,updated_by=?,updated_at=? WHERE id=?',(json.dumps(s),json.dumps(attendees),instructions,reviewed,h.user['id'],a.now(),item_id))
            if instructions:
                c.execute('''INSERT INTO hearing_instruction_directory(instruction_text,used_count,last_used_at)
                    VALUES(?,1,?) ON CONFLICT(instruction_text)
                    DO UPDATE SET used_count=used_count+1,last_used_at=excluded.last_used_at''',(instructions,a.now()))
            for officer in set(attendees):
                c.execute('''INSERT INTO hearing_officer_directory(officer_name,used_count,last_used_at) VALUES(?,1,?)
                    ON CONFLICT(officer_name) DO UPDATE SET used_count=used_count+1,last_used_at=excluded.last_used_at''',(officer,a.now()))
            changes=[k for k in keys if s[k]!=source.get(k,'')]
            a.audit(c,h.user['id'],'EDIT','hearing_notice_case',item_id,'changed fields '+','.join(changes)+', reviewed '+str(bool(reviewed)))
        return h.redirect('/notice/'+str(item['notice_id']))
    if path=='/notice/remove':
        id=getid('item_id')
        with a.db() as c:
            item=c.execute('SELECT i.*,d.notice_id FROM hearing_items_v2 i JOIN hearing_days_v2 d ON d.id=i.day_id WHERE i.id=?',(id,)).fetchone()
            if not item:raise ValueError('Entry not found')
            notice_open(c,item['notice_id'])
            if item['group_id']:
                c.execute('UPDATE hearing_items_v2 SET group_id=NULL WHERE group_id=?',(item['group_id'],))
                c.execute('DELETE FROM hearing_groups_v2 WHERE id=?',(item['group_id'],))
                a.audit(c,h.user['id'],'SPLIT_HEARING_GROUP','hearing_group',item['group_id'],'member removed from draft')
            c.execute('DELETE FROM hearing_items_v2 WHERE id=?',(id,))
            a.audit(c,h.user['id'],'REMOVE','hearing_notice_case',id,'from draft only')
        return h.redirect('/notice/'+str(item['notice_id']))
    if path=='/notice/move':
        item_id=getid('item_id');direction=f.get('direction','')
        if direction not in ('up','down'):raise ValueError('Invalid direction')
        with a.db() as c:
            item=c.execute('SELECT i.*,d.notice_id FROM hearing_items_v2 i JOIN hearing_days_v2 d ON d.id=i.day_id WHERE i.id=?',(item_id,)).fetchone()
            if not item:raise ValueError('Entry not found')
            notice_open(c,item['notice_id'])
            if item['group_id']:
                raise ValueError('Separate this combined row before reordering its individual cases.')
            op='<' if direction=='up' else '>'
            seq_order='DESC' if direction=='up' else 'ASC'
            other=c.execute(f'SELECT * FROM hearing_items_v2 WHERE day_id=? AND seq {op} ? ORDER BY seq {seq_order} LIMIT 1',(item['day_id'],item['seq'])).fetchone()
            if other:
                if other['group_id']:
                    raise ValueError('A combined hearing row must stay together. Separate the cases before moving past it.')
                c.execute('UPDATE hearing_items_v2 SET seq=? WHERE id=?',(other['seq'],item_id))
                c.execute('UPDATE hearing_items_v2 SET seq=? WHERE id=?',(item['seq'],other['id']))
                a.audit(c,h.user['id'],'REORDER','hearing_notice_case',item_id,direction)
        return h.redirect('/notice/'+str(item['notice_id']))
    if path=='/notice/revise':
        id=getid('notice_id');n,days=get_notices(h,id)
        if n['status']!='Approved':raise ValueError('Revision requires approved source')
        with a.db() as c:
            cur=c.execute('''INSERT INTO hearing_notices_v2(title,annex_label,annex_prefix,letter_reference,letter_date,version,supersedes_id,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',(n['title'],n['annex_label'],n['annex_prefix'],n['letter_reference'],n['letter_date'],n['version']+1,id,h.user['id'],a.now(),a.now()))
            target=cur.lastrowid
            for d in days:
                dy=d['day'];nd=c.execute('''INSERT INTO hearing_days_v2(notice_id,seq,hearing_date,hearing_time,area_caption,source_case_order,reconciliation_note,reconciled,annex_label,annex_prefix,letter_reference,letter_date,title) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(target,dy['seq'],dy['hearing_date'],dy['hearing_time'],dy['area_caption'],dy['source_case_order'],dy['reconciliation_note'],dy['reconciled'],dy['annex_label'],dy['annex_prefix'],dy['letter_reference'],dy['letter_date'],dy['title'])).lastrowid
                group_ids={}
                for it in d['items']:
                    old_gid=it.get('group_id')
                    if old_gid and old_gid not in group_ids:
                        g=it['group']
                        group_ids[old_gid]=c.execute('''INSERT INTO hearing_groups_v2(day_id,shared_address,attendees_json,special_instructions,reviewed,updated_by,updated_at)
                            VALUES(?,?,?,?,?,?,?)''',(nd,g['shared_address'],g['attendees_json'],g['special_instructions'],g['reviewed'],h.user['id'],a.now())).lastrowid
                    c.execute('''INSERT INTO hearing_items_v2(day_id,case_id,seq,source_snapshot_json,snapshot_json,attendees_json,special_instructions,updated_by,updated_at,reviewed,group_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(nd,it['case_id'],it['seq'],json.dumps(it['source_snapshot']),json.dumps(it['snapshot']),json.dumps(it['attendees']),it['special_instructions'],h.user['id'],a.now(),it['reviewed'],group_ids.get(old_gid)))
            a.audit(c,h.user['id'],'REVISION','hearing_notice',target,'supersedes '+str(id))
        return h.redirect('/notice/'+str(target))
    return h.abort(404,'Notice action not found')