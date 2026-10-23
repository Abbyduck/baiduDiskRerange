"""Cross-check and complete the inventory using Baidu's cursor-based listall API."""
import collections
import json
import sqlite3
import time
from pathlib import PurePosixPath

import requests
from crawl_inventory import OUT, credential, now, progress
PAGE_SIZE=10000

token=credential()
session=requests.Session()
start=0
pages=0
seen=set()
records=[]
started=now()
cache=OUT/'recursive-pages';cache.mkdir(exist_ok=True)
has_more=True
for saved_page in sorted(cache.glob('page-*.json')):
    saved=json.loads(saved_page.read_text(encoding='utf-8'))
    for row in saved['records']:
        if row[0] in seen:raise SystemExit('Duplicate ID in saved recursive pages')
        seen.add(row[0]);records.append(tuple(row))
    pages+=1;start=saved['cursor'];has_more=saved['hasMore']
while has_more:
    data=None
    first_cache=OUT/'recursive-large-first-page.json'
    if not pages and first_cache.exists():data=json.loads(first_cache.read_text(encoding='utf-8'))
    for attempt in range(12):
        if data is not None:break
        try:
            response=session.get('https://pan.baidu.com/rest/2.0/xpan/multimedia',params={
                'method':'listall','path':'/','recursion':1,'start':start,'limit':PAGE_SIZE,
                'order':'name','desc':0,'web':0,'access_token':token},timeout=(10,40))
            candidate=response.json()
            if response.status_code==200 and candidate.get('errno')==0:
                data=candidate;break
            print(json.dumps({'retryPageStart':start,'httpStatus':response.status_code,'errno':candidate.get('errno'),'attempt':attempt+1}),flush=True)
            if candidate.get('errno') in (-6,110,111):raise SystemExit('Recursive API authorization failed')
        except Exception as exc:
            print(json.dumps({'retryPageStart':start,'errorType':type(exc).__name__,'attempt':attempt+1}),flush=True)
        time.sleep(min(2**attempt,20))
    if data is None:raise SystemExit('Recursive API page failed; existing directory inventory is preserved.')
    items=data.get('list')
    if not isinstance(items,list) or 'has_more' not in data:raise SystemExit('Unexpected recursive page schema')
    pages+=1
    page_records=[]
    for item in items:
        fsid=str(item['fs_id']);path=item['path']
        if path=='/':continue
        if fsid in seen:raise SystemExit('Repeated file ID in recursive pagination; report not finalized.')
        if item.get('isdir') not in (0,1) or not path.startswith('/'):raise SystemExit('Malformed recursive metadata')
        seen.add(fsid)
        normalized=(fsid,path,str(PurePosixPath(path).parent),item.get('server_filename',PurePosixPath(path).name),
                        item['isdir'],item.get('size',0),item.get('category'),item.get('md5',''),
                        item.get('server_mtime'),item.get('server_ctime'),now())
        records.append(normalized);page_records.append(normalized)
    has_more=bool(data['has_more'])
    saved={'cursor':data.get('cursor'),'hasMore':has_more,'records':page_records}
    (cache/f'page-{pages:05d}.json').write_text(json.dumps(saved,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    state={'method':'recursive_listall','pageSize':PAGE_SIZE,'pages':pages,'entries':len(records),'hasMore':has_more,'updatedAt':now()}
    (OUT/'recursive-progress.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(state),flush=True)
    if not has_more:break
    cursor=data.get('cursor')
    if not isinstance(cursor,int) or cursor<=start:raise SystemExit('Recursive cursor did not advance')
    start=cursor
    time.sleep(0.40)

db=sqlite3.connect(OUT/'inventory.sqlite')
db.execute('PRAGMA journal_mode=WAL')
old_ids={str(row[0]) for row in db.execute('select fsid from nodes')}
missing=old_ids-seen
audit={'method':'recursive_listall','startedAt':started,'finishedAt':now(),'pages':pages,
       'recursiveEntries':len(records),'previousEntries':len(old_ids),'previousEntriesMissing':len(missing),
       'previousDirectoriesRead':db.execute("select count(*) from directories where status='done'").fetchone()[0]}
if missing:
    audit['missingPaths']=[row[0] for fsid in missing for row in db.execute('select path from nodes where fsid=?',(fsid,))]
    (OUT/'recursive-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    raise SystemExit('Recursive list omitted previously seen entries. Existing scan was preserved for investigation.')
all_paths={row[1] for row in records if row[4]}|{'/'}
if any(row[2] not in all_paths for row in records):raise SystemExit('Recursive tree has missing parents')
backup=sqlite3.connect(OUT/'inventory_directory_walk.sqlite')
db.backup(backup);backup.close()
db.executemany('INSERT OR REPLACE INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?)',records)
columns={row[1] for row in db.execute('pragma table_info(directories)')}
if 'scan_source' not in columns:db.execute('alter table directories add column scan_source TEXT')
db.execute("UPDATE directories SET scan_source=CASE WHEN status='done' THEN 'direct_list+recursive_listall' ELSE 'recursive_listall' END")
for path in sorted(all_paths):
    db.execute("INSERT OR IGNORE INTO directories(path,depth,status,scan_source) VALUES(?,?,'done','recursive_listall')",(path,0 if path=='/' else path.count('/')))
child_counts=collections.defaultdict(lambda:[0,0])
for row in records:child_counts[row[2]][row[4]]+=1
stamp=now()
db.executemany("UPDATE directories SET status='done',error=NULL,child_files=?,child_dirs=?,listed_at=? WHERE path=?",
               [(child_counts[path][0],child_counts[path][1],stamp,path) for path in all_paths])
db.execute("INSERT OR REPLACE INTO run_info VALUES('recursive_verification',?)",(json.dumps(audit,ensure_ascii=False),))
db.execute("INSERT OR REPLACE INTO run_info VALUES('finished_at',?)",(stamp,))
db.commit()
fields=[row[1] for row in db.execute('pragma table_info(nodes)')]
with (OUT/'all-files.jsonl').open('w',encoding='utf-8') as output:
    for row in db.execute('select * from nodes order by path'):
        output.write(json.dumps(dict(zip(fields,row)),ensure_ascii=False)+'\n')
(OUT/'recursive-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(progress(db,finished=True),ensure_ascii=False),flush=True)
db.close()
