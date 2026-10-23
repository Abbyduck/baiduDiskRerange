import json
import tempfile
import unittest
from pathlib import Path

from sqlite_inventory import (load_complete_snapshot, snapshot_diff,
                              observe_packages, approve_metadata, connect)
from package_boundaries import CONTAINERS, infer_roots, deletion_candidate_boundary


class SQLiteCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def scan(self, scan_id, rows, status='complete', expected=None):
        root = self.home / '实时索引'
        (root / scan_id).mkdir(parents=True, exist_ok=True)
        (root / scan_id / 'page-00001.json').write_text(
            json.dumps({'items': rows}, ensure_ascii=False), encoding='utf-8')
        (root / 'latest-scan.json').write_text(json.dumps({
            'directory': scan_id, 'status': status, 'pages': 1,
            'entries': len(rows) if expected is None else expected,
            'completedAt': '2026-10-23T12:00:00+08:00'
        }, ensure_ascii=False), encoding='utf-8')

    def test_import_and_diff_preserve_previous_scan(self):
        rows1 = [{'fsid': 'inbox', 'path': '/我的资源', 'name': '我的资源', 'isdir': 1},
                 {'fsid': 'f1', 'path': '/我的资源/课程', 'name': '课程', 'isdir': 1},
                 {'fsid': 'f2', 'path': '/我的资源/课程/A.mp4', 'name': 'A.mp4',
                  'isdir': 0, 'size': 10, 'md5': 'a'}]
        self.scan('scan-111abc', rows1)
        scan, loaded = load_complete_snapshot(self.home)
        self.assertEqual(len(loaded), 3)
        self.assertIsNone(snapshot_diff(self.home, scan['directory'])['previousScanId'])
        rows2 = [{'fsid': 'child', 'path': '/01恩恩学习资料', 'name': '01恩恩学习资料', 'isdir': 1},
                 {'fsid': 'english', 'path': '/01恩恩学习资料/英文', 'name': '英文', 'isdir': 1},
                 {'fsid': 'f1', 'path': '/01恩恩学习资料/英文/0_课程', 'name': '0_课程', 'isdir': 1},
                 {'fsid': 'f2', 'path': '/01恩恩学习资料/英文/0_课程/A.mp4',
                  'name': 'A.mp4', 'isdir': 0, 'size': 10, 'md5': 'a'},
                 {'fsid': 'f3', 'path': '/01恩恩学习资料/英文/0_课程/B.pdf',
                  'name': 'B.pdf', 'isdir': 0, 'size': 2, 'md5': 'b'}]
        self.scan('scan-222abc', rows2)
        load_complete_snapshot(self.home)
        d = snapshot_diff(self.home, 'scan-222abc')
        self.assertEqual((d['movedOrRenamed'], d['added'], d['removed']), (2, 3, 1))
        load_complete_snapshot(self.home)
        with connect(self.home) as cx:
            self.assertEqual(cx.execute('SELECT COUNT(*) FROM catalog_scans').fetchone()[0], 2)

    def test_incomplete_scan_does_not_replace_last_good_scan(self):
        self.scan('scan-111aaa', [{'fsid': 'x', 'path': '/x', 'name': 'x', 'isdir': 1}])
        load_complete_snapshot(self.home)
        self.scan('scan-222bbb', [], status='reading')
        with self.assertRaises(ValueError):
            load_complete_snapshot(self.home)
        self.scan('scan-333ccc', [], expected=2)
        with self.assertRaises(ValueError):
            load_complete_snapshot(self.home)
        with connect(self.home) as cx:
            self.assertEqual(cx.execute('SELECT COUNT(*) FROM catalog_scans').fetchone()[0], 1)

    def test_missing_parent_rolls_back(self):
        self.scan('scan-111aaa', [{'fsid': 'x', 'path': '/x', 'name': 'x', 'isdir': 1}])
        load_complete_snapshot(self.home)
        self.scan('scan-222bbb', [{'fsid': 'child', 'path': '/absent/child',
                                    'name': 'child', 'isdir': 1}])
        with self.assertRaisesRegex(ValueError, 'missing directory parent'):
            load_complete_snapshot(self.home)
        with connect(self.home) as cx:
            self.assertEqual(cx.execute('SELECT COUNT(*) FROM catalog_scans').fetchone()[0], 1)

    def test_manual_review_survives_rename_and_move(self):
        item = {'fsid': '123', 'path': '/我的资源/Susan', 'title': 'Susan',
                'displayTitle': 'Susan', 'category': '英文', 'summary': '猜测',
                'purpose': '猜测', 'notes': []}
        observe_packages([item], self.home)
        ident = item['id']
        approve_metadata(item['path'], self.home, display_name='Susan五件套', purpose='启蒙用')
        moved = {'fsid': '123', 'path': '/01恩恩学习资料/英文/0_Susan', 'title': '0_Susan',
                 'displayTitle': '0_Susan', 'category': '待定', 'summary': '猜测',
                 'purpose': '猜测', 'notes': []}
        observe_packages([moved], self.home)
        self.assertEqual(moved['id'], ident)
        self.assertEqual(moved['priority'], 0)
        self.assertEqual(moved['displayTitle'], 'Susan五件套')
        self.assertEqual(moved['purpose'], '启蒙用')
        self.assertTrue(moved['manuallyReviewed'])

    def test_changed_fsid_does_not_inherit_review(self):
        item = {'fsid': 'old', 'path': '/我的资源/Susan', 'title': 'Susan',
                'displayTitle': 'Susan', 'category': '英文',
                'summary': '猜测', 'purpose': '猜测', 'notes': []}
        observe_packages([item], self.home)
        approve_metadata(item['path'], self.home, display_name='人工名称')
        other = {'fsid': 'new', 'path': item['path'], 'title': 'Susan',
                 'displayTitle': 'Susan', 'category': '英文',
                 'summary': '猜测', 'purpose': '猜测', 'notes': []}
        observe_packages([other], self.home)
        self.assertNotEqual(item['id'], other['id'])
        self.assertEqual(other['displayTitle'], 'Susan')

    def test_oxford_container_is_not_deletion_root(self):
        ox = '/01恩恩学习资料/小孩资源/牛津树'
        self.assertIn(ox, CONTAINERS)
        roots = infer_roots([ox, ox+'/音效', ox+'/pdf'])
        self.assertIn(ox+'/音效', roots)
        self.assertNotIn(ox, roots)
        self.assertFalse(deletion_candidate_boundary(roots[ox+'/音效']))


if __name__ == '__main__':
    unittest.main()
