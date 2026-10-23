import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from package_delete_service import PackageReviewManager,service_dataset,validate_package_plan
from dedup_review_server import Cloud,require_deletion_enabled
from test_folder_dedup_actions import FakeCloud
from test_package_boundaries import record


def fixture():
    from dedup_review_server import Tree
    records=[record(p) for p in ['/01恩恩学习资料','/我的资源','/01恩恩学习资料/A','/我的资源/A',
                               '/01恩恩学习资料/A/Week 1','/我的资源/A/Week 1']]
    records += [record(p+'/lesson.mp3',0) for p in ['/01恩恩学习资料/A/Week 1','/我的资源/A/Week 1']]
    tree=Tree(records)
    units=[{'path':p,'fsid':p,'files':1,'folders':1,'bytes':3,'protected':False,
            'fingerprint':tree.nodes[p]['fingerprint']} for p in ['/01恩恩学习资料/A','/我的资源/A']]
    return {'meta':{'createdAt':'test','sourceScan':{'completedAt':'test'}},'packages':units,
            'exact':[{'id':'P001','type':'exact','title':'A','copies':units}],'contains':[]},records


class PackageDeleteServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.data,records=fixture();self.cloud=FakeCloud(records)
        self.manager=PackageReviewManager(self.data,cloud_factory=lambda:self.cloud,audit_dir=Path(self.tmp.name))
        guard=patch('dedup_review_server.require_deletion_enabled');guard.start();self.addCleanup(guard.stop)

    def test_prepare_requires_final_confirmation_and_can_only_delete_root(self):
        key=self.manager.start_prepare([{'id':'P001'}],run=False);self.manager.prepare(key)
        self.assertEqual(self.cloud.deletes,[])
        self.assertEqual(self.cloud.full_reads,0)
        job=self.manager.jobs[key];self.assertEqual(job['scope'],'whole-package-v1')
        self.assertEqual([x['path'] for x in job['plan']['remove']],['/我的资源/A'])
        self.manager.confirm(key,job['confirmationToken'],1,run=False)
        self.manager.execute(key)
        self.assertEqual(self.cloud.deletes,[['/我的资源/A']])
        self.assertEqual(job['status'],'done')

    def test_old_case_ids_and_child_keeper_paths_rejected(self):
        for choice in ({'id':'E1540'},{'id':'P001','keep':'/我的资源/A/Week 1'}):
            with self.assertRaises(ValueError):self.manager.start_prepare([choice],run=False)
        self.assertEqual(self.cloud.deletes,[])

    def test_tampered_preview_with_internal_folder_rejected_at_confirm(self):
        key=self.manager.start_prepare([{'id':'P001'}],run=False);self.manager.prepare(key)
        job=self.manager.jobs[key];job['plan']['groups'][0]['remove'][0]['path']+='/Week 1'
        with self.assertRaisesRegex(ValueError,'包内子目录'):
            self.manager.confirm(key,job['confirmationToken'],1,run=False)
        self.assertEqual(self.cloud.deletes,[])

    def test_live_package_content_change_stops_before_deletion(self):
        key=self.manager.start_prepare([{'id':'P001'}],run=False);self.manager.prepare(key);j=self.manager.jobs[key]
        self.cloud.records[-1]['md5']='changed'
        self.manager.confirm(key,j['confirmationToken'],1,run=False);self.manager.execute(key)
        self.assertEqual(self.cloud.deletes,[]);self.assertEqual(j['status'],'changed')

    def test_rolled_back_old_submissions_never_become_pending(self):
        path=Path(self.tmp.name)/('a'*24+'.json')
        path.write_text(json.dumps({'id':'a'*24,'scope':'legacy-subtrees','status':'partial',
            'confirmedAt':'old','events':[{'paths':['/我的资源/A/Week 1'],'status':'unconfirmed'}]}))
        manager=PackageReviewManager(self.data,cloud_factory=lambda:self.cloud,audit_dir=Path(self.tmp.name))
        self.assertEqual(manager.known_state()['pending'],[]);self.assertEqual(manager.jobs,{})


class GlobalPackageGuardTests(unittest.TestCase):
    def test_policy_rejects_child_path_even_at_low_level_cloud_delete(self):
        data,_=fixture()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);lock=root/'policy.json';lock.write_text(json.dumps({'disabled':False,'mode':'package-roots-only'}))
            (root/'整包去重清单.json').write_text(json.dumps(data),encoding='utf-8')
            with patch('dedup_review_server.DELETE_LOCK',lock),patch('dedup_review_server.OUT',root):
                require_deletion_enabled(['/我的资源/A'])
                for path in ['/我的资源/A/Week 1','/我的资源','/unlisted']:
                    with self.assertRaisesRegex(ValueError,'包内子目录'):Cloud.delete(object(),[path])


if __name__=='__main__':unittest.main()
