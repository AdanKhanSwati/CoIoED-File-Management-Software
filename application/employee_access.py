"""Individual employee permissions, irrespective of branch or job title."""
PERMISSIONS = {
 'cases.view':'View electronic case register',
 'production.view':'View issued production orders and execution status',
 'production.edit':'Record and update production orders',
 'proceedings.view':'View chronological case proceedings',
 'proceedings.add':'Add and verify a proceeding',
 'proceedings.import':'Import historical proceedings CSV',
 'cases.edit':'Register and edit case details',
 'cases.archive':'Archive / restore cases with reason',
 'cases.import':'Import master register',
 'cases.export':'Export complete case register',
 'documents.view':'Open authorized scanned documents',
 'documents.upload':'Attach scanned files',
 'movement.view':'View physical file custody movements',
 'movement.dispatch':'Dispatch physical case files',
 'movement.receive':'Accept or reject branch receipt',
 'hearing.view':'View hearing notices',
 'hearing.edit':'Prepare and edit hearing notices',
 'hearing.approve':'Approve/issue hearing schedules',
 'hearing.export':'Export hearing PDF/Excel',
 'reports.view':'View monthly reports',
 'procurement.view':'View procurement requisitions and purchases',
 'procurement.edit':'Create requisitions and manage procurement documents',
 'procurement.approve':'Approve or return procurement requisitions',
 'procurement.export':'Export procurement register (CSV)',
 'fsp.view':'View family support package records',
 'fsp.edit':'Edit family support records',
 'hr.view':'View employee, leave and contract records',
 'hr.manage':'Maintain employee, attendance and leave records',
 'hr.leave_approve':'Approve or reject leave',
 'users.manage':'Manage employee logins and permissions',
 'audit.view':'Review system audit log',
 'backups.export':'Download offline backup',
}
LEGACY = {
 'admin':list(PERMISSIONS),
 'registrar':['production.view','production.edit','cases.view','cases.edit','cases.archive','cases.export','documents.view','documents.upload','movement.view','movement.dispatch','movement.receive','hearing.view','hearing.edit','hearing.approve','hearing.export','proceedings.view','proceedings.add','reports.view','fsp.view','fsp.edit','procurement.view','procurement.edit','procurement.approve','procurement.export','audit.view'],
 'inquiry':['production.view','cases.view','documents.view','movement.view','movement.dispatch','hearing.view','hearing.edit','hearing.export','proceedings.view','proceedings.add','reports.view'],
 'records':['production.view','cases.view','cases.edit','cases.import','cases.export','documents.view','documents.upload','movement.view','movement.dispatch','hearing.view','hearing.edit','hearing.export','fsp.view','fsp.edit'],
 'hr':['hr.view','hr.manage','hr.leave_approve','documents.view','documents.upload'],
 'viewer':['production.view','cases.view','hearing.view','proceedings.view','reports.view']
}

def init(c):
    cols=[r['name'] for r in c.execute('PRAGMA table_info(users)')]
    if 'access_initialized' not in cols:c.execute('ALTER TABLE users ADD COLUMN access_initialized INTEGER NOT NULL DEFAULT 0')
    if 'staff_id' not in cols:c.execute('ALTER TABLE users ADD COLUMN staff_id INTEGER REFERENCES staff(id)')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS user_permissions(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, permission TEXT NOT NULL, PRIMARY KEY (user_id,permission));
    CREATE UNIQUE INDEX IF NOT EXISTS idx_user_staff_link ON users(staff_id) WHERE staff_id IS NOT NULL;
    ''')
    c.execute('CREATE TABLE IF NOT EXISTS access_migrations(version TEXT PRIMARY KEY)')
    # Explicit one-time admin-only migration for newly introduced procurement module.
    # Existing staff access is not silently granted when this upgrade is installed.
    if not c.execute('SELECT 1 FROM access_migrations WHERE version=?',('procurement_permissions_v118',)).fetchone():
        for u in c.execute("SELECT id FROM users WHERE role='admin' AND staff_id IS NULL").fetchall():
            for perm in ('procurement.view','procurement.edit','procurement.approve','procurement.export'):
                c.execute('INSERT OR IGNORE INTO user_permissions(user_id,permission) VALUES(?,?)',(u['id'],perm))
        c.execute('INSERT INTO access_migrations(version) VALUES(?)',('procurement_permissions_v118',))
    # One-time migration for the original bootstrap admin; never repeat on restart.
    # Individual permission removals must be respected after this upgrade.
    c.execute('CREATE TABLE IF NOT EXISTS access_migrations(version TEXT PRIMARY KEY)')
    if not c.execute('SELECT 1 FROM access_migrations WHERE version=?',('production_order_permissions_v09',)).fetchone():
        for user in c.execute('SELECT id,role,staff_id FROM users WHERE role=? AND staff_id IS NULL',('admin',)).fetchall():
            for p in ('production.view','production.edit'):
                c.execute('INSERT OR IGNORE INTO user_permissions(user_id,permission) VALUES(?,?)',(user['id'],p))
        c.execute('INSERT INTO access_migrations(version) VALUES(?)',('production_order_permissions_v09',))
    # One-time migration: old role is only a default; all future access checks use explicit permissions.
    for u in c.execute('SELECT id,role FROM users WHERE access_initialized=0').fetchall():
        for p in LEGACY.get(u['role'],[]):c.execute('INSERT OR IGNORE INTO user_permissions(user_id,permission) VALUES(?,?)',(u['id'],p))
        c.execute('UPDATE users SET access_initialized=1 WHERE id=?',(u['id'],))

def allowed(c,user_id,p):
    if p not in PERMISSIONS:return False
    return c.execute('SELECT 1 FROM user_permissions WHERE user_id=? AND permission=?',(user_id,p)).fetchone() is not None

def save_permissions(c,admin_id,user_id,permissions):
    requested=set(permissions)
    if not requested.issubset(PERMISSIONS):raise ValueError('Unrecognized access permission.')
    if 'users.manage' not in requested:
        managers=c.execute("SELECT count(DISTINCT user_id) FROM user_permissions JOIN users ON users.id=user_permissions.user_id WHERE permission='users.manage' AND users.active=1 AND users.id<>?",(user_id,)).fetchone()[0]
        if managers==0:raise ValueError('The last active user administrator cannot lose user-management access.')
    c.execute('UPDATE users SET access_initialized=1 WHERE id=?',(user_id,))
    c.execute('DELETE FROM user_permissions WHERE user_id=?',(user_id,))
    c.executemany('INSERT INTO user_permissions(user_id,permission) VALUES(?,?)',[(user_id,p) for p in sorted(requested)])