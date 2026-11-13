"""Local-only restore utility for administrators. Stop the LAN server first.
Never restore an untrusted ZIP archive. Restores into a new folder; no overwrites.
"""
import argparse, hashlib, re, sqlite3, sys, zipfile
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('backup',help='Backup ZIP exported from CoIoED pilot')
p.add_argument('destination',help='NEW empty destination folder for recovered database and uploads')
a=p.parse_args()
zp=Path(a.backup)
dest=Path(a.destination).resolve()
if not zp.is_file():sys.exit('Backup ZIP not found.')
if dest.exists() and any(dest.iterdir()):sys.exit('Destination must not exist or must be empty. Do not overwrite a running system.')
with zipfile.ZipFile(zp) as z:
    names=z.namelist()
    if 'database.sql' not in names:sys.exit('No database SQL found in backup')
    if any(n.startswith('/') or '..' in Path(n).parts or '\\' in n for n in names):sys.exit('Unsafe ZIP archive path')
    if sum(v.file_size for v in z.infolist()) > 5*1024*1024*1024:sys.exit('Archive is unexpectedly large')
    sql=z.read('database.sql').decode('utf-8')
    dest.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(dest/'coioed_restored.db')
    try:
        con.executescript(sql)
        integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':raise ValueError('Database integrity failure: '+integrity)
        files=con.execute('SELECT stored_name,sha256 FROM documents').fetchall()
    finally:con.close()
    folder=dest/'uploads';folder.mkdir(exist_ok=True)
    for name,checksum in files:
        if not re.fullmatch(r'[0-9a-f]{32}\.(pdf|png|jpg|jpeg|tif|tiff)',name):sys.exit('Unexpected stored file name')
        content=z.read('uploads/'+name)
        if hashlib.sha256(content).hexdigest()!=checksum:sys.exit('Checksum mismatch: '+name)
        (folder/name).write_bytes(content)
print('Verified backup restored into',dest)
print('Manually move the verified database and uploads onto a NEW isolated deployment after security approval.')