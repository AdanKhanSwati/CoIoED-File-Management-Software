"""OFFLINE-ONLY import of the original five-sheet CoIoED XLSX register.
Reads XLSX source without rewriting it; stores every source row and reports exceptions.
Never send source or result to the public phone demo.
"""
from __future__ import annotations
import argparse, collections, csv, io, json, os, re, shutil, sys, zipfile, sqlite3, hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta
NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
TARGET_SHEETS=['Comprehensive Register','Under Investigation Reference','Sindh FSP Reference','Balochistan Reference','Punjab Reference']
DEFAULT_COLUMNS=['S. #','CoIoED No.','Name/ Perantage','Missing Date','Address','Date of Registration','CNIC','District','Province','Refering Agency','Status','Production Order (Date)','Remarks']
ID_RE=re.compile(r'(\d{3,8})\s*-\s*([KPBSIAF])',re.I)

def iter_sheet_rows(z,name,strings):
    with z.open(name) as handle:
        for _,el in ET.iterparse(handle,events=('end',)):
            if el.tag!=NS+'row':continue
            d={}
            for cell in el.findall(NS+'c'):
                key=re.match(r'[A-Z]+',cell.get('r',''))
                if not key:continue
                col=0
                for letter in key.group(0):col=col*26+ord(letter)-64
                t=cell.attrib.get('t','');v=cell.find(NS+'v')
                if t=='inlineStr':
                    si=cell.find(NS+'is');val=''.join(q.text or '' for q in si.iter(NS+'t')) if si is not None else ''
                else:val=v.text if v is not None and v.text else ''
                if t=='s' and val:val=strings[int(val)]
                elif t=='b':val='TRUE' if val=='1' else 'FALSE'
                if val!='':d[col]=val
            row=int(el.attrib.get('r','0'));el.clear()
            yield row,d

def parse_xlsx(path):
    with zipfile.ZipFile(path) as z:
        root=ET.fromstring(z.read('xl/sharedStrings.xml')) if 'xl/sharedStrings.xml' in z.namelist() else None
        strings=[''.join(t.text or '' for t in si.iter(NS+'t')) for si in root.findall(NS+'si')] if root is not None else []
        # workbook sheets and their actual XML paths resolved through workbook relationships
        wb=ET.fromstring(z.read('xl/workbook.xml'));rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets={r.get('Id'):r.get('Target','') for r in rels}
        sheets=[]
        for i,sh in enumerate(wb.findall('.//'+NS+'sheet')):
            rid=sh.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target=targets.get(rid,f'worksheets/sheet{i+1}.xml').lstrip('/')
            member=target if target.startswith('xl/') else 'xl/'+target
            sheets.append((sh.get('name'),member))
        for i,(title,member) in enumerate(sheets):
            if member not in z.namelist():raise ValueError('Worksheet XML not found: '+member)
            yield i,title,iter_sheet_rows_copy(z,member,strings)

def iter_sheet_rows_copy(z,member,strings):
    # Materialize the *individual* worksheet while the source ZIP remains open.
    # 6 MB XML is bounded; source workbook never travels over a network.
    return list(iter_sheet_rows(z,member,strings))

def clean_id(v):
    s=str(v or '').strip().upper().replace('–','-').replace('—','-')
    m=ID_RE.fullmatch(s)
    if not m:return None
    return m.group(1)+'-'+m.group(2)

def split_parentage(s):
    if not s:return '', ''
    m=re.search(r'\b(s/o|d/o|w/o|f/o|son of|daughter of)\b',s,re.I)
    return (s[:m.start()].strip(),s[m.start():].strip()) if m else (s.strip(),'')

def canonical_date(v):
    v=str(v or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):return v
    for fmt in ('%d/%m/%Y','%d.%m.%Y'):
        try:return datetime.strptime(v,fmt).date().isoformat()
        except ValueError:pass
    if re.fullmatch(r'\d+(?:\.0)?',v):
        try:
            x=int(float(v));d=datetime(1899,12,30)+timedelta(days=x)
            if 1900<=d.year<=2100:return d.date().isoformat()
        except (ValueError,OverflowError):pass
    return ''

