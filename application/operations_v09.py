"""Head Office operational queues: hearing rotation, production orders and family support.
Offline test pilot. Data must be explicitly verified before decisions or official exports.
"""
from __future__ import annotations
import html
from datetime import date,datetime
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP

ORDER_STATES=('Pending execution','Partially executed','Executed','Stayed / Withdrawn')
FSP_STATES=('Referred','Pending Review','In Progress','Approved','Rejected','Received')

def e(v):return html.escape(str(v if v is not None else ''),quote=True)

def schema(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS production_orders(
          id INTEGER PRIMARY KEY,case_id INTEGER NOT NULL REFERENCES cases(id),
          order_reference TEXT NOT NULL,issued_date TEXT NOT NULL,order_status TEXT NOT NULL DEFAULT 'Pending execution',
          executed_date TEXT NOT NULL DEFAULT '',follow_up_date TEXT NOT NULL DEFAULT '',
          agency TEXT NOT NULL DEFAULT '',remarks TEXT NOT NULL DEFAULT '',
          document_id INTEGER REFERENCES documents(id),created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL,updated_by INTEGER REFERENCES users(id),updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS production_order_events(
          id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES production_orders(id),
          prior_status TEXT NOT NULL DEFAULT '',new_status TEXT NOT NULL,
          remarks TEXT NOT NULL DEFAULT '',changed_at TEXT NOT NULL,changed_by INTEGER REFERENCES users(id));
        CREATE INDEX IF NOT EXISTS idx_production_case ON production_orders(case_id);
        CREATE INDEX IF NOT EXISTS idx_rotation_hearing_case ON hearing_items_v2(case_id);
        CREATE INDEX IF NOT EXISTS idx_production_status ON production_orders(order_status);
        CREATE TABLE IF NOT EXISTS hearing_history_reviews(
          case_id INTEGER PRIMARY KEY REFERENCES cases(id),reviewed_by INTEGER NOT NULL REFERENCES users(id),
          reviewed_at TEXT NOT NULL,review_note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fsp_allocations(
          id INTEGER PRIMARY KEY,case_id INTEGER NOT NULL REFERENCES cases(id),
          beneficiary_name TEXT NOT NULL,relationship TEXT NOT NULL,percentage TEXT NOT NULL,
          created_at TEXT NOT NULL,updated_by INTEGER REFERENCES users(id),
          UNIQUE(case_id,beneficiary_name,relationship));
        CREATE TABLE IF NOT EXISTS fsp_status_events(
          id INTEGER PRIMARY KEY,case_id INTEGER NOT NULL REFERENCES cases(id),
          old_status TEXT NOT NULL DEFAULT '',new_status TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',
          amount_pkr INTEGER NOT NULL,event_at TEXT NOT NULL,user_id INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS system_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_by INTEGER REFERENCES users(id),updated_at TEXT);
        INSERT OR IGNORE INTO system_settings(key,value) VALUES('fsp_default_pkr','5000000');
    ''')
    columns={r['name'] for r in c.execute('PRAGMA table_info(fsp)').fetchall()}
    for col,kind in [
        ('amount_pkr','INTEGER NOT NULL DEFAULT 5000000'),
        ('referral_date',"TEXT NOT NULL DEFAULT ''"),
        ('rejection_reason',"TEXT NOT NULL DEFAULT ''"),
        ('receipt_date',"TEXT NOT NULL DEFAULT ''"),
        ('receipt_reference',"TEXT NOT NULL DEFAULT ''"),
        ('verification_reference',"TEXT NOT NULL DEFAULT ''")]:
        if col not in columns:c.execute(f'ALTER TABLE fsp ADD COLUMN {col} {kind}')
    # Older FSP rows retain their original statuses and are labelled "legacy - review" if needed.

def _iso(text,required=False):
    if not text and not required:return ''
    try:
        d=date.fromisoformat(text)
        if d.isoformat()==text:return text
    except (ValueError,TypeError):pass
    raise ValueError('Use a valid date in YYYY-MM-DD format.')

def order_save(c,form,actor,timestamp):
    no=form.get('case_no','').strip().upper();case=c.execute('SELECT id FROM cases WHERE case_no=?',(no,)).fetchone()
    if not case:raise ValueError('Production order must link to an existing case number.')
    case_id=case['id'];status=form.get('status','')
    if status not in ORDER_STATES:raise ValueError('Select an allowed production-order status.')
    issued=_iso(form.get('issued_date',''),True);executed=_iso(form.get('executed_date',''))
    follow=_iso(form.get('follow_up_date',''))
    if status=='Executed' and not executed:raise ValueError('Executed orders require an execution date.')
    if executed and (status!='Executed' and status!='Partially executed'):
        raise ValueError('Execution date is permitted only for executed or partially executed orders.')
    if executed and executed<issued:raise ValueError('Execution date cannot precede issuance.')
    reference=form.get('order_reference','').strip()
    if not reference:raise ValueError('The order number / reference is required.')
    docid=int(form.get('document_id','0') or '0')
    if docid:
        if not c.execute('SELECT 1 FROM documents WHERE id=? AND case_id=?',(docid,case_id)).fetchone():raise ValueError('The scanned order must belong to the selected case.')
    order_id=int(form.get('order_id','0') or '0');prior=''
    values=(reference[:120],issued,status,executed,follow,form.get('agency','')[:120],form.get('remarks','')[:2000],docid or None,actor,timestamp)
    if order_id:
        old=c.execute('SELECT case_id,order_status FROM production_orders WHERE id=?',(order_id,)).fetchone()
        if not old or old['case_id']!=case_id:raise ValueError('No matching order to update for this case.')
        prior=old['order_status']
        c.execute('''UPDATE production_orders SET order_reference=?,issued_date=?,order_status=?,executed_date=?,
        follow_up_date=?,agency=?,remarks=?,document_id=?,updated_by=?,updated_at=? WHERE id=?''',(*values,order_id))
    else:
        cur=c.execute('''INSERT INTO production_orders(case_id,order_reference,issued_date,order_status,
        executed_date,follow_up_date,agency,remarks,document_id,created_by,created_at,updated_by,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(case_id,reference[:120],issued,status,executed,follow,form.get('agency','')[:120],form.get('remarks','')[:2000],docid or None,actor,timestamp,actor,timestamp))
        order_id=cur.lastrowid
    c.execute('INSERT INTO production_order_events(order_id,prior_status,new_status,remarks,changed_at,changed_by) VALUES(?,?,?,?,?,?)',
        (order_id,prior,status,form.get('remarks','')[:500],timestamp,actor))
    return order_id,case_id

def _pct(x):
    try:v=Decimal(str(x))
    except InvalidOperation:raise ValueError('Allocation percentage must be numeric.')
    if not v.is_finite() or v<=0 or v>100 or v.as_tuple().exponent < -2:raise ValueError('Percentage must be >0 and <=100 with up to two decimals.')
    return v

def allocations(c,case_id):
    return c.execute('SELECT * FROM fsp_allocations WHERE case_id=? ORDER BY id',(case_id,)).fetchall()

def share_total(c,case_id):
    return sum((Decimal(row['percentage']) for row in allocations(c,case_id)),Decimal('0'))

def calc_shares(c,case_id,amount):
    result=[]
    for row in allocations(c,case_id):
        percent=Decimal(row['percentage'])
        value=(Decimal(amount)*percent/Decimal('100')).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
        result.append((row,value))
    return result

def add_share(c,form,actor,timestamp):
    case_no=form.get('case_no','').strip().upper();case=c.execute('SELECT id FROM cases WHERE case_no=?',(case_no,)).fetchone()
    if not case:raise ValueError('Case not found in comprehensive register.')
    case_id=case['id']; fsp=c.execute('SELECT status FROM fsp WHERE case_id=?',(case_id,)).fetchone()
    if not fsp:raise ValueError('First create a Family Support Package record for the case.')
    if fsp['status'] in ('Approved','Received'):raise ValueError('Received package allocations are locked in this pilot; corrections need a controlled amendment workflow.')
    name=form.get('beneficiary_name','').strip();relation=form.get('relationship','').strip()
    if not name or not relation:raise ValueError('Beneficiary and relationship are required.')
    p=_pct(form.get('percentage',''))
    share_id=int(form.get('share_id','0') or 0)
    if share_id:
        old=c.execute('SELECT case_id FROM fsp_allocations WHERE id=?',(share_id,)).fetchone()
        if not old or old['case_id']!=case_id:raise ValueError('Selected allocation does not belong to this case.')
        current=share_total(c,case_id)
        prior=Decimal(c.execute('SELECT percentage FROM fsp_allocations WHERE id=?',(share_id,)).fetchone()[0])
        if current-prior+p>100:raise ValueError('The allocation total cannot exceed 100%.')
        c.execute('UPDATE fsp_allocations SET beneficiary_name=?,relationship=?,percentage=?,updated_by=? WHERE id=?',
                  (name[:150],relation[:100],str(p),actor,share_id))
    else:
        if share_total(c,case_id)+p>100:raise ValueError('Allocation percentages cannot exceed 100%.')
        c.execute('INSERT INTO fsp_allocations(case_id,beneficiary_name,relationship,percentage,created_at,updated_by) VALUES(?,?,?,?,?,?)',
                  (case_id,name[:150],relation[:100],str(p),timestamp,actor))
    return case_id

def remove_share(c,form):
    share_id=int(form.get('share_id','0') or 0);case_id=int(form.get('case_id','0') or 0)
    fsp=c.execute('SELECT status FROM fsp WHERE case_id=?',(case_id,)).fetchone()
    if fsp and fsp['status'] in ('Approved','Received'):raise ValueError('Cannot delete allocation of received package.')
    if not c.execute('SELECT 1 FROM fsp_allocations WHERE id=? AND case_id=?',(share_id,case_id)).fetchone():raise ValueError('Allocation not found')
    c.execute('DELETE FROM fsp_allocations WHERE id=? AND case_id=?',(share_id,case_id))

def fsp_save(c,form,actor,timestamp):
    case_no=form.get('case_no','').strip().upper();case=c.execute('SELECT id FROM cases WHERE case_no=?',(case_no,)).fetchone()
    if not case:raise ValueError('Case not found in master register.')
    case_id=case['id'];status=form.get('status','')
    if status not in FSP_STATES:raise ValueError('Unrecognized support package status.')
    amount_text=str(form.get('amount_pkr','')).replace(',','').strip()
    try:amount=Decimal(amount_text)
    except InvalidOperation:raise ValueError('Enter a numeric package amount in PKR.')
    if amount!=amount.to_integral_value() or amount<=0 or amount>10**12:raise ValueError('Package amount must be a positive whole PKR amount.')
    amount=int(amount)
    rejected=form.get('rejection_reason','').strip()
    if status=='Rejected' and not rejected:raise ValueError('Rejected package requires a recorded reason.')
    referral=_iso(form.get('referral_date',''));received=_iso(form.get('receipt_date',''))
    receipt_ref=form.get('receipt_reference','').strip()
    if status=='Received' and (not received or not receipt_ref):raise ValueError('A received package requires receipt date and receipt reference.')
    if status in ('Approved','Received') and share_total(c,case_id)!=Decimal('100'):
        raise ValueError('Approved or received package requires family allocations totalling exactly 100%.')
    previous=c.execute('SELECT status FROM fsp WHERE case_id=?',(case_id,)).fetchone()
    if previous and previous['status']=='Received':
        raise ValueError('A received package cannot be silently reopened; use an approved correction process.')
    notes=form.get('notes','').strip()[:1500]
    c.execute('''INSERT INTO fsp(case_id,status,notes,updated_at,amount_pkr,referral_date,rejection_reason,receipt_date,receipt_reference,verification_reference)
    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET
    status=excluded.status,notes=excluded.notes,updated_at=excluded.updated_at,amount_pkr=excluded.amount_pkr,
    referral_date=excluded.referral_date,rejection_reason=excluded.rejection_reason,receipt_date=excluded.receipt_date,
    receipt_reference=excluded.receipt_reference,verification_reference=excluded.verification_reference''',
    (case_id,status,notes,timestamp,amount,referral,rejected,received,receipt_ref[:200],form.get('verification_reference','')[:250]))
    c.execute('INSERT INTO fsp_status_events(case_id,old_status,new_status,reason,amount_pkr,event_at,user_id) VALUES(?,?,?,?,?,?,?)',
        (case_id,previous['status'] if previous else '',status,rejected if status=='Rejected' else notes[:400],amount,timestamp,actor))
    return case_id

def rotation(c,days=90,today=None,query='',limit=200):
    today=today or date.today(); days=int(days)
    if days<1 or days>3650:raise ValueError('Rotation interval must be between 1 and 3650 days.')
    # Approved notices count as official scheduled appearances, not drafts. Historic 'hearings' are separate.
    sql='''
    SELECT c.id,c.case_no,c.name,c.district,c.status,c.case_verified,
      (SELECT MAX(hearing_date) FROM hearings h WHERE h.case_id=c.id AND h.hearing_date<=?) AS last_legacy,
      (SELECT MAX(d.hearing_date) FROM hearing_items_v2 i JOIN hearing_days_v2 d ON d.id=i.day_id
         JOIN hearing_notices_v2 n ON n.id=d.notice_id
         WHERE i.case_id=c.id AND n.status='Approved' AND n.review_stage='Issued' AND NOT EXISTS (SELECT 1 FROM hearing_notices_v2 newest WHERE newest.supersedes_id=n.id AND newest.status='Approved') AND d.hearing_date<=?) AS last_notice,
      (SELECT MIN(d.hearing_date) FROM hearing_items_v2 i JOIN hearing_days_v2 d ON d.id=i.day_id
         JOIN hearing_notices_v2 n ON n.id=d.notice_id
         WHERE i.case_id=c.id AND n.status='Approved' AND n.review_stage='Issued' AND NOT EXISTS (SELECT 1 FROM hearing_notices_v2 newest WHERE newest.supersedes_id=n.id AND newest.status='Approved') AND d.hearing_date>?) AS next_notice,
      (SELECT COUNT(*) FROM hearing_items_v2 i JOIN hearing_days_v2 d ON d.id=i.day_id
         JOIN hearing_notices_v2 n ON n.id=d.notice_id
         WHERE i.case_id=c.id AND n.status='Draft' AND d.hearing_date>=?) AS pending_draft,
      (SELECT MAX(proceeding_date) FROM case_proceedings p WHERE p.case_id=c.id AND p.verified=1
          AND NOT EXISTS(SELECT 1 FROM case_proceedings newer WHERE newer.supersedes_id=p.id)) AS last_proceeding,
      r.reviewed_at as history_verified
    FROM cases c LEFT JOIN hearing_history_reviews r ON r.case_id=c.id
    WHERE c.archived=0 AND LOWER(COALESCE(c.status,'')) NOT IN ('disposed','traced','closed')
      AND (c.case_no LIKE ? OR c.name LIKE ?)
    ORDER BY c.id DESC LIMIT ?'''
    q='%'+query.strip()[:80]+'%'; parameters=(today.isoformat(),)*4+(q,q,min(30000,max(1,int(limit))))
    data=[]
    for r in c.execute(sql,parameters):
        obj=dict(r);last=max(filter(None,[obj['last_legacy'],obj['last_notice'],obj['last_proceeding']]),default=None)
        age=None
        if last:
            try:age=(today-date.fromisoformat(last)).days
            except ValueError:pass
        obj['last_hearing']=last;obj['days_since']=age
        if obj['next_notice']:obj['rotation_status']='Upcoming scheduled'
        elif obj['pending_draft']:obj['rotation_status']='Draft scheduled - review'
        elif age is not None and age>=days:obj['rotation_status']='Rotation due'
        elif age is not None:obj['rotation_status']='Recently scheduled'
        elif obj['history_verified']:obj['rotation_status']='Never scheduled (verified)'
        else:obj['rotation_status']='History unverified'
        data.append(obj)
    priorities={'Rotation due':0,'Never scheduled (verified)':1,'Draft scheduled - review':2,'History unverified':3,'Recently scheduled':4,'Upcoming scheduled':5}
    data.sort(key=lambda x:(priorities.get(x['rotation_status'],9),-(x['days_since'] if x['days_since'] is not None else -1),x['case_no']))
    return data

def _links(h):
    return '<div class="actions">'+''.join(f'<a class="btn secondary" href="{url}">{label}</a>' for url,label in [('/rotation','Hearing rotation'),('/production','Production orders'),('/fsp','Family Support') ] if h.allowed('cases.view') if url!='/fsp' or h.allowed('fsp.view'))+'</div>'

def rotation_page(h,q):
    a=__import__('app');threshold=q.get('days','90');query=q.get('q','').strip()
    if not str(threshold).isdigit():raise ValueError('Rotation threshold must be a whole number of days')
    days=int(threshold)
    with a.db() as c:rows=rotation(c,days,query=query,limit=30000)
    counts={}
    for row in rows:counts[row['rotation_status']]=counts.get(row['rotation_status'],0)+1
    filt=q.get('bucket','All');rows=[r for r in rows if filt=='All' or r['rotation_status']==filt][:300]
    form=f'<div class="panel"><form method="get" class="bar">{a.field("days","Days without a recorded hearing",days,typ="number")}{a.field("q","Search case number / person",query)}{a.field("bucket","Queue category",filt,options=["All","Rotation due","Never scheduled (verified)","History unverified","Draft scheduled - review","Recently scheduled","Upcoming scheduled"])}<button>Apply</button></form></div>'
    status=' '.join(f'<div class="stat"><b>{v:,}</b><span>{e(k)}</span></div>' for k,v in counts.items())
    t=[]
    for r in rows:
        check=''
        if r['rotation_status']=='History unverified' and h.allowed('hearing.edit'):
            check=f'''<form method="post" action="/rotation/verify">{h.csrf()}<input type="hidden" name="case_id" value="{r['id']}"><label>Sources reviewed<input name="review_note" required minlength="15" placeholder="Historical file/register reviewed and relevant periods"></label><label><input type="checkbox" name="confirmed" value="yes" required style="width:auto"> I reviewed the hearing history</label><button class="secondary">Mark review complete</button></form>'''
        t.append((f'<a href="/case/{r["id"]}"><b>{e(r["case_no"])}</b></a>',e(r['name']),e(r['last_hearing'] or 'No verified hearing/proceeding'),e(str(r['days_since']) if r['days_since'] is not None else '—'),e(r['last_proceeding'] or 'Not recorded'),e(r['next_notice'] or '—'),e(r['rotation_status']),check))
    html=f'''<h1>Hearing Rotation & Unscheduled Cases</h1><p class="sub">Find cases last scheduled long ago and avoid leaving eligible inquiries out of rotation.</p><div class="notice"><b>Hearing history is not complete by default.</b> “History unverified” is not equivalent to “never heard.” Actual proceedings and scheduled hearing dates are separate. Only approved schedules count toward hearing history; cases listed on an upcoming approved schedule are not rotation-due. Historical schedules must be verified before relying on this queue.</div>{_links(h)}<div class="grid">{status}</div>{form}<div class="panel"><h2>Rotation review list — showing up to 300 matching cases</h2>{a.table(['Case No.','Missing person','Last hearing / verified proceeding','Days elapsed','Last verified proceeding','Next approved hearing','Queue status','Review history'],t)}</div>'''
    return h.write(h.wrap('Hearing Rotation',html))

def verify_history(h,form):
    a=__import__('app');id=int(form.get('case_id','0'))
    reason=form.get('review_note','').strip()
    if form.get('confirmed')!='yes':raise ValueError('Explicit historical review confirmation is required.')
    if len(reason)<15:raise ValueError('Enter a meaningful statement of the historical hearing register checked.')
    with a.db() as c:
        if not c.execute('SELECT 1 FROM cases WHERE id=?',(id,)).fetchone():raise ValueError('Case not found')
        c.execute('INSERT INTO hearing_history_reviews(case_id,reviewed_by,reviewed_at,review_note) VALUES(?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET reviewed_by=excluded.reviewed_by,reviewed_at=excluded.reviewed_at,review_note=excluded.review_note',(id,h.user['id'],a.now(),reason[:500]))
        a.audit(c,h.user['id'],'VERIFY_HEARING_HISTORY','case',id,reason)
    return h.redirect('/rotation')

def orders_page(h,q):
    a=__import__('app');flt=q.get('status','All');search=q.get('q','').strip();edit_id=int(q.get('edit','0') or 0)
    import procurement
    start,end=procurement.range_params(q)
    sql='''SELECT po.*,c.case_no,c.name,u.fullname changed_by,d.original_name as doc_name FROM production_orders po JOIN cases c ON po.case_id=c.id LEFT JOIN users u ON po.updated_by=u.id LEFT JOIN documents d ON po.document_id=d.id WHERE (?='All' OR po.order_status=?) AND (c.case_no LIKE ? OR c.name LIKE ?)'''
    args=[flt,flt,'%'+search+'%','%'+search+'%']
    if start:sql+=' AND po.issued_date>=?';args.append(start)
    if end:sql+=' AND po.issued_date<=?';args.append(end)
    sql+=' ORDER BY po.id DESC LIMIT 500'
    with a.db() as c:
        rows=c.execute(sql,args).fetchall()
        stats=c.execute('SELECT order_status,count(*) n FROM production_orders GROUP BY order_status').fetchall()
        old=c.execute('SELECT po.*,c.case_no FROM production_orders po JOIN cases c ON c.id=po.case_id WHERE po.id=?',(edit_id,)).fetchone() if edit_id else None
        events=c.execute('SELECT pe.*,c.case_no FROM production_order_events pe JOIN production_orders po ON pe.order_id=po.id JOIN cases c ON c.id=po.case_id ORDER BY pe.id DESC LIMIT 30').fetchall()
    if old and not h.allowed('production.edit'):raise ValueError('You cannot edit this order')
    start=dict(old) if old else {}
    f=''
    if h.allowed('production.edit'):
        f=f'''<div class="panel"><h2>{'Update order #' + str(edit_id) if edit_id else 'Register issued production order'}</h2><p class="muted">The source comprehensive register does not reliably identify production order status. Enter only verified orders and attach the source scan to the case file first.</p><form method="post" action="/production/save">{h.csrf()}<input type="hidden" name="order_id" value="{edit_id}"><div class="three">'''
        fields=[('case_no','CoIoED No.',start.get('case_no',''),None),('order_reference','Order reference *',start.get('order_reference',''),None),('issued_date','Date issued *',start.get('issued_date',''),'date'),('status','Execution status',start.get('order_status','Pending execution'),'choice'),('executed_date','Execution date',start.get('executed_date',''),'date'),('follow_up_date','Next follow-up',start.get('follow_up_date',''),'date'),('agency','Agency / office concerned',start.get('agency',''),None),('document_id','Linked scan/document ID (optional)',start.get('document_id','') or '', 'number')]
        for name,label,value,typ in fields:
            f+=a.field(name,label,value,options=ORDER_STATES if typ=='choice' else None,typ='text' if typ in (None,'choice') else typ)
        f+='</div>'+a.field('remarks','Directions / status remarks',start.get('remarks',''),typ='textarea')+'<button>Save verified order</button></form></div>'
    rows_html=[]
    for r in rows:
        link=(f'<a href="/documents/get?id={r["document_id"]}">{e(r["doc_name"] or "Scanned order")}</a>' if r['document_id'] and h.allowed('documents.view') else ('Recorded' if r['document_id'] else '—'))
        edit=f'<a href="/production?edit={r["id"]}">Edit</a>' if h.allowed('production.edit') else ''
        rows_html.append((f'<a href="/case/{r["case_id"]}">{e(r["case_no"])}</a>',e(r['order_reference']),e(r['issued_date']),e(r['order_status']),e(r['executed_date'] or '—'),e(r['follow_up_date'] or '—'),e(r['agency']),link,edit))
    form=f'<div class="panel"><form class="bar" method="get">{a.field("q","Search case / missing person",search)}{a.field("status","Execution status",flt,options=["All",*ORDER_STATES])}{a.field("from","Issued from",start,typ="date")}{a.field("to","Issued through",end,typ="date")}<button>Filter</button><a class="btn secondary" href="/production">Clear</a></form></div>'
    stats_html=''.join(f'<div class="stat"><b>{r["n"]:,}</b><span>{e(r["order_status"])}</span></div>' for r in stats)
    audit_html=a.table(['Case','Former status','Recorded status','Changed at','Remarks'],[(e(r['case_no']),e(r['prior_status']),e(r['new_status']),e(r['changed_at']),e(r['remarks'])) for r in events])
    return h.write(h.wrap('Production Orders',f'<h1>Production Order Register</h1><p class="sub">Issued, pending, partly executed, executed and stayed/withdrawn orders; source-linked and audited.</p><div class="notice">“Not in register” means not yet verified, <b>not</b> “pending execution.” The uploaded master list does not contain reliable structured production-order outcomes.</div>{_links(h)}<div class="grid">{stats_html}</div>{form}{f}<div class="panel"><h2>Recorded production orders</h2>{a.table(["Case No.","Order reference","Issued","Status","Executed","Follow-up","Concerned agency","Source scan","Action"],rows_html)}</div><div class="panel"><h2>Recent order status changes</h2>{audit_html}</div>'))

def save_order_page(h,form):
    a=__import__('app')
    with a.db() as c:
        order_id,case_id=order_save(c,form,h.user['id'],a.now())
        a.audit(c,h.user['id'],'PRODUCTION_ORDER_SAVE','production_order',order_id,form.get('status',''))
    return h.redirect('/production?edit='+str(order_id))

def fsp_page(h,q):
    a=__import__('app');no=q.get('case_no','').strip().upper();
    with a.db() as c:
        cases=c.execute('SELECT f.*,c.case_no,c.name,c.id case_key FROM fsp f JOIN cases c ON c.id=f.case_id ORDER BY f.id DESC LIMIT 400').fetchall()
        default_amt=int(c.execute("SELECT value FROM system_settings WHERE key='fsp_default_pkr'").fetchone()[0])
        selected=c.execute('SELECT f.*,c.case_no,c.name FROM fsp f JOIN cases c ON c.id=f.case_id WHERE c.case_no=?',(no,)).fetchone() if no else None
        beneficiaries=allocations(c,selected['case_id']) if selected else []
        history=c.execute('SELECT se.*,u.fullname FROM fsp_status_events se LEFT JOIN users u ON u.id=se.user_id WHERE se.case_id=? ORDER BY se.id DESC',(selected['case_id'],)).fetchall() if selected else []
        totals=c.execute('SELECT status,count(*) n FROM fsp GROUP BY status').fetchall()
    stats=''.join(f'<div class="stat"><b>{r["n"]}</b><span>{e(r["status"])}</span></div>' for r in totals)
    forms=''
    if h.allowed('fsp.edit'):
        r=dict(selected) if selected else {}
        forms=f'''<div class="panel"><h2>{'Update package - ' + e(no) if selected else 'Refer / register case for Family Support Package'}</h2><p class="muted">Default amount PKR {default_amt:,}; amount may be overridden for each case. Approval/receipt requires allocation totals of exactly 100%.</p><form action="/fsp/save" method="post">{h.csrf()}<div class="three">'''
        vals=[('case_no','CoIoED case number',no,None),('status','Package workflow status',r.get('status','Referred'),'choice'),('amount_pkr','Total package amount (PKR)',r.get('amount_pkr',default_amt),'number'),('referral_date','Referred on',r.get('referral_date',''),'date'),('rejection_reason','Reason for rejection',r.get('rejection_reason',''),None),('receipt_date','Received on',r.get('receipt_date',''),'date'),('receipt_reference','Receipt / acknowledgement reference',r.get('receipt_reference',''),None),('verification_reference','File / order verification reference',r.get('verification_reference',''),None)]
        for name,label,val,typ in vals:forms+=a.field(name,label,val,options=FSP_STATES if typ=='choice' else None,typ=typ if typ not in (None,'choice') else 'text')
        forms+='</div>'+a.field('notes','Administrative notes / next step',r.get('notes',''),typ='textarea')+('<div class="notice">Package receipt has been recorded. This record is locked; corrections require an approved amendment workflow.</div></form></div>' if selected and selected['status']=='Received' else '<button>Save package workflow</button></form></div>')
    detail=''
    if selected:
        total=share_total_from_rows(beneficiaries)
        share_rows=[]
        for xrow in beneficiaries:
            value=(Decimal(selected['amount_pkr'])*Decimal(xrow['percentage'])/100).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
            delete=''
            if h.allowed('fsp.edit') and selected['status'] not in ('Approved','Received'):
                delete=f'<form method="post" action="/fsp/share/delete">{h.csrf()}<input type="hidden" name="case_id" value="{selected["case_id"]}"><input type="hidden" name="share_id" value="{xrow["id"]}"><button class="secondary">Remove</button></form>'
            share_rows.append((e(xrow['beneficiary_name']),e(xrow['relationship']),e(xrow['percentage'])+'%',f'PKR {value:,.2f}',delete))
        add=''
        if h.allowed('fsp.edit') and selected['status'] not in ('Approved','Received'):
            add=f'''<form method="post" action="/fsp/share/add">{h.csrf()}<input type="hidden" name="case_no" value="{e(no)}"><div class="three">{a.field('beneficiary_name','Family member *',required=True)}{a.field('relationship','Relationship to missing person *',required=True)}{a.field('percentage','Share % (up to 2 decimals)',typ='text',required=True)}</div><button>Add family member</button></form>'''
        detail=f'<div class="panel"><h2>Family distribution — {e(no)}</h2><div class="notice"><b>Allocation total: {total}%</b> of 100%. Shares are planning records until approved; a “Received” status additionally requires a receipt and date. Allocations are locked after approval or recorded receipt.</div>{a.table(["Family member","Relationship","Percentage","Calculated amount","Action"],share_rows)}{add}</div><div class="panel"><h2>Package decision history</h2>{a.table(["Date","Previous","New status","Reason / note","Amount PKR","By"],[(e(i['event_at']),e(i['old_status']),e(i['new_status']),e(i['reason']),f'{i["amount_pkr"]:,}',e(i['fullname'])) for i in history])}</div>'
    browse=a.table(["CoIoED No.","Missing person","Stage","Amount PKR","Last updated","Details"],[(f'<a href="/case/{r["case_id"]}">{e(r["case_no"])}</a>',e(r['name']),e(r['status']),f'{r["amount_pkr"]:,}',e(r['updated_at'][:10]),f'<a href="/fsp?case_no={e(r["case_no"])}">Open allocation</a>') for r in cases])
    settings=''
    if h.allowed('users.manage'):
        settings=f'''<div class="panel"><h2>Organization default package amount</h2><form method="post" action="/fsp/default">{h.csrf()}{a.field('amount_pkr','Default PKR for NEW package records',default_amt,typ='number',required=True)}<button>Update default</button></form><p class="muted small">This does not alter amounts already recorded on existing cases.</p></div>'''
    return h.write(h.wrap('Family Support',f'<h1>Family Support Package</h1><p class="sub">Referral, review, rejection reasons, receipt and percentage-wise family allocation.</p><div class="notice">Amounts and eligibility require written administrative approval; this pilot only records the file evidence. It does not transfer funds.</div>{_links(h)}<div class="grid">{stats}</div><div class="panel"><form class="bar" method="get">{a.field("case_no","View/edit an existing package case",no)}<button>Open</button></form></div>{forms}{detail}<div class="panel"><h2>All recorded package files</h2>{browse}</div>{settings}'))

def share_total_from_rows(rows):
    return sum((Decimal(v['percentage']) for v in rows),Decimal('0'))

def save_fsp_page(h,form):
    a=__import__('app')
    with a.db() as c:
        cid=fsp_save(c,form,h.user['id'],a.now())
        a.audit(c,h.user['id'],'FSP_STATUS_SAVE','fsp',cid,form.get('status',''))
    return h.redirect('/fsp?case_no='+__import__('urllib.parse',fromlist=['quote']).quote(form.get('case_no','').strip().upper()))

def save_share_page(h,form):
    a=__import__('app')
    with a.db() as c:
        case_id=add_share(c,form,h.user['id'],a.now())
        a.audit(c,h.user['id'],'FSP_ALLOCATION_SAVE','fsp',case_id,form.get('beneficiary_name',''))
    return h.redirect('/fsp?case_no='+__import__('urllib.parse',fromlist=['quote']).quote(form.get('case_no','').strip().upper()))

def delete_share_page(h,form):
    a=__import__('app')
    with a.db() as c:
        remove_share(c,form);a.audit(c,h.user['id'],'FSP_ALLOCATION_DELETE','fsp',form.get('case_id',''),form.get('share_id',''))
        row=c.execute('SELECT case_no FROM cases WHERE id=?',(int(form.get('case_id','0')),)).fetchone()
    return h.redirect('/fsp?case_no='+row['case_no'])

def save_fsp_default(h,form):
    a=__import__('app')
    try:amount=Decimal(form.get('amount_pkr',''))
    except InvalidOperation:raise ValueError('Invalid amount')
    if amount!=amount.to_integral_value() or amount<1 or amount>10**12:raise ValueError('Positive whole PKR amount required')
    with a.db() as c:
        c.execute('UPDATE system_settings SET value=?,updated_by=?,updated_at=? WHERE key=?',(str(int(amount)),h.user['id'],a.now(),'fsp_default_pkr'))
        a.audit(c,h.user['id'],'FSP_DEFAULT_AMOUNT','setting','fsp_default_pkr',str(amount))
    return h.redirect('/fsp')