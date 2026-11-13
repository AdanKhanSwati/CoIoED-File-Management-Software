"""Offline procurement request register for the CoIoED administrative pilot.

This is a records workflow, not a substitute for government procurement rules,
budget authorization, financial controls or official purchase-order issuance.
"""
from __future__ import annotations
import csv
import io
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

STATUSES=('Draft','Submitted','Returned','Approved','Rejected','PO Issued','Partially Received','Received')

def schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS procurement_requests(
      id INTEGER PRIMARY KEY, request_no TEXT NOT NULL UNIQUE,
      request_date TEXT NOT NULL, department TEXT NOT NULL DEFAULT '',
      requested_for TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL,
      budget_head TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Draft',
      estimated_total TEXT NOT NULL DEFAULT '0.00',
      supplier_name TEXT NOT NULL DEFAULT '',supplier_contact TEXT NOT NULL DEFAULT '',
      quote_reference TEXT NOT NULL DEFAULT '', approved_by INTEGER REFERENCES users(id),
      approved_at TEXT NOT NULL DEFAULT '',approval_note TEXT NOT NULL DEFAULT '',
      po_number TEXT NOT NULL DEFAULT '',po_date TEXT NOT NULL DEFAULT '',
      receipt_date TEXT NOT NULL DEFAULT '',receipt_reference TEXT NOT NULL DEFAULT '',
      receipt_note TEXT NOT NULL DEFAULT '',created_by INTEGER NOT NULL REFERENCES users(id),
      created_at TEXT NOT NULL, updated_by INTEGER REFERENCES users(id),updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_procurement_req_date ON procurement_requests(request_date);
    CREATE INDEX IF NOT EXISTS idx_procurement_status ON procurement_requests(status);
    CREATE TABLE IF NOT EXISTS procurement_lines(
      id INTEGER PRIMARY KEY,request_id INTEGER NOT NULL REFERENCES procurement_requests(id),
      line_no INTEGER NOT NULL,description TEXT NOT NULL,quantity TEXT NOT NULL,unit TEXT NOT NULL,
      estimate_per_unit TEXT NOT NULL,UNIQUE(request_id,line_no));
    CREATE TABLE IF NOT EXISTS procurement_quotes(
      id INTEGER PRIMARY KEY,request_id INTEGER NOT NULL REFERENCES procurement_requests(id),
      supplier TEXT NOT NULL,reference TEXT NOT NULL,quote_date TEXT NOT NULL,
      quoted_amount TEXT NOT NULL,remarks TEXT NOT NULL DEFAULT '',
      created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);
    ''')

def iso(v,optional=False):
    v=str(v or '').strip()
    if not v and optional:return ''
    try:
        if date.fromisoformat(v).isoformat()==v:return v
    except (ValueError,TypeError):pass
    raise ValueError('Enter a valid date (YYYY-MM-DD).')

def range_params(q):
    start=iso(q.get('from',''),True);end=iso(q.get('to',''),True)
    if start and end and start>end:raise ValueError('Start date cannot be later than end date.')
    return start,end

def money(v):
    try:d=Decimal(str(v).strip())
    except (InvalidOperation,ValueError):raise ValueError('Amounts must be numeric.')
    if not d.is_finite() or d<0 or d>Decimal('99999999999.99') or d.as_tuple().exponent < -2:
        raise ValueError('Amounts must be non-negative PKR with at most two decimal places.')
    return str(d.quantize(Decimal('0.01')))

def positive(v):
    try:d=Decimal(str(v).strip())
    except (InvalidOperation,ValueError):raise ValueError('Quantity must be numeric.')
    if not d.is_finite() or d<=0 or d>Decimal('1000000') or d.as_tuple().exponent < -3:
        raise ValueError('Quantity must be positive, with at most three decimals.')
    return str(d.normalize())

def line_rows(f):
    rows=[]
    for idx in range(1,9):
        title=str(f.get(f'item{idx}','')).strip()[:400]
        qty=str(f.get(f'qty{idx}','')).strip()
        unit=str(f.get(f'unit{idx}','')).strip()[:40]
        price=str(f.get(f'price{idx}','')).strip()
        if not (title or qty or unit or price):continue
        if not all([title,qty,unit,price]):raise ValueError(f'Item {idx} requires description, quantity, unit and estimated unit price.')
        rows.append((len(rows)+1,title,positive(qty),unit,money(price)))
    if not rows:raise ValueError('Enter at least one procurement item.')
    return rows

def total(rows):
    return str(sum((Decimal(x[2])*Decimal(x[4]) for x in rows),Decimal('0')).quantize(Decimal('0.01')))

def record(c,rid):
    r=c.execute('SELECT * FROM procurement_requests WHERE id=?',(rid,)).fetchone()
    if not r:raise ValueError('Procurement requisition not found.')
    return r

def list_page(h,q):
    a=sys.modules[h.__class__.__module__]
    start,end=range_params(q)
    status=str(q.get('status','')).strip();keyword=str(q.get('q','')).strip()[:120]
    if status and status not in STATUSES:raise ValueError('Unknown procurement status.')
    sql='SELECT p.*,u.fullname creator FROM procurement_requests p JOIN users u ON u.id=p.created_by WHERE 1=1';params=[]
    if start:sql+=' AND p.request_date>=?';params.append(start)
    if end:sql+=' AND p.request_date<=?';params.append(end)
    if status:sql+=' AND p.status=?';params.append(status)
    if keyword:
        sql+=' AND (p.request_no LIKE ? OR p.purpose LIKE ? OR p.supplier_name LIKE ? OR p.department LIKE ?)';params+=['%'+keyword+'%']*4
    with a.db() as c:
        count=c.execute('SELECT COUNT(*) FROM ('+sql+')',params).fetchone()[0]
        results=c.execute(sql+' ORDER BY p.request_date DESC,p.id DESC LIMIT 500',params).fetchall()
    fil=(f'<form method="get" class="bar">'
         +a.field('q','Request / purpose / supplier',keyword)
         +a.field('from','From date',start,typ='date')+a.field('to','To date',end,typ='date')
         +a.field('status','Status',status,options=['']+list(STATUSES))
         +'<button>Filter records</button><a class="btn secondary" href="/procurement">Clear</a></form>')
    rows=[(f'<a href="/procurement/{r["id"]}"><strong>{a.e(r["request_no"])}</strong></a>',a.e(r['request_date']),a.e(r['department']),a.e(r['purpose']),a.e(r['estimated_total']),a.e(r['status']),a.e(r['creator'])) for r in results]
    export='/procurement/export.csv?'+urlencode({k:v for k,v in [('from',start),('to',end),('status',status),('q',keyword)] if v})
    actions='<a class="btn" href="/procurement/new">+ New requisition</a>' if h.allowed('procurement.edit') else ''
    actions+=f' <a class="btn secondary" href="{a.e(export)}">Export filtered CSV</a>' if h.allowed('procurement.export') else ''
    body=(f'<div class="page-top"><div><div class="page-overline">ADMINISTRATION / PROCUREMENT</div><h1>Procurement</h1><p class="sub">Requisitions, quotation references, approvals, purchase orders and delivery receipts. Amounts in PKR.</p></div><div class="actions">{actions}</div></div>'
          f'<div class="notice">Administrative tracking only. Obtain approvals and follow applicable procurement rules outside this pilot before committing expenditure.</div>'
          f'<div class="panel"><h2>Search & date-range filters</h2>{fil}<p class="muted small">{count} matching records; showing up to 500. Export CSV for the complete filtered result.</p>'
          +a.table(['Request No.','Date','Department','Purpose','Estimated PKR','Status','Created by'],rows)+'</div>')
    return h.write(h.wrap('Procurement',body))

def edit_page(h,rid=0):
    a=sys.modules[h.__class__.__module__]
    with a.db() as c:
        row=record(c,rid) if rid else None
        items=c.execute('SELECT * FROM procurement_lines WHERE request_id=? ORDER BY line_no',(rid,)).fetchall() if rid else []
        quotes=c.execute('SELECT * FROM procurement_quotes WHERE request_id=? ORDER BY id DESC',(rid,)).fetchall() if rid else []
    if rid and not row:return h.abort(404,'Not found')
    draft=not row or row['status'] in ('Draft','Returned')
    canedit=h.allowed('procurement.edit') and draft
    detail=(f'<div class="page-top"><div><div class="page-overline">PROCUREMENT / REQUISITIONS</div><h1>{a.e(row["request_no"]) if row else "New requisition"}</h1><p class="sub">{a.e(row["status"]) if row else "Draft"} · Case files are not linked to this administrative purchase request.</p></div><div class="actions"><a href="/procurement" class="btn secondary">Back to register</a></div></div>')
    if canedit:
        form=(f'<div class="panel"><h2>Requisition details</h2><form method="post" action="/procurement/save">{h.csrf()}'
              +f'<input type="hidden" name="id" value="{rid}"><div class="two">'
              +a.field('request_no','Requisition reference',row['request_no'] if row else '',required=True)
              +a.field('request_date','Requisition date',row['request_date'] if row else date.today().isoformat(),typ='date',required=True)
              +a.field('department','Office / section',row['department'] if row else '')
              +a.field('requested_for','Requested by (name / designation)',row['requested_for'] if row else '')
              +a.field('budget_head','Budget head / allocation',row['budget_head'] if row else '')
              +a.field('purpose','Purpose / justification',row['purpose'] if row else '',typ='textarea',required=True)+'</div><h3>Items (up to 8 lines per requisition)</h3>')
        form+='<div class="three">'
        for i in range(1,9):
            item=items[i-1] if i<=len(items) else None
            form+=f'<div class="panel" style="padding:12px"><b>Item {i}</b>'
            for key,label,value in [('item','Description',item['description'] if item else ''),('qty','Qty',item['quantity'] if item else ''),('unit','Unit',item['unit'] if item else ''),('price','Estimated price/unit (PKR)',item['estimate_per_unit'] if item else '')]:
                form+=a.field(key+str(i),label,value)
            form+='</div>'
        form+='</div><button>Save draft</button></form></div>'
        detail+=form
    elif row:
        detail+='<div class="panel"><h2>Requisition</h2>'+a.table(['Field','Value'],[(a.e(k),a.e(row[v])) for k,v in [('Date','request_date'),('Department','department'),('Requester','requested_for'),('Purpose','purpose'),('Budget head','budget_head'),('Estimated PKR','estimated_total'),('Status','status')]])+'</div>'
        detail+='<div class="panel"><h2>Items</h2>'+a.table(['Description','Quantity','Unit','Estimated unit price'],[(a.e(x['description']),a.e(x['quantity']),a.e(x['unit']),a.e(x['estimate_per_unit'])) for x in items])+'</div>'
    if not row:return h.write(h.wrap('New Procurement',detail))
    if h.allowed('procurement.edit') and row['status'] in ('Draft','Returned'):
        detail+=f'<div class="panel"><form method="post" action="/procurement/submit">{h.csrf()}<input type="hidden" name="id" value="{rid}"><button>Submit for approval</button></form></div>'
    if h.allowed('procurement.approve') and row['status']=='Submitted':
        detail+=(f'<div class="panel"><h2>Approval decision</h2><p class="muted small">Maker–checker: the creator cannot approve their own requisition.</p><form method="post" action="/procurement/decision">{h.csrf()}<input type="hidden" name="id" value="{rid}">'
                 +a.field('approval_note','Decision / reasons',typ='textarea')
                 +'<button name="decision" value="Approved">Approve</button> <button class="secondary" name="decision" value="Returned">Return for correction</button> <button class="secondary" name="decision" value="Rejected">Reject</button></form></div>')
    if h.allowed('procurement.edit') and row['status'] in ('Submitted','Approved','PO Issued','Partially Received','Received'):
        detail+=(f'<div class="panel"><h2>Quotations / vendor records</h2><form method="post" action="/procurement/quote">{h.csrf()}<input type="hidden" name="id" value="{rid}"><div class="two">'
                 +a.field('supplier','Supplier name',required=True)+a.field('quote_ref','Quotation number / reference',required=True)
                 +a.field('quote_date','Quotation date',date.today().isoformat(),typ='date',required=True)
                 +a.field('quote_amount','Quoted amount (PKR)',required=True)+a.field('remarks','Remarks',typ='textarea')
                 +'</div><button>Add quotation record</button></form></div>')
    detail+='<div class="panel"><h2>Saved quotations</h2>'+a.table(['Supplier','Reference','Date','Quoted PKR','Remarks'],[(a.e(x['supplier']),a.e(x['reference']),a.e(x['quote_date']),a.e(x['quoted_amount']),a.e(x['remarks'])) for x in quotes])+'</div>'
    if h.allowed('procurement.edit') and row['status']=='Approved':
        detail+=(f'<div class="panel"><h2>Record approved purchase order</h2><form method="post" action="/procurement/po">{h.csrf()}<input type="hidden" name="id" value="{rid}"><div class="two">'
                 +a.field('supplier_name','Selected supplier',required=True)+a.field('supplier_contact','Supplier contact (optional)')
                 +a.field('quote_reference','Quotation / selection reference',required=True)
                 +a.field('po_number','Purchase order number',required=True)+a.field('po_date','PO date',date.today().isoformat(),typ='date',required=True)
                 +'</div><button>Record purchase order</button></form></div>')
    if h.allowed('procurement.edit') and row['status'] in ('PO Issued','Partially Received'):
        detail+=(f'<div class="panel"><h2>Record receipt of goods / services</h2><form method="post" action="/procurement/receipt">{h.csrf()}<input type="hidden" name="id" value="{rid}"><div class="two">'
                 +a.field('receipt_date','Receipt date',date.today().isoformat(),typ='date',required=True)
                 +a.field('receipt_reference','GRN / delivery reference',required=True)
                 +a.field('receipt_note','Receipt notes',typ='textarea')+'</div>'
                 +'<label>Receipt type<select name="receipt_status"><option value="Partially Received">Partially Received</option><option value="Received">Received in full</option></select></label><br><button>Save receipt</button></form></div>')
    detail+='<div class="panel"><h2>Approval & fulfilment</h2>'+a.table(['Field','Recorded value'],[(a.e(k),a.e(row[v])) for k,v in [('Approver decision','approval_note'),('Approved at','approved_at'),('Supplier','supplier_name'),('PO number','po_number'),('PO date','po_date'),('Receipt date','receipt_date'),('Receipt reference','receipt_reference'),('Receipt notes','receipt_note')]])+'</div>'
    return h.write(h.wrap('Procurement',detail))

def save(h,path,f):
    a=sys.modules[h.__class__.__module__]
    if path=='/procurement/save':
        if not h.allowed('procurement.edit'):return h.abort(403,'Permission denied')
        rid=int(f.get('id','0') or 0);req=str(f.get('request_no','')).strip()[:100]
        purpose=str(f.get('purpose','')).strip()[:3000]
        if not req or not purpose:raise ValueError('Reference and purpose are required.')
        day=iso(f.get('request_date'));items=line_rows(f);amount=total(items)
        with a.db() as c:
            if rid:
                prior=record(c,rid)
                if prior['status'] not in ('Draft','Returned'):raise ValueError('Submitted records cannot be edited without return for correction.')
                c.execute('''UPDATE procurement_requests SET request_no=?,request_date=?,department=?,requested_for=?,purpose=?,budget_head=?,estimated_total=?,updated_by=?,updated_at=? WHERE id=?''',
                 (req,day,str(f.get('department',''))[:150],str(f.get('requested_for',''))[:150],purpose,str(f.get('budget_head',''))[:150],amount,h.user['id'],a.now(),rid))
                c.execute('DELETE FROM procurement_lines WHERE request_id=?',(rid,))
            else:
                rid=c.execute('''INSERT INTO procurement_requests(request_no,request_date,department,requested_for,purpose,budget_head,estimated_total,created_by,created_at,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                 (req,day,str(f.get('department',''))[:150],str(f.get('requested_for',''))[:150],purpose,str(f.get('budget_head',''))[:150],amount,h.user['id'],a.now(),h.user['id'],a.now())).lastrowid
            c.executemany('INSERT INTO procurement_lines(request_id,line_no,description,quantity,unit,estimate_per_unit) VALUES(?,?,?,?,?,?)',[(rid,*line) for line in items])
            a.audit(c,h.user['id'],'PROCUREMENT_DRAFT_SAVED','procurement',rid,'reference='+req)
        return h.redirect('/procurement/'+str(rid))
    rid=int(f.get('id','0') or 0)
    with a.db() as c:
        rec=record(c,rid)
        if path=='/procurement/submit':
            if not h.allowed('procurement.edit'):return h.abort(403,'Permission denied')
            if rec['status'] not in ('Draft','Returned'):raise ValueError('Only a draft or returned requisition can be submitted.')
            if not c.execute('SELECT 1 FROM procurement_lines WHERE request_id=? LIMIT 1',(rid,)).fetchone():raise ValueError('At least one item is required.')
            c.execute('UPDATE procurement_requests SET status=?,updated_by=?,updated_at=? WHERE id=?',('Submitted',h.user['id'],a.now(),rid))
            a.audit(c,h.user['id'],'PROCUREMENT_SUBMITTED','procurement',rid)
        elif path=='/procurement/decision':
            if not h.allowed('procurement.approve'):return h.abort(403,'Permission denied')
            if rec['status']!='Submitted':raise ValueError('Only submitted requisitions can be decided.')
            if rec['created_by']==h.user['id']:raise ValueError('Creator cannot approve or decide their own requisition.')
            decision=f.get('decision','');reason=f.get('approval_note','').strip()[:2000]
            if decision not in ('Approved','Returned','Rejected'):raise ValueError('Invalid decision')
            if decision!='Approved' and not reason:raise ValueError('Return and rejection require a reason.')
            c.execute('UPDATE procurement_requests SET status=?,approved_by=?,approved_at=?,approval_note=?,updated_by=?,updated_at=? WHERE id=?',(decision,h.user['id'],a.now(),reason,h.user['id'],a.now(),rid))
            a.audit(c,h.user['id'],'PROCUREMENT_'+decision.upper(),'procurement',rid)
        elif path=='/procurement/quote':
            if not h.allowed('procurement.edit'):return h.abort(403,'Permission denied')
            if rec['status'] not in ('Submitted','Approved','PO Issued','Partially Received','Received'):raise ValueError('Submit requisition before recording quotations.')
            supplier=str(f.get('supplier','')).strip()[:150];ref=str(f.get('quote_ref','')).strip()[:150]
            if not supplier or not ref:raise ValueError('Supplier and quotation reference are required.')
            c.execute('''INSERT INTO procurement_quotes(request_id,supplier,reference,quote_date,quoted_amount,remarks,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)''',
              (rid,supplier,ref,iso(f.get('quote_date')),money(f.get('quote_amount','')),str(f.get('remarks',''))[:1200],h.user['id'],a.now()))
            a.audit(c,h.user['id'],'PROCUREMENT_QUOTE_ADDED','procurement',rid,ref)
        elif path=='/procurement/po':
            if not h.allowed('procurement.edit'):return h.abort(403,'Permission denied')
            if rec['status']!='Approved':raise ValueError('Approve requisition before purchase-order entry.')
            name=str(f.get('supplier_name','')).strip()[:150];reference=str(f.get('quote_reference','')).strip()[:150];number=str(f.get('po_number','')).strip()[:100]
            if not (name and reference and number):raise ValueError('Supplier, source/selection reference and PO number are required.')
            if not c.execute('SELECT 1 FROM procurement_quotes WHERE request_id=?',(rid,)).fetchone():raise ValueError('Record at least one supplier quotation before entering a PO.')
            po_date=iso(f.get('po_date'))
            if po_date<rec['request_date']:raise ValueError('PO date cannot precede requisition date.')
            c.execute('''UPDATE procurement_requests SET supplier_name=?,supplier_contact=?,quote_reference=?,po_number=?,po_date=?,status='PO Issued',updated_by=?,updated_at=? WHERE id=?''',
             (name,str(f.get('supplier_contact',''))[:150],reference,number,po_date,h.user['id'],a.now(),rid))
            a.audit(c,h.user['id'],'PROCUREMENT_PO_RECORDED','procurement',rid,number)
        elif path=='/procurement/receipt':
            if not h.allowed('procurement.edit'):return h.abort(403,'Permission denied')
            if rec['status'] not in ('PO Issued','Partially Received'):raise ValueError('Receipt must follow a recorded PO.')
            receipt_date=iso(f.get('receipt_date'))
            if receipt_date<rec['po_date']:raise ValueError('Receipt cannot predate PO issuance.')
            reference=str(f.get('receipt_reference','')).strip()[:150];status=f.get('receipt_status','')
            if not reference or status not in ('Partially Received','Received'):raise ValueError('Receipt reference and valid status are required.')
            c.execute('''UPDATE procurement_requests SET receipt_date=?,receipt_reference=?,receipt_note=?,status=?,updated_by=?,updated_at=? WHERE id=?''',
             (receipt_date,reference,str(f.get('receipt_note',''))[:3000],status,h.user['id'],a.now(),rid))
            a.audit(c,h.user['id'],'PROCUREMENT_'+status.replace(' ','_').upper(),'procurement',rid,reference)
        else:raise ValueError('Unknown procurement action')
    return h.redirect('/procurement/'+str(rid))

def export_csv(h,q):
    a=sys.modules[h.__class__.__module__];start,end=range_params(q)
    status=str(q.get('status','')).strip();keyword=str(q.get('q','')).strip()[:120]
    if status and status not in STATUSES:raise ValueError('Unknown procurement status.')
    sql='SELECT * FROM procurement_requests WHERE 1=1';params=[]
    if start:sql+=' AND request_date>=?';params.append(start)
    if end:sql+=' AND request_date<=?';params.append(end)
    if status:sql+=' AND status=?';params.append(status)
    if keyword:sql+=' AND (request_no LIKE ? OR purpose LIKE ? OR supplier_name LIKE ? OR department LIKE ?)';params+=['%'+keyword+'%']*4
    with a.db() as c:
        rows=c.execute(sql+' ORDER BY request_date DESC,id DESC',params).fetchall()
        a.audit(c,h.user['id'],'PROCUREMENT_CSV_EXPORT','procurement',len(rows),'Filtered administrative register')
    cols=['request_no','request_date','department','requested_for','purpose','budget_head','estimated_total','status','supplier_name','supplier_contact','quote_reference','approved_at','approval_note','po_number','po_date','receipt_date','receipt_reference','receipt_note']
    out=io.StringIO();w=csv.writer(out);w.writerow(cols)
    for r in rows:
        w.writerow([("'"+str(r[k]) if str(r[k]).startswith(('=','+','-','@')) else str(r[k])) for k in cols])
    return h.write(b'\xef\xbb\xbf'+out.getvalue().encode('utf-8'),typ='text/csv; charset=utf-8',extra={'Content-Disposition':'attachment; filename="CoIoED_Procurement_Filtered.csv"'})