"""Meaningful deletion-boundary tests; all cloud effects use an in-memory fake."""
import copy
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from folder_dedup_plan import collapse_exact_folders, make_exact_plan, make_deletion_plan, inside
from dedup_review_server import ReviewManager, Tree, Cloud, CloudError, stamp
from dedup_live_index import check_paths


def item(path, files=1, folders=0, fingerprint='same'):
    return {'path':path,'files':files,'folders':folders,'bytes':files*4,
            'fingerprint':fingerprint,'protected':''}


def exact(key,paths,files=1,folders=0,fingerprint='same'):
    return {'id':key,'type':'exact','title':paths[0].rsplit('/',1)[-1],
            'copies':[item(p,files,folders,fingerprint) for p in paths],
            'files':files,'bytes':files*4,'nestedUnder':None,'protected':False,
            'reason':'test'}


def dataset(cases):
    return {'cases':cases,'meta':{'protectedPaths':['/00熏听','/06照片','/来自：iPhone','/apps'],
           'preservedRootEntries':['/我的资源','/01恩恩学习资料'],'snapshotFinishedAt':'test'}}


class HighestFolderTests(unittest.TestCase):
    def test_parent_duplicates_hide_all_corresponding_children(self):
        raw=[exact('E1',['/01恩恩学习资料/A','/我的资源/A'],4,4,'parent')]
        for number in range(1,5):raw.append(exact('E'+str(number+1),[f'/01恩恩学习资料/A/{number}',f'/我的资源/A/{number}'],fingerprint=str(number)))
        output,hidden=collapse_exact_folders(raw)
        self.assertEqual([c['id'] for c in output],['E1'])
        self.assertEqual(len(hidden),4)
        plan=make_exact_plan(dataset(output),[{'id':'E1'}])
        self.assertEqual([p['path'] for p in plan['remove']],['/我的资源/A'])

    def test_independent_third_copy_survives_as_only_delete_candidate(self):
        raw=[exact('E1',['/01恩恩学习资料/A','/我的资源/A'],4,4,'parent'),
             exact('E2',['/01恩恩学习资料/A/1','/我的资源/A/1','/其他/独立1'],fingerprint='child')]
        output,_=collapse_exact_folders(raw)
        c=next(c for c in output if c['id']=='E2')
        self.assertEqual([p['path'] for p in c['copies']],['/其他/独立1'])
        plan=make_exact_plan(dataset(output),[{'id':'E1'},{'id':'E2'}])
        self.assertEqual({p['path'] for p in plan['remove']},{'/我的资源/A','/其他/独立1'})
        self.assertEqual(plan['groups'][1]['keep']['path'],'/01恩恩学习资料/A/1')

    def test_changing_parent_keeper_rebases_child_reference(self):
        raw=[exact('E1',['/01恩恩学习资料/A','/我的资源/A'],4,4,'parent'),
             exact('E2',['/01恩恩学习资料/A/1','/我的资源/A/1','/其他/独立1'],fingerprint='child')]
        output,_=collapse_exact_folders(raw)
        plan=make_exact_plan(dataset(output),[{'id':'E1','keep':'/我的资源/A'},{'id':'E2'}])
        self.assertEqual(plan['groups'][1]['keep']['path'],'/我的资源/A/1')
        for removed in plan['remove']:
            self.assertFalse(any(inside(g['keep']['path'],removed['path']) for g in plan['groups']))

    def test_arbitrary_paths_and_contains_are_not_accepted(self):
        output,_=collapse_exact_folders([exact('E1',['/01恩恩学习资料/A','/我的资源/A'])])
        with self.assertRaises(ValueError):make_exact_plan(dataset(output),[{'id':'E1','keep':'/00熏听'}])
        with self.assertRaises(ValueError):make_exact_plan(dataset(output),[{'id':'I0005'}])


