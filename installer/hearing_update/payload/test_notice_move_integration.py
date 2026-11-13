import sys, sqlite3, tempfile, contextlib, datetime, types, pathlib
sys.path.insert(0,str(pathlib.Path(__file__).parent))
import hearing_v2
class App:
    def __init__(self,p):self.p=p
    @contextlib.contextmanager
    def db(self):
        c=sqlite3.connect(self.p);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    @staticmethod
    def now():return '2026-11-13T14:47:00'
    @staticmethod
    def valid_iso_date(x):
        try:datetime.date.fromisoformat(x);return True
        except: return False
    @staticmethod
    def audit(c,u,verb,entity,id,details=''):
        c.execute('INSERT INTO audit_events(user_id,action,entity,id,details) VALUES(?,?,?,?,?)',(u,verb,entity,id,details))
class Handler:
    user={'id':1}
    def allowed(self,what):return True
    def redirect(self,path):return path
    def csrf(self):return '<input type="hidden">'
with tempfile.TemporaryDirectory() as d:
    app=App(d+'/test.db')
    with app.db() as c:
        c.executescript('''
        CREATE TABLE hearing_notices_v2(id INTEGER PRIMARY KEY,status TEXT,review_stage TEXT,updated_at TEXT);
        CREATE TABLE hearing_days_v2(id INTEGER PRIMARY KEY,notice_id INTEGER,hearing_date TEXT,seq INTEGER);
        CREATE TABLE hearing_items_v2(id INTEGER PRIMARY KEY,day_id INTEGER,seq INTEGER);
        CREATE TABLE hearing_groups_v2(id INTEGER PRIMARY KEY,day_id INTEGER);
        CREATE TABLE audit_events(user_id INTEGER,action TEXT,entity TEXT,id INTEGER,details TEXT);
        INSERT INTO hearing_notices_v2 VALUES(1,'Draft','Draft',''),(2,'Draft','Draft',''),(3,'Approved','Approved','');
        INSERT INTO hearing_days_v2 VALUES(10,1,'2026-11-11',1),(11,1,'2026-11-13',2);
        INSERT INTO hearing_items_v2 VALUES(100,10,1),(101,10,2);
        INSERT INTO hearing_groups_v2 VALUES(200,10);
        ''')
    hearing_v2.appmod=lambda h:app
    h=Handler()
    assert hearing_v2.post(h,'/notice/move-day',{'day_id':'10','source_notice_id':'1','destination_notice_id':'2','new_hearing_date':'2026-11-18'})=='/notice/2'
    with app.db() as c:
        assert tuple(c.execute('select notice_id,hearing_date from hearing_days_v2 where id=10').fetchone())==(2,'2026-11-18')
        assert c.execute('select count(*) from hearing_items_v2 where day_id=10').fetchone()[0]==2
        assert c.execute('select count(*) from hearing_groups_v2 where day_id=10').fetchone()[0]==1
        assert c.execute('select action from audit_events').fetchone()[0]=='MOVE_HEARING_DAY'
        assert hearing_v2.notice_range_label('2026-11-11','2026-11-13')=='11th November 2026 to 13th November 2026'
        c.execute("INSERT INTO hearing_days_v2 VALUES(12,1,'2026-11-20',3)")
    try:
        hearing_v2.post(h,'/notice/move-day',{'day_id':'11','source_notice_id':'1','destination_notice_id':'3','new_hearing_date':'2026-11-20'})
        assert False,'Expected approved refusal'
    except ValueError:pass
    try:
        hearing_v2.post(h,'/notice/move-day',{'day_id':'11','source_notice_id':'1','destination_notice_id':'2','new_hearing_date':'2026-11-18'})
        assert False,'Expected duplicate refusal'
    except ValueError:pass
    with app.db() as c:
        assert c.execute('select notice_id from hearing_days_v2 where id=11').fetchone()[0]==1
        assert c.execute('select count(*) from audit_events').fetchone()[0]==1
    print('PASS: date ranges, atomic move, optional reschedule, preserved groups/cases, audit, approved/dedup blocks')