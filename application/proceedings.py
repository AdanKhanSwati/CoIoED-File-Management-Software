"""Actual case proceedings: separate from scheduled hearings; append-only case chronology."""
import csv,io,re
from datetime import date
KINDS=('Inquiry hearing','JIT proceedings','PTF proceedings','Court proceedings','Order / direction','Other official proceeding')

def schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS case_proceedings (
      id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL REFERENCES cases(id),
      proceeding_date TEXT NOT NULL,proceeding_type TEXT NOT NULL,summary TEXT NOT NULL,
      directions TEXT NOT NULL DEFAULT '',next_date TEXT NOT NULL DEFAULT '',
      document_id INTEGER REFERENCES documents(id),source_reference TEXT NOT NULL DEFAULT '',
      entered_by INTEGER NOT NULL REFERENCES users(id),recorded_at TEXT NOT NULL,
      verified INTEGER NOT NULL DEFAULT 0,supersedes_id INTEGER REFERENCES case_proceedings(id));
    CREATE INDEX IF NOT EXISTS idx_proceeding_date ON case_proceedings(case_id,proceeding_date DESC);
    """)

def history(c,case_id):
    return c.execute('''SELECT p.*,u.fullname as entered_by_name,d.original_name as document_name FROM case_proceedings p
       LEFT JOIN users u ON u.id=p.entered_by LEFT JOIN documents d ON d.id=p.document_id
       WHERE p.case_id=? AND NOT EXISTS(SELECT 1 FROM case_proceedings n WHERE n.supersedes_id=p.id)
       ORDER BY p.proceeding_date DESC,p.id DESC''',(case_id,)).fetchall()

def latest(c,case_id):
    return c.execute('''SELECT MAX(proceeding_date) FROM case_proceedings p WHERE case_id=? AND verified=1
        AND NOT EXISTS(SELECT 1 FROM case_proceedings n WHERE n.supersedes_id=p.id)''',(case_id,)).fetchone()[0]

def add(c,case_id,form,actor,recorded_at):
    d=form.get('proceeding_date','').strip();next_date=form.get('next_date','').strip()
    try:
        if not d or date.fromisoformat(d).isoformat()!=d:raise ValueError
        if next_date and date.fromisoformat(next_date).isoformat()!=next_date:raise ValueError
    except ValueError:raise ValueError('A real proceeding date in YYYY-MM-DD is required; next date must be valid if provided.')
    kind=form.get('proceeding_type','')
    if kind not in KINDS:raise ValueError('Invalid proceeding type')
    summary=form.get('summary','').strip()
    if len(summary)<5:raise ValueError('Enter a meaningful proceeding summary')
    docid=int(form['document_id']) if form.get('document_id','').strip() else None
    if docid and not c.execute('SELECT 1 FROM documents WHERE id=? AND case_id=?',(docid,case_id)).fetchone():raise ValueError('Selected order document does not belong to this case')
    sup=int(form['supersedes_id']) if form.get('supersedes_id','').strip() else None
    if sup and not c.execute('SELECT 1 FROM case_proceedings WHERE id=? AND case_id=?',(sup,case_id)).fetchone():raise ValueError('Cannot correct a proceeding from another case')
    cur=c.execute('''INSERT INTO case_proceedings(case_id,proceeding_date,proceeding_type,summary,directions,next_date,
    document_id,source_reference,entered_by,recorded_at,verified,supersedes_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
    (case_id,d,kind,summary[:12000],form.get('directions','')[:12000],next_date,docid,form.get('source_reference','')[:500],actor,recorded_at,1 if form.get('verified')=='yes' else 0,sup))
    return cur.lastrowid

def import_csv(c,data,actor,recorded_at):
    rd=csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
    if not {'case_no','proceeding_date','proceeding_type','summary'}.issubset(rd.fieldnames or []):
        raise ValueError('CSV missing required columns: case_no,proceeding_date,proceeding_type,summary')
    count=0;issues=[]
    for idx,row in enumerate(rd,start=2):
        if idx>20001:raise ValueError('Limit is 20,000 rows per import')
        number=re.sub(r'\s+','',row.get('case_no','').upper()).replace('–','-')
        case=c.execute('SELECT id FROM cases WHERE case_no=?',(number,)).fetchone()
        if not case:issues.append((idx,'No matching case ID'));continue
        # Duplicates are never silently overwritten.
        if c.execute('SELECT 1 FROM case_proceedings WHERE case_id=? AND proceeding_date=? AND proceeding_type=? AND summary=?',
            (case['id'],row.get('proceeding_date',''),row.get('proceeding_type',''),row.get('summary',''))).fetchone():
            issues.append((idx,'Duplicate source entry'));continue
        try:
            row['verified']='no'  # All bulk-imported history must be verified manually.
            add(c,case['id'],row,actor,recorded_at);count+=1
        except ValueError as ex:issues.append((idx,str(ex)))
    return count,issues