class FakeCloud:
    def __init__(self,records):self.records=copy.deepcopy(records);self.deletes=[];self.full_reads=0
    timestamp=staticmethod(stamp)
    def read_tree(self,progress):self.full_reads+=1;progress(1,len(self.records));return Tree(self.records)
    def list_directory(self,path):return [r for r in self.records if (r['path'].rsplit('/',1)[0] or '/')==path]
    def delete(self,paths):
        self.deletes.append(paths)
        self.records=[r for r in self.records if not any(inside(r['path'],p) for p in paths)]
        return {'errno':0}


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        guard=patch('dedup_review_server.require_deletion_enabled')
        guard.start();self.addCleanup(guard.stop)
        self.test_root=Path(__file__).resolve().parent/'test-tmp'
        self.test_root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=self.test_root)
        records=[]
        for path in ['/01恩恩学习资料','/我的资源','/01恩恩学习资料/A','/我的资源/A']:
            records.append({'path':path,'name':path.rsplit('/',1)[-1],'fsid':path,'isdir':1,'size':0,'md5':''})
        for parent in ['/01恩恩学习资料/A','/我的资源/A']:
            records.append({'path':parent+'/lesson.mp3','name':'lesson.mp3','fsid':parent+'/f','isdir':0,'size':4,'md5':'content'})
        self.cloud=FakeCloud(records)
        fingerprint=Tree(records).nodes['/我的资源/A']['fingerprint']
        output,_=collapse_exact_folders([exact('E1',['/01恩恩学习资料/A','/我的资源/A'],fingerprint=fingerprint)])
        self.manager=ReviewManager(dataset(output),cloud_factory=lambda:self.cloud,
                 expected_ids={p:p for p in ['/01恩恩学习资料/A','/我的资源/A']},audit_dir=Path(self.tmp.name))
    def tearDown(self):
        assert Path(self.tmp.name).resolve().is_relative_to(self.test_root.resolve())
        self.tmp.cleanup()
    def prepare(self):
        key=self.manager.start_prepare([{'id':'E1'}],run=False);self.manager.prepare(key);return key
    def test_prepare_and_bad_confirmation_cannot_delete(self):
        key=self.prepare();job=self.manager.public(key)
        self.assertEqual(job['status'],'ready');self.assertEqual(self.cloud.deletes,[])
        with self.assertRaises(ValueError):self.manager.confirm(key,'bad',1,run=False)
        with self.assertRaises(ValueError):self.manager.confirm(key,job['confirmationToken'],99,run=False)
        self.assertEqual(self.cloud.deletes,[])
    def test_live_change_after_confirmation_stops_delete(self):
        key=self.prepare();job=self.manager.public(key)
        self.manager.confirm(key,job['confirmationToken'],1,run=False)
        self.cloud.records[-1]['md5']='changed'
        self.manager.execute(key)
        self.assertEqual(self.cloud.deletes,[])
        self.assertEqual(self.manager.public(key)['status'],'changed')
    def test_confirmed_exact_plan_deletes_only_redundant_root_once(self):
        key=self.prepare();job=self.manager.public(key)
        self.manager.confirm(key,job['confirmationToken'],1,run=False)
        with self.assertRaises(ValueError):self.manager.confirm(key,job['confirmationToken'],1,run=False)
        self.manager.execute(key)
        self.assertEqual(self.cloud.deletes,[['/我的资源/A']])
        result=self.manager.public(key)
        self.assertEqual(result['status'],'done');self.assertEqual(result['result']['retainedChanges'],[])
        self.assertIn('/01恩恩学习资料/A',[p['path'] for p in self.cloud.records])
        self.assertEqual(self.cloud.full_reads,1)

    def test_preview_reuses_inventory_without_reading_cloud(self):
        key=self.prepare()
        self.assertEqual(self.cloud.full_reads,0)
        self.assertEqual(self.manager.public(key)['previewSource'],'local-inventory')

    def test_uncertain_batch_blocks_replaying_same_paths(self):
        key=self.prepare();job=self.manager.public(key)
        self.manager.confirm(key,job['confirmationToken'],1,run=False)
        with patch.object(self.cloud,'delete',side_effect=CloudError('response unconfirmed')):
            self.manager.execute(key)
        self.assertEqual(self.manager.public(key)['result']['pending'],['/我的资源/A'])
        with self.assertRaises(ValueError):self.manager.start_prepare([{'id':'E1'}],run=False)

    def test_async_task_identifier_is_durable_before_result_check(self):
        key=self.prepare();job=self.manager.public(key)
        self.manager.confirm(key,job['confirmationToken'],1,run=False)
        original=self.cloud.delete
        def asynchronous(paths):
            original(paths)
            return {'errno':0,'taskId':'123456'}
        with patch.object(self.cloud,'delete',side_effect=asynchronous):self.manager.execute(key)
        import json
        saved=json.loads((Path(self.tmp.name)/(key+'.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['events'][0]['response']['taskId'],'123456')
        self.assertEqual(saved['result']['removed'],['/我的资源/A'])

    def test_read_error_cannot_be_interpreted_as_absence(self):
        with patch.object(self.cloud,'list_directory',side_effect=CloudError('read failed')):
            with self.assertRaises(CloudError):check_paths(self.cloud,['/我的资源/A'],{},Path(self.tmp.name)/'check')

    def test_missing_parent_is_proved_from_ancestor_listing(self):
        self.cloud.records=[r for r in self.cloud.records if not inside(r['path'],'/我的资源')]
        result=check_paths(self.cloud,['/我的资源/A'],{},Path(self.tmp.name)/'check')
        self.assertEqual(result['states'][0]['state'],'absent')
        self.assertEqual(result['parentReads'],1)

    def test_same_content_with_replaced_folder_id_is_blocked(self):
        key=self.prepare();job=self.manager.public(key)
        self.manager.confirm(key,job['confirmationToken'],1,run=False)
        next(r for r in self.cloud.records if r['path']=='/我的资源/A')['fsid']='replacement'
        self.manager.execute(key)
        self.assertEqual(self.cloud.deletes,[])
        self.assertEqual(self.manager.public(key)['status'],'changed')

    def test_expired_confirmation_cannot_start_delete(self):
        key=self.prepare();job=self.manager.public(key)
        self.manager.jobs[key]['expires']=0
        with self.assertRaises(ValueError):self.manager.confirm(key,job['confirmationToken'],1,run=False)
        self.assertEqual(self.cloud.deletes,[])

    def test_audit_failure_blocks_all_cloud_mutations(self):
        key=self.prepare();job=self.manager.public(key)
        with patch.object(self.manager,'audit',side_effect=OSError('test log unavailable')):
            with self.assertRaises(ValueError):self.manager.confirm(key,job['confirmationToken'],1,run=False)
        self.assertEqual(self.cloud.deletes,[])
        self.assertIsNone(self.manager.active_commit)

    def test_frequency_limit_retry_only_performs_reads(self):
        cloud=Cloud.__new__(Cloud);cloud.token='synthetic-test-token'
        raw=[{'path':r['path'],'fs_id':r['fsid'],'server_filename':r['name'],
              'isdir':r['isdir'],'size':r['size'],'md5':r['md5']} for r in self.cloud.records]
        limited=Mock(status_code=400);limited.json.return_value={'errno':31034}
        success=Mock(status_code=200);success.json.return_value={'errno':0,'list':raw,'has_more':False}
        cloud.session=Mock();cloud.session.get.side_effect=[limited,success]
        progress=Mock()
        with patch('dedup_review_server.cloud_slot'),patch('dedup_review_server.time.sleep') as sleep:
            tree=cloud.read_tree(progress)
        self.assertIn('/我的资源/A',tree.nodes)
        self.assertEqual(cloud.session.get.call_count,2)
        cloud.session.post.assert_not_called()
        sleep.assert_called_once_with(5)

    def test_cloud_async_acknowledgement_preserves_task_id_without_reposting(self):
        cloud=Cloud.__new__(Cloud);cloud.token='synthetic-test-token';cloud.session=Mock()
        response=Mock(status_code=200);response.json.return_value={'errno':0,'taskid':987654,'request_id':123}
        cloud.session.post.return_value=response
        with patch('dedup_review_server.cloud_slot'):result=cloud.delete(['/我的资源/A'])
        self.assertEqual(result['taskId'],'987654')
        self.assertEqual(cloud.session.post.call_count,1)

    def test_inconsistent_live_scan_keeps_page_evidence_on_disk(self):
        import json
        cloud=Cloud.__new__(Cloud);cloud.token='synthetic-test-token';cloud.session=Mock()
        cloud.cache_dir=Path(self.tmp.name)/'live-cache'
        response=Mock(status_code=200)
        response.json.return_value={'errno':0,'list':[{'path':'/missing/a.mp3','fs_id':9,
                'server_filename':'a.mp3','isdir':0,'size':4,'md5':'content'}],'has_more':False}
        cloud.session.get.return_value=response
        with patch('dedup_review_server.cloud_slot'):
            with self.assertRaises(CloudError):cloud.read_tree(Mock())
        scan_dir=next(cloud.cache_dir.glob('scan-*'))
        self.assertEqual(json.loads((scan_dir/'progress.json').read_text(encoding='utf-8'))['status'],'inconsistent')
        self.assertEqual(json.loads((scan_dir/'page-00001.json').read_text(encoding='utf-8'))['items'][0]['path'],'/missing/a.mp3')

    def test_running_async_batch_finishes_then_automatically_submits_next_batch(self):
        records=[r for r in self.cloud.records if r['path'] in ['/01恩恩学习资料','/我的资源','/01恩恩学习资料/A','/01恩恩学习资料/A/lesson.mp3']]
        copies=['/01恩恩学习资料/A']+[f'/我的资源/copy{i}' for i in range(101)]
        for root in copies[1:]:
            records.extend([{'path':root,'name':root.rsplit('/',1)[1],'fsid':root,'isdir':1,'size':0,'md5':''},
                 {'path':root+'/lesson.mp3','name':'lesson.mp3','fsid':root+'/f','isdir':0,'size':4,'md5':'content'}])
        cloud=FakeCloud(records);pending={};polls={}
        def submit(paths):
            key=str(len(cloud.deletes)+1);cloud.deletes.append(paths);pending[key]=paths
            return {'errno':0,'taskId':key}
        def query(key):
            polls[key]=polls.get(key,0)+1
            if polls[key]==1:return {'status':'running','task_errno':0,'progress':50}
            cloud.records=[r for r in cloud.records if not any(inside(r['path'],p) for p in pending[key])]
            return {'status':'success','task_errno':0,'total':len(pending[key]),'list':[{'path':p,'fs_id':p} for p in pending[key]]}
        cloud.delete=submit;cloud.task_status=query
        fingerprint=Tree(records).nodes[copies[0]]['fingerprint']
        output,_=collapse_exact_folders([exact('Ebulk',copies,fingerprint=fingerprint)])
        manager=ReviewManager(dataset(output),cloud_factory=lambda:cloud,expected_ids={p:p for p in copies},audit_dir=Path(self.tmp.name)/'async')
        key=manager.start_prepare([{'id':'Ebulk'}],run=False);manager.prepare(key);ready=manager.public(key)
        manager.confirm(key,ready['confirmationToken'],101,run=False)
        with patch('dedup_review_server.time.sleep'):manager.execute(key)
        job=manager.public(key)
        self.assertEqual(job['status'],'done')
        self.assertEqual([len(p) for p in cloud.deletes],[100,1])
        self.assertEqual(len(job['result']['removed']),101)
        self.assertEqual(job['result']['remaining'],[])
        self.assertEqual(cloud.full_reads,1)
        self.assertEqual(polls,{'1':2,'2':2})

    def test_resume_never_reposts_a_previously_accepted_batch(self):
        key=self.prepare();job=self.manager.public(key)
        self.manager.confirm(key,job['confirmationToken'],1,run=False)
        self.manager.jobs[key].update(status='partial',events=[{'paths':['/我的资源/A'],'status':'async','response':{'taskId':'existing'}}])
        self.manager.active_commit=None
        self.cloud.task_status=lambda task:{'status':'success','task_errno':0,'total':1,'list':[{'path':'/我的资源/A','fs_id':'/我的资源/A'}]}
        self.cloud.records=[r for r in self.cloud.records if not inside(r['path'],'/我的资源/A')]
        self.manager.resume_confirmed(key,run=False);self.manager.execute(key)
        self.assertEqual(self.cloud.deletes,[])
        self.assertEqual(self.manager.public(key)['status'],'done')

    def test_async_task_failure_preserves_pending_and_stops_next_batches(self):
        key=self.prepare();job=self.manager.public(key)
        self.manager.confirm(key,job['confirmationToken'],1,run=False)
        self.cloud.delete=lambda paths:{'errno':0,'taskId':'failed-task'}
        self.cloud.task_status=lambda task:{'status':'failed','task_errno':12}
        self.manager.execute(key)
        job=self.manager.public(key)
        self.assertEqual(job['status'],'partial')
        self.assertEqual(job['result']['pending'],['/我的资源/A'])
        self.assertEqual(job['result']['removed'],[])

    def test_resume_without_original_final_confirmation_is_rejected(self):
        key=self.prepare()
        with self.assertRaises(ValueError):self.manager.resume_confirmed(key,run=False)

    def containment_manager(self,invalid=False):
        cloud=FakeCloud(self.cloud.records)
        # Retain an additional file in the larger package. In the invalid case,
        # rename the original lesson so it no longer covers the smaller path.
        if invalid:cloud.records[4].update(path='/01恩恩学习资料/A/renamed.mp3',name='renamed.mp3')
        cloud.records.append({'path':'/01恩恩学习资料/A/extra.pdf','name':'extra.pdf','fsid':'extra',
                              'isdir':0,'size':9,'md5':'extra-content'})
        tree=Tree(cloud.records)
        small=item('/我的资源/A',fingerprint=tree.nodes['/我的资源/A']['fingerprint'])
        large=item('/01恩恩学习资料/A',files=2,fingerprint=tree.nodes['/01恩恩学习资料/A']['fingerprint'])
        case={'id':'I1','type':'contains','title':'Course','smaller':[small],'larger':[large],
              'protected':False,'reviewState':'pending','defaultKeep':large['path']}
        manager=ReviewManager(dataset([case]),cloud_factory=lambda:cloud,
             expected_ids={p:p for p in [small['path'],large['path']]},audit_dir=Path(self.tmp.name)/'contains')
        return manager,cloud,case

    def test_strict_containment_requires_confirmation_then_only_removes_smaller(self):
        manager,cloud,_=self.containment_manager()
        key=manager.start_prepare([{'id':'I1'}],run=False);manager.prepare(key);job=manager.public(key)
        self.assertEqual(cloud.deletes,[])
        self.assertEqual(job['plan']['containmentGroupCount'],1)
        manager.confirm(key,job['confirmationToken'],1,run=False);manager.execute(key)
        self.assertEqual(cloud.deletes,[['/我的资源/A']])
        self.assertEqual(manager.public(key)['status'],'done')
        self.assertIn('/01恩恩学习资料/A/extra.pdf',[r['path'] for r in cloud.records])

    def test_invalid_live_containment_cannot_delete_even_when_fingerprints_match_plan(self):
        manager,cloud,_=self.containment_manager(invalid=True)
        key=manager.start_prepare([{'id':'I1'}],run=False);manager.prepare(key);job=manager.public(key)
        manager.confirm(key,job['confirmationToken'],1,run=False);manager.execute(key)
        self.assertEqual(cloud.deletes,[])
        self.assertEqual(manager.public(key)['status'],'changed')

    def test_manual_review_and_completed_cases_are_not_actionable(self):
        manager,_,case=self.containment_manager()
        for state in ['review','completed']:
            case['reviewState']=state
            with self.assertRaises(ValueError):make_deletion_plan(dataset([case]),[{'id':'I1'}])

    def test_containment_cannot_choose_smaller_as_keeper(self):
        manager,_,_=self.containment_manager()
        with self.assertRaises(ValueError):manager.start_prepare([{'id':'I1','keep':'/我的资源/A'}],run=False)


if __name__=='__main__':unittest.main()
