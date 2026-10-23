"""Read-only recovery of submitted batches; never repeats a delete request."""
import argparse
import json
import sqlite3
from pathlib import Path

from dedup_review_server import Cloud, CloudError, OUT, stamp
from dedup_live_index import atomic_json, check_paths


def reconcile(job_id):
    audit_path=OUT/'去重执行记录'/(job_id+'.json')
    original=json.loads(audit_path.read_text(encoding='utf-8'))
    submitted=list(dict.fromkeys(p for event in original.get('events',[]) for p in event['paths']))
    if not submitted:raise ValueError('没有已提交的路径。')
    connection=sqlite3.connect(f'file:{(OUT/"inventory.sqlite").as_posix()}?mode=ro',uri=True)
    expected=dict(connection.execute('select path,fsid from nodes where isdir=1'));connection.close()
    result_path=OUT/'去重执行记录'/(job_id+'-reconciliation.json')
    cache=OUT/'实时索引'/('reconcile-'+job_id)
    atomic_json(result_path,{'jobId':job_id,'status':'checking','startedAt':stamp(),'submittedCount':len(submitted)})
    cloud=Cloud()
    def progress(reads,entries,note=None):
        print(json.dumps({'parentReads':reads,'checkedTargets':entries,'message':note},ensure_ascii=False),flush=True)
    result=check_paths(cloud,submitted,expected,cache,progress)
    keepers=list(dict.fromkeys(g['keep']['path'] for g in original['plan']['groups']))
    keeper_result=check_paths(cloud,keepers,expected,OUT/'实时索引'/('keepers-'+job_id),progress)
    removed=[x['path'] for x in result['states'] if x['state']=='absent']
    remaining=[x['path'] for x in result['states'] if x['state']!='absent']
    unsubmitted=[x['path'] for x in original['plan']['remove'] if x['path'] not in submitted]
    result.update(jobId=job_id,status='verified' if not remaining else 'partial',removed=removed,
                  remainingSubmitted=remaining,notSubmitted=unsubmitted,submittedCount=len(submitted),
                  retainedChanges=[x['path'] for x in keeper_result['states'] if x['state']!='present'],
                  keeperCheck=keeper_result,
                  note='只读核对路径是否仍存在；原始完整盘点和原执行记录保持保留。')
    atomic_json(result_path,result)
    known_path=OUT/'实时索引'/'known-deletions.json'
    known=json.loads(known_path.read_text(encoding='utf-8')) if known_path.exists() else {}
    atomic_json(known_path,{'jobId':job_id,'checkedAt':stamp(),
                'removed':sorted(set(known.get('removed',[])+removed)),
                'pending':sorted(set([p for p in known.get('pending',[]) if p not in submitted]+remaining)),
                'notSubmitted':unsubmitted,
                'baselineUnchanged':True,'scope':'Only this previously submitted batch has been reconciled.'})
    print(json.dumps({'status':result['status'],'removed':len(removed),'remainingSubmitted':len(remaining),
                      'notSubmitted':len(unsubmitted),'parentReads':result['parentReads']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('job_id');args=parser.parse_args()
    if len(args.job_id)!=24 or any(x not in '0123456789abcdef' for x in args.job_id):
        raise SystemExit('Invalid audit ID')
    try:reconcile(args.job_id)
    except (CloudError,ValueError) as exc:print(str(exc),flush=True);raise SystemExit(1)
    except Exception as exc:print('Read-only recovery stopped: '+type(exc).__name__,flush=True);raise SystemExit(1)
