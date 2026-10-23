import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from package_boundaries import infer_roots,package_owner,exact_groups
from dedup_review_server import Tree,Cloud,ReviewManager,require_deletion_enabled
from restore_deleted_packages import restore_response_ok


def record(path,isdir=1,md5='x'):
    return {'path':path,'name':path.rsplit('/',1)[-1],'fsid':path,'isdir':isdir,
            'size':0 if isdir else 3,'md5':'' if isdir else md5}


class PackageBoundaryTests(unittest.TestCase):
    def test_internal_week_is_hidden_even_when_parent_packages_differ(self):
        dirs=['/分类','/分类/牛津树','/分类/牛津树/Week 1','/我的资源',
              '/我的资源/牛津树','/我的资源/牛津树/Week 1']
        rs=[record(p) for p in dirs]
        rs += [record('/分类/牛津树/Week 1/a.mp3',0),record('/我的资源/牛津树/Week 1/a.mp3',0),
               record('/分类/牛津树/extra.pdf',0)]
        tree=Tree(rs)
        roots=infer_roots(dirs,{'/':'root','/分类':'分类','/我的资源':'收件箱'})
        stats={p:{'files':1} for p in dirs}
        self.assertEqual(set(roots),{'/分类/牛津树','/我的资源/牛津树'})
        self.assertEqual(package_owner('/分类/牛津树/Week 1',roots),'/分类/牛津树')
        self.assertEqual(exact_groups(tree,roots,stats),[])

    def test_subfolder_of_package_cannot_reference_independent_root_for_deletion(self):
        dirs=['/分类','/分类/整包','/分类/整包/视频','/我的资源','/我的资源/视频']
        rs=[record(p) for p in dirs]+[record(p+'/a.mp4',0) for p in ('/分类/整包/视频','/我的资源/视频')]
        tree=Tree(rs);roots=infer_roots(dirs,{'/':'root','/分类':'分类','/我的资源':'收件箱'})
        self.assertEqual(exact_groups(tree,roots,{p:{'files':1} for p in dirs}),[])

    def test_only_both_independent_whole_packages_are_grouped(self):
        dirs=['/分类','/分类/A','/分类/A/视频','/我的资源','/我的资源/A','/我的资源/A/视频']
        rs=[record(p) for p in dirs]+[record(p+'/lesson.mp4',0) for p in ('/分类/A/视频','/我的资源/A/视频')]
        tree=Tree(rs);roots=infer_roots(dirs,{'/':'root','/分类':'分类','/我的资源':'收件箱'})
        self.assertEqual(exact_groups(tree,roots,{p:{'files':1} for p in dirs}),[['/分类/A','/我的资源/A']])

    def test_known_nested_container_cannot_break_ancestor_package(self):
        dirs=['/我的资源','/我的资源/合集','/我的资源/合集/音频','/我的资源/合集/音频/A']
        roots=infer_roots(dirs,{'/':'root','/我的资源':'inbox','/我的资源/合集/音频':'format'})
        self.assertEqual(set(roots),{'/我的资源/合集'})

    def test_restore_response_requires_exact_original_identity_and_path(self):
        batch=[{'fsid':'123','path':'/我的资源/A'}]
        good={'extra':{'list':[{'fs_id':123,'path':'/我的资源/A'}]}}
        self.assertTrue(restore_response_ok(good,batch))
        self.assertFalse(restore_response_ok({'error_code':31066},batch))
        self.assertFalse(restore_response_ok({'extra':{'list':[{'fs_id':123,'path':'/我的资源/A(1)'}]}},batch))
        self.assertFalse(restore_response_ok({'extra':{'list':[{'fs_id':456,'path':'/我的资源/A'}]}},batch))

    def test_persistent_lock_blocks_all_delete_entrypoints_before_cloud_calls(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            lock=Path(tmp)/'locked.json';lock.write_text('{}')
            with patch('dedup_review_server.DELETE_LOCK',lock):
                for operation in (require_deletion_enabled,
                                  lambda:Cloud.delete(object(),['/anything']),
                                  lambda:ReviewManager.start_prepare(object(),[]),
                                  lambda:ReviewManager.confirm(object(),'id','token',1),
                                  lambda:ReviewManager.resume_confirmed(object(),'id'),
                                  lambda:ReviewManager.execute(object(),'id')):
                    with self.assertRaisesRegex(ValueError,'删除已停用'):operation()


if __name__=='__main__':unittest.main()