def schema(c):
    cols=[r['name'] for r in c.execute('PRAGMA table_info(cases)')]
    for col,sql in [('source_row','INTEGER'),('source_snapshot','TEXT'),('source_issue',"TEXT DEFAULT ''"),('source_case_no','TEXT'),('source_original_case_no','TEXT')]:
        if col not in cols:c.execute(f'ALTER TABLE cases ADD COLUMN {col} {sql}')
    c.execute('CREATE INDEX IF NOT EXISTS idx_cases_original_source_no ON cases(source_case_no)')
    c.executescript('''
      CREATE TABLE IF NOT EXISTS master_source_meta(id INTEGER PRIMARY KEY CHECK(id=1),name TEXT,imported_at TEXT,master_header TEXT,source_sha256 TEXT);
      CREATE TABLE IF NOT EXISTS master_source_rows(sheet_index INTEGER NOT NULL, sheet_name TEXT NOT NULL, row_number INTEGER NOT NULL, raw_json TEXT NOT NULL, linked_case_id INTEGER REFERENCES cases(id),issue TEXT DEFAULT '', PRIMARY KEY(sheet_index,row_number));
      CREATE TABLE IF NOT EXISTS master_import_issues(id INTEGER PRIMARY KEY, row_number INTEGER, source_id TEXT, problem TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS idx_master_link ON master_source_rows(linked_case_id);
    ''')

