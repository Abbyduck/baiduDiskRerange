import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dedup_review_server import Tree
from package_boundaries import infer_roots
from package_containment import find_package_coverage
from package_delete_service import PackageReviewManager
from test_folder_dedup_actions import FakeCloud
from test_package_boundaries import record


def sample(extra=False):
    source='/我的资源/课程A';keeper='/01恩恩学习资料/合集';match=keeper+'/已收录课程A'
    rs=[record(p) for p in ['/我的资源','/01恩恩学习资料',source,keeper,match]]
    rs += [record(source+'/lesson.mp4',0),record(match+'/lesson.mp4',0)]
    if extra:rs.append(record(match+'/handout.pdf',0))
    tree=Tree(rs)
    for p in sorted(tree.nodes,key=lambda p:p.count('/') if p!='/' else 0,reverse=True):
        node=tree.nodes[p];node.update(files=0,folders=0,bytes=0)
        for x in node['children'].values():
            if x['isdir']:
                n=tree.nodes[x['path']];node['files']+=n['files'];node['folders']+=1+n['folders'];node['bytes']+=n['bytes']
            else:node['files']+=1;node['bytes']+=x['size']
    roots=infer_roots(tree.nodes)
    def unit(p):return {k:tree.nodes[p][k] for k in ('path','fsid','fingerprint','files','folders','bytes')}|{'protected':False}
    data={'meta':{'createdAt':'test','sourceScan':{'completedAt':'test'}},'packages':[unit(source),unit(keeper)],'exact':[],
          'contains':[{'id':'S1','type':'contains','title':'课程A','copies':[unit(keeper),unit(source)],
            'matchWithin':unit(match)}]}
    return rs,tree,roots,data,source,keeper,match


class CoverageFinderTests(unittest.TestCase):
    def test_independent_root_covered_by_internal_directory(self):
        _,t,roots,_,source,keeper,match=sample()
        cases=find_package_coverage(t,roots,t.nodes)
        self.assertEqual([c['source'] for c in cases],[source])
        self.assertEqual(cases[0]['reference']['keeper'],keeper)
        self.assertEqual(cases[0]['reference']['matchPath'],match)
        self.assertTrue(cases[0]['reference']['exactAtMatch'])
        self.assertNotIn(match,[c['source'] for c in cases])

    def test_strict_subset_in_internal_position_is_found(self):
        _,t,roots,_,source,_,_=sample(extra=True)
        cases=find_package_coverage(t,roots,t.nodes)
        self.assertEqual([c['source'] for c in cases],[source])
        self.assertFalse(cases[0]['reference']['exactAtMatch'])
        self.assertEqual(cases[0]['reference']['matchFiles'],2)

    def test_partial_contents_or_wrong_relative_names_not_coverage(self):
        for changed in ('hash','name'):
            rs,_,_,_,_,_,match=sample()
            row=next(x for x in rs if x['path']==match+'/lesson.mp4')
            if changed=='hash':row['md5']='different'
            else:row.update(path=match+'/renamed.mp4',name='renamed.mp4')
            t=Tree(rs)
            # stats remain one file at each package and position.
            for n in t.nodes.values():n.update(files=1,folders=1,bytes=3)
            self.assertEqual(find_package_coverage(t,infer_roots(t.nodes),t.nodes),[])


class CoveredPackageActionTests(unittest.TestCase):
    def setUp(self):
        rs,_,_,data,self.source,self.keeper,self.match=sample()
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.cloud=FakeCloud(rs)
        self.m=PackageReviewManager(data,cloud_factory=lambda:self.cloud,audit_dir=Path(self.tmp.name))
        guard=patch('dedup_review_server.require_deletion_enabled');guard.start();self.addCleanup(guard.stop)

    def prepare(self):
        key=self.m.start_prepare([{'id':'S1'}],run=False);self.m.prepare(key)
        j=self.m.jobs[key];self.m.confirm(key,j['confirmationToken'],1,run=False)
        return key

    def test_only_independent_root_deleted_and_whole_keeper_preserved(self):
        key=self.prepare();self.assertEqual(self.cloud.deletes,[])
        self.m.execute(key)
        self.assertEqual(self.cloud.deletes,[[self.source]])
        self.assertTrue(any(x['path']==self.match+'/lesson.mp4' for x in self.cloud.records))
        self.assertEqual(self.m.jobs[key]['status'],'done')

    def test_changed_corresponding_directory_identity_prevents_delete(self):
        key=self.prepare()
        next(x for x in self.cloud.records if x['path']==self.match)['fsid']='new-id'
        self.m.execute(key)
        self.assertEqual(self.cloud.deletes,[]);self.assertEqual(self.m.jobs[key]['status'],'changed')

    def test_corresponding_position_cannot_be_submitted_as_keeper_or_removal(self):
        with self.assertRaises(ValueError):self.m.start_prepare([{'id':'S1','keep':self.match}],run=False)
        key=self.m.start_prepare([{'id':'S1'}],run=False);self.m.prepare(key);j=self.m.jobs[key]
        j['plan']['groups'][0]['remove'][0]['path']=self.match
        with self.assertRaises(ValueError):self.m.confirm(key,j['confirmationToken'],1,run=False)
        self.assertEqual(self.cloud.deletes,[])


if __name__=='__main__':unittest.main()
