"""CoIoED Head Office - isolated offline LAN *pilot*, Python stdlib only.
Not approved for real personal-data processing without security acceptance.
"""
from __future__ import annotations
import argparse, base64, csv, hashlib, hmac, html, io, json, mimetypes, os, re, secrets, shutil, sqlite3, ssl, sys, threading, time, uuid, zipfile
from datetime import date, datetime, timedelta
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'coioed.db'
UPLOAD=ROOT/'uploads'
MAX_REQUEST=18*1024*1024
PBKDF_ROUNDS=600_000
SESSIONS_DAYS=1
ROLES={'admin','registrar','inquiry','records','hr','viewer'}
CASE_READ={'admin','registrar','inquiry','records','viewer'}
CASE_WRITE={'admin','registrar','inquiry','records'}
HEARING_WRITE={'admin','registrar'}
FSP_WRITE={'admin','records'}
HR_ACCESS={'admin','hr'}
MOVEMENT_WRITE={'admin','registrar','inquiry','records'}
MOVEMENT_RECEIVE={'admin','registrar'}
LOGIN_FAIL={}
LOGIN_LOCK=threading.Lock()

def e(value): return html.escape('' if value is None else str(value),quote=True)
def now(): return datetime.now().isoformat(timespec='seconds')
@contextmanager
def db():
    c=sqlite3.connect(DB,timeout=10)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=10000')
    try:
        with c:
            yield c
    finally:
        c.close()

