import sqlite3
import unittest
import pathlib, sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from hearing_notice import install_schema, notice_label, move_day, single_day_print_html

class HearingNoticeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        install_schema(self.db)
        self.db.executemany('INSERT INTO hearing_notices(id) VALUES(?)',[(1,), (2,)])
        self.db.execute("INSERT INTO hearing_days(id,notice_id,hearing_date,districts) VALUES(10,1,'2026-11-11','Mardan & Swat')")
        self.db.executemany('INSERT INTO hearing_day_cases(day_id,case_code,sort_order,details) VALUES(10,?,?,?)',[('123-K',1,'A'),('456-K',2,'B')])
    def tearDown(self): self.db.close()
    def test_label(self):
        self.assertEqual(notice_label(self.db,1),'11th November 2026')
        self.db.execute("INSERT INTO hearing_days(notice_id,hearing_date) VALUES(1,'2026-11-13')")
        self.assertEqual(notice_label(self.db,1),'11th November 2026 to 13th November 2026')
    def test_move_preserves_cases_and_recalculates_labels(self):
        move_day(self.db,10,2,actor='test registrar')
        self.assertEqual(self.db.execute('SELECT notice_id FROM hearing_days WHERE id=10').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM hearing_day_cases WHERE day_id=10').fetchone()[0],2)
        self.assertEqual(notice_label(self.db,1),'No hearing dates assigned')
        self.assertEqual(notice_label(self.db,2),'11th November 2026')
        self.assertEqual(self.db.execute('SELECT moved_by FROM hearing_day_moves').fetchone()[0],'test registrar')
    def test_duplicate_blocks_and_rolls_back(self):
        self.db.execute("INSERT INTO hearing_days(notice_id,hearing_date) VALUES(2,'2026-11-11')")
        with self.assertRaises(ValueError):move_day(self.db,10,2,actor='test')
        self.assertEqual(self.db.execute('SELECT notice_id FROM hearing_days WHERE id=10').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM hearing_day_moves').fetchone()[0],0)
    def test_approved_blocks(self):
        self.db.execute('UPDATE hearing_days SET approved=1 WHERE id=10')
        with self.assertRaises(PermissionError):move_day(self.db,10,2,actor='test')
    def test_single_day_print(self):
        html=single_day_print_html(self.db,10)
        self.assertIn('Print this day',html)
        self.assertIn('Mardan &amp; Swat',html)
        self.assertNotIn('ANNEX-A',html)
        self.assertIn('123-K',html)
        self.assertNotIn('456-X',html)

if __name__=='__main__': unittest.main()
