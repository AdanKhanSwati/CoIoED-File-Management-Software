"""CoIoED Windows hearing update / rollback. No DB or case files are copied."""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, pathlib, shutil, socket, subprocess, sys, tempfile

ROOT=pathlib.Path(__file__).resolve().parent
PAYLOAD=ROOT/'payload'
CODE_FILES=('hearing_v2.py','notice_export.py')
TEST_FILES=('test_notice_move_integration.py','test_clubbed_parentage_export.py')
BACKUP_DIR='_hearing_update_backups'

def check_location(root):
    if not root.is_dir() or not (root/'app.py').is_file() or not all((root/f).is_file() for f in CODE_FILES):
        raise RuntimeError('Select the CoIoED folder that contains app.py and hearing_v2.py, not the data folder.')
    if not (root/'hearing_workflow_v121.py').is_file():
        raise RuntimeError('Expected hearing workflow module was not found. Refusing an incompatible update.')
    if not (root/'notice_export.py').is_file():
        raise RuntimeError('Existing notice export missing. Refusing installation.')

def refuse_if_running():
    try:
        with socket.create_connection(('127.0.0.1',8787),timeout=0.6):
            raise RuntimeError('Local app port 8787 is active. Close the CoIoED server/launcher before updating.')
    except (ConnectionRefusedError,TimeoutError,OSError):
        pass

def verify_files():
    for name in CODE_FILES+TEST_FILES:
        data=(PAYLOAD/name).read_bytes()
        compile(data,str(PAYLOAD/name),'exec')

def backup(root):
    location=root/BACKUP_DIR/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    location.mkdir(parents=True,exist_ok=False)
    hashes={}
    for name in CODE_FILES:
        origin=root/name
        data=origin.read_bytes();(location/name).write_bytes(data)
        hashes[name]=hashlib.sha256(data).hexdigest()
    manifest={'format':1,'source_root':str(root),'files':hashes,'created_at':datetime.datetime.now().isoformat()}
    (location/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return location

def replace_source(root):
    for name in CODE_FILES:
        target=root/name
        with tempfile.NamedTemporaryFile(dir=root,prefix='.coioed_update_',suffix='.tmp',delete=False) as f:
            pending=pathlib.Path(f.name)
            f.write((PAYLOAD/name).read_bytes());f.flush();os.fsync(f.fileno())
        try:os.replace(pending,target)
        finally:pending.unlink(missing_ok=True)

def restore(root,location):
    manifest=json.loads((location/'manifest.json').read_text(encoding='utf-8'))
    if pathlib.Path(manifest['source_root']).resolve()!=root.resolve():
        raise RuntimeError('Backup belongs to a different installation.')
    for name in CODE_FILES:
        data=(location/name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=manifest['files'][name]:
            raise RuntimeError('Backup checksum mismatch. Refusing restoration.')
    for name in CODE_FILES:shutil.copy2(location/name,root/name)

def run_tests(root):
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    env['PYTHONPATH']=str(root)+os.pathsep+str(PAYLOAD)+os.pathsep+env.get('PYTHONPATH','')
    for name in TEST_FILES:
        result=subprocess.run([sys.executable,str(PAYLOAD/name)],cwd=str(root),env=env,
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
        print(result.stdout)
        if result.returncode:raise RuntimeError(f'Post-install synthetic test failed: {name}')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--target',help='Existing CoIoED installation directory')
    p.add_argument('--rollback',action='store_true',help='Restore newest source backup')
    args=p.parse_args()
    value=args.target or input('Full path to your EXISTING CoIoED software folder: ').strip().strip('"')
    root=pathlib.Path(value).expanduser().resolve()
    check_location(root);refuse_if_running()
    if args.rollback:
        saved=sorted((root/BACKUP_DIR).glob('*/manifest.json'),reverse=True)
        if not saved:raise RuntimeError('No earlier update backups exist.')
        print('Restoring program files from:',saved[0].parent)
        restore(root,saved[0].parent)
        print('Rollback completed. No database or uploads were altered.')
        return
    verify_files()
    location=backup(root)
    print('Original application files backed up to:',location)
    try:
        replace_source(root)
        run_tests(root)
        print('SUCCESS: Hearing update installed and synthetic checks passed.')
        print('Live database, case records, attachments and encryption keys were NOT changed.')
        print('Restart LAUNCH_COIOED.bat and verify with fabricated draft notices first.')
    except Exception:
        print('Update validation failed; rolling back application code.')
        restore(root,location)
        raise

if __name__=='__main__':
    try:main()
    except Exception as err:
        print('INSTALLER ERROR:',str(err),file=sys.stderr)
        sys.exit(1)