def import_master(c,path,actor_id=None):
    from pathlib import Path
    import hashlib
    raw=Path(path).read_bytes()
    if not zipfile.is_zipfile(io.BytesIO(raw)):raise ValueError('Not a valid XLSX file')
    if c.execute('SELECT COUNT(*) FROM master_source_rows WHERE sheet_index=0').fetchone()[0]:raise ValueError('Master register is already imported. Keep the existing database; use an approved replacement/migration procedure.')
    stats=collections.Counter();seen=set();issues=[];header=None;main_extra_max=13
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        root=ET.fromstring(z.read('xl/sharedStrings.xml')) if 'xl/sharedStrings.xml' in z.namelist() else None
        strings=[''.join(t.text or '' for t in si.iter(NS+'t')) for si in root.findall(NS+'si')] if root is not None else []
        wb=ET.fromstring(z.read('xl/workbook.xml'));rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets={r.get('Id'):r.get('Target','') for r in rels}
        sheets=[]
        for i,sh in enumerate(wb.findall('.//'+NS+'sheet')):
            rid=sh.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target=targets.get(rid,f'worksheets/sheet{i+1}.xml').lstrip('/')
            member=target if target.startswith('xl/') else 'xl/'+target
            sheets.append((sh.get('name'),member))
        if len(sheets)<5:raise ValueError('Expected source workbook with five reference sheets')
        for si,(title,member) in enumerate(sheets):
            for rn,vals in iter_sheet_rows(z,member,strings):
                if si==0 and rn==3:
                    header=[vals.get(i,'') for i in range(1,14)];continue
                if (si==0 and rn<4) or (si>0 and not vals):continue
                # Preserve ALL populated cells, including unnamed columns beyond M.
                maxcol=max(13,max(vals,default=0))
                row=[vals.get(i,'') for i in range(1,maxcol+1)]
                if si==0:main_extra_max=max(main_extra_max,maxcol)
                if not any(str(v).strip() for v in row):continue
                issue='';link=None
                if si==0:
                    stats['source_rows']+=1
                    official=str(vals.get(2,'')).strip()
                    key=clean_id(official)
                    if not official:
                        issue='Missing case number (source cell empty)';stats['irregular']+=1
                    elif not key:
                        issue='Original case number requires verification (kept unchanged)';stats['irregular']+=1
                    # Existing case numbers and every source row have distinct internal database keys.
                    # Do not alter the official original text or mistake a clubbed number for a single case.
                    stable=key if key else official
                    if not stable or stable in seen or c.execute('SELECT 1 FROM cases WHERE case_no=?',(stable,)).fetchone():
                        if official and key:issue='Duplicate case number (original value retained; separate case file)';stats['duplicates']+=1
                        elif official and not key and (stable in seen or c.execute('SELECT 1 FROM cases WHERE case_no=?',(stable,)).fetchone()):
                            issue='Repeated irregular case number (original value retained; separate case file)'
                        stable=f'SOURCE-ROW-{rn:06d}'
                        while c.execute('SELECT 1 FROM cases WHERE case_no=?',(stable,)).fetchone():stable+='X'
                    seen.add(stable)
                    name,parentage=split_parentage(str(vals.get(3,'')))
                    status=str(vals.get(11,'')).strip()[:4000]
                    note='Imported source row '+str(rn)+'; original case identifier retained independently. '+str(vals.get(13,''))[:1300]
                    stamp=datetime.now().isoformat(timespec='seconds')
                    cur=c.execute('''INSERT INTO cases(case_no,name,parentage,cnic,missing_date,registered_date,address,district,region,status,notes,created_at,updated_at,updated_by,cnic_owner,complainant_name,case_verified,file_branch,source_row,source_snapshot,source_issue,source_case_no,source_original_case_no)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                        stable,name,parentage,str(vals.get(7,'')),str(vals.get(4,'')),canonical_date(vals.get(6,'')),str(vals.get(5,'')),str(vals.get(8,'')),str(vals.get(9,'')) or 'HO Islamabad',status or 'Unknown',note,stamp,stamp,actor_id,'Unknown','',0,'Record Room',rn,'{}',issue,official,official))
                    link=cur.lastrowid
                    original_db=c.execute('SELECT name,parentage,cnic,missing_date,registered_date,address,district,region,status FROM cases WHERE id=?',(link,)).fetchone()
                    c.execute('UPDATE cases SET source_snapshot=? WHERE id=?',(json.dumps(dict(original_db),ensure_ascii=False),link))
                    stats['linked_cases']+=1
                    if issue:issues.append((rn,official,issue))
                else:stats[f'reference_sheet_{si}_rows']+=1
                c.execute('INSERT INTO master_source_rows(sheet_index,sheet_name,row_number,raw_json,linked_case_id,issue) VALUES(?,?,?,?,?,?)',(si,title,rn,json.dumps(row,ensure_ascii=False),link,issue))
    if not header:header=DEFAULT_COLUMNS.copy()
    if main_extra_max>len(header):header.extend('Unlabelled original column '+str(i)+' (preserved)' for i in range(len(header)+1,main_extra_max+1))
    c.execute('INSERT OR REPLACE INTO master_source_meta(id,name,imported_at,master_header,source_sha256) VALUES(1,?,?,?,?)',(Path(path).name,datetime.now().isoformat(timespec='seconds'),json.dumps(header,ensure_ascii=False),hashlib.sha256(raw).hexdigest()))
    c.executemany('INSERT INTO master_import_issues(row_number,source_id,problem) VALUES(?,?,?)',issues)
    return dict(stats),issues

def link_unlinked_master_rows(c, actor_id=None):
    """Idempotent migration for imports produced by older pilots. Never overwrite linked cases."""
    schema(c)
    rows=c.execute('SELECT * FROM master_source_rows WHERE sheet_index=0 AND linked_case_id IS NULL ORDER BY row_number').fetchall()
    counter=collections.Counter()
    stamp=datetime.now().isoformat(timespec='seconds')
    for row in rows:
        rn=row['row_number']; vals=json.loads(row['raw_json'])
        official=str(vals[1] if len(vals)>1 else '').strip()
        name,parentage=split_parentage(str(vals[2] if len(vals)>2 else ''))
        value=lambda i: str(vals[i-1]) if i<=len(vals) else ''
        status=value(11).strip()
        # Use raw original case number where unique, or a private internal source-row key.
        stable=official
        if not stable or c.execute('SELECT 1 FROM cases WHERE case_no=?',(stable,)).fetchone():
            stable=f'SOURCE-ROW-{rn:06d}'
            while c.execute('SELECT 1 FROM cases WHERE case_no=?',(stable,)).fetchone():stable+='X'
        issue=row['issue'] or 'Original case number requires verification'
        snap={'name':name,'parentage':parentage,'cnic':value(7),'missing_date':value(4),'registered_date':canonical_date(value(6)),'address':value(5),'district':value(8),'region':value(9) or 'HO Islamabad','status':status or 'Unknown'}
        cur=c.execute('''INSERT INTO cases(case_no,name,parentage,cnic,missing_date,registered_date,address,district,region,status,notes,created_at,updated_at,updated_by,cnic_owner,complainant_name,case_verified,file_branch,source_row,source_snapshot,source_issue,source_case_no,source_original_case_no)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            stable,name,parentage,value(7),value(4),canonical_date(value(6)),value(5),value(8),value(9) or 'HO Islamabad',status or 'Unknown',
            'Original master register row '+str(rn)+'; case number requires staff review.',stamp,stamp,actor_id,'Unknown','',0,'Record Room',rn,json.dumps(snap,ensure_ascii=False),issue,official,official))
        c.execute('UPDATE master_source_rows SET linked_case_id=? WHERE sheet_index=0 AND row_number=?',(cur.lastrowid,rn))
        counter['linked_existing_unresolved']+=1
    # Add source ID for old linked cases without affecting edits to their current case numbers.
    for r in c.execute('SELECT id,source_row FROM cases WHERE source_row IS NOT NULL AND source_original_case_no IS NULL').fetchall():
        src=c.execute('SELECT raw_json FROM master_source_rows WHERE sheet_index=0 AND row_number=?',(r['source_row'],)).fetchone()
        if not src:continue
        v=json.loads(src[0]);orig=str(v[1] if len(v)>1 else '').strip()
        c.execute('UPDATE cases SET source_case_no=?,source_original_case_no=? WHERE id=?',(orig,orig,r['id']))
        counter['backfilled_original_id']+=1
    return dict(counter)


