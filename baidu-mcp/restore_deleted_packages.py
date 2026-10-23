"""Undo only the 468 audited folder removals, using native recycle restore.

Never deletes, overwrites, or retries a mutating request. Durable request intent
precedes transmission; unconfirmed batches require read-only reconciliation.
"""
import json
import sqlite3
from pathlib import Path

from dedup_review_server import Cloud, OUT, cloud_slot, stamp
from dedup_live_index import atomic_json

RECOVERY = OUT / '恢复记录'


def restore_response_ok(data, batch):
    if data.get('errno', data.get('error_code', 0)) != 0:
        return False
    actual = data.get('extra', {}).get('list', [])
    expected = {str(x['fsid']): x['path'] for x in batch}
    return len(actual) == len(batch) and {
        str(x.get('fs_id')): x.get('path') for x in actual
    } == expected


def run():
    manifest = json.loads((RECOVERY / '恢复清单.json').read_text(encoding='utf-8'))
    roots = manifest['roots']
    assert len(roots) == len({x['fsid'] for x in roots}) == 468
    assert (OUT / '删除已停用.json').exists()
    cloud = Cloud()
    present = cloud.metadata_by_id([x['fsid'] for x in roots])
    conflicts = [x['path'] for x in roots if x['fsid'] in present and
                 present[x['fsid']]['path'] != x['path']]
    if conflicts:
        atomic_json(RECOVERY / '恢复冲突.json', conflicts)
        raise ValueError('Original folder identity is now at a different path; stopped.')
    pending = [x for x in roots if x['fsid'] not in present]
    print(json.dumps({'alreadyPresent':len(roots)-len(pending),'pending':len(pending)}), flush=True)
    # An existing intent without a verified response cannot be retried.
    prior_ids = {x['fsid'] for f in RECOVERY.glob('batch-*.json')
                 for x in json.loads(f.read_text(encoding='utf-8'))['roots']}
    if prior_ids.intersection(x['fsid'] for x in pending):
        raise ValueError('Prior restore request requires read-only reconciliation; no retry.')
    for start in range(0, len(pending), 50):
        batch = pending[start:start+50]
        record = {'operation':'restore','createdAt':stamp(),'status':'submitting','roots':batch}
        target = RECOVERY / ('batch-'+batch[0]['fsid']+'.json')
        atomic_json(target, record)
        try:
            cloud_slot()
            response = cloud.session.post('https://pcs.baidu.com/rest/2.0/pcs/file',
                params={'method':'restore','access_token':cloud.token},
                data={'param':json.dumps({'list':[{'fs_id':int(x['fsid'])} for x in batch]},
                                        ensure_ascii=False,separators=(',',':'))}, timeout=(10,55))
            data = response.json()
        except Exception as exc:
            record.update(status='unconfirmed',exceptionType=type(exc).__name__)
            atomic_json(target,record)
            raise ValueError('Restore response uncertain; request was not retried.') from None
        record.update(http=response.status_code,response={k:v for k,v in data.items()
            if k in ('errno','error_code','error_msg','request_id','extra','taskid')})
        good = response.status_code == 200 and restore_response_ok(data,batch)
        record.update(status='native-restored' if good else 'needs-reconciliation',respondedAt=stamp())
        atomic_json(target,record)
        if not good:
            raise ValueError('Restore response did not confirm the exact original IDs and paths.')
        print(json.dumps({'restoredThisRun':start+len(batch),'remaining':len(pending)-start-len(batch)}),flush=True)
    present=cloud.metadata_by_id([x['fsid'] for x in roots])
    missing=[x['path'] for x in roots if present.get(x['fsid'],{}).get('path') != x['path']]
    atomic_json(RECOVERY/'根目录恢复核验.json',{'checkedAt':stamp(),'rootCount':len(roots),
        'verifiedOriginalIds':len(roots)-len(missing),'missing':missing,'subtreeVerification':'pending'})
    if missing:raise ValueError('Some original roots are still absent; subtree verification not started.')
    print('All 468 original folder IDs and paths verified. Scanning full live tree once.',flush=True)
    tree=cloud.read_tree(lambda page,entries,note=None:print(json.dumps(
        {'scanPage':page,'entries':entries}),flush=True))
    db=sqlite3.connect('file:'+str(OUT/'inventory.sqlite')+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    baseline={r['path']:dict(r) for r in db.execute('select * from nodes')}
    expected={p:r for p,r in baseline.items() if any(p==x['path'] or p.startswith(x['path']+'/') for x in roots)}
    discrepancies=[]
    for p,r in expected.items():
        live=tree.paths.get(p)
        if live is None:discrepancies.append({'path':p,'reason':'missing'})
        elif str(live['fsid'])!=str(r['fsid']) or live['isdir']!=r['isdir'] or (
            not r['isdir'] and (live['size']!=r['size'] or live.get('md5')!=r['md5'])):
            discrepancies.append({'path':p,'reason':'identity-or-content-different'})
    extras=[p for p in tree.paths if p not in expected and any(
        p==x['path'] or p.startswith(x['path']+'/') for x in roots)]
    result={'checkedAt':stamp(),'status':'verified' if not discrepancies and not extras else 'differences',
        'rootCount':len(roots),'expectedFiles':sum(not x['isdir'] for x in expected.values()),
        'expectedFolders':sum(x['isdir'] for x in expected.values()),'verifiedEntries':len(expected)-len(discrepancies),
        'discrepancies':discrepancies,'extraPaths':extras,'verification':'Original path, fsid, type, size, content hash; full live tree after restore.'}
    atomic_json(RECOVERY/'完整恢复核验.json',result)
    if discrepancies or extras:raise ValueError('Restore subtree differs from baseline; see verification report.')
    statefile=OUT/'实时索引/known-deletions.json'
    old=json.loads(statefile.read_text(encoding='utf-8'))
    if not (RECOVERY/'恢复前删除状态.json').exists():atomic_json(RECOVERY/'恢复前删除状态.json',old)
    old.update(removed=[],pending=[],notSubmitted=[],restored=[x['path'] for x in roots],
        rollbackVerifiedAt=stamp(),rollbackStatus='verified',previouslyRemovedCount=len(roots))
    atomic_json(statefile,old)
    print(json.dumps({k:v for k,v in result.items() if k not in ('discrepancies','extraPaths')},ensure_ascii=True),flush=True)


if __name__=='__main__':
    try:run()
    except Exception as exc:
        print(json.dumps({'status':'stopped','exceptionType':type(exc).__name__,
            'message':str(exc) if isinstance(exc,ValueError) else 'No credentials or raw network errors are logged.'}),flush=True)
        raise SystemExit(1)