def init_db():
    DB.parent.mkdir(parents=True,exist_ok=True); UPLOAD.mkdir(parents=True,exist_ok=True)
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,fullname TEXT NOT NULL,role TEXT NOT NULL, salt TEXT NOT NULL,password_hash TEXT NOT NULL, active INTEGER DEFAULT 1,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),csrf TEXT NOT NULL,expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY,case_no TEXT NOT NULL UNIQUE,name TEXT NOT NULL,parentage TEXT DEFAULT '',cnic TEXT DEFAULT '',missing_date TEXT DEFAULT '',registered_date TEXT DEFAULT '',address TEXT DEFAULT '',district TEXT DEFAULT '',region TEXT DEFAULT 'HO Islamabad',status TEXT DEFAULT 'Under Investigation',notes TEXT DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS case_status_events(id INTEGER PRIMARY KEY,case_id INTEGER NOT NULL REFERENCES cases(id),old_status TEXT,new_status TEXT,changed_at TEXT NOT NULL,user_id INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,case_id INTEGER REFERENCES cases(id),employee_id INTEGER REFERENCES staff(id),section TEXT NOT NULL,category TEXT NOT NULL,original_name TEXT NOT NULL,stored_name TEXT UNIQUE NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,uploaded_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hearings(id INTEGER PRIMARY KEY,case_id INTEGER NOT NULL REFERENCES cases(id),hearing_date TEXT NOT NULL,hearing_time TEXT DEFAULT '10:00 am onwards',officers TEXT DEFAULT '',instructions TEXT DEFAULT '',reference_no TEXT DEFAULT '',created_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(case_id,hearing_date));
        CREATE TABLE IF NOT EXISTS fsp(id INTEGER PRIMARY KEY,case_id INTEGER NOT NULL UNIQUE REFERENCES cases(id),status TEXT DEFAULT 'Under Review',notes TEXT DEFAULT '',updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS staff(id INTEGER PRIMARY KEY,name TEXT NOT NULL,designation TEXT DEFAULT '',branch TEXT DEFAULT 'Head Office',joining_date TEXT DEFAULT '',status TEXT DEFAULT 'Active',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY,staff_id INTEGER NOT NULL REFERENCES staff(id),date TEXT NOT NULL,kind TEXT NOT NULL,notes TEXT DEFAULT '',user_id INTEGER REFERENCES users(id),UNIQUE(staff_id,date));
        CREATE TABLE IF NOT EXISTS leaves(id INTEGER PRIMARY KEY,staff_id INTEGER NOT NULL REFERENCES staff(id),kind TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,reason TEXT DEFAULT '',status TEXT DEFAULT 'Pending',created_by INTEGER REFERENCES users(id),approved_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),action TEXT NOT NULL,entity TEXT NOT NULL,entity_id TEXT DEFAULT '',detail TEXT DEFAULT '',at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_case_status ON cases(status); CREATE INDEX IF NOT EXISTS idx_case_region ON cases(region); CREATE INDEX IF NOT EXISTS idx_case_name ON cases(name); CREATE INDEX IF NOT EXISTS idx_hearing_date ON hearings(hearing_date); CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at);
        ''')
        # Preserve older pilot databases and add the movement chain without modifying existing cases.
        cols=[x['name'] for x in c.execute('PRAGMA table_info(cases)').fetchall()]
        if 'file_branch' not in cols:c.execute("ALTER TABLE cases ADD COLUMN file_branch TEXT DEFAULT 'Record Room'")
        if 'archived' not in cols:c.execute("ALTER TABLE cases ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        if 'archive_note' not in cols:c.execute("ALTER TABLE cases ADD COLUMN archive_note TEXT DEFAULT ''")
        c.executescript('''
        CREATE TABLE IF NOT EXISTS file_movements(
          id INTEGER PRIMARY KEY,case_id INTEGER NOT NULL REFERENCES cases(id),
          from_branch TEXT NOT NULL,to_branch TEXT NOT NULL,purpose TEXT NOT NULL,
          expected_return TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'Pending Receipt',
          sent_by INTEGER NOT NULL REFERENCES users(id),sent_at TEXT NOT NULL,
          received_by INTEGER REFERENCES users(id),received_at TEXT DEFAULT '',decision_note TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_movement_case ON file_movements(case_id);
        CREATE INDEX IF NOT EXISTS idx_movement_status ON file_movements(status);
        ''')
        # DDL referencing staff prior to its CREATE is allowed in SQLite.
        import hearing_v2
        hearing_v2.db_schema(c)
        import employee_access,proceedings,master_import
        employee_access.init(c);proceedings.schema(c);master_import.schema(c)
        import operations_v09
        operations_v09.schema(c)
        import procurement
        procurement.schema(c)

def hash_pw(pw,salt): return hashlib.pbkdf2_hmac('sha256',pw.encode(),bytes.fromhex(salt),PBKDF_ROUNDS).hex()
def audit(c,user,action,entity,entity_id='',detail=''):
    c.execute('INSERT INTO audit(user_id,action,entity,entity_id,detail,at) VALUES(?,?,?,?,?,?)',(user,action,entity,str(entity_id),str(detail)[:400],now()))

def valid_iso_date(s):
    if not s: return True
    try: return date.fromisoformat(s).isoformat()==s
    except ValueError: return False

def valid_missing_date(s):
    if not s or s in ('Unknown','Not provided','N/A'):return True
    if valid_iso_date(s):return True
    if re.fullmatch(r'[12]\d{3}',s):return True
    if re.fullmatch(r'(0?[1-9]|1[0-2])/[12]\d{3}',s):return True
    try:
        datetime.strptime(s,'%d/%m/%Y');return True
    except ValueError:return False

def normalized_case_no(s):
    value=str(s or '').strip().upper().replace('–','-').replace('—','-')
    # A clubbed file contains two or more official IDs and is ONE case file.
    if valid_clubbed_case_no(value):return ' '.join(value.split())
    return re.sub(r'\s+','',value)

def valid_clubbed_case_no(s):
    return bool(re.fullmatch(r'\d{3,8}-[KPBSIAF](?:\s+\d{3,8}-[KPBSIAF])+',str(s or '').strip().upper()))

def valid_hearing_case_no(s):
    return valid_case_no(s) or valid_clubbed_case_no(s)


def valid_case_no(s):
    # CoIoED numeric identifier + regional suffix. Current examples use four digits; allow longer official IDs and preserve zeros.
    return bool(re.fullmatch(r'\d{3,8}-[KPBSIAF]', s or ''))

def display_case_no(row):
    """Official source text, even when incomplete/clubbed; never expose surrogate as official ID."""
    if row['source_row'] is not None and row['source_case_no'] is not None:
        return row['source_case_no'] or f'[ID not recorded · source row {row["source_row"]}]'
    return row['case_no']


def setup_admin(username, password, fullname='System Administrator'):
    if not username or len(password)<12: raise ValueError('Username required and password must be at least 12 characters.')
    init_db()
    with db() as c:
        if c.execute('SELECT 1 FROM users LIMIT 1').fetchone(): raise ValueError('Setup already completed; create users from Admin / Users.')
        salt=secrets.token_hex(16)
        c.execute('INSERT INTO users(username,fullname,role,salt,password_hash,created_at) VALUES(?,?,?,?,?,?)',(username,fullname,'admin',salt,hash_pw(password,salt),now()))
        import employee_access
        employee_access.init(c)
    return True

CSS=(ROOT/'assets'/'app_ui.css').read_text(encoding='utf-8')

def field(name,label,value='',typ='text',required=False,options=None):
    attr=' required' if required else ''
    if options is not None:
        control='<select name="%s">%s</select>'%(e(name),''.join('<option value="%s"%s>%s</option>'%(e(o), ' selected' if str(o)==str(value) else '',e(o)) for o in options))
    elif typ=='textarea': control=f'<textarea name="{e(name)}">{e(value)}</textarea>'
    else: control=f'<input type="{e(typ)}" name="{e(name)}" value="{e(value)}"{attr}>'
    return f'<div class="field"><label>{e(label)}</label>{control}</div>'

def lookup_field(name,label,value='',mode='all',action='fill',required=False):
    """Accessible case picker. Server still validates submitted case identity."""
    return ('<div class="field"><label>'+e(label)+'</label>'
            +'<input type="text" name="'+e(name)+'" value="'+e(value)+'" '
            +'data-case-lookup="'+e(mode)+'" data-case-action="'+e(action)+'" '
            +'autocomplete="off" placeholder="Search ID or missing person..." '
            +('required ' if required else '')+'></div>')

def table(headers,rows):
    return '<div class="table-container"><table><thead><tr>'+''.join(f'<th>{e(h)}</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'

def fmt(s): return e(s) if s not in (None,'') else '<span class="muted">-</span>'

def date_or_blank(s): return s.strip()[:10] if s else ''

class App(BaseHTTPRequestHandler):
    server_version='CoIoED-Offline-Pilot/0.9'
    def log_message(self,format,*args):
        print('%s - %s'%(self.address_string(),format%args))
    def security_headers(self,typ='text/html; charset=utf-8',length=None,extra=None):
        self.send_header('Content-Type',typ)
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        self.send_header('Cross-Origin-Opener-Policy','same-origin')
        if getattr(self.server,'is_tls',False): self.send_header('Strict-Transport-Security','max-age=31536000')
        self.send_header('Cache-Control','no-store, private')
        self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        if length is not None: self.send_header('Content-Length',str(length))
        if extra:
            for k,v in extra.items(): self.send_header(k,v)
    def write(self,text,status=200,typ='text/html; charset=utf-8',extra=None):
        data=text.encode('utf-8') if isinstance(text,str) else text
        self.send_response(status);self.security_headers(typ,len(data),extra);self.end_headers();self.wfile.write(data)
    def redirect(self,path,cookie=None):
        self.send_response(303);self.security_headers('text/plain',0,{'Location':path,**({'Set-Cookie':cookie} if cookie else {})});self.end_headers()
    def abort(self,code=403,message='Access denied'):
        self.write(self.wrap('Error',f'<div class="panel danger"><h2>{e(message)}</h2></div>'),code)
    def parse(self):
        u=urlsplit(self.path);return u.path,{k:v[0] for k,v in parse_qs(u.query,keep_blank_values=True).items()}
    def get_user(self):
        cookie=SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: return None
        if 'sid' not in cookie:return None
        token=cookie['sid'].value
        if not re.fullmatch('[A-Za-z0-9_-]{30,120}',token):return None
        token_hash=hashlib.sha256(token.encode()).hexdigest()
        with db() as c:
            row=c.execute('SELECT users.*,sessions.csrf,sessions.expires_at FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND users.active=1',(token_hash,)).fetchone()
        return row if row and row['expires_at']>now() else None
    def csrf(self): return f'<input type="hidden" name="csrf" value="{e(self.user["csrf"])}">' if self.user else ''
    def wrap(self,title,body):
        # COIOED_UI_V13_SHELL
        import ui_v13
        return ui_v13.render(self,title,body)
        if not getattr(self,'user',None):
            return f'<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} | CoIoED</title><link rel="stylesheet" href="/style.css"></head><body><main>{body}</main></body></html>'
        role='Individual access profile' if self.user['staff_id'] else 'Administrator'
        quick='<form class="quick-case" action="/cases" method="get" role="search"><input name="q" data-case-lookup="all" data-case-action="navigate" aria-label="Find case number or missing person" placeholder="Search a case number or missing person" autocomplete="off"><button type="submit">Search</button></form>' if self.allowed('cases.view') else ''
        nav=[('/','Dashboard')]
        for url,name,perm in [('/cases','Comprehensive Register','cases.view'),('/rotation','Hearing Rotation','hearing.view'),('/production','Production Orders','production.view'),('/movements','File Movement','movement.view'),('/notices','Hearing Schedules','hearing.view'),('/reports/monthly','Reports & Statistics','reports.view'),('/procurement','Procurement','procurement.view'),('/fsp','Family Support','fsp.view'),('/hr','HR & Attendance','hr.view'),('/users','Employees & Access','users.manage'),('/audit','Audit & History','audit.view')]:
            if self.allowed(perm):nav.append((url,name))
        icons={
          '/':'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
          '/cases':'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
          '/rotation':'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
          '/production':'<path d="M12 3v18M5 6h14M7 6l-4 7h8L7 6zm10 0-4 7h8l-4-7zM8 21h8"/>',
          '/movements':'<path d="M4 7h16l-4-4m4 4-4 4M20 17H4l4-4m-4 4 4 4"/>',
          '/notices':'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18m-13 4h3m2 0h3m-8 3h3"/>',
          '/reports/monthly':'<path d="M4 20V11h4v9m3 0V4h4v16m3 0v-7h3v7M3 20h19"/>',
          '/procurement':'<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 3v4m8-4v4M7 12h10M7 16h6"/>',
          '/fsp':'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.7 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.7 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z"/>',
          '/hr':'<circle cx="12" cy="7" r="4"/><path d="M4 21v-3a8 8 0 0 1 16 0v3"/>',
          '/users':'<circle cx="9" cy="8" r="3"/><path d="M3 19v-2a6 6 0 0 1 12 0v2M17 8h4m-2-2v4M17 15h4m-2-2v4"/>',
          '/audit':'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>'}
        current=self.parse()[0];links=[]
        for path,name in nav:
            if path=='/cases':links.append('<div class="nav-section">CASE MANAGEMENT</div>')
            if path=='/reports/monthly':links.append('<div class="nav-section">ADMINISTRATION</div>')
            if path=='/audit':links.append('<div class="nav-section">SYSTEM</div>')
            active=current==path or (path=='/cases' and current.startswith('/case/')) or (path=='/notices' and current.startswith('/notice/'))
            glyph='<svg viewBox="0 0 24 24" aria-hidden="true">'+icons[path]+'</svg>'
            links.append(f'<a class="nav-link {"active" if active else ""}" href="{path}" {"aria-current=page" if active else ""}><span class="nav-icon">{glyph}</span><span class="nav-label">{e(name)}</span></a>')
        initials=''.join(word[0] for word in self.user['fullname'].split()[:2]).upper() or 'CO'
        return f'''<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} | CoIoED</title><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/dashboard.css"><link rel="stylesheet" href="/ui_refinement_v12.css"><link rel="stylesheet" href="/ui_v122.css"><script src="/case_lookup.js" defer></script><script src="/workspace.js" defer></script><script src="/ui_refinement_v12.js" defer></script><script src="/ui_v122.js" defer></script><script src="/officer_lookup.js" defer></script><script src="/instruction_lookup.js" defer></script></head><body class="authenticated ui-v122"><a class="skip-link" href="#main-content">Skip to main content</a><header class="app-header"><button type="button" id="nav-toggle" class="nav-toggle" aria-label="Open navigation" aria-controls="app-sidebar" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button><div class="header-title"><strong>Commission Workspace</strong><small>Head Office · Islamabad</small></div>{quick}<div class="header-right"><span class="ui-build" aria-label="Installed interface version 12.2">UI 12.2</span><span class="avatar" aria-hidden="true">{e(initials)}</span><div class="user-meta"><strong>{e(self.user['fullname'])}</strong><small>{e(role)}</small></div><a class="btn secondary" href="/account/security" style="font-size:11px;padding:7px 10px">My Account</a><form action="/logout" method="post" class="inline">{self.csrf()}<button class="logout">Sign out</button></form></div></header><div id="sidebar-backdrop" class="sidebar-backdrop" hidden aria-hidden="true"></div><nav id="app-sidebar" class="app-sidebar" aria-label="Primary navigation"><div class="nav-brand"><img src="/logo.png" alt="Commission emblem"><div><strong>CoIoED</strong><small>Head Office · Islamabad</small></div></div><div class="nav-section">WORKSPACE</div>{''.join(links)}<div class="nav-foot"><b>OFFLINE · LOCAL NETWORK</b><small>Interface 12.2 · Offline pilot</small></div></nav><main id="main-content" class="workspace" tabindex="-1"><div class="workspace-kicker"><b>CoIoED Head Office</b><span class="crumb-divider">/</span>{e(title)}</div>{body}</main></body></html>'''
    def allowed(self,permission):
        if not self.user:return False
        import employee_access
        with db() as c:return employee_access.allowed(c,self.user['id'],permission)
    def require(self,permission):
        if not self.user:
            self.redirect('/login');return False
        if not self.allowed(permission):
            self.abort(403,'Permission not granted to this employee');return False
        return True
    def formdata(self):
        try: n=int(self.headers.get('Content-Length','0'))
        except ValueError:return {},[],400
        if n<0 or n>MAX_REQUEST:return {},[],413
        body=self.rfile.read(n)
        mime=self.headers.get('Content-Type','')
        if mime.startswith('multipart/form-data'):
            raw=b'Content-Type: '+mime.encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+body
            try: msg=BytesParser(policy=default).parsebytes(raw)
            except Exception: return {},[],400
            fields={};files=[]
            for part in msg.iter_parts():
                if part.get_content_disposition()!='form-data':continue
                key=part.get_param('name',header='content-disposition')
                filename=part.get_filename()
                blob=part.get_payload(decode=True) or b''
                if filename is not None:files.append((key,filename,blob))
                elif key:fields[key]=blob.decode('utf-8','replace')
            return fields,files,200
        try:return {k:v[0] for k,v in parse_qs(body.decode('utf-8'),keep_blank_values=True).items()},[],200
        except UnicodeDecodeError:return {},[],400
    def check_post(self,data):
        return self.user and hmac.compare_digest(data.get('csrf',''),self.user['csrf'])
    def do_GET(self):
        path,q=self.parse()
        # COIOED_UI_V13_STATIC
        if path=='/ui-v13.css':return self.write((ROOT/'assets'/'ui_v13.css').read_bytes(),typ='text/css; charset=utf-8')
        if path=='/ui-v13.js':return self.write((ROOT/'ui_v13.js').read_bytes(),typ='application/javascript; charset=utf-8')
        if path=='/style.css':return self.write(CSS,typ='text/css; charset=utf-8')
        if path=='/dashboard.css':return self.write((ROOT/'assets'/'dashboard_analytics.css').read_bytes(),typ='text/css; charset=utf-8')
        if path=='/ui_refinement_v12.css':return self.write((ROOT/'assets'/'ui_refinement_v12.css').read_bytes(),typ='text/css; charset=utf-8')
        if path=='/ui_refinement_v12.js':return self.write((ROOT/'ui_refinement_v12.js').read_bytes(),typ='application/javascript; charset=utf-8')
        if path=='/ui_v122.css':return self.write((ROOT/'assets'/'ui_v122.css').read_bytes(),typ='text/css; charset=utf-8')
        if path=='/ui_v122.js':return self.write((ROOT/'ui_v122.js').read_bytes(),typ='application/javascript; charset=utf-8')
        if path=='/case_lookup.js':return self.write((ROOT/'case_lookup.js').read_bytes(),typ='application/javascript; charset=utf-8')
        if path=='/officer_lookup.js':return self.write((ROOT/'officer_lookup.js').read_bytes(),typ='application/javascript; charset=utf-8')
        if path=='/instruction_lookup.js':return self.write((ROOT/'instruction_lookup.js').read_bytes(),typ='application/javascript; charset=utf-8')
        if path=='/workspace.js':return self.write((ROOT/'workspace.js').read_bytes(),typ='application/javascript; charset=utf-8')
        if path=='/logo.png':return self.write((ROOT/'assets'/'commission_logo.png').read_bytes(),typ='image/png')
        self.user=self.get_user()
        if path=='/login':return self.login_page()
        if not self.user:return self.redirect('/login')
        try:
            if path=='/':
                import dashboard_analytics
                return dashboard_analytics.render(self,q)
            if path=='/cases':
                if not self.require('cases.view'):return
                return self.cases(q)
            if path=='/case/new':
                if not self.require('cases.edit'):return
                return self.case_form(None)
            if re.fullmatch(r'/case/\d+',path):
                if not self.require('cases.view'):return
                return self.case_detail(int(path.split('/')[-1]))
            if re.fullmatch(r'/case/edit/\d+',path):
                if not self.require('cases.edit'):return
                return self.case_form(int(path.split('/')[-1]))
            if path=='/rotation':
                if not self.require('hearing.view'):return
                if not self.require('cases.view'):return
                import operations_v09
                return operations_v09.rotation_page(self,q)
            if path=='/production':
                if not self.require('production.view'):return
                import operations_v09
                return operations_v09.orders_page(self,q)
            if path=='/movements':
                if not self.require('movement.view'):return
                return self.movements(q)
            if path in ('/cases/export.csv','/cases/export.xlsx','/cases/export.pdf'):
                if not self.require('cases.export'):return
                return self.register_export(path,q)
            if path=='/cases/source-review':
                if not self.require('cases.view'):return
                return self.source_review(q)
            if path=='/cases/original.xlsx':
                if not self.require('cases.export'):return
                return self.original_workbook_export()
            if path=='/officers/suggest.json':
                if not self.require('hearing.edit'):return
                import hearing_v2
                return hearing_v2.suggest_officers(self,q)
            if path=='/instructions/suggest.json':
                if not self.require('hearing.edit'):return
                import hearing_v2
                return hearing_v2.suggest_instructions(self,q)
            if path=='/cases/search.json':
                if not any(self.allowed(p) for p in ('cases.view','hearing.view','movement.view','production.view','fsp.view')):
                    return self.abort(403,'Case lookup permission not granted')
                return self.search_case_options(q)
            if path=='/cases/template.csv':
                if not self.require('cases.edit'):return
                return self.csv_template()
            if path=='/documents/get' and self.user:
                if not self.require('documents.view'):return
                return self.document_get(q)
            if path.startswith('/notice/') or path=='/notices':
                if not self.require('hearing.view'):return
                import hearing_v2
                return hearing_v2.get(self,path,q)
            if path=='/hearings':
                if not self.require('hearing.view'):return
                return self.hearings(q)
            if path=='/hearings/print':
                if not self.require('hearing.export'):return
                return self.hearing_print(q)
            if path=='/reports/monthly':
                if not self.require('reports.view'):return
                return self.monthly(q)
            if path=='/procurement':
                if not self.require('procurement.view'):return
                import procurement
                return procurement.list_page(self,q)
            if path=='/procurement/new':
                if not self.require('procurement.edit'):return
                import procurement
                return procurement.edit_page(self)
            if re.fullmatch(r'/procurement/\d+',path):
                if not self.require('procurement.view'):return
                import procurement
                return procurement.edit_page(self,int(path.rsplit('/',1)[-1]))
            if path=='/procurement/export.csv':
                if not self.require('procurement.export'):return
                import procurement
                return procurement.export_csv(self,q)
            if path=='/fsp':
                if not self.require('fsp.view'):return
                import operations_v09
                return operations_v09.fsp_page(self,q)
            if path=='/hr':
                if not self.require('hr.view'):return
                return self.hr(q)
            if re.fullmatch(r'/hr/\d+',path):
                if not self.require('hr.view'):return
                return self.staff_detail(int(path.split('/')[-1]))
            if path=='/account/security':
                return self.account_security_page(self.user['id'],self_service=True)
            if re.fullmatch(r'/users/credentials/\d+',path):
                if not self.require('users.manage'):return
                return self.account_security_page(int(path.rsplit('/',1)[-1]))
            if path=='/users/employee/new':
                if not self.require('users.manage'):return
                return self.employee_edit_page()
            if re.fullmatch(r'/users/employee/\d+',path):
                if not self.require('users.manage'):return
                return self.employee_edit_page(int(path.rsplit('/',1)[-1]))
            if re.fullmatch(r'/users/\d+',path):
                if not self.require('users.manage'):return
                return self.employee_permissions_page(int(path.rsplit('/',1)[-1]))
            if path=='/proceedings/history.csv':
                if not self.require('proceedings.view'):return
                return self.proceedings_csv(q)
            if path=='/proceedings/template.csv':
                if not self.require('proceedings.import'):return
                sample='case_no,proceeding_date,proceeding_type,summary,directions,next_date,source_reference\n0001-K,2026-11-01,Inquiry hearing,Sample proceeding only,Order follow-up,2026-11-15,Page 1\n'
                return self.write(sample,typ='text/csv; charset=utf-8',extra={'Content-Disposition':'attachment; filename=proceedings_template.csv'})
            if path=='/users':
                if not self.require('users.manage'):return
                return self.users()
            if path=='/audit':
                if not self.require('audit.view'):return
                return self.audit_page()
            if path=='/admin/backup':
                if not self.require('backups.export'):return
                return self.backup()
            return self.abort(404,'Page not found')
        except (ValueError,sqlite3.IntegrityError) as exc:return self.abort(400,str(exc))
        except Exception as exc:
            print('ERROR:',repr(exc),file=sys.stderr);return self.abort(500,'Unexpected server error. See host console.')
    def do_POST(self):
        path,q=self.parse();self.user=self.get_user()
        form,files,status=self.formdata()
        if status!=200:return self.abort(status,'Invalid or oversized request')
        if path=='/login':return self.login_action(form)
        if not self.user:return self.redirect('/login')
        if not self.check_post(form):return self.abort(403,'Invalid request token')
        try:
            if path=='/logout':return self.logout()
            # COIOED_V131_HISTORIC_PROCEEDINGS POST route
            if path=='/proceedings/historical/add':
                if not self.require('proceedings.add'):return
                import historical_proceedings_v131
                return historical_proceedings_v131.add_entry(self,form)
            if path=='/proceedings/historical/verify':
                if not self.require('proceedings.add'):return
                import historical_proceedings_v131
                return historical_proceedings_v131.verify_entry(self,form)
            if path=='/proceedings/save':
                if not self.require('proceedings.add'):return
                return self.save_proceeding(form)
            if path=='/proceedings/import':
                if not self.require('proceedings.import'):return
                return self.import_proceedings(files)
            if path=='/account/credentials/save':
                return self.save_account_credentials(form)
            if path=='/users/employee/save':
                if not self.require('users.manage'):return
                return self.save_employee_details(form)
            if path=='/users/account/status':
                if not self.require('users.manage'):return
                return self.set_employee_login_status(form)
            if path=='/users/permissions':
                if not self.require('users.manage'):return
                return self.update_employee_permissions(form)
            if path=='/cases/archive':
                if not self.require('cases.archive'):return
                return self.archive_case(form)
            if path=='/movements/dispatch':
                if not self.require('movement.dispatch'):return
                return self.dispatch_file(form)
            if path=='/movements/decision':
                if not self.require('movement.receive'):return
                return self.decide_movement(form)
            if path=='/cases/save':
                if not self.require('cases.edit'):return
                return self.save_case(form)
            if path=='/cases/import':
                if not self.require('cases.import'):return
                return self.import_cases(files)
            if path=='/documents/upload':
                if not self.require('documents.upload'):return
                return self.upload_document(form,files)
            if path.startswith('/notice/'):
                import hearing_workflow_v121,hearing_v2
                permission=('hearing.edit' if path=='/notice/submit-secretary' or path not in hearing_workflow_v121.PATHS else 'hearing.approve')
                if not self.require(permission):return
                return hearing_v2.post(self,path,form)
            if path=='/hearings/add':
                if not self.require('hearing.edit'):return
                return self.add_hearing(form)
            if path=='/fsp/save':
                if not self.require('fsp.edit'):return
                import operations_v09
                return operations_v09.save_fsp_page(self,form)
            if path=='/fsp/share/add':
                if not self.require('fsp.edit'):return
                import operations_v09
                return operations_v09.save_share_page(self,form)
            if path=='/fsp/share/delete':
                if not self.require('fsp.edit'):return
                import operations_v09
                return operations_v09.delete_share_page(self,form)
            if path=='/fsp/default':
                if not self.require('users.manage'):return
                import operations_v09
                return operations_v09.save_fsp_default(self,form)
            if path.startswith('/procurement/'):
                import procurement
                return procurement.save(self,path,form)
            if path=='/production/save':
                if not self.require('production.edit'):return
                import operations_v09
                return operations_v09.save_order_page(self,form)
            if path=='/rotation/verify':
                if not self.require('hearing.edit'):return
                import operations_v09
                return operations_v09.verify_history(self,form)
            if path=='/hr/staff/save':
                if not self.require('hr.manage'):return
                return self.save_staff(form)
            if path=='/hr/attendance/save':
                if not self.require('hr.manage'):return
                return self.save_attendance(form)
            if path=='/hr/leave/save':
                if not self.require('hr.manage'):return
                return self.save_leave(form)
            if path=='/hr/leave/decide':
                if not self.require('hr.leave_approve'):return
                return self.decide_leave(form)
            if path=='/users/add':
                if not self.require('users.manage'):return
                return self.add_user(form)
            return self.abort(404,'Action not found')
        except (ValueError,sqlite3.IntegrityError) as exc:return self.abort(400,str(exc))
        except Exception as exc:
            print('ERROR:',repr(exc),file=sys.stderr);return self.abort(500,'Unexpected server error. See host console.')
    def login_page(self,error=''):
        if self.user:return self.redirect('/')
        err=f'<div class="notice danger">{e(error)}</div>' if error else ''
        return self.write(self.wrap('Sign in',f'<div class="panel login"><img src="/logo.png" alt="CoIoED"><h1>Head Office Records</h1><p class="sub" style="text-align:center">LAN-only system | Authorized personnel</p>{err}<form method="post" action="/login">{field("username","Username",required=True)}{field("password","Password",typ="password",required=True)}<button style="width:100%;margin-top:10px">Secure Sign In</button></form><p class="muted small">PILOT - use synthetic data until security approval.</p></div>'))
    def login_action(self,f):
        ip=self.client_address[0]
        with LOGIN_LOCK:
            ts=[x for x in LOGIN_FAIL.get(ip,[]) if time.time()-x<900];LOGIN_FAIL[ip]=ts
            if len(ts)>=8:return self.login_page('Too many failed attempts; retry in 15 minutes.')
        with db() as c: u=c.execute('SELECT * FROM users WHERE username=? AND active=1',(f.get('username','').strip(),)).fetchone()
        if not u or not hmac.compare_digest(hash_pw(f.get('password',''),u['salt']),u['password_hash']):
            with LOGIN_LOCK:LOGIN_FAIL.setdefault(ip,[]).append(time.time())
            return self.login_page('Invalid username or password.')
        with LOGIN_LOCK:LOGIN_FAIL.pop(ip,None)
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        expires=(datetime.now()+timedelta(days=SESSIONS_DAYS)).isoformat(timespec='seconds')
        with db() as c:
            c.execute('DELETE FROM sessions WHERE expires_at<?',(now(),))
            c.execute('INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],csrf,expires))
            audit(c,u['id'],'LOGIN','session')
        secure='; Secure' if getattr(self.server,'is_tls',False) else ''
        self.redirect('/',f'sid={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age=86400{secure}')
    def logout(self):
        cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
        token=cookie['sid'].value if 'sid' in cookie else ''
        with db() as c:
            c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
            audit(c,self.user['id'],'LOGOUT','session')
        secure='; Secure' if getattr(self.server,'is_tls',False) else ''
        self.redirect('/login',f'sid=; HttpOnly; Path=/; SameSite=Strict; Max-Age=0{secure}')
    def home(self):
        stats=[];upcoming=[];movements=[];alerts=[]
        with db() as c:
            if self.allowed('cases.view'):
                total=c.execute('SELECT COUNT(*) FROM cases WHERE archived=0').fetchone()[0]
                inq=c.execute("SELECT COUNT(*) FROM cases WHERE archived=0 AND status='Under Investigation'").fetchone()[0]
                stats.extend([('Registered cases',total,'green','Indexed electronic records'),('Under investigation',inq,'gold','Active inquiries')])
            if self.allowed('hearing.view'):
                nxt=c.execute('SELECT COUNT(*) FROM hearing_items_v2 i JOIN hearing_days_v2 d ON d.id=i.day_id WHERE d.hearing_date>=?',(date.today().isoformat(),)).fetchone()[0]
                stats.append(('Upcoming case hearings',nxt,'blue','Scheduled appearances'))
                upcoming=c.execute('SELECT d.hearing_date,d.area_caption,d.notice_id,COUNT(i.id) n FROM hearing_days_v2 d LEFT JOIN hearing_items_v2 i ON i.day_id=d.id WHERE d.hearing_date>=? GROUP BY d.id ORDER BY d.hearing_date,d.seq LIMIT 5',(date.today().isoformat(),)).fetchall()
            if self.allowed('movement.view'):
                pending=c.execute("SELECT COUNT(*) FROM file_movements WHERE status='Pending Receipt'").fetchone()[0]
                stats.append(('Awaiting file receipt',pending,'red','Movement acknowledgements'))
                movements=c.execute('SELECT m.*,k.case_no FROM file_movements m JOIN cases k ON k.id=m.case_id ORDER BY m.id DESC LIMIT 5').fetchall()
            if self.allowed('production.view'):
                po=c.execute("SELECT COUNT(*) FROM production_orders WHERE order_status='Pending execution'").fetchone()[0]
                alerts.append(('Orders pending execution',po,'/production','Production orders'))
            if self.allowed('fsp.view'):
                fsp=c.execute("SELECT COUNT(*) FROM fsp WHERE status IN ('Referred','Pending review','Under Review','In progress')").fetchone()[0]
                alerts.append(('Family support reviews',fsp,'/fsp','Support package'))
            if self.allowed('hr.view'):
                leave=c.execute("SELECT COUNT(*) FROM leaves WHERE status='Pending'").fetchone()[0]
                alerts.append(('Leave approvals',leave,'/hr','Staff administration'))
        content='<section class="dashboard-welcome"><div><div class="page-overline">COMMISSION OPERATIONS</div><h1>Head Office overview</h1><p>Casework, hearings and file custody — one operational workspace.</p></div><div class="actions"><a href="/cases" class="btn secondary">Open case register</a><a href="/notices" class="btn">Prepare hearing schedule</a></div></section>'
        if stats:
            signs={'green':'▤','gold':'◷','blue':'▦','red':'⇄'}
            content+='<div class="grid">'+''.join(f'<div class="stat {tone}"><div class="stat-top"><span>{e(label)}</span><span class="stat-symbol">{signs[tone]}</span></div><b>{v:,}</b><small>{e(hint)}</small></div>' for label,v,tone,hint in stats)+'</div>'
        content+='<div class="dashboard-cols"><section class="panel"><div class="panel-title"><div><div class="page-overline">HEARING DESK</div><h2>Upcoming hearings</h2></div><a href="/notices">All hearing notices →</a></div>'
        if upcoming:
            content+='<div class="dashboard-list">'+''.join(f'<div class="item"><div><a href="/notice/{row["notice_id"]}">{e(row["hearing_date"])}</a><small>{e(row["area_caption"] or "District not recorded")}</small></div><span class="pill">{row["n"]} case(s)</span></div>' for row in upcoming)+'</div>'
        else:content+='<div class="empty">No upcoming hearings have been registered.<br><a href="/notices">Create a hearing notice →</a></div>'
        content+='</section><section class="panel"><div class="panel-title"><div><div class="page-overline">FILE CONTROL</div><h2>Latest file movement</h2></div><a href="/movements">Movement register →</a></div>'
        if movements:
            content+='<div class="dashboard-list">'+''.join(f'<div class="item"><div><a href="/case/{row["case_id"]}">{e(row["case_no"])}</a><small>{e(row["from_branch"])} → {e(row["to_branch"])}</small></div><span class="pill {"amber" if row["status"]=="Pending Receipt" else "gray"}">{e(row["status"])}</span></div>' for row in movements)+'</div>'
        else:content+='<div class="empty">No movement recorded yet.<br><a href="/movements">Open custody register →</a></div>'
        content+='</section></div>'
        if alerts:
            content+='<section class="panel"><div class="panel-title"><div><div class="page-overline">ACTION CENTRE</div><h2>Items requiring follow-up</h2></div><small>Based on recorded workflow status</small></div><div class="dashboard-attention">'+''.join(f'<a href="{link}"><span class="caption">{e(name)}</span><span class="count">{v:,}</span><span class="more">Open {e(module)} →</span></a>' for name,v,link,module in alerts)+'</div></section>'
        content+='<div class="notice"><b>Development pilot:</b> These figures reflect stored and verified system records only. Historical proceedings, production-order execution and the imported master register must be verified before official reporting.</div>'
        return self.write(self.wrap('Dashboard',content))
    def archive_case(self,f):
        try:id=int(f.get('case_id','0'))
        except ValueError:raise ValueError('Invalid case ID')
        desired=1 if f.get('archive')=='1' else 0
        reason=f.get('reason','').strip()
        if not reason:raise ValueError('A reason is required for archiving or restoring cases')
        with db() as c:
            case=c.execute('SELECT case_no,archived FROM cases WHERE id=?',(id,)).fetchone()
            if not case:raise ValueError('Case not found')
            if case['archived']==desired:raise ValueError('Case already has requested archive state')
            if desired and c.execute("SELECT 1 FROM file_movements WHERE case_id=? AND status='Pending Receipt'",(id,)).fetchone():raise ValueError('Cannot archive a file while dispatch is pending')
            c.execute('UPDATE cases SET archived=?,archive_note=?,updated_by=?,updated_at=? WHERE id=?',(desired,reason[:1000],self.user['id'],now(),id))
            audit(c,self.user['id'],'ARCHIVE' if desired else 'RESTORE','case',id,case['case_no']+': '+reason)
        self.redirect('/case/'+str(id))

    def search_case_options(self,q):
        """Authorized minimal metadata only; names and IDs never include CNIC/address."""
        term=q.get('q','').strip()[:90]
        if len(term)<2:return self.write('[]',typ='application/json; charset=utf-8')
        scope=q.get('scope','verified')
        if scope not in ('all','verified'):scope='verified'
        # Literal LIKE search: percent/underscore should not be wildcard queries.
        escaped=term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        needle='%'+escaped+'%';prefix=escaped+'%'
        condition=' AND case_verified=1 AND source_issue=\'\'' if scope=='verified' else ''
        query=('SELECT id,case_no,source_row,source_case_no,name,parentage,case_verified FROM cases WHERE archived=0'+condition+
               " AND (case_no LIKE ? ESCAPE '\\' OR source_case_no LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' OR parentage LIKE ? ESCAPE '\\')"+
               " ORDER BY CASE WHEN case_no=? THEN 0 WHEN case_no LIKE ? ESCAPE '\\' THEN 1 WHEN name LIKE ? ESCAPE '\\' THEN 2 ELSE 3 END,case_no LIMIT 22")
        with db() as c:
            rows=c.execute(query,(needle,needle,needle,needle,normalized_case_no(term),prefix,prefix)).fetchall()
        payload=[{'case_id':r['id'],'case_no':display_case_no(r),'name':r['name'],
                  'parentage':r['parentage'],'source_row':r['source_row'],
                  'verified':bool(r['case_verified'])} for r in rows]
        return self.write(json.dumps(payload,ensure_ascii=False),typ='application/json; charset=utf-8')

    def register_export(self,path,q):
        import register_exports
        import master_import
        with db() as c:
            source=master_import.current_register(c)
            if source:
                columns,values=source  # Original workbook column order and nonlinked source rows retained.
            else:
                rows=[dict(r) for r in c.execute('SELECT * FROM cases '+('' if q.get('include_archived')=='1' and self.allowed('cases.archive') else 'WHERE archived=0 ')+'ORDER BY id ASC').fetchall()]
                columns,values=register_exports.register_data(rows)
            audit(c,self.user['id'],'EXPORT','complete_register',len(values),path)
        
        if path.endswith('.csv'):
            data=register_exports.csv_bytes(columns,values);mime='text/csv; charset=utf-8';suffix='csv'
        elif path.endswith('.xlsx'):
            data=register_exports.xlsx_bytes(columns,values);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';suffix='xlsx'
        else:
            try:
                data=register_exports.pdf_bytes(columns,values);mime='application/pdf';suffix='pdf'
            except ValueError as exc:
                if not any(word in str(exc) for word in ('non-Latin','too long to fit')):raise
                # A3 print view is Unicode-safe. Never silently replace source names with lossy question marks.
                data=register_exports.print_html(columns,values)
                return self.write(data,typ='text/html; charset=utf-8')
        return self.write(data,typ=mime,extra={'Content-Disposition':f'attachment; filename="CoIoED_Complete_Case_Register_{date.today().isoformat()}.{suffix}"'})

    def original_workbook_export(self):
        # The original workbook is archived ONLY on the authorized offline host.
        # This route should not be used on plaintext LAN with sensitive case data.
        import master_import
        if self.client_address[0] not in ('127.0.0.1','::1'):
            return self.abort(403,'Original workbook download is allowed from the offline host only')
        path=master_import.source_archive_path()
        if not path.exists():return self.abort(404,'Original source workbook has not been imported on this installation')
        raw=path.read_bytes()
        with db() as c:
            expected=c.execute('SELECT source_sha256 FROM master_source_meta WHERE id=1').fetchone()
            if not expected:return self.abort(404,'Source import metadata missing')
            if hashlib.sha256(raw).hexdigest()!=expected['source_sha256']:
                return self.abort(409,'Original workbook failed SHA-256 verification')
            audit(c,self.user['id'],'EXPORT','original_source_workbook',0,'Full five-sheet workbook, exact original bytes')
        return self.write(raw,typ='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',extra={
          'Content-Disposition':'attachment; filename="CoIoED_Verified_Original_Source.xlsx"'})

    def source_review(self,q):
        # Searchable ledger for every original entry including incomplete/duplicate case numbers.
        # Personal data is available only through authenticated, per-employee case-view permission.
        import master_import
        with db() as c:
            counts=master_import.source_overview(c)
            if not counts:return self.write(self.wrap('Source register',
                '<h1>Source Register</h1><div class="panel">No comprehensive workbook imported yet. Use IMPORT_MASTER_OFFLINE.bat on the offline host.</div>'))
            sheet=int(q.get('sheet','0')) if str(q.get('sheet','0')).isdigit() else 0
            sheet=max(0,min(sheet,max(r['sheet_index'] for r in counts)))
            page=max(1,min(100000,int(q.get('page','1')) if str(q.get('page','1')).isdigit() else 1))
            review_only=q.get('review','')=='1'
            term=q.get('q','').strip()[:70]
            params=[sheet]
            sql='FROM master_source_rows WHERE sheet_index=?'
            if review_only:sql+=' AND linked_case_id IS NULL'
            if term:sql+=' AND raw_json LIKE ?';params.append('%'+term+'%')
            count=c.execute('SELECT COUNT(*) '+sql,params).fetchone()[0]
            entries=c.execute('SELECT * '+sql+' ORDER BY row_number LIMIT 35 OFFSET ?',params+[(page-1)*35]).fetchall()
            headrow=c.execute('SELECT master_header FROM master_source_meta WHERE id=1').fetchone()
            head=json.loads(headrow[0]) if headrow else []
            audit(c,self.user['id'],'VIEW','master_source_sheet',sheet,'page='+str(page))
        opts=''.join(f'<option value="{r["sheet_index"]}" {"selected" if sheet==r["sheet_index"] else ""}>{e(r["sheet_name"])} ({r["source_rows"]} rows)</option>' for r in counts)
        selector=f'<form class="panel bar" method="get"><label>Worksheet <select name="sheet">{opts}</select></label><label>Search source row values <input name="q" value="{e(term)}"></label><label><input type="checkbox" name="review" value="1" style="width:auto" {"checked" if review_only else ""}> Unlinked rows only</label><button>Search</button></form>'
        entries_html=[]
        for r in entries:
            values=json.loads(r['raw_json']);fields=[(e((head[i] if sheet==0 and i<len(head) else 'Original column '+str(i+1))),e(v)) for i,v in enumerate(values) if str(v).strip()]
            source_no=values[1] if sheet==0 and len(values)>1 else ''
            link=f'<a href="/case/{r["linked_case_id"]}">Open linked case</a>' if r['linked_case_id'] else '<span class="pill">Needs reconciliation</span>'
            entries_html.append(f'<div class="panel"><h3>Original worksheet row {r["row_number"]} · {e(source_no)}</h3><p>{link} {e(r["issue"] or "")}</p>{table(["Original source field","Exact imported text"],fields)}</div>')
        more=f'<p>{count} matching source rows; page {page}. <a class="btn secondary" href="?sheet={sheet}&page={page+1}&review={int(review_only)}&q={quote(term)}">Next page</a></p>' if page*35<count else f'<p>{count} matching source rows; page {page}</p>'
        summary=''.join(f'<li>{e(r["sheet_name"])}: {r["source_rows"]} stored source rows</li>' for r in counts)
        content=f'<h1>Complete Original Source Ledger</h1><p>All nonempty rows from all five sheets retained. Unmatched/duplicate case IDs remain available here without inventing or merging case files.</p><ul>{summary}</ul>{selector}{more}{"".join(entries_html)}'
        return self.write(self.wrap('Original Source Register',content))

    def movements(self,q):
        case_no=normalized_case_no(q.get('case_no',''))
        import procurement
        start,end=procurement.range_params(q)
        sql=('SELECT m.*,c.case_no,s.username sender,r.username receiver FROM file_movements m '
             'JOIN cases c ON m.case_id=c.id LEFT JOIN users s ON s.id=m.sent_by '
             'LEFT JOIN users r ON r.id=m.received_by WHERE 1=1')
        args=[]
        if case_no:sql+=' AND c.case_no=?';args.append(case_no)
        if start:sql+=' AND substr(m.sent_at,1,10)>=?';args.append(start)
        if end:sql+=' AND substr(m.sent_at,1,10)<=?';args.append(end)
        with db() as c:
            rows=c.execute(sql+' ORDER BY m.id DESC LIMIT 500',args).fetchall()
            pending=c.execute("SELECT count(*) FROM file_movements WHERE status='Pending Receipt'").fetchone()[0]
        dispatch=''
        if self.allowed('movement.dispatch'):
            dispatch=f'''<div class="panel"><h2>Dispatch physical file</h2><p class="muted">The source branch is read from the case. Custody changes only when the receiving office records receipt.</p><form method="post" action="/movements/dispatch">{self.csrf()}<div class="two">{lookup_field('case_no','Search case ID or missing person',case_no,required=True)}{field('to_branch','Destination branch',options=['Registration Branch','Inquiry Branch','Record Room','Registrar Office','Administration','Family Support Package Cell'])}{field('purpose','Movement reason / purpose',typ='textarea',required=True)}{field('expected_return','Due date (optional)',typ='date')}</div><button>Dispatch file</button></form></div>'''
        data=[]
        for r in rows:
            action=''
            if r['status']=='Pending Receipt' and self.allowed('movement.receive'):
                action=f'''<form method="post" action="/movements/decision">{self.csrf()}<input type="hidden" name="id" value="{r['id']}">{field('decision_note','Receipt note (optional)')}<button name="decision" value="Received">Receive file</button> <button name="decision" class="secondary" value="Rejected">Reject</button></form>'''
            data.append((e(r['case_no']),e(r['from_branch']),e(r['to_branch']),e(r['purpose']),e(r['expected_return']),e(r['status']),e(r['sender']),e(r['sent_at']),e(r['receiver'] or ''),action))
        filters=f'<form method="get" class="bar">{lookup_field("case_no","Filter by Case ID / missing person",case_no)}{field("from","Dispatched from",start,typ="date")}{field("to","Dispatched through",end,typ="date")}<button>Filter movement history</button><a class="btn secondary" href="/movements">Clear</a></form>'
        return self.write(self.wrap('File Movement',f'<h1>Physical File Movement</h1><p class="sub">Branch-to-branch dispatch, receipt, rejection and chronological custody trail. Pending receipts: {pending}.</p>{dispatch}<div class="panel">{filters}{table(["Case ID","From Branch","To Branch","Purpose","Due","Status","Dispatched By","Dispatched At","Received By","Decision"],data)}</div>'))

    def dispatch_file(self,f):
        case_no=normalized_case_no(f.get('case_no',''));dest=f.get('to_branch','').strip();purpose=f.get('purpose','').strip()
        allowed={'Registration Branch','Inquiry Branch','Record Room','Registrar Office','Administration','Family Support Package Cell'}
        if not valid_case_no(case_no) or dest not in allowed or not purpose:raise ValueError('Valid case ID, destination and reason are mandatory')
        if f.get('expected_return') and not valid_iso_date(f['expected_return']):raise ValueError('Due date invalid')
        with db() as c:
            r=c.execute('SELECT id,file_branch FROM cases WHERE case_no=?',(case_no,)).fetchone()
            if not r:raise ValueError('Case file not found in master register')
            if r['file_branch']==dest:raise ValueError('File already recorded at destination branch')
            if c.execute("SELECT 1 FROM file_movements WHERE case_id=? AND status='Pending Receipt'",(r['id'],)).fetchone():raise ValueError('Previous transfer pending receipt')
            cur=c.execute('INSERT INTO file_movements(case_id,from_branch,to_branch,purpose,expected_return,status,sent_by,sent_at) VALUES(?,?,?,?,?,?,?,?)',(r['id'],r['file_branch'],dest,purpose[:1000],f.get('expected_return',''),'Pending Receipt',self.user['id'],now()))
            audit(c,self.user['id'],'DISPATCH','file_movement',cur.lastrowid,case_no+': '+r['file_branch']+' to '+dest)
        self.redirect('/movements?case_no='+quote(case_no))

    def decide_movement(self,f):
        try:id=int(f.get('id','0'))
        except ValueError:raise ValueError('Invalid movement number')
        decision=f.get('decision','')
        if decision not in ('Received','Rejected'):raise ValueError('Invalid movement decision')
        with db() as c:
            m=c.execute('SELECT * FROM file_movements WHERE id=?',(id,)).fetchone()
            if not m or m['status']!='Pending Receipt':raise ValueError('Movement not pending')
            c.execute('UPDATE file_movements SET status=?,received_by=?,received_at=?,decision_note=? WHERE id=? AND status=?',(decision,self.user['id'],now(),f.get('decision_note','')[:500],id,'Pending Receipt'))
            if decision=='Received':
                result=c.execute('UPDATE cases SET file_branch=?,updated_at=?,updated_by=? WHERE id=? AND file_branch=?',(m['to_branch'],now(),self.user['id'],m['case_id'],m['from_branch']))
                if result.rowcount!=1:raise ValueError('Source branch changed during transfer; manual reconciliation required')
            audit(c,self.user['id'],'MOVEMENT_'+decision.upper(),'file_movement',id,m['from_branch']+' to '+m['to_branch'])
        self.redirect('/movements')

    def cases(self,q):
        text=q.get('q','').strip();stat=q.get('status','');region=q.get('region','');branch=q.get('branch','')
        date_field=q.get('date_field','registered_date')
        if date_field not in ('registered_date','missing_date','created_at','updated_at'):
            raise ValueError('Unknown case date filter.')
        import procurement
        start,end=procurement.range_params(q)
        sql="SELECT cases.*, (SELECT MAX(cp.proceeding_date) FROM case_proceedings cp WHERE cp.case_id=cases.id AND cp.verified=1 AND NOT EXISTS(SELECT 1 FROM case_proceedings newer WHERE newer.supersedes_id=cp.id)) as last_proceeding_date FROM cases WHERE 1=1";args=[]
        if q.get('show_archived')!='1':sql+=' AND archived=0'
        if text:sql+=' AND (case_no LIKE ? OR source_case_no LIKE ? OR name LIKE ? OR parentage LIKE ? OR district LIKE ? OR complainant_name LIKE ? OR file_branch LIKE ?)';args.extend(['%'+text+'%']*7)
        if stat:sql+=' AND status=?';args.append(stat)
        if region:sql+=' AND region=?';args.append(region)
        if branch:sql+=' AND file_branch=?';args.append(branch)
        # Missing dates can be DD/MM/YYYY, YYYY, MM/YYYY or Unknown in the
        # original workbook. Convert only recognized full dates; exclude
        # unparseable/partial dates from exact date-range results.
        date_sql=("CASE WHEN missing_date GLOB '????-??-??' THEN missing_date "
                  "WHEN missing_date GLOB '??/??/????' THEN "
                  "substr(missing_date,7,4)||'-'||substr(missing_date,4,2)||'-'||substr(missing_date,1,2) "
                  "ELSE NULL END") if date_field=='missing_date' else f'substr({date_field},1,10)'
        if start:sql+=f' AND ({date_sql})>=?';args.append(start)
        if end:sql+=f' AND ({date_sql})<=?';args.append(end)
        try:page=max(1,int(q.get('page','1')))
        except (TypeError,ValueError):page=1
        per_page=100
        with db() as c:
            total=c.execute('SELECT COUNT(*) '+sql[sql.index('FROM cases WHERE'):],args).fetchone()[0]
            total_pages=max(1,(total+per_page-1)//per_page)
            page=min(page,total_pages)
            rows=c.execute(sql+' ORDER BY COALESCE(source_row,999999999),id LIMIT ? OFFSET ?',args+[per_page,(page-1)*per_page]).fetchall()
            regs=[r[0] for r in c.execute('SELECT DISTINCT region FROM cases ORDER BY region')]
            branches=[r[0] for r in c.execute('SELECT DISTINCT file_branch FROM cases ORDER BY file_branch')]
        filters=f'<form class="bar noprint" method="get">{lookup_field("q","Search case ID or missing person",text,mode="all",action="navigate")}{field("status","Case status",stat,options=["","Under Investigation","Disposed","Traced","Transferred","Other"])}{field("region","Office",region,options=[""]+regs)}{field("branch","File currently at",branch,options=[""]+branches)}{field('date_field','Date type',date_field,options=['registered_date','missing_date','created_at','updated_at'])}{field('from','From date',start,typ='date')}{field('to','To date',end,typ='date')}<label><input type="checkbox" name="show_archived" value="1" style="width:auto" {'checked' if q.get('show_archived')=='1' else ''}> Include archived</label><button>Filter</button></form>'
        controls='<a class="btn" href="/case/new">+ New case</a>' if self.allowed('cases.edit') else ''
        if self.allowed('cases.export'):
            exports='<details class="actions-menu"><summary>Export register</summary><div class="menu-items"><a href="/cases/export.xlsx">Export complete Excel</a><a href="/cases/export.pdf">Export complete PDF</a><a href="/cases/export.csv">Export complete CSV</a><a href="/cases/original.xlsx">Original five-sheet workbook (unaltered)</a></div></details>'
            controls+=' '+exports
        controls+=' <a class="btn secondary" href="/cases/source-review">Original source ledger</a>'
        if self.allowed('cases.edit'):controls+=' <a class="btn secondary" href="/cases/template.csv">CSV template</a>'
        upload=f'<div class="panel"><h2>Bulk import of verified case register (CSV)</h2><p class="muted small">UTF-8 CSV only. New case numbers are inserted; existing numbers are skipped without overwriting.</p><form method="post" enctype="multipart/form-data" action="/cases/import">{self.csrf()}<input type="file" name="file" accept=".csv" required><button style="margin-top:10px">Import new cases</button></form></div>' if self.allowed('cases.edit') else ''
        proceedings_upload=''
        if self.allowed('proceedings.import'):
            proceedings_upload=f'<div class="panel"><h2>Import previous proceedings (CSV)</h2><p class="muted small">Bulk import verified historical data from the physical case files. Imported events start UNVERIFIED until manually checked. The master workbook alone does not contain this chronology.</p><p><a href="/proceedings/template.csv">Download proceedings import CSV template</a></p><form method="post" action="/proceedings/import" enctype="multipart/form-data">{self.csrf()}<input name="file" type="file" accept=".csv" required><button>Import proceedings CSV</button></form></div>'
        rows2=[(f'<a href="/case/{r["id"]}"><strong>{e(display_case_no(r))}</strong></a>',f'<a href="/case/{r["id"]}">{e(r["name"])}</a>',e(r['missing_date']),e(r['last_proceeding_date'] or 'Not recorded'),e(r['district']),e(r['file_branch']),f'<span class="pill {"gray" if r["status"]=="Disposed" else ""}">{e(r["status"])}</span>' + (f'<small class="muted">ID to verify · source row {r["source_row"]}</small>' if r['source_issue'] else '')) for r in rows]
        heading='<div class="page-top"><div><div class="page-overline">CASE MANAGEMENT / MASTER REGISTER</div><h1>Comprehensive case register</h1><p class="sub">Find and manage case files, review last proceedings, and track physical custody.</p></div><div class="actions">'+controls+'</div></div>'
        date_help='<p class="muted small">Date ranges are inclusive. Partial or unknown missing dates cannot be included in an exact day-range search.</p>'
        search='<div class="register-filter-panel"><h2>Find a case file</h2>'+filters+date_help+'</div>'
        paging_query={k:v for k,v in q.items() if k!='page'}
        make_page=lambda n:'/cases?'+urlencode(dict(paging_query,page=n))
        nav='<div class="bar"><span class="muted small">Showing '+str((page-1)*per_page+1 if total else 0)+'–'+str(min(total,page*per_page))+' of '+str(total)+' matching records · Page '+str(page)+' of '+str(total_pages)+'</span><span class="actions">'
        if page>1:nav+='<a class="btn secondary" href="'+e(make_page(page-1))+'">← Previous</a>'
        if page<total_pages:nav+='<a class="btn secondary" href="'+e(make_page(page+1))+'">Next →</a>'
        nav+='</span></div>'
        content=heading+'<div class="panel register-panel"><div class="panel-title"><div><div class="page-overline">SEARCH & FILTER</div><h2>Case records</h2></div><small>'+str(total)+' matching case files</small></div>'+search+nav+table(['CoIoED No.','Missing person','Missing date','Last Verified Proceeding','District','Physical file location','Status'],rows2)+nav+'</div>'
        if upload:content+='<details class="panel"><summary style="font-weight:750;cursor:pointer">Bulk import of verified case records (CSV)</summary>'+upload+'</details>'
        if proceedings_upload:content+='<details class="panel"><summary style="font-weight:750;cursor:pointer">Import past proceedings (CSV)</summary>'+proceedings_upload+'</details>'
        return self.write(self.wrap('Case Register',content))
    def case_form(self,id):
        with db() as c:r=c.execute('SELECT * FROM cases WHERE id=?',(id,)).fetchone() if id else None
        if id and not r:return self.abort(404,'Case not found')
        val=lambda n: (r['source_case_no'] if n=='case_no' and r['source_row'] is not None else r[n]) if r else ''
        status_options=['Under Investigation','Disposed','Traced','Transferred','Other']
        if r and r['status'] and r['status'] not in status_options:status_options.insert(0,r['status'])
        fields=field('case_no','CoIoED case number / original clubbed ID (editable, no suffix required for source records)',val('case_no'),required=not (r and r['source_row'] is not None))+field('name','Missing Person Name (from original source, if supplied)',val('name'),required=not (r and r['source_row'] is not None))+field('parentage','Parentage',val('parentage'))+field('cnic','CNIC / Identifier',val('cnic'))+field('cnic_owner','CNIC belongs to',val('cnic_owner') or 'Unknown',options=['Unknown','Missing person','Relative / complainant'])+field('cnic_relation','If relative, relationship to missing person (e.g. Brother)',val('cnic_relation'))+field('case_verified','Case source and notice details verified?',str(val('case_verified')) if id else '0',options=['0','1'])+field('complainant_name','Complainant name (verified)',val('complainant_name'))+field('missing_date','Missing date (YYYY-MM-DD, YYYY, MM/YYYY or DD/MM/YYYY)',val('missing_date'))+field('registered_date','Date Registered',val('registered_date'),typ='date')+field('address','Complainant / Contact Address',val('address'),typ='textarea')+field('district','District',val('district'))+field('region','Office / Region',val('region') or 'HO Islamabad')+field('status','Status',val('status') or 'Under Investigation',options=status_options)+field('notes','Administrative Notes',val('notes'),typ='textarea')
        body=f'<h1>{"Edit" if id else "New"} Case</h1><div class="panel"><form method="post" action="/cases/save">{self.csrf()}<input type="hidden" name="id" value="{id or ""}"><div class="two">{fields}</div><button>Save case</button> <a class="btn secondary" href="/cases">Cancel</a></form></div>'
        return self.write(self.wrap('Case form',body))
    def save_case(self,f):
        raw_case_no=f.get('case_no','').strip();name=f.get('name','').strip();id=f.get('id','').strip()
        with db() as c:
            existing=c.execute('SELECT * FROM cases WHERE id=?',(id,)).fetchone() if id else None
        sourced=existing is not None and existing['source_row'] is not None
        original_display=existing['source_case_no'] if sourced else None
        # Source records are permitted to retain missing suffixes, clubbed IDs, duplicates, and blank IDs.
        # An edited ID may not collide with another internal case identifier.
        if sourced:
            if raw_case_no==original_display:
                case_no=existing['case_no']
            elif raw_case_no:
                case_no=normalized_case_no(raw_case_no) if valid_case_no(normalized_case_no(raw_case_no)) else raw_case_no
            else:
                case_no=existing['case_no']
        else:
            case_no=normalized_case_no(raw_case_no)
            if not valid_case_no(case_no):raise ValueError('New case records require an official ID with a valid K/P/B/S/I/A/F suffix; imported source rows can be corrected later.')
        if not name and not sourced:raise ValueError('Missing-person name required for a new case.')
        if not valid_missing_date(f.get('missing_date','')) and (not sourced or f.get('missing_date','') != existing['missing_date']):raise ValueError('A changed missing date must be a verified full or partial date; original imported text may remain unchanged.')
        if not valid_iso_date(f.get('registered_date','')):raise ValueError('Registration date must be YYYY-MM-DD.')
        keys=['case_no','name','parentage','cnic','missing_date','registered_date','address','district','region','status','notes','cnic_owner','cnic_relation','complainant_name','case_verified']
        vals={k:f.get(k,'').strip()[:4000] for k in keys};vals['case_no']=case_no;vals['name']=name;vals['cnic_owner']=vals['cnic_owner'] or 'Unknown'
        if existing and 'cnic_relation' not in f:vals['cnic_relation']=existing['cnic_relation'] or ''
        if vals['cnic_owner'] not in ('Missing person','Relative / complainant','Unknown'):raise ValueError('Invalid CNIC ownership')
        vals['cnic_relation']=vals['cnic_relation'][:120]
        vals['case_verified']=1 if str(vals['case_verified'])=='1' else 0
        with db() as c:
            if id:
                old=c.execute('SELECT status,source_row,source_case_no,source_original_case_no FROM cases WHERE id=?',(id,)).fetchone()
                if not old:raise ValueError('Case not found')
                c.execute('UPDATE cases SET '+','.join(k+'=?' for k in keys)+',updated_at=?,updated_by=? WHERE id=?', [vals[k] for k in keys]+[now(),self.user['id'],id])
                if sourced:
                    c.execute('UPDATE cases SET source_case_no=?,source_issue=? WHERE id=?',(raw_case_no, '' if valid_hearing_case_no(normalized_case_no(raw_case_no)) else 'Case number requires verification (original source entry retained)',id))
                    # Source metadata never changes; subsequent exports overlay only deliberate correction.
                    if old['source_case_no']!=raw_case_no:audit(c,self.user['id'],'CORRECT_CASE_ID','case',id,f'Original source number kept in source ledger; updated to {raw_case_no}')
                if old['status']!=vals['status']:c.execute('INSERT INTO case_status_events(case_id,old_status,new_status,changed_at,user_id) VALUES(?,?,?,?,?)',(id,old['status'],vals['status'],now(),self.user['id']))
                audit(c,self.user['id'],'UPDATE','case',id,'fields updated')
            else:
                cur=c.execute('INSERT INTO cases('+','.join(keys)+',created_at,updated_at,updated_by) VALUES('+','.join('?' for _ in range(len(keys)+3))+')',[vals[k] for k in keys]+[now(),now(),self.user['id']]);id=cur.lastrowid
                c.execute('INSERT INTO case_status_events(case_id,old_status,new_status,changed_at,user_id) VALUES(?,?,?,?,?)',(id,'',vals['status'],now(),self.user['id']))
                audit(c,self.user['id'],'CREATE','case',id,case_no)
        self.redirect('/case/'+str(id))
    def case_detail(self,id):
        with db() as c:
            r=c.execute('SELECT * FROM cases WHERE id=?',(id,)).fetchone()
            scans=c.execute("SELECT * FROM documents WHERE case_id=? AND section='case' ORDER BY id DESC",(id,)).fetchall()
            meetings=c.execute('SELECT * FROM hearings WHERE case_id=? ORDER BY hearing_date DESC',(id,)).fetchall()
        if not r:return self.abort(404,'Case not found')
        with db() as c:audit(c,self.user['id'],'VIEW','case',id)
        details=table(['Field','Information'],[(e(k),fmt(r[v])) for k,v in [('Case Number','source_case_no' if r['source_row'] is not None else 'case_no'),('Missing Person','name'),('Parentage','parentage'),('CNIC','cnic'),('Missing Date','missing_date'),('Date Registered','registered_date'),('Complainant Name','complainant_name'),('Contact Address','address'),('CNIC Owner','cnic_owner'),('Relative CNIC Relationship','cnic_relation'),('Verified for Notices','case_verified'),('District','district'),('Office','region'),('Status','status'),('Notes','notes')]])
        # Display the exact original source fields, including columns that do not map
        # to the normalized case profile (referring agency, production-order text, remarks).
        original_source_html=''
        if r['source_row']:
            with db() as c:
                raw=c.execute('SELECT raw_json,issue FROM master_source_rows WHERE sheet_index=0 AND row_number=?',(r['source_row'],)).fetchone()
                hdr=c.execute('SELECT master_header FROM master_source_meta WHERE id=1').fetchone()
            if raw and hdr:
                vals=json.loads(raw['raw_json']);headers=json.loads(hdr['master_header'])
                exact=[(e(headers[i] if i<len(headers) else 'Original column '+str(i+1)),e(v)) for i,v in enumerate(vals)]
                original_source_html='<div class="panel"><h2>All original Comprehensive List details</h2><p class="muted small">Exact source field text; separate from any later edits to the electronic case profile.</p>'+table(['Original workbook column','Unmodified source value'],exact)+f'<p class="small muted">Original Excel row: {r["source_row"]}. '+e(raw['issue'] or '')+'</p></div>'
        upload=self.doc_form('case',id) if self.allowed('documents.upload') and self.allowed('cases.edit') else ''
        docs=table(['File','Category','Size','Date'],[(f'<a href="/documents/get?id={x["id"]}">{e(x["original_name"])}</a>',e(x['category']),f'{x["size"]/1024:.0f} KB',e(x['created_at'][:10])) for x in scans]) if self.allowed('documents.view') else '<p class="muted">Document list restricted to authorized employees.</p>'
        hearings=table(['Date','Time','Attendees','Instructions'],[(e(x['hearing_date']),e(x['hearing_time']),e(x['officers']).replace('\n','<br>'),e(x['instructions'])) for x in meetings])
        import hearing_workflow_v121
        with db() as c:scheduled_issued_html=hearing_workflow_v121.case_panel(c,id,self.allowed('hearing.view'))
        import proceedings
        proceed_html=''
        if self.allowed('proceedings.view'):
            with db() as c:
                records=proceedings.history(c,id);last=proceedings.latest(c,id)
                # COIOED_V132_COMBINED_CASE_CHRONOLOGY read-only approved hearing + actual proceedings chronology
                import case_chronology_v132
                chronology_html=case_chronology_v132.render(c,id,records,include_schedule=self.allowed('hearing.view'))
                docs=c.execute('SELECT id,original_name FROM documents WHERE case_id=? ORDER BY id DESC LIMIT 150',(id,)).fetchall()
            entries=[]
            for pr in records:
                link=(f'<a href="/documents/get?id={pr["document_id"]}">{e(pr["document_name"] or "Attached source")}</a>' if self.allowed('documents.view') else 'Restricted') if pr['document_id'] else '-'
                entries.append((e(pr['proceeding_date']),e(pr['proceeding_type']),e(pr['summary']),e(pr['directions']),e(pr['next_date']),e('Yes' if pr['verified'] else 'Pending'),link))
            proceed_html=chronology_html+f'<div class="panel"><h2>Last Verified Proceeding Date</h2><p style="font-size:22px"><strong>{e(last or "No verified proceeding recorded")}</strong></p><p class="muted small">Computed only from verified actual proceedings, not hearing notices or status values.</p></div>'
            proceed_html+='<div class="panel"><h2>All Past Proceedings ('+str(len(records))+')</h2>'+table(['Date','Type','Proceedings summary','Directions','Next date','Verified','Order / scan'],entries)+f'<p><a href="/proceedings/history.csv?case_id={id}">Export proceeding history to CSV</a></p></div>'
            if self.allowed('proceedings.add'):
                import historical_proceedings_v131
                proceed_html+=historical_proceedings_v131.form_html(self,id,docs)
                proceed_html+=historical_proceedings_v131.verification_html(self,id,records)
                options='<option value="">No attachment linked</option>'+''.join(f'<option value="{doc["id"]}">{e(doc["original_name"])}</option>' for doc in docs)
                proceed_html+=f'''<div class="panel"><h2>Add Previous / Current Proceeding</h2><p class="muted">Record only factual proceedings based on an order or physical record. Entries are retained historically.</p><form method="post" action="/proceedings/save">{self.csrf()}<input type="hidden" name="case_id" value="{id}"><div class="two">{field('proceeding_date','Actual proceeding date',typ='date',required=True)}{field('proceeding_type','Proceeding type',options=proceedings.KINDS)}{field('summary','Proceedings and order details',typ='textarea',required=True)}{field('directions','Directions / further action',typ='textarea')}{field('next_date','Next hearing / action date',typ='date')}{field('source_reference','Order / page number')}</div><div class="field"><label>Link existing scanned source document</label><select name="document_id">{options}</select></div><label><input type="checkbox" name="verified" value="yes" style="width:auto"> Verified against official order or physical case file</label><br><button>Save proceeding</button></form></div>'''
        content=f'<div class="case-hero"><div class="page-overline">ELECTRONIC CASE FILE / {e(display_case_no(r))}</div><h1><span class="case-id">{e(display_case_no(r))}</span> &nbsp;{e(r["name"])}</h1><p class="sub">{e(r["parentage"])} &nbsp;·&nbsp; {e(r["district"] or "District not recorded")} &nbsp;·&nbsp; <span class="pill">{e(r["status"])}</span></p></div><div class="section-tabs"><a href="#case-profile">Case details</a><a href="#case-documents">Documents</a><a href="#case-hearings">Hearing history</a><a href="#case-proceedings">Proceedings</a><a href="#case-original">Original register</a></div><div class="actions">{f"<a class=\"btn\" href=\"/case/edit/{id}\">Edit record</a>" if self.allowed('cases.edit') else ""}<a class="btn secondary" href="/cases">Back to register</a><a class="btn secondary" href="/movements?case_no={e(r['case_no'])}">Movement history</a><a class="btn secondary" href="/production?q={e(r['case_no'])}">Production orders</a><a class="btn secondary" href="/rotation?q={e(r['case_no'])}">Hearing rotation</a></div><div id="case-profile" class="panel"><h2>Case Profile</h2>{details}</div><div id="case-documents" class="panel"><h2>Scanned Documents</h2>{docs}{upload}</div><div id="case-hearings" class="panel"><h2>Hearing History</h2>{hearings}</div>{scheduled_issued_html}'
        if self.allowed('cases.archive'):
            content+=f'''<div id="case-retention" class="panel"><h2>Record retention / active register</h2><p>Archiving removes this case from the current export, without deleting source records, scans, hearing snapshots or its audit history. Active cases can be restored.</p><form method="post" action="/cases/archive">{self.csrf()}<input type="hidden" name="case_id" value="{id}"><input type="hidden" name="archive" value="{0 if r['archived'] else 1}">{field('reason','Reason for archive / restoration',required=True)}<button>{'Restore to active register' if r['archived'] else 'Archive from current register'}</button></form></div>'''
        content+='<div id="case-original">'+original_source_html+'</div><div id="case-proceedings">'+proceed_html+'</div>'
        return self.write(self.wrap('Case File',content))
    def save_proceeding(self,f):
        import proceedings
        case_id=int(f.get('case_id','0'))
        with db() as c:
            if not c.execute('SELECT 1 FROM cases WHERE id=?',(case_id,)).fetchone():raise ValueError('Case not found')
            proceeding_id=proceedings.add(c,case_id,f,self.user['id'],now())
            audit(c,self.user['id'],'RECORD_PROCEEDING','case',case_id,'Event '+str(proceeding_id)+' dated '+f.get('proceeding_date',''))
        self.redirect('/case/'+str(case_id))
    def proceedings_csv(self,q):
        import proceedings
        case_id=int(q.get('case_id','0'))
        with db() as c:
            case=c.execute('SELECT case_no FROM cases WHERE id=?',(case_id,)).fetchone()
            if not case:raise ValueError('Case not found')
            records=proceedings.history(c,case_id)
            audit(c,self.user['id'],'EXPORT','proceedings',case_id)
        out=io.StringIO();w=csv.writer(out)
        w.writerow(['case_no','proceeding_date','proceeding_type','summary','directions','next_date','source_reference','verified','attached_document'])
        for r in records:w.writerow([case['case_no'],r['proceeding_date'],r['proceeding_type'],r['summary'],r['directions'],r['next_date'],r['source_reference'],r['verified'],r['document_name']])
        return self.write('\ufeff'+out.getvalue(),typ='text/csv; charset=utf-8',extra={'Content-Disposition':'attachment; filename="CoIoED_Proceedings_History.csv"'})
    def import_proceedings(self,files):
        import proceedings
        if not files:raise ValueError('Select historical proceedings CSV')
        key,filename,raw=files[0]
        if not filename.lower().endswith('.csv') or len(raw)>5*1024*1024:raise ValueError('Unsupported proceedings CSV')
        with db() as c:
            count,issues=proceedings.import_csv(c,raw,self.user['id'],now())
            audit(c,self.user['id'],'IMPORT','proceedings',count,'Needs verification '+str(len(issues)))
        return self.write(self.wrap('Proceedings imported',f'<h1>Historical proceedings import</h1><div class="panel"><p>{count} rows imported as UNVERIFIED. {len(issues)} exceptions require reconciliation.</p><a class="btn" href="/cases">Back to Case Register</a></div>'))
    def csv_template(self):
        output=io.StringIO();w=csv.writer(output)
        w.writerow(['case_no','name','parentage','cnic','missing_date','registered_date','address','district','region','status','notes'])
        w.writerow(['0001-K','Sample Person','Sample Parentage','','2020-01-01','2020-02-01','Sample address','Example District','HO Islamabad','Under Investigation','TEST RECORD ONLY'])
        return self.write(output.getvalue(),typ='text/csv; charset=utf-8',extra={'Content-Disposition':'attachment; filename="coioed_import_template.csv"'})
    def import_cases(self,files):
        if not files:raise ValueError('CSV file required')
        key,name,raw=files[0]
        if not name.lower().endswith('.csv') or len(raw)>8*1024*1024:raise ValueError('CSV must be below 8 MB')
        text=raw.decode('utf-8-sig')
        reader=csv.DictReader(io.StringIO(text));headers=set(reader.fieldnames or [])
        required={'case_no','name'}
        if not required.issubset(headers):raise ValueError('CSV requires case_no and name columns; use the template.')
        count=skipped=0;errors=[]
        with db() as c:
            for ix,row in enumerate(reader,start=2):
                if ix>15002:raise ValueError('Maximum 15,000 rows per import.')
                number=normalized_case_no(row.get('case_no') or '');name=(row.get('name') or '').strip()
                if not valid_case_no(number) or not name:errors.append(ix);continue
                if c.execute('SELECT 1 FROM cases WHERE case_no=?',(number,)).fetchone():skipped+=1;continue
                dates=[(row.get(k) or '').strip() for k in ('missing_date','registered_date')]
                if not valid_missing_date(dates[0]) or not valid_iso_date(dates[1]):errors.append(ix);continue
                keys=['case_no','name','parentage','cnic','missing_date','registered_date','address','district','region','status','notes']
                values=[number,name]+[(row.get(k) or '').strip()[:4000] for k in keys[2:]]
                if not values[8]:values[8]='HO Islamabad'
                if not values[9]:values[9]='Under Investigation'
                cur=c.execute('INSERT INTO cases('+','.join(keys)+',created_at,updated_at,updated_by) VALUES('+','.join('?' for _ in range(14))+')',values+[now(),now(),self.user['id']]);count+=1
            audit(c,self.user['id'],'IMPORT','case','',f'imported {count}; skipped {skipped}; invalid {len(errors)}')
        return self.write(self.wrap('Import Result',f'<h1>Import completed</h1><div class="panel"><p>Inserted: <strong>{count}</strong> | Existing skipped: <strong>{skipped}</strong> | Invalid rows: <strong>{len(errors)}</strong></p><p>Invalid CSV line numbers: {e(", ".join(map(str,errors[:30])) or "None")}</p><a href="/cases" class="btn">Return to cases</a></div>'))
    def doc_form(self,section,id):
        return f'''<h2 style="margin-top:20px">Attach scanned file</h2><form method="post" action="/documents/upload" enctype="multipart/form-data">{self.csrf()}<input type="hidden" name="section" value="{e(section)}"><input type="hidden" name="owner_id" value="{id}">{field('category','Document Category',options=['Last Order','JIT','PTF','Application','Evidence','Family Support','Employee Contract','Other'])}<div class="field"><label>PDF or scanned image (up to 15 MB)</label><input type="file" name="file" accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff" required></div><button>Add to electronic file</button></form>'''
    def upload_document(self,f,files):
        if not files:raise ValueError('Select a file to upload.')
        section=f.get('section','');owner=f.get('owner_id','')
        if section not in ('case','fsp','hr'):raise ValueError('Invalid document section')
        if section=='hr' and not self.allowed('hr.manage'):return self.abort()
        if section in ('case','fsp') and not self.allowed('documents.upload'):return self.abort()
        if section=='fsp' and not self.allowed('fsp.edit'):return self.abort()
        fieldname,filename,data=files[0]
        if fieldname!='file' or len(data)>15*1024*1024 or len(data)==0:raise ValueError('File is empty or exceeds 15 MB')
        if '\x00' in filename or '/' in filename or '\\' in filename:raise ValueError('Unsupported file name')
        ext=Path(filename).suffix.lower()
        sig={'.pdf':data.startswith(b'%PDF-'),'.png':data.startswith(b'\x89PNG\r\n\x1a\n'),'.jpg':data.startswith(b'\xff\xd8\xff'),'.jpeg':data.startswith(b'\xff\xd8\xff'),'.tif':data.startswith((b'II*\x00',b'MM\x00*')),'.tiff':data.startswith((b'II*\x00',b'MM\x00*'))}
        if not sig.get(ext,False):raise ValueError('Only supported PDF / image files are permitted')
        ownerid=int(owner)
        with db() as c:
            if section=='hr':exists=c.execute('SELECT 1 FROM staff WHERE id=?',(ownerid,)).fetchone()
            else:exists=c.execute('SELECT 1 FROM cases WHERE id=?',(ownerid,)).fetchone()
            if not exists:raise ValueError('Associated record not found')
            stored=uuid.uuid4().hex+ext
            dest=UPLOAD/stored;dest.write_bytes(data)
            try:
                cur=c.execute('INSERT INTO documents(case_id,employee_id,section,category,original_name,stored_name,sha256,size,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(
                    ownerid if section!='hr' else None,ownerid if section=='hr' else None,section,f.get('category','Other')[:80],filename[:150],stored,hashlib.sha256(data).hexdigest(),len(data),self.user['id'],now()))
                audit(c,self.user['id'],'UPLOAD','document',cur.lastrowid,section)
            except:dest.unlink(missing_ok=True);raise
        self.redirect('/hr/'+owner if section=='hr' else ('/fsp' if section=='fsp' else '/case/'+owner))
    def document_get(self,q):
        id=int(q.get('id','0'))
        with db() as c:r=c.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone()
        if not r:return self.abort(404,'File not found')
        allowed=self.allowed('documents.view') and self.allowed('hr.view' if r['section']=='hr' else ('fsp.view' if r['section']=='fsp' else 'cases.view'))
        if not allowed:return self.abort()
        path=UPLOAD/r['stored_name']
        if not path.is_file():return self.abort(404,'Stored file missing - contact administrator')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=r['sha256']:return self.abort(500,'File checksum does not match the database record')
        with db() as c:audit(c,self.user['id'],'DOWNLOAD','document',id)
        mime=mimetypes.guess_type(r['original_name'])[0] or 'application/octet-stream'
        filename=re.sub('[^A-Za-z0-9_.-]','_',r['original_name'])[:100]
        return self.write(data,typ=mime,extra={'Content-Disposition':f'attachment; filename="{filename}"'})
    def hearings(self,q):
        target=q.get('date',date.today().isoformat())
        if not valid_iso_date(target):raise ValueError('Invalid hearing date')
        with db() as c:
            entries=c.execute('''SELECT h.*,c.case_no,c.name,c.parentage,c.cnic,c.missing_date,c.address
            FROM hearings h JOIN cases c ON c.id=h.case_id WHERE h.hearing_date=? ORDER BY h.id''',(target,)).fetchall()
        addform=''
        if self.allowed('hearing.edit'):
            addform=f'''<div class="panel"><h2>Schedule a case for hearing</h2><p class="muted small">Use an existing CoIoED case number. An entry for the same case and hearing date cannot be duplicated.</p><form method="post" action="/hearings/add">{self.csrf()}<div class="two">{field('case_no','CoIoED Case Number',required=True)}{field('hearing_date','Hearing date',target,typ='date',required=True)}{field('hearing_time','Time','10:00 am onwards')}{field('reference_no','Notice reference number')}{field('officers','Officers concerned / focal persons, one per line',typ='textarea')}{field('instructions','Special instructions from last order',typ='textarea')}</div><button>Add to hearing schedule</button></form></div>'''
        outrows=[(str(i),f'<a href="/case/{r["case_id"]}">{e(r["case_no"])}</a>',e(r['name'])+'<br><span class="muted small">'+e(r['parentage'])+'</span>',e(r['missing_date']),e(r['officers']).replace('\n','<br>'),e(r['instructions'])) for i,r in enumerate(entries,1)]
        body=f'''<h1>Legacy Weekly Hearing Schedule</h1><p class="sub">Matching the Commission schedule fields: CoIoED No., name and parentage, CNIC, missing date, address, numbered focal persons and special instructions.</p><div class="panel"><form method="get" class="bar"><div class="field">{field('date','Schedule date',target,typ='date')}</div><button>View</button></form><div class="actions"><a class="btn" href="/hearings/print?date={quote(target)}" target="_blank">Print A4 landscape / Save PDF</a></div>{table(['#','CoIoED No.','Name','Missing date','Officers','Special instructions'],outrows)}</div>{addform}'''
        return self.write(self.wrap('Hearing Schedule',body))
    def add_hearing(self,f):
        no=normalized_case_no(f.get('case_no',''));d=f.get('hearing_date','')
        if not valid_iso_date(d) or not d:raise ValueError('Enter a valid date')
        with db() as c:
            r=c.execute('SELECT id FROM cases WHERE case_no=?',(no,)).fetchone()
            if not r:raise ValueError('Case not in register; add or import the case first.')
            cur=c.execute('INSERT INTO hearings(case_id,hearing_date,hearing_time,officers,instructions,reference_no,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',(r['id'],d,f.get('hearing_time','10:00 am onwards')[:60],f.get('officers','')[:2000],f.get('instructions','')[:2000],f.get('reference_no','')[:150],self.user['id'],now()))
            audit(c,self.user['id'],'CREATE','hearing',cur.lastrowid,'case '+no+' '+d)
        self.redirect('/hearings?date='+quote(d))
    def hearing_print(self,q):
        d=q.get('date',date.today().isoformat())
        if not valid_iso_date(d):raise ValueError('Invalid date')
        with db() as c:
            entries=c.execute('''SELECT h.*,c.case_no,c.name,c.parentage,c.cnic,c.missing_date,c.address FROM hearings h JOIN cases c ON c.id=h.case_id WHERE h.hearing_date=? ORDER BY h.id''',(d,)).fetchall()
        date_long=date.fromisoformat(d).strftime('%A, %d %B %Y')
        refs={r['reference_no'] for r in entries if r['reference_no']}
        ref=next(iter(refs)) if len(refs)==1 else '__________ / CoIoED / '+str(date.fromisoformat(d).year)
        label='<p class="muted small noprint">This print layout follows the column structure of the 2/4/6 November 2026 office schedule. Use browser Print (Ctrl+P), A4 landscape, and turn off browser headers/footers.</p>'
        rows=[]
        for i,r in enumerate(entries,1):
            officers=[x.strip() for x in (r['officers'] or '').splitlines() if x.strip()]
            # Legacy print view: no artificial eight-person limit or empty numbered slots.
            numbered=('<div class="officers-label">Focal Person of :-</div><div class="officer-grid">'+''.join(
                f'<div class="attendee-name">{j}. {e(name)}</div>' for j,name in enumerate(officers,1))+'</div>') if officers else '' 
            rows.append((str(i),e(r['case_no']),f'<strong>{e(r["name"])}</strong><div>{e(r["parentage"])}</div><small>CNIC: {e(r["cnic"]) or "-"}</small>',e(r['missing_date']),e(r['address']),numbered,e(r['instructions'])))
        head=f'''<div style="text-align:center"><img src="/logo.png" style="width:65px;height:65px;object-fit:contain" alt="Logo"><p style="text-align:left;font-size:11px;margin:0"><strong>No. {e(ref)}</strong> <span style="float:right">Dated: __________</span></p><h2 style="margin:7px 0">COMMISSION OF INQUIRY ON ENFORCED DISAPPEARANCES</h2><h2 style="margin:6px">NOTICE FOR HEARING - {e(date_long.upper())}</h2><p style="margin:4px">10:00 am onwards | Head Office Islamabad</p></div>'''
        css='<style>@page{size:A4 landscape;margin:9mm}body{font-family:Arial,sans-serif}table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:9px}th,td{border:1px solid #333;padding:6px;vertical-align:top;word-break:break-word}th{background:#eaeaea}thead{display:table-header-group}tr{break-inside:avoid}button{margin:12px}</style>'
        widths=['4%','10%','18%','10%','20%','23%','15%']
        tab='<table><colgroup>'+''.join(f'<col style="width:{w}">' for w in widths)+'</colgroup><thead><tr>'+''.join('<th>'+e(k)+'</th>' for k in ['S. #','CoIoED No.','Name / Parentage','Missing Date','Complainant & Address','Officers to Attend','Special Instructions'])+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'
        doc=f'<!doctype html><html><head><meta charset="utf-8"><title>Hearing - {e(d)}</title>{css}</head><body>{label}{head}{tab}<script>/* No network use. Use browser print. */</script></body></html>'
        # Separate print rendering; no automatic browser redirects or external assets.
        return self.write(doc)
    def monthly(self,q):
        month=q.get('month',date.today().strftime('%Y-%m'))
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise ValueError('Month must be YYYY-MM')
        with db() as c:
            total=c.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
            registered=c.execute('SELECT COUNT(*) FROM cases WHERE registered_date LIKE ?', (month+'%',)).fetchone()[0]
            heard=c.execute('SELECT COUNT(DISTINCT case_id) FROM hearings WHERE hearing_date LIKE ?', (month+'%',)).fetchone()[0]
            slots=c.execute('SELECT COUNT(*) FROM hearings WHERE hearing_date LIKE ?', (month+'%',)).fetchone()[0]
            transitions=c.execute('SELECT new_status, COUNT(*) n FROM case_status_events WHERE changed_at LIKE ? GROUP BY new_status',(month+'%',)).fetchall()
            statuses=c.execute('SELECT status,COUNT(*) n FROM cases GROUP BY status ORDER BY status').fetchall()
            regions=c.execute('SELECT region,COUNT(*) n FROM cases GROUP BY region ORDER BY region').fetchall()
        css=''
        body=f'''<h1>Monthly Statistical Report</h1><p class="sub">Period: {e(month)} | Head Office pilot database</p><div class="notice">DRAFT - NOT FOR OFFICIAL SUBMISSION. Figures are computed from records imported into this pilot and require Registrar / Inquiry Branch reconciliation. 'Status now' is not a historical month-end snapshot.</div><div class="panel noprint"><form method="get" class="bar">{field('month','Reporting month',month,typ='month')}<button>Generate</button><span class="muted small">Use Ctrl+P / Print to PDF for the report</span></form></div><div class="grid">{''.join(f'<div class="stat"><b>{n}</b><span>{e(k)}</span></div>' for k,n in [('Total records',total),('Registrations dated in month',registered),('Unique cases heard',heard),('Hearing slots',slots)])}</div><div class="two"><div class="panel"><h2>Current Case Status Breakdown</h2>{table(['Status','Cases'],[(e(r['status']),str(r['n'])) for r in statuses])}</div><div class="panel"><h2>Office / Region</h2>{table(['Office','Cases'],[(e(r['region']),str(r['n'])) for r in regions])}</div></div><div class="panel"><h2>Status changes recorded during month</h2>{table(['New status','Recorded changes'],[(e(r['new_status']),str(r['n'])) for r in transitions])}<p class="small muted">Counts show changes recorded by software in the reporting period, not necessarily cases disposed that month before historical backfill.</p></div>'''
        return self.write(self.wrap('Monthly Report',body))
    def fsp(self,q):
        if not self.allowed('fsp.view'):return self.abort()
        with db() as c:
            cases=c.execute('SELECT f.*,c.case_no,c.name FROM fsp f JOIN cases c ON c.id=f.case_id ORDER BY f.id DESC LIMIT 200').fetchall()
        form=''
        if self.allowed('fsp.edit'):
            form=f'''<div class="panel"><h2>Family Support Package record</h2><form method="post" action="/fsp/save">{self.csrf()}<div class="three">{field('case_no','CoIoED Case Number',required=True)}{field('status','Package status',options=['Under Review','Pending Documents','Approved','Disbursed','Closed'])}{field('notes','Administrative note')}</div><button>Save package status</button></form></div>'''
        rows=[]
        for r in cases:
            with db() as c:files=c.execute("SELECT id,original_name FROM documents WHERE section='fsp' AND case_id=? ORDER BY id DESC",(r['case_id'],)).fetchall()
            scans=' '.join(f'<a href="/documents/get?id={v["id"]}">{e(v["original_name"])}</a>' for v in files) if self.allowed('documents.view') else '[restricted]'
            upload=self.doc_form('fsp',r['case_id']) if self.allowed('fsp.edit') and self.allowed('documents.upload') else ''
            rows.append((f'<a href="/case/{r["case_id"]}">{e(r["case_no"])}</a>',e(r['name']),e(r['status']),e(r['notes']),scans or '-',upload))
        body=f'<h1>Family Support Package</h1><p class="sub">Separate restricted package status and supporting scans, linked to the case register.</p>{form}<div class="panel">{table(["CoIoED No.","Missing person","Status","Notes","Files","Upload"],rows)}</div>'
        return self.write(self.wrap('Family Support',body))
    def save_fsp(self,f):
        no=normalized_case_no(f.get('case_no',''))
        with db() as c:
            case=c.execute('SELECT id FROM cases WHERE case_no=?',(no,)).fetchone()
            if not case:raise ValueError('Case not found in register')
            c.execute('INSERT INTO fsp(case_id,status,notes,updated_at) VALUES(?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET status=excluded.status,notes=excluded.notes,updated_at=excluded.updated_at',(case['id'],f.get('status','Under Review')[:50],f.get('notes','')[:1000],now()))
            audit(c,self.user['id'],'UPSERT','fsp',case['id'],f.get('status',''))
        self.redirect('/fsp')
    def hr(self,q):
        with db() as c:
            people=c.execute('SELECT * FROM staff ORDER BY id DESC').fetchall()
            leave=c.execute('SELECT l.*,s.name FROM leaves l JOIN staff s ON s.id=l.staff_id ORDER BY l.id DESC LIMIT 60').fetchall()
        staffform=f'''<div class="panel"><h2>Register Employee</h2><form action="/hr/staff/save" method="post">{self.csrf()}<div class="three">{field('name','Employee name *',required=True)}{field('designation','Designation')}{field('branch','Branch','Head Office')}{field('joining_date','Joining Date',typ='date')}</div><button>Add employee</button></form></div>'''
        stafftable=table(['Name','Designation','Branch','Status'],[(f'<a href="/hr/{s["id"]}">{e(s["name"])}</a>',e(s['designation']),e(s['branch']),e(s['status'])) for s in people])
        forms=f'''<div class="two"><div class="panel"><h2>Attendance / short leave entry</h2><form method="post" action="/hr/attendance/save">{self.csrf()}{field('staff_id','Employee ID',typ='number',required=True)}{field('date','Date',date.today().isoformat(),typ='date',required=True)}{field('kind','Type',options=['Present','Absent','Late','Half Day','Short Leave','Official Duty'])}{field('notes','Remarks')}<button>Record</button></form></div><div class="panel"><h2>Leave application</h2><form method="post" action="/hr/leave/save">{self.csrf()}{field('staff_id','Employee ID',typ='number',required=True)}{field('kind','Leave type',options=['Casual','Medical','Earned','Unpaid','Short Leave'])}{field('start_date','From',date.today().isoformat(),typ='date',required=True)}{field('end_date','To',date.today().isoformat(),typ='date',required=True)}{field('reason','Reason',typ='textarea')}<button>Record request</button></form></div></div>'''
        lrows=[]
        for r in leave:
            decision=(f'<form method="post" action="/hr/leave/decide">{self.csrf()}<input type="hidden" name="id" value="{r["id"]}"><button name="decision" value="Approved">Approve</button> <button class="secondary" name="decision" value="Rejected">Reject</button></form>') if r['status']=='Pending' else ''
            lrows.append((e(r['name']),e(r['kind']),e(r['start_date'])+' to '+e(r['end_date']),e(r['status']),decision))
        return self.write(self.wrap('HR',f'<h1>HR and Attendance</h1><p class="sub">No payroll or salary module. Restricted by individual employee permissions.</p>{staffform}<div class="panel"><h2>Employees</h2>{stafftable}</div>{forms}<div class="panel"><h2>Leave register</h2>{table(["Employee","Type","Period","Decision","Action"],lrows)}</div>'))
    def save_staff(self,f):
        if not f.get('name','').strip():raise ValueError('Employee name required')
        if not valid_iso_date(f.get('joining_date','')):raise ValueError('Invalid joining date')
        with db() as c:
            r=c.execute('INSERT INTO staff(name,designation,branch,joining_date,created_at) VALUES(?,?,?,?,?)',(f['name'].strip()[:160],f.get('designation','')[:120],f.get('branch','Head Office')[:120],f.get('joining_date',''),now()))
            audit(c,self.user['id'],'CREATE','employee',r.lastrowid)
        self.redirect('/hr/'+str(r.lastrowid))
    def staff_detail(self,id):
        with db() as c:
            s=c.execute('SELECT * FROM staff WHERE id=?',(id,)).fetchone()
            records=c.execute('SELECT * FROM attendance WHERE staff_id=? ORDER BY date DESC LIMIT 60',(id,)).fetchall()
            files=c.execute("SELECT * FROM documents WHERE section='hr' AND employee_id=? ORDER BY id DESC",(id,)).fetchall()
        if not s:return self.abort(404,'Employee not found')
        scans=table(['Contract / file','Category','Uploaded'],[(f'<a href="/documents/get?id={x["id"]}">{e(x["original_name"])}</a>',e(x['category']),e(x['created_at'][:10])) for x in files])
        record=table(['Date','Attendance','Notes'],[(e(x['date']),e(x['kind']),e(x['notes'])) for x in records])
        return self.write(self.wrap('Employee',f'<h1>Employee | {e(s["name"])}</h1><p class="sub">ID {id} | {e(s["designation"])} | {e(s["branch"])}</p><div class="panel"><h2>Employment Contract and Documents</h2>{scans}{self.doc_form('hr',id) if self.allowed('hr.manage') and self.allowed('documents.upload') else ''}</div><div class="panel"><h2>Attendance History</h2>{record}</div>'))
    def save_attendance(self,f):
        id=int(f.get('staff_id','0'));d=f.get('date','');kind=f.get('kind','')
        if not valid_iso_date(d) or not d or kind not in ('Present','Absent','Late','Half Day','Short Leave','Official Duty'):raise ValueError('Invalid attendance record')
        with db() as c:
            if not c.execute('SELECT 1 FROM staff WHERE id=?',(id,)).fetchone():raise ValueError('Employee ID not found')
            c.execute('INSERT INTO attendance(staff_id,date,kind,notes,user_id) VALUES(?,?,?,?,?) ON CONFLICT(staff_id,date) DO UPDATE SET kind=excluded.kind,notes=excluded.notes,user_id=excluded.user_id',(id,d,kind,f.get('notes','')[:500],self.user['id']))
            audit(c,self.user['id'],'UPSERT','attendance',id,d+' '+kind)
        self.redirect('/hr/'+str(id))
    def save_leave(self,f):
        id=int(f.get('staff_id','0'));start=f.get('start_date','');end=f.get('end_date','')
        if not start or not end or not valid_iso_date(start) or not valid_iso_date(end) or start>end:raise ValueError('Invalid leave dates')
        with db() as c:
            if not c.execute('SELECT 1 FROM staff WHERE id=?',(id,)).fetchone():raise ValueError('Employee not found')
            r=c.execute('INSERT INTO leaves(staff_id,kind,start_date,end_date,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?)',(id,f.get('kind','Casual')[:40],start,end,f.get('reason','')[:1000],self.user['id'],now()))
            audit(c,self.user['id'],'CREATE','leave',r.lastrowid)
        self.redirect('/hr')
    def decide_leave(self,f):
        id=int(f.get('id','0'));decision=f.get('decision','')
        if decision not in ('Approved','Rejected'):raise ValueError('Invalid decision')
        with db() as c:
            r=c.execute('UPDATE leaves SET status=?,approved_by=? WHERE id=? AND status="Pending"',(decision,self.user['id'],id))
            if r.rowcount!=1:raise ValueError('Leave not pending')
            audit(c,self.user['id'],'DECIDE','leave',id,decision)
        self.redirect('/hr')
    def users(self):
        # Employee directory includes people without logins. Permissions belong to users, not branches.
        with db() as c:
            employees=c.execute('''SELECT s.*,u.id login_id,u.username,u.active login_active
                FROM staff s LEFT JOIN users u ON u.staff_id=s.id ORDER BY s.name COLLATE NOCASE,s.id''').fetchall()
            users=c.execute('''SELECT u.id,u.username,u.fullname,u.active,s.name employee,s.branch
                FROM users u LEFT JOIN staff s ON s.id=u.staff_id ORDER BY u.id''').fetchall()
            cnt={u['id']:c.execute('SELECT COUNT(*) FROM user_permissions WHERE user_id=?',(u['id'],)).fetchone()[0] for u in users}
        personnel=[]
        for r in employees:
            edit=f'<a class="btn secondary" href="/users/employee/{r["id"]}">Edit Employee</a>'
            access=(f'<a class="btn secondary" href="/users/{r["login_id"]}">Manage Access</a>'
                    if r['login_id'] is not None else '<span class="muted">No login yet</span>')
            personnel.append((e(r['name']),e(r['designation']),e(r['branch']),e(r['status']),
                              e(r['username']) if r['login_id'] is not None else '-', edit+' '+access))
        directory=table(['Employee','Designation','Branch (information only)','Employment status','Login','Actions'],personnel)
        people=table(['Login','Linked employee','Login status','Permissions','Actions'],[
            (e(r['username']),e(r['employee'] or '[Initial administrator]'),
             e('Enabled' if r['active'] else 'Disabled'),str(cnt[r['id']]),
             f'<a class="btn secondary" href="/users/{r["id"]}">Manage Access</a> <a class="btn secondary" href="/users/credentials/{r["id"]}">Edit Credentials</a>') for r in users])
        choices=''.join(f'<option value="{r["id"]}">{e(r["name"])} | {e(r["designation"])} | {e(r["branch"])}</option>'
                        for r in employees if r['status']=='Active' and r['login_id'] is None)
        make=f'''<div class="panel" id="create-login"><h2>Create employee login</h2><p>Only active employees without a login can be selected. New accounts have no permissions until explicitly assigned.</p><form action="/users/add" method="post">{self.csrf()}<div class="two"><div class="field"><label>Select registered employee</label><select name="staff_id" required>{choices}</select></div>{field('username','Username',required=True)}{field('password','Password (12 or more characters)',typ='password',required=True)}</div><button {'disabled' if not choices else ''}>Create Login</button></form></div>'''
        body=f'''<h1>Employees & Access</h1><p class="sub">Select an employee to edit their personnel information or set permissions for their individual login. Branch and designation do not grant access.</p>
        <div class="panel"><div class="section-head"><h2>Employee Directory</h2><a class="btn" href="/users/employee/new">+ Add Employee</a></div>{directory}</div>
        <div class="panel"><h2>Login Accounts & Permissions</h2>{people}</div>'''+make
        if self.allowed('backups.export'):
            body+='<div class="panel"><h2>Offline Backup</h2><p>Exports may contain confidential records. Store only on approved encrypted media.</p><a href="/admin/backup" class="btn">Download backup</a></div>'
        return self.write(self.wrap('Employees & Access',body))

    def employee_edit_page(self,staff_id=None):
        with db() as c:
            employee=c.execute('SELECT * FROM staff WHERE id=?',(staff_id,)).fetchone() if staff_id is not None else None
            if staff_id is not None and employee is None:return self.abort(404,'Employee not found')
            login=c.execute('SELECT id,username,active FROM users WHERE staff_id=?',(staff_id,)).fetchone() if employee else None
        get=lambda key,default='':employee[key] if employee else default
        title='Edit Employee' if employee else 'Add Employee'
        status_opts=['Active','Inactive']
        fields=f'''<div class="two">{field('name','Employee name *',get('name'),required=True)}
            {field('designation','Designation',get('designation'))}
            {field('branch','Branch / office (informational)',get('branch','Head Office'))}
            {field('joining_date','Joining date',get('joining_date'),typ='date')}
            {field('status','Employment status',get('status','Active'),options=status_opts)}</div>'''
        status_message='Changing the employee to Inactive will disable any linked login and revoke its sessions. Reactivation does not automatically restore login access.'
        links=(f'<a class="btn secondary" href="/users/{login["id"]}">Manage Access for {e(login["username"])}</a>'
               if login else '<span class="muted">No employee login linked yet. Create it from Employees & Access after saving.</span>')
        content=f'''<h1>{title}</h1><p class="sub">Personnel details are independent of software permissions. All edits are audit logged.</p>
            <div class="panel"><form action="/users/employee/save" method="post">{self.csrf()}
            <input type="hidden" name="staff_id" value="{staff_id or 0}">{fields}
            <p class="notice">{e(status_message)}</p><button>Save Employee</button>
            <a class="btn secondary" href="/users">Cancel</a></form></div>
            {f'<div class="panel"><h2>Linked login</h2>{links}</div>' if employee else ''}'''
        return self.write(self.wrap(title,content))

    def save_employee_details(self,f):
        staff_id=int(f.get('staff_id','0'))
        name=f.get('name','').strip()
        if not name or len(name)>160:raise ValueError('Employee name is required (160 characters maximum).')
        designation=f.get('designation','').strip()
        branch=f.get('branch','').strip() or 'Head Office'
        joining=f.get('joining_date','').strip()
        status=f.get('status','Active')
        if len(designation)>120 or len(branch)>120:raise ValueError('Designation or branch is too long (120 characters maximum).')
        if not valid_iso_date(joining):raise ValueError('Invalid joining date.')
        if status not in ('Active','Inactive'):raise ValueError('Invalid employment status.')
        with db() as c:
            if staff_id:
                existing=c.execute('SELECT * FROM staff WHERE id=?',(staff_id,)).fetchone()
                if existing is None:raise ValueError('Employee not found.')
                if status=='Inactive' and existing['status']!='Inactive':
                    linked=c.execute('SELECT id FROM users WHERE staff_id=? AND active=1',(staff_id,)).fetchall()
                    for login in linked:
                        if login['id']==self.user['id']:
                            raise ValueError('You cannot deactivate your own active employee account.')
                        managers=c.execute('''SELECT COUNT(DISTINCT u.id) FROM users u
                             JOIN user_permissions p ON p.user_id=u.id
                             WHERE p.permission='users.manage' AND u.active=1 AND u.id<>?''',(login['id'],)).fetchone()[0]
                        if managers==0 and c.execute('SELECT 1 FROM user_permissions WHERE user_id=? AND permission=?',
                                                     (login['id'],'users.manage')).fetchone():
                            raise ValueError('Cannot disable the last active user administrator.')
                    for login in linked:
                        c.execute('UPDATE users SET active=0 WHERE id=?',(login['id'],))
                        c.execute('DELETE FROM sessions WHERE user_id=?',(login['id'],))
                        audit(c,self.user['id'],'DISABLE_LOGIN','employee_user',login['id'],'Employment status changed to Inactive')
                c.execute('''UPDATE staff SET name=?,designation=?,branch=?,joining_date=?,status=? WHERE id=?''',
                          (name,designation,branch,joining,status,staff_id))
                c.execute('UPDATE users SET fullname=? WHERE staff_id=?',(name,staff_id))
                audit(c,self.user['id'],'EDIT','employee',staff_id,
                      f'Personnel details updated; status {existing["status"]} -> {status}; branch {existing["branch"]} -> {branch}')
            else:
                cur=c.execute('INSERT INTO staff(name,designation,branch,joining_date,status,created_at) VALUES(?,?,?,?,?,?)',
                              (name,designation,branch,joining,status,now()))
                staff_id=cur.lastrowid
                audit(c,self.user['id'],'CREATE','employee',staff_id,'Created from Employees & Access')
        self.redirect('/users/employee/'+str(staff_id))
    def employee_permissions_page(self,uid):
        import employee_access
        with db() as c:
            row=c.execute('SELECT u.*,s.name employee,s.designation,s.branch FROM users u LEFT JOIN staff s ON u.staff_id=s.id WHERE u.id=?',(uid,)).fetchone()
            if not row:raise ValueError('Employee login not found')
            granted={r[0] for r in c.execute('SELECT permission FROM user_permissions WHERE user_id=?',(uid,))}
        checks=[]
        for key,label in employee_access.PERMISSIONS.items():
            checked='checked' if key in granted else ''
            checks.append(f'<label style="display:flex;gap:10px;align-items:center;margin:7px 0"><input type="checkbox" name="perm_{e(key)}" value="yes" style="width:18px" {checked}><span>{e(label)} <small class="muted">({e(key)})</small></span></label>')
        login_action=('Disable login' if row['active'] else 'Enable login')
        login_value=('disable' if row['active'] else 'enable')
        login_status=e('Enabled' if row['active'] else 'Disabled')
        linked=(f'<a class="btn secondary" href="/users/employee/{row["staff_id"]}">Edit Employee Details</a>'
                if row['staff_id'] is not None else '')
        status_html=f'''<div class="panel"><h2>Account status: {login_status}</h2>
            <p>Employment details and system permissions are managed independently. Inactive employees cannot sign in.</p>
            <form action="/users/account/status" method="post">{self.csrf()}
            <input type="hidden" name="user_id" value="{uid}">
            <input type="hidden" name="action" value="{login_value}">
            <button type="submit" class="secondary">{login_action}</button>
            {linked}</form></div>'''
        content=f'<h1>Permissions for {e(row["employee"] or row["fullname"])}</h1><p class="sub">Employee: {e(row["username"])} | Branch: {e(row["branch"] or "-")} (informational only)</p><div class="panel"><form method="post" action="/users/permissions">{self.csrf()}<input type="hidden" name="user_id" value="{uid}"><h2>Grant exactly these abilities</h2>'+''.join(checks)+'<button>Save access permissions</button></form></div>'+status_html+f'<a class="btn secondary" href="/users/credentials/{uid}">Edit Username / Reset Password</a> '+'<a class="btn secondary" href="/users">Back to employee list</a>'
        return self.write(self.wrap('Employee Permissions',content))
    def update_employee_permissions(self,f):
        import employee_access
        uid=int(f.get('user_id','0'))
        permissions=[key for key in employee_access.PERMISSIONS if f.get('perm_'+key)=='yes']
        with db() as c:
            if not c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():raise ValueError('No such employee login')
            employee_access.save_permissions(c,self.user['id'],uid,permissions)
            audit(c,self.user['id'],'SET_PERMISSIONS','employee_user',uid,','.join(permissions))
        self.redirect('/users/'+str(uid))
    def set_employee_login_status(self,f):
        uid=int(f.get('user_id','0'))
        action=f.get('action','')
        if action not in ('enable','disable'):raise ValueError('Invalid login status operation.')
        with db() as c:
            target=c.execute('''SELECT u.id,u.active,u.staff_id,s.status employee_status
                FROM users u LEFT JOIN staff s ON s.id=u.staff_id WHERE u.id=?''',(uid,)).fetchone()
            if not target:raise ValueError('Employee login not found.')
            if action=='disable':
                if uid==self.user['id']:raise ValueError('You cannot disable your own logged-in account.')
                if c.execute('SELECT 1 FROM user_permissions WHERE user_id=? AND permission=?',(uid,'users.manage')).fetchone():
                    remaining=c.execute('''SELECT COUNT(DISTINCT u.id) FROM users u
                        JOIN user_permissions p ON p.user_id=u.id
                        WHERE u.active=1 AND u.id<>? AND p.permission='users.manage' ''',(uid,)).fetchone()[0]
                    if remaining==0:raise ValueError('Cannot disable the last active user administrator.')
                c.execute('UPDATE users SET active=0 WHERE id=?',(uid,))
                c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            else:
                if target['staff_id'] is not None and target['employee_status']!='Active':
                    raise ValueError('Activate this employee in the directory before enabling login.')
                c.execute('UPDATE users SET active=1 WHERE id=?',(uid,))
                c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            audit(c,self.user['id'],'LOGIN_STATUS','employee_user',uid,action)
        self.redirect('/users/'+str(uid))

    def account_security_page(self,uid,self_service=False):
        with db() as c:
            target=c.execute('SELECT id,username,fullname,staff_id,active FROM users WHERE id=?',(uid,)).fetchone()
        if not target:raise ValueError('Employee account not found.')
        own=(uid==self.user['id'])
        if not own and not self.allowed('users.manage'):
            return self.abort(403,'Permission required to edit another account')
        note=('Confirm your current password to change your own login.' if own else
              'Enter YOUR administrator password to authorize this employee credential change. The employee’s old password is neither needed nor recoverable.')
        form=(f'<h1>{"My Account Security" if self_service else "Edit Login Credentials"}</h1>'
              f'<p class="sub">{e(target["fullname"])} | Account ID {uid}</p>'
              f'<div class="panel"><div class="notice">{e(note)} Changes invalidate all existing sessions for the modified account, including the current session if you edit yourself.</div>'
              f'<form method="post" action="/account/credentials/save">{self.csrf()}'
              f'<input type="hidden" name="user_id" value="{uid}">'
              f'<div class="two">'
              f'{field("username","Username (3–40 lowercase letters, digits, . _ -)",target["username"],required=True)}'
              f'{field("new_password","New password (leave empty to keep existing; 12+ characters)",typ="password")}'
              f'{field("confirm_password","Confirm new password",typ="password")}'
              f'{field("current_password","Your current login password (required to authorize)",typ="password",required=True)}'
              f'</div><p class="muted">Password values are never displayed, returned or written to the audit log. A reset does not disclose the previous password.</p>'
              f'<button type="submit">Save Credentials</button> '
              f'<a class="btn secondary" href="/users" >Back to Employees & Access</a>'
              f'</form></div>')
        return self.write(self.wrap('Account Security',form))

    def save_account_credentials(self,f):
        try:uid=int(f.get('user_id','0'))
        except ValueError:raise ValueError('Invalid account.')
        own=uid==self.user['id']
        if not own and not self.allowed('users.manage'):
            return self.abort(403,'Permission required to change another account')
        username=f.get('username','').strip().lower()
        if not re.fullmatch(r'[a-z0-9_.-]{3,40}',username):
            raise ValueError('Username must be 3–40 characters: a–z, 0–9, period, underscore or hyphen.')
        new_password=f.get('new_password','')
        confirmation=f.get('confirm_password','')
        if new_password != confirmation:raise ValueError('New password confirmation does not match.')
        if new_password and len(new_password)<12:raise ValueError('New password must contain at least 12 characters.')
        # Strict operation: changes cannot proceed without validating the actor's current credential.
        entered=f.get('current_password','')
        with db() as c:
            actor=c.execute('SELECT salt,password_hash FROM users WHERE id=? AND active=1',(self.user['id'],)).fetchone()
            target=c.execute('SELECT id,username FROM users WHERE id=?',(uid,)).fetchone()
            if not actor or not target:raise ValueError('Account not found or administrator disabled.')
            if not hmac.compare_digest(hash_pw(entered,actor['salt']),actor['password_hash']):
                raise ValueError('Your current login password was incorrect; no change was made.')
            changed_username=target['username']!=username
            if not changed_username and not new_password:raise ValueError('No credential changes were entered.')
            if c.execute('SELECT id FROM users WHERE username=? AND id!=?',(username,uid)).fetchone():
                raise ValueError('This username is already in use.')
            if new_password:
                salt=secrets.token_hex(16)
                c.execute('UPDATE users SET username=?,salt=?,password_hash=? WHERE id=?',
                          (username,salt,hash_pw(new_password,salt),uid))
            else:
                c.execute('UPDATE users SET username=? WHERE id=?',(username,uid))
            c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            # Never include passwords, their derivatives or reset tokens in audit detail.
            audit(c,self.user['id'],'UPDATE_CREDENTIALS','employee_login',uid,
                  ('Username changed; ' if changed_username else '')+
                  ('password reset' if new_password else 'username updated'))
        if own:
            self.redirect('/login',cookie='sid=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/')
        else:self.redirect('/users/'+str(uid))

    def add_user(self,f):
        username=f.get('username','').strip().lower();pw=f.get('password','');staff_id=int(f.get('staff_id','0'))
        if not re.fullmatch(r'[a-z0-9_.-]{3,40}',username) or len(pw)<12:raise ValueError('Invalid username or password (minimum 12 characters).')
        with db() as c:
            employee=c.execute('SELECT id,name FROM staff WHERE id=? AND status="Active"',(staff_id,)).fetchone()
            if not employee:raise ValueError('Select a registered active employee')
            salt=secrets.token_hex(16)
            cur=c.execute('''INSERT INTO users(username,fullname,role,salt,password_hash,active,created_at,staff_id,access_initialized)
                VALUES(?,?,?,?,?,1,?,?,1)''',(username,employee['name'],'custom',salt,hash_pw(pw,salt),now(),staff_id))
            audit(c,self.user['id'],'CREATE','employee_login',cur.lastrowid,username+' / no permissions assigned')
        self.redirect('/users/'+str(cur.lastrowid))
    def audit_page(self):
        with db() as c:rows=c.execute('SELECT a.*,u.username FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300').fetchall()
        data=[(e(r['at']),e(r['username'] or 'SYSTEM'),e(r['action']),e(r['entity']),e(r['entity_id']),e(r['detail'])) for r in rows]
        return self.write(self.wrap('Audit',f'<h1>Audit Log</h1><p class="sub">Latest 300 user actions. Host administrator can technically alter this pilot SQLite database - immutable audit is a production requirement.</p><div class="panel">{table(["Time","User","Action","Entity","ID","Detail"],data)}</div>'))
    def backup(self):
        with db() as c:
            contains_real=c.execute('SELECT COUNT(*) FROM master_source_rows WHERE sheet_index=0').fetchone()[0]>0
        if contains_real and os.environ.get('COIOED_EXPLICITLY_ALLOW_UNENCRYPTED_BACKUP')!='YES':
            return self.abort(403,'Plaintext backup export disabled for imported case records. Use an IT-approved encrypted backup procedure.')
        memory=io.BytesIO()
        with db() as c:audit(c,self.user['id'],'BACKUP','database')
        with zipfile.ZipFile(memory,'w',compression=zipfile.ZIP_DEFLATED) as z:
            # SQLite online backup gives consistent snapshot while server serves users.
            snapshot=sqlite3.connect(':memory:')
            with db() as source:source.backup(snapshot)
            dump='\n'.join(snapshot.iterdump())
            z.writestr('database.sql',dump)
            snapshot.close()
            for file in UPLOAD.iterdir():
                if file.is_file():z.write(file,'uploads/'+file.name)
            z.writestr('README_BACKUP.txt','Contains unencrypted personal information. Store only on encrypted removable media. Restore following administrator-reviewed procedure.')
        archive=memory.getvalue()
        return self.write(archive,typ='application/zip',extra={'Content-Disposition':f'attachment; filename="CoIoED_Backup_{date.today().isoformat()}.zip"'})


def main():
    p=argparse.ArgumentParser(description='CoIoED isolated LAN pilot')
    p.add_argument('--init-admin',action='store_true',help='Create initial administrator interactively')
    p.add_argument('--host',default='127.0.0.1',help='Bind address. 0.0.0.0 exposes to LAN; use only on isolated, approved network.')
    p.add_argument('--port',type=int,default=8787)
    p.add_argument('--tls-cert',default=None,help='Organization-approved certificate for HTTPS LAN')
    p.add_argument('--tls-key',default=None,help='Private key for HTTPS LAN')
    args=p.parse_args()
    local_hosts=('127.0.0.1','localhost','::1')
    if args.host not in local_hosts and not (args.tls_cert and args.tls_key):
        sys.exit('BLOCKED: Non-local binding requires --tls-cert and --tls-key for HTTPS. Plaintext LAN is disabled.')
    if bool(args.tls_cert)!=bool(args.tls_key):sys.exit('TLS requires both --tls-cert and --tls-key.')
    if (ROOT/'private_data.coioed').exists() and not DB.exists():
        sys.exit('Encrypted original register not unlocked. Start using LAUNCH_COIOED.bat first.')
    init_db()
    if args.init_admin:
        import getpass
        name=input('Choose admin username: ').strip()
        fullname=input('Administrator full name: ').strip() or 'System Administrator'
        password=getpass.getpass('Password (minimum 12 characters): ')
        confirm=getpass.getpass('Confirm password: ')
        if password!=confirm:sys.exit('Passwords do not match')
        try:setup_admin(name,password,fullname)
        except ValueError as ex:sys.exit(str(ex))
        print('Administrator created. Keep credentials private.');return
    with db() as c:
        if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():sys.exit('Before starting, run: python app.py --init-admin')
    scheme='https' if args.tls_cert else 'http'
    print(f'PILOT running on {scheme}://{args.host}:{args.port}/ (Ctrl+C to stop)')
    print('Not cleared for confidential records or production until security and administrative acceptance.')
    server=ThreadingHTTPServer((args.host,args.port),App)
    server.is_tls=bool(args.tls_cert)
    if args.tls_cert:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.tls_cert,args.tls_key)
        server.socket=context.wrap_socket(server.socket,server_side=True)
    try:server.serve_forever()
    except KeyboardInterrupt:print('\nStopping...')
    finally:server.server_close()

if __name__=='__main__':main()