def current_register(c):
    schema(c)
    m=c.execute('SELECT master_header FROM master_source_meta WHERE id=1').fetchone()
    if not m:return None
    header=json.loads(m[0]);output=[];linked=set()
    for row in c.execute('SELECT r.*,c.case_no,c.name,c.parentage,c.cnic,c.missing_date,c.registered_date,c.address,c.district,c.region,c.status,c.archived,c.source_snapshot,c.source_case_no,c.source_original_case_no FROM master_source_rows r LEFT JOIN cases c ON c.id=r.linked_case_id WHERE r.sheet_index=0 ORDER BY r.row_number'):
        arr=json.loads(row['raw_json']);source=json.loads(row['source_snapshot'] or '{}')
        if len(arr)<len(header):arr.extend(['']*(len(header)-len(arr)))
        if row['linked_case_id']:
            # Only override fields explicitly amended after import. Otherwise exact source text remains intact.
            if row['source_original_case_no'] is not None and row['source_case_no'] is not None and row['source_case_no'] != row['source_original_case_no']:
                arr[1]=str(row['source_case_no'])
            overlay={3:(row['name']+' '+(row['parentage'] or '')).strip(),4:row['missing_date'],5:row['address'],6:row['registered_date'],7:row['cnic'],8:row['district'],9:row['region'],11:row['status']}
            for col,value in overlay.items():
                oldkey={3:'name',4:'missing_date',5:'address',6:'registered_date',7:'cnic',8:'district',9:'region',11:'status'}[col]
                old=source.get(oldkey,'')
                if col==3:old=(source.get('name','')+' '+source.get('parentage','')).strip()
                if value is not None and str(value).strip()!=str(old).strip():arr[col-1]=str(value)
            linked.add(row['linked_case_id'])
            if row['archived']:
                arr[10]=str(arr[10])+' [ARCHIVED - retained for audit]'
        output.append(arr[:len(header)])
    extra=c.execute('SELECT * FROM cases WHERE source_row IS NULL ORDER BY id').fetchall()
    for r in extra:
        values=[len(output)+1,r['case_no'],(r['name']+' '+(r['parentage'] or '')).strip(),r['missing_date'],r['address'],r['registered_date'],r['cnic'],r['district'],r['region'],'',r['status'],'',r['notes']]
        output.append(values[:len(header)])
    return header,output

def source_overview(c):
    """Conservation counts for ALL original sheets, without leaking case details."""
    schema(c)
    summary=[]
    for row in c.execute("""SELECT sheet_index,sheet_name,COUNT(*) AS source_rows,
      SUM(CASE WHEN linked_case_id IS NULL THEN 1 ELSE 0 END) AS unlinked_rows
      FROM master_source_rows GROUP BY sheet_index,sheet_name ORDER BY sheet_index"""):
        summary.append(dict(row))
    return summary

def source_row(c,sheet_index,row_number):
    schema(c)
    row=c.execute('SELECT * FROM master_source_rows WHERE sheet_index=? AND row_number=?',(sheet_index,row_number)).fetchone()
    return dict(row) if row else None

def source_archive_path():
    import app
    return app.ROOT/'data'/'private_original_master.xlsx'

def stage_original_workbook(path, source_hash):
    """Store the exact XLSX bytes locally only, with no public/web copy."""
    dest=source_archive_path()
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        if hashlib.sha256(dest.read_bytes()).hexdigest()!=source_hash:
            raise ValueError('A different original workbook has already been archived. Do not overwrite it.')
        return dest
    temp=dest.with_suffix('.partial')
    shutil.copyfile(path,temp)
    if hashlib.sha256(temp.read_bytes()).hexdigest()!=source_hash:
        temp.unlink(missing_ok=True)
        raise ValueError('Source archive SHA-256 mismatch')
    os.replace(temp,dest)
    try:os.chmod(dest,0o600)
    except OSError:pass
    return dest

def main():
    p=argparse.ArgumentParser();p.add_argument('workbook');p.add_argument('--dry-run',action='store_true');args=p.parse_args()
    # Import directly into local copy of the Windows host's DB; no internet service required.
    import app
    app.init_db()
    if args.dry_run:
        mem=sqlite3.connect(':memory:');mem.row_factory=sqlite3.Row
        # Safe validation in a throwaway database, without altering the installed source.
        with app.db() as disk:disk.backup(mem)
        schema(mem);stats,issues=import_master(mem,args.workbook)
        print('DRY RUN:',stats,'\nSource exceptions:',len(issues));return
    with app.db() as c:
        schema(c);stats,issues=import_master(c,args.workbook)
        app.audit(c,None,'IMPORT','master_register',stats.get('linked_cases',0),f'Incomplete/duplicate source entries: {len(issues)}')
    with app.db() as c:
        checksum=c.execute('SELECT source_sha256 FROM master_source_meta WHERE id=1').fetchone()[0]
    archived=stage_original_workbook(args.workbook,checksum)
    out=app.ROOT/'data'/'IMPORT_RECONCILIATION.csv';out.parent.mkdir(exist_ok=True)
    with out.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['Source row','Source case ID','Issue requiring review']);w.writerows(issues)
    print('Imported OFFLINE:',stats,'\nReconciliation report:',out,'\nExact source workbook archived (offline):',archived)
    print('Unverified case records cannot be auto-inserted into hearing notices until reviewed.')
if __name__=='__main__':main()