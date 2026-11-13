import json, pathlib, sqlite3, tempfile, unittest, sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import backup_manager as b

class BackupTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=pathlib.Path(self.tmp.name);(self.root/'files').mkdir()
  (self.root/'files'/'test.txt').write_text('evidence sample')
  with sqlite3.connect(self.root/'live.db') as db:
   db.execute('create table cases(id text)');db.execute("insert into cases values('1234-K')")
  self.c={'database':str(self.root/'live.db'),'attachments':str(self.root/'files'),'primary_backup_dir':str(self.root/'backupsA'),'secondary_backup_dir':str(self.root/'backupsB'),'key_file':str(self.root/'secrets'/'key'),'state_dir':str(self.root/'state')}
  b.init_key(self.c)
 def test_backup_restore_and_replica(self):
  first,second=b.build_backup(self.c,'snapshot')
  self.assertEqual(b.sha(first),b.sha(second))
  self.assertEqual(len(b.verify_archive(self.c,first)['attachments']),1)
  m=b.unpack_verified(self.c,first,self.root/'restore')
  with sqlite3.connect(self.root/'restore'/'database.sqlite') as db:
   self.assertEqual(db.execute('select id from cases').fetchone()[0],'1234-K')
  self.assertEqual((self.root/'restore'/'attachments'/'test.txt').read_text(),'evidence sample')
  with self.assertRaises(FileExistsError):b.unpack_verified(self.c,first,self.root/'restore')
 def test_corruption_fails_closed(self):
  first,_=b.build_backup(self.c,'snapshot')
  data=bytearray(first.read_bytes());data[len(data)//2]^=0x01;first.write_bytes(data)
  with self.assertRaises(Exception):b.verify_archive(self.c,first)
 def test_wrong_key_fails_closed(self):
  first,_=b.build_backup(self.c,'snapshot');self.c['key_file']=str(self.root/'wrongkey');pathlib.Path(self.c['key_file']).write_bytes(b'X'*32)
  with self.assertRaises(Exception):b.verify_archive(self.c,first)
 def test_missing_replica_detected(self):
  first,second=b.build_backup(self.c,'snapshot');second.unlink()
  with self.assertRaises(ValueError):b.check_latest(self.c)
if __name__=='__main__':unittest.main()
