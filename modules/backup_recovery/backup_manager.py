#!/usr/bin/env python3
"""CoIoED backup recovery CLI. Python >=3.11, cryptography >=42.
Offline by default. No live restore or automatic destructive recovery.
"""
import argparse, contextlib, datetime as dt, hashlib, io, json, os, pathlib, shutil, sqlite3, sys, tarfile, tempfile, time, uuid
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC=b'COIOEDBK1'; CHUNK=1024*1024

def load_config(path):
    c=json.loads(pathlib.Path(path).read_text(encoding='utf8'))
    for k in ('database','attachments','primary_backup_dir','secondary_backup_dir','key_file','state_dir'):
        if k not in c: raise ValueError('Missing config: '+k)
    return c

def keybytes(c):
    p=pathlib.Path(c['key_file']); k=p.read_bytes()
    if len(k)!=32: raise ValueError('Key file must contain exactly 32 random bytes')
    return k

def now(): return dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while x:=f.read(CHUNK):h.update(x)
    return h.hexdigest()

def log(c,action,result,detail=''):
    p=pathlib.Path(c['state_dir']);p.mkdir(parents=True,exist_ok=True)
    record={'timestamp_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'action':action,'result':result,'detail':str(detail)[:1000]}
    with (p/'backup_events.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(record)+'\n')
    print(json.dumps(record))

def init_key(c):
    p=pathlib.Path(c['key_file']);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('xb') as f:
        if os.name!='nt':os.fchmod(f.fileno(),0o600)
        f.write(os.urandom(32))
    log(c,'init-key','ok','Store a separate offline copy of the key; losing it makes backups unrecoverable.')

class EncryptWriter:
    def __init__(self,f,key):
        self.f=f;self.nonce=os.urandom(12);self.enc=Cipher(algorithms.AES(key),modes.GCM(self.nonce)).encryptor()
        self.f.write(MAGIC+self.nonce)
    def write(self,data):
        if data:self.f.write(self.enc.update(data))
        return len(data)
    def flush(self):self.f.flush()
    def finish(self):self.f.write(self.enc.finalize());self.f.write(self.enc.tag);self.f.flush();os.fsync(self.f.fileno())

def decrypt_archive(archive,key,out):
    size=pathlib.Path(archive).stat().st_size
    if size<len(MAGIC)+12+16:raise ValueError('Archive too short')
    with open(archive,'rb') as src, open(out,'wb') as dst:
        if src.read(len(MAGIC))!=MAGIC:raise ValueError('Unknown archive format')
        nonce=src.read(12);src.seek(-16,2);tag=src.read(16);src.seek(len(MAGIC)+12)
        dec=Cipher(algorithms.AES(key),modes.GCM(nonce,tag)).decryptor()
        remain=size-len(MAGIC)-12-16
        while remain:
            block=src.read(min(CHUNK,remain));remain-=len(block)
            dst.write(dec.update(block))
        dst.write(dec.finalize());dst.flush();os.fsync(dst.fileno())

def add_path(tar,path,arcname):
    # Prevent attaching symlinks or junction-like unexpected directory traversal.
    if path.is_symlink():raise ValueError(f'Symlinks not supported: {path}')
    if path.is_file():tar.add(str(path),arcname=arcname,recursive=False)
    elif path.is_dir():
        for item in sorted(path.rglob('*')):
            if item.is_symlink():raise ValueError(f'Symlinks not supported: {item}')
            if item.is_file():tar.add(str(item),arcname=f'{arcname}/{item.relative_to(path).as_posix()}',recursive=False)

def build_backup(c,tier):
    db=pathlib.Path(c['database']);atts=pathlib.Path(c['attachments'])
    if not db.is_file():raise FileNotFoundError(db)
    if not atts.is_dir():raise FileNotFoundError(atts)
    primary=pathlib.Path(c['primary_backup_dir']);secondary=pathlib.Path(c['secondary_backup_dir'])
    primary.mkdir(parents=True,exist_ok=True);secondary.mkdir(parents=True,exist_ok=True)
    if primary.resolve()==secondary.resolve():raise ValueError('Primary and secondary cannot be the same directory')
    uid=uuid.uuid4().hex[:8];name=f'CoIoED_{tier}_{now()}_{uid}.cbk'
    with tempfile.TemporaryDirectory(prefix='coioed-snapshot-') as work:
        snap=pathlib.Path(work)/'database.sqlite'
        with sqlite3.connect(f'file:{db.resolve().as_posix()}?mode=ro',uri=True,timeout=60) as source:
            with sqlite3.connect(snap) as target:source.backup(target,pages=512,sleep=0.1)
        with sqlite3.connect(snap) as chk:
            if chk.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity check failed')
        # File manifest captures attachments at backup time. To avoid app/attachment race,
        # application must quiesce writes during capture or support immutable attachments.
        manifest={'format_version':1,'tier':tier,'created_utc':now(),'database_sha256':sha(snap),'attachments':{}}
        for item in sorted(atts.rglob('*')):
            if item.is_symlink():raise ValueError('Attachment symlink rejected')
            if item.is_file():manifest['attachments'][item.relative_to(atts).as_posix()]=sha(item)
        partial=primary/(name+'.partial')
        try:
            with open(partial,'xb') as file:
                if os.name!='nt':os.fchmod(file.fileno(),0o600)
                enc=EncryptWriter(file,keybytes(c))
                with tarfile.open(fileobj=enc,mode='w|gz') as tar:
                    add_path(tar,snap,'database.sqlite');add_path(tar,atts,'attachments')
                    data=json.dumps(manifest,sort_keys=True).encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);tar.addfile(info,io.BytesIO(data))
                enc.finish()
            verify_archive(c,partial)
            final=primary/name;partial.replace(final)
            dest=secondary/name
            tmp=secondary/(name+'.partial')
            try:
                shutil.copyfile(final,tmp)
                if sha(final)!=sha(tmp):raise ValueError('Replica checksum mismatch')
                tmp.replace(dest)
            finally:tmp.unlink(missing_ok=True)
            return final,dest
        finally:partial.unlink(missing_ok=True)

def unpack_verified(c,archive,target):
    """Decrypt authenticated archive, validate each file, stage in an empty directory."""
    target=pathlib.Path(target)
    if target.exists():raise FileExistsError('Restore/verification destination already exists')
    target.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(prefix='coioed-decrypt-') as work:
            plain=pathlib.Path(work)/'archive.tar.gz'
            decrypt_archive(archive,keybytes(c),plain)
            with tarfile.open(plain,'r:gz') as tar:
                members=tar.getmembers();names=set()
                for m in members:
                    p=pathlib.PurePosixPath(m.name)
                    if m.name in names or p.is_absolute() or '..' in p.parts or not m.isfile() or not (m.name=='database.sqlite' or m.name=='manifest.json' or m.name.startswith('attachments/')):
                        raise ValueError('Unsafe or duplicate archive member: '+m.name)
                    names.add(m.name)
                if 'manifest.json' not in names or 'database.sqlite' not in names:raise ValueError('Backup missing required files')
                for m in members:
                    destination=target.joinpath(*pathlib.PurePosixPath(m.name).parts)
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    with tar.extractfile(m) as source, destination.open('xb') as dest:shutil.copyfileobj(source,dest)
        manifest=json.loads((target/'manifest.json').read_text())
        if manifest.get('format_version')!=1:raise ValueError('Unsupported manifest version')
        if sha(target/'database.sqlite')!=manifest['database_sha256']:raise ValueError('Database hash mismatch')
        observed={p.relative_to(target/'attachments').as_posix():sha(p) for p in (target/'attachments').rglob('*') if p.is_file()} if (target/'attachments').exists() else {}
        if observed!=manifest['attachments']:raise ValueError('Attachment list or file hashes mismatch')
        with sqlite3.connect(target/'database.sqlite') as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Restored database failed integrity check')
        return manifest
    except Exception:
        shutil.rmtree(target,ignore_errors=True)
        raise

def verify_archive(c,archive):
    with tempfile.TemporaryDirectory(prefix='coioed-verify-') as root:
        manifest=unpack_verified(c,archive,pathlib.Path(root)/'check')
    return manifest

def check_latest(c):
    d=pathlib.Path(c['primary_backup_dir']);archives=sorted(d.glob('*.cbk'),reverse=True)
    if not archives:raise ValueError('No backup archives found')
    archive=archives[0];verify_archive(c,archive)
    replica=pathlib.Path(c['secondary_backup_dir'])/archive.name
    if not replica.is_file() or sha(archive)!=sha(replica):raise ValueError('Replica missing or different')
    verify_archive(c,replica)
    return archive

def prune(c,tier):
    limits={'snapshot':int(c.get('keep_snapshots',96)),'nightly':int(c.get('keep_nightly',30)),'weekly':int(c.get('keep_weekly',12)),'preupdate':int(c.get('keep_preupdate',20))}
    lim=max(2,limits[tier])
    for folder in (c['primary_backup_dir'],c['secondary_backup_dir']):
        files=sorted(pathlib.Path(folder).glob(f'CoIoED_{tier}_*.cbk'),reverse=True)
        for old in files[lim:]:old.unlink()

@contextlib.contextmanager
def backup_lock(c):
    state=pathlib.Path(c['state_dir']);state.mkdir(parents=True,exist_ok=True)
    lock=state/'backup_running.lock'
    try:
        fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        raise RuntimeError('Another backup may be running; inspect lock and running tasks before manual removal')
    try:
        with os.fdopen(fd,'w') as f:f.write(f'pid={os.getpid()} started={now()}\n')
        yield
    finally:
        lock.unlink(missing_ok=True)

def cli():
    p=argparse.ArgumentParser(description='CoIoED encrypted offline backups and restore staging')
    p.add_argument('--config',default='backup_config.json');sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init-key')
    b=sub.add_parser('backup');b.add_argument('--tier',choices=['snapshot','nightly','weekly','preupdate'],default='snapshot')
    v=sub.add_parser('verify');v.add_argument('--archive');sub.add_parser('health')
    r=sub.add_parser('restore-stage');r.add_argument('--archive',required=True);r.add_argument('--destination',required=True)
    a=p.parse_args();c=load_config(a.config)
    try:
        if a.cmd=='init-key':init_key(c)
        elif a.cmd=='backup':
            with backup_lock(c):
                first,second=build_backup(c,a.tier);prune(c,a.tier)
            log(c,'backup '+a.tier,'ok',str(first)+' | '+str(second))
        elif a.cmd=='verify':
            arc=a.archive or str(check_latest(c))
            verify_archive(c,arc);log(c,'verify','ok',arc)
        elif a.cmd=='health':
            arc=check_latest(c);age=time.time()-arc.stat().st_mtime
            maxage=int(c.get('max_backup_age_minutes',45))*60
            if age>maxage:raise ValueError(f'Latest backup stale ({int(age/60)} min)')
            log(c,'health','ok',f'Latest verified/replicated: {arc.name}')
        elif a.cmd=='restore-stage':
            manifest=unpack_verified(c,a.archive,a.destination)
            log(c,'restore-stage','ok',f'Staged at {a.destination}; {len(manifest["attachments"])} attachments. No live data was overwritten.')
    except Exception as ex:
        log(c,a.cmd,'failed',f'{type(ex).__name__}: {ex}');sys.exit(1)
if __name__=='__main__':cli()
