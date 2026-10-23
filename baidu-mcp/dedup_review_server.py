"""Loopback-only review service. Cloud deletes require a prepared, confirmed ticket.

The server never accepts arbitrary delete paths. It resolves exact-case IDs from
the trusted report, rechecks the live cloud tree before preview and execution,
and records each request/outcome. Merely opening a page cannot mutate Netdisk.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import mimetypes
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests
from analyze_folder_duplicates import digest, normalized_title
from crawl_inventory import credential
from folder_dedup_plan import inside, make_exact_plan, make_deletion_plan
from dedup_live_index import atomic_json, metadata, check_paths

ROOT = Path(__file__).resolve().parents[1]
from project_paths import DATA_DIR

OUT = DATA_DIR
DELETE_LOCK = OUT / '删除已停用.json'


def package_deletion_mode():
    if not DELETE_LOCK.exists():return False
    try:
        policy=json.loads(DELETE_LOCK.read_text(encoding='utf-8'))
        return policy.get('disabled') is False and policy.get('mode')=='package-roots-only'
    except (OSError,ValueError):return False


def require_deletion_enabled(paths=None):
    if DELETE_LOCK.exists():
        if not package_deletion_mode():
            raise ValueError('删除已停用：正在撤销原删除并重新核对转存整包边界。')
        if paths is not None:
            data=json.loads((OUT/'整包去重清单.json').read_text(encoding='utf-8'))
            roots={x['path'] for x in data['packages'] if not x['protected']}
            candidates={x['path'] for c in data['exact']+data['contains'] for x in c['copies']}
            if not paths or any(p not in roots or p not in candidates for p in paths):
                raise ValueError('只允许删除新清单中的最高层整包；包内子目录不能提交。')
_CLOUD_RATE_LOCK=threading.Lock()
_CLOUD_NEXT_CALL=0.0


def cloud_slot():
    global _CLOUD_NEXT_CALL
    with _CLOUD_RATE_LOCK:
        delay=max(0.0,_CLOUD_NEXT_CALL-time.monotonic())
        if delay:time.sleep(delay)
        _CLOUD_NEXT_CALL=time.monotonic()+4.2


def stamp():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


class CloudError(RuntimeError):
    pass


class Cloud:
    def __init__(self):
        self.token = credential()
        self.session = requests.Session()
        self.cache_dir = OUT/'实时索引'

    timestamp = staticmethod(stamp)

    def read(self, endpoint, params):
        for attempt in range(7):
            try:
                cloud_slot()
                response=self.session.get('https://pan.baidu.com/rest/2.0/xpan/'+endpoint,
                    params={**params,'access_token':self.token},timeout=(10,45))
                data=response.json()
                if response.status_code==200 and data.get('errno')==0:return data
                if (data.get('errno')==31034 or response.status_code in (429,500,502,503,504)) and attempt<6:
                    time.sleep(min(5*2**attempt,60));continue
                raise CloudError(f"网盘读取失败：HTTP {response.status_code}，错误码 {data.get('errno',data.get('error_code'))}。")
            except requests.RequestException:
                if attempt==6:raise CloudError('网盘连接失败，检查结果尚未确认。') from None
                time.sleep(attempt+1)
            except ValueError:
                raise CloudError('网盘返回的数据格式异常，检查结果尚未确认。') from None

    def list_directory(self,path):
        records=[];seen=set();start=0
        while True:
            data=self.read('file',{'method':'list','dir':path,'start':start,'limit':1000,
                'order':'name','desc':0,'folder':0,'web':0,'showempty':1})
            items=data.get('list')
            if not isinstance(items,list):raise CloudError('目录返回不完整，检查结果尚未确认。')
            for item in items:
                row=metadata(item)
                if not row['fsid'] or row['fsid'] in seen or row['isdir'] not in (0,1):
                    raise CloudError('目录分页条目异常，检查结果尚未确认。')
                seen.add(row['fsid']);records.append(row)
            start+=len(items)
            if len(items)<1000:return records

    def metadata_by_id(self,ids):
        output={}
        for start in range(0,len(ids),100):
            batch=ids[start:start+100]
            data=self.read('multimedia',{'method':'filemetas','fsids':json.dumps([int(x) for x in batch]),'dlink':0})
            items=data.get('list')
            if not isinstance(items,list):raise CloudError('批量目录信息不完整，尚未确认状态。')
            for item in items:
                row=metadata(item)
                if row['fsid'] not in batch or row['fsid'] in output:
                    raise CloudError('批量目录身份返回异常，尚未确认状态。')
                output[row['fsid']]=row
        return output

    def task_status(self,task_id):
        # File-manager task status is read-only. Keep only operation evidence.
        try:
            cloud_slot()
            response=self.session.get('https://pan.baidu.com/share/taskquery',
                params={'access_token':self.token,'taskid':str(task_id)},timeout=(10,35))
            data=response.json()
        except (requests.RequestException,ValueError):
            raise CloudError('异步任务状态暂时无法读取；请求已受理，不能重复提交。') from None
        if response.status_code!=200 or data.get('errno')!=0:
            raise CloudError(f"异步任务查询失败：HTTP {response.status_code}，错误码 {data.get('errno',data.get('error_code'))}；不能重复提交。")
        safe={k:data[k] for k in ['errno','status','task_errno','progress','total','request_id'] if k in data}
        safe['list']=[{k:x[k] for k in ['path','fs_id','isdir','errno','error_code','from','to'] if k in x}
                      for x in data.get('list',[])]
        return safe

    def read_tree(self, progress):
        records, seen, cursor, page = [], set(), 0, 0
        cache=getattr(self,'cache_dir',None)
        run_dir=Path(cache)/('scan-'+secrets.token_hex(8)) if cache else None
        if run_dir:
            run_dir.mkdir(parents=True,exist_ok=True)
            atomic_json(Path(cache)/'latest-scan.json',{'status':'reading','directory':run_dir.name,'startedAt':stamp()})
        while True:
            data = None
            for attempt in range(7):
                try:
                    cloud_slot()
                    response = self.session.get('https://pan.baidu.com/rest/2.0/xpan/multimedia',
                        params={'method':'listall','path':'/','recursion':1,'start':cursor,
                                'limit':10000,'order':'name','desc':0,'web':0,'access_token':self.token},
                        timeout=(10,45))
                    candidate = response.json()
                    if response.status_code==200 and candidate.get('errno')==0:
                        data=candidate;break
                    if candidate.get('errno')==31034 and attempt<6:
                        delay=min(5*2**attempt,60)
                        progress(page,len(records),f'网盘限制访问频率，等待 {delay} 秒后继续核对')
                        time.sleep(delay)
                        continue
                    raise CloudError(f"网盘读取失败：HTTP {response.status_code}，错误码 {candidate.get('errno',candidate.get('error_code'))}。")
                except requests.RequestException:
                    if attempt==6:raise CloudError('网盘连接失败，未能完成读取。') from None
                    time.sleep(attempt+1)
                except ValueError:
                    raise CloudError('网盘返回的数据格式异常，未能完成读取。') from None
            items=data.get('list')
            if not isinstance(items,list) or 'has_more' not in data:
                raise CloudError('网盘列表不完整，未执行删除。')
            for item in items:
                path=item.get('path');fsid=str(item.get('fs_id',''))
                if not path or not path.startswith('/') or not fsid or fsid in seen:
                    raise CloudError('网盘分页条目异常，未执行删除。')
                seen.add(fsid)
                records.append(metadata(item))
            page+=1
            if run_dir:
                atomic_json(run_dir/f'page-{page:05d}.json',{'start':cursor,'cursor':data.get('cursor'),
                    'hasMore':bool(data['has_more']),'receivedAt':stamp(),'items':[metadata(x) for x in items]})
                atomic_json(run_dir/'progress.json',{'status':'reading','pages':page,'entries':len(records),
                    'nextCursor':data.get('cursor'),'updatedAt':stamp()})
            progress(page,len(records))
            if not data['has_more']:break
            next_cursor=data.get('cursor')
            if not isinstance(next_cursor,int) or next_cursor<=cursor:
                raise CloudError('网盘分页游标异常，未执行删除。')
            cursor=next_cursor
        try:tree=Tree(records)
        except CloudError:
            if run_dir:atomic_json(run_dir/'progress.json',{'status':'inconsistent','pages':page,
                'entries':len(records),'updatedAt':stamp(),'reason':'目录在分页期间发生变化或父级缺失，不能用于删除核对'})
            raise
        if run_dir:
            info={'status':'complete','directory':run_dir.name,'pages':page,'entries':len(records),'completedAt':stamp()}
            atomic_json(run_dir/'progress.json',info)
            atomic_json(Path(cache)/'latest-scan.json',info)
        return tree

    def delete(self, paths):
        require_deletion_enabled(paths)
        # Official SDK: baidudriver/api/file_manager.go, doManage("delete",0,...).
        # Mutating calls are deliberately never retried after a timeout.
        try:
            cloud_slot()
            response=self.session.post('https://pan.baidu.com/rest/2.0/xpan/file',
                params={'method':'filemanager','opera':'delete','access_token':self.token},
                data={'async':'0','filelist':json.dumps(paths,ensure_ascii=False,separators=(',',':'))},
                timeout=(10,120))
            data=response.json()
        except (requests.RequestException,ValueError):
            raise CloudError('删除请求的响应未能确认，已停止后续提交；正在核对实际结果。') from None
        if response.status_code!=200 or data.get('errno')!=0:
            raise CloudError(f"网盘删除接口返回 HTTP {response.status_code}，错误码 {data.get('errno',data.get('error_code'))}；已停止后续提交。")
        # Persist task IDs in the caller's audit before any status check. An
        # asynchronous acknowledgement is not proof of completed deletion.
        return {'errno':0,'taskId':str(data['taskid']) if data.get('taskid') else '',
                'requestId':str(data.get('request_id','')),
                'info':[{'path':r.get('path'),'errno':r.get('errno')} for r in data.get('info',[])]}


class Tree:
    def __init__(self, records):
        self.paths={}
        self.nodes={'/':{'children':{},'fsid':'','name':'/','isdir':1}}
        for item in records:
            path=item['path']
            if path in self.paths:raise CloudError('网盘出现重复路径，停止操作。')
            self.paths[path]=item
            if item['isdir']==1:self.nodes[path]={'children':{},**item}
            elif item['isdir']!=0:raise CloudError('网盘条目类型不明确，停止操作。')
        for item in records:
            path=item['path']
            if path=='/':continue
            parent=path.rsplit('/',1)[0] or '/'
            if parent not in self.nodes:raise CloudError('网盘目录树缺少父级，停止操作。')
            self.nodes[parent]['children'][item['name']]=item
        for path in sorted(self.nodes,key=lambda p:p.count('/') if p!='/' else 0,reverse=True):
            node=self.nodes[path];values=[];coded=False
            for name,item in sorted(node['children'].items()):
                if item['isdir']:
                    child=self.nodes[item['path']]
                    values.append(('D',name,child['fingerprint']));coded|=child['codedAudio']
                else:
                    if not item['md5']:raise CloudError('网盘返回缺少内容指纹的文件，停止操作。')
                    values.append(('F',name,item['size'],item['md5']))
                    coded|=bool(re.fullmatch(r'\d+\.(?:mp3|wav|m4a)',name,re.I))
            title=normalized_title(node['name']);code=re.match(r'^(\d{4,9})(?:[_\-]|$)',title)
            identity=(title if title.isdecimal() else code[1] if code else None) if coded and any(t in path for t in ['小蝌蚪','点读','MP3格式mp4格式']) else None
            if identity is not None:values.append(('POINT_READING_DIRECTORY_CODE',identity))
            node['fingerprint']=digest(values);node['codedAudio']=coded;node['codeIdentity']=identity

    def contains(self,small,big):
        a=self.nodes[small];b=self.nodes[big]
        if a.get('codeIdentity')!=b.get('codeIdentity'):return False
        for name,item in a['children'].items():
            other=b['children'].get(name)
            if not other or item['isdir']!=other['isdir']:return False
            if item['isdir']:
                if not self.contains(item['path'],other['path']):return False
            elif (item['size'],item['md5'])!=(other['size'],other['md5']):return False
        return True


class ReviewManager:
    def __init__(self,dataset,cloud_factory=Cloud,expected_ids=None,audit_dir=None):
        self.dataset=dataset;self.cloud_factory=cloud_factory
        self.expected_ids=expected_ids or {}
        self.audit_dir=audit_dir or OUT/'去重执行记录'
        self.state_dir=OUT/'实时索引' if audit_dir is None else Path(audit_dir)/'live-index'
        self.jobs={};self.lock=threading.RLock();self.active_commit=None
        if self.audit_dir.exists():
            for path in self.audit_dir.glob('*.json'):
                if not re.fullmatch('[a-f0-9]{24}',path.stem):continue
                record=json.loads(path.read_text(encoding='utf-8'))
                recovery=path.with_name(path.stem+'-reconciliation.json')
                if recovery.exists():
                    recovered=json.loads(recovery.read_text(encoding='utf-8'))
                    if recovered.get('status') in ('verified','partial') and recovered.get('checkedAt','')>max(
                            record.get('updatedAt',''),record.get('finishedAt',''),record.get('resumedAt','')):
                        record.update(status='partial',phase=f"已核实移除 {len(recovered['removed'])} 个目录；其余 {len(recovered['notSubmitted'])} 个尚未提交。",
                            result={'removed':recovered['removed'],
                                    'remaining':recovered['remainingSubmitted']+recovered['notSubmitted'],
                                    'pending':recovered['remainingSubmitted'],'notSubmitted':recovered['notSubmitted'],
                                    'retainedChanges':recovered.get('retainedChanges',[]),
                                    'verification':recovered['verification']},reconciledAt=recovered['checkedAt'])
                if record.get('status') in ('committing','validating'):
                    record.update(status='failed',phase='上次服务中断，已提交批次需要只读核对；不会自动重提。')
                self.jobs[record['id']]=record

    def state(self):
        with self.lock:
            known=self.known_state()
            # Refresh local projections after each settled deletion, so the next
            # selection never reuses containment fingerprints changed by cleanup.
            if not self.active_commit and not known.get('pending') and self.state_dir==OUT/'实时索引' and len(
                    self.dataset['meta'].get('knownRemovedPaths',[]))!=len(known.get('removed',[])):
                from build_remaining_report import main as refresh_remaining
                self.dataset=refresh_remaining(quiet=True)
            latest=max((j for j in self.jobs.values() if j.get('confirmedAt')),key=lambda j:j['confirmedAt'],default=None)
            return {**self.known_state(),'lastJobId':latest['id'] if latest else None,
                    'activeJobId':self.active_commit if self.active_commit and self.jobs[self.active_commit]['status']=='committing' else None,
                    'datasetVersion':self.dataset['meta'].get('remainingUpdatedAt'),
                    'snapshotFinishedAt':self.dataset['meta']['snapshotFinishedAt'],
                    'inventoryFiles':self.dataset['meta'].get('scannedFiles'),
                    'inventoryFolders':self.dataset['meta'].get('scannedFolders')}

    def known_state(self):
        path=self.state_dir/'known-deletions.json'
        state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'removed':[],'pending':[]}
        uncertain=[p for j in self.jobs.values() for e in j.get('events',[]) if e.get('status')!='completed'
                   for p in e['paths']]
        state['pending']=sorted(set(state.get('pending',[])+[p for p in uncertain
                    if not any(inside(p,r) for r in state.get('removed',[]))]))
        return state

    def exclude_removed(self,plan):
        known=self.known_state()
        if known.get('pending'):raise ValueError('上一批删除结果仍待确认，请先完成只读核对，不能重复提交。')
        missing=known.get('removed',[])
        if not missing:return plan
        plan=copy.deepcopy(plan)
        for group in plan['groups']:
            if any(inside(group['keep']['path'],p) for p in missing):
                raise ValueError('选择的保留目录已在旧批次删除，请调整保留选择。')
            group['remove']=[x for x in group['remove'] if not any(inside(x['path'],p) for p in missing)]
        plan['groups']=[g for g in plan['groups'] if g['remove']]
        plan['remove']=[x for x in plan['remove'] if not any(inside(x['path'],p) for p in missing)]
        if not plan['remove']:raise ValueError('这些重复副本已核实删除，无需再次提交。')
        roots=plan['remove']
        plan.update(groupCount=len(plan['groups']),folderCount=len(roots),fileCount=sum(x['files'] for x in roots),
            bytes=sum(x['bytes'] for x in roots),myResourcesFolderCount=sum(x['path'].startswith('/我的资源/') for x in roots),
            previouslyRemovedCount=len(missing))
        return plan

    def start_prepare(self,decisions,run=True):
        require_deletion_enabled()
        plan=self.exclude_removed(make_deletion_plan(self.dataset,decisions))
        with self.lock:
            if self.active_commit:raise ValueError('已有删除操作进行中，请先等它完成。')
            job_id=secrets.token_hex(12)
            self.jobs[job_id]={'id':job_id,'status':'validating','phase':'正在从本地完整盘点生成确认清单',
                               'createdAt':stamp(),'plan':plan,'progress':{},'events':[],
                               'scope':self.dataset['meta'].get('planScope','legacy-subtrees')}
        if run:threading.Thread(target=self.prepare,args=(job_id,),daemon=True).start()
        return job_id

    def progress(self,job_id,phase):
        def update(page,entries,note=None):
            with self.lock:self.jobs[job_id].update(phase=note or phase,progress={'page':page,'entries':entries})
        return update

    def verify(self,plan,tree):
        mismatches=[]
        unique={p['path']:p for group in plan['groups'] for p in [group['keep'],*group['remove']]}
        for path,item in unique.items():
            current=tree.nodes.get(path)
            if current is None:mismatches.append({'path':path,'reason':'文件夹已不存在或改名'})
            elif self.expected_ids.get(path) and current['fsid']!=self.expected_ids[path]:mismatches.append({'path':path,'reason':'目录身份已变化'})
            elif current['fingerprint']!=item['fingerprint']:mismatches.append({'path':path,'reason':'内容或目录结构已变化'})
        if mismatches:
            return mismatches
        for group in plan['groups']:
            if group['type']=='contains':
                target=group['keep']['path']
                if group.get('matchWithin'):
                    match=group['matchWithin'];target=match['path'];node=tree.nodes.get(target)
                    if not inside(target,group['keep']['path']):raise ValueError('包含比对位置必须位于保留整包内部。')
                    if not node or str(node['fsid'])!=str(match['fsid']) or node['fingerprint']!=match['fingerprint']:
                        return [{'path':target,'reason':'保留包内的对应目录身份或内容已变化，未执行删除'}]
                if any(not tree.contains(p['path'],target) for p in group['remove']):
                    return [{'path':p['path'],'reason':'实时内容已不满足严格包含，未执行删除'} for p in group['remove']]
            elif any(p['fingerprint']!=group['keep']['fingerprint'] for p in group['remove']):
                raise ValueError('候选不再满足完全重复条件。')
        return []

    def prepare(self,job_id):
        try:
            # Preview reuses the immutable inventory. Confirmation is followed by
            # one live recursive validation before the first mutating request.
            with self.lock:self.jobs[job_id].update(status='ready',
                  phase='本地快照清单已就绪；确认后先实时复核，通过才会提交删除',
                  previewSource='local-inventory',preparedAt=stamp(),
                  confirmationToken=secrets.token_urlsafe(32),expires=time.monotonic()+600)
        except Exception as exc:
            message=str(exc) if isinstance(exc,(CloudError,ValueError)) else '实时核对未完成，未执行删除。'
            with self.lock:self.jobs[job_id].update(status='failed',phase=message)

    def confirm(self,job_id,token,confirmed_count,run=True):
        require_deletion_enabled()
        with self.lock:
            job=self.jobs.get(job_id)
            if not job or job['status']!='ready':raise ValueError('请先完成本次实时核对。')
            if time.monotonic()>job['expires']:raise ValueError('核对结果已过期，请重新核对。')
            if not isinstance(token,str) or not secrets.compare_digest(token,job['confirmationToken']):raise ValueError('确认凭据不匹配。')
            if type(confirmed_count) is not int or confirmed_count!=job['plan']['folderCount']:raise ValueError('确认的删除数量不匹配。')
            if self.active_commit:raise ValueError('已有删除操作进行中。')
            self.active_commit=job_id
            job.update(status='committing',phase='再次核对实时内容',confirmedAt=stamp(),events=[])
            job.pop('confirmationToken',None)
            try:self.audit(job)
            except OSError:
                self.active_commit=None
                job.update(status='failed',phase='无法保存执行记录，未启动删除。')
                raise ValueError('无法保存执行记录，未启动删除。') from None
        if run:threading.Thread(target=self.execute,args=(job_id,),daemon=True).start()

    def audit(self,job):
        record=copy.deepcopy({k:v for k,v in job.items() if k not in ['confirmationToken','expires']})
        record['updatedAt']=stamp()
        atomic_json(self.audit_dir/(job['id']+'.json'),record)

    def resume_confirmed(self,job_id,run=True):
        require_deletion_enabled()
        with self.lock:
            job=self.jobs.get(job_id)
            if not job or not job.get('confirmedAt') or job['status'] not in ('partial','failed'):
                raise ValueError('只能接续已有最终确认的未完成任务。')
            if self.active_commit:raise ValueError('已有删除操作进行中。')
            trusted=make_deletion_plan(self.dataset,[{'id':g['id'],'keep':g['keep']['path']} for g in job['plan']['groups']])
            allowed={p['path']:p for p in trusted['remove']}
            if any(p['path'] not in allowed or p['fingerprint']!=allowed[p['path']]['fingerprint'] for p in job['plan']['remove']):
                raise ValueError('原确认范围与可信候选不符，不能接续。')
            if any(p not in {x['path'] for x in job['plan']['remove']} for e in job['events'] for p in e['paths']):
                raise ValueError('原提交记录超出确认范围，不能接续。')
            job.update(status='committing',phase='正在接续原确认任务；先核实已受理批次',resumedAt=stamp())
            job.pop('finishedAt',None);self.active_commit=job_id
            try:self.audit(job)
            except OSError:self.active_commit=None;raise
        if run:threading.Thread(target=self.execute,args=(job_id,),daemon=True).start()

    def publish_result(self,job,phase=None,terminal=None):
        authorized={p['path'] for p in job['plan']['remove']}
        submitted={p for e in job.get('events',[]) for p in e['paths']}
        known=self.known_state()
        removed=authorized & set(known.get('removed',[]))
        pending=submitted-removed
        not_submitted=authorized-submitted-removed
        job['result']={'removed':sorted(removed),'pending':sorted(pending),'notSubmitted':sorted(not_submitted),
            'remaining':sorted(pending|not_submitted),'retainedChanges':job.get('result',{}).get('retainedChanges',[]),
            'verification':'per-batch asynchronous task results, original IDs, and parent-list fallback'}
        if phase:job['phase']=phase
        if terminal:job.update(status=terminal,finishedAt=stamp())
        atomic_json(self.state_dir/'known-deletions.json',{**known,'jobId':job['id'],'checkedAt':stamp(),
            'pending':sorted((set(known.get('pending',[]))-authorized)|pending),'notSubmitted':sorted(not_submitted),
            'baselineUnchanged':True})
        self.audit(job)

    def complete_batch(self,cloud,job,event):
        batch=event['paths'];task_id=event.get('response',{}).get('taskId')
        for attempt in range(180):
            completed_ids={}
            if task_id and hasattr(cloud,'task_status'):
                status=cloud.task_status(task_id)
                event['taskStatus']=status;event['lastCheckedAt']=stamp()
                if status.get('status')=='failed' or status.get('task_errno',0)!=0:
                    self.audit(job)
                    raise CloudError('百度异步任务返回失败；已保存逐项结果，未重复提交。')
                if status.get('status')!='success':
                    note=f"百度正在处理已受理批次（{len(batch)} 个目录，进度 {status.get('progress','未知')}）；完成后自动接续下一批"
                    with self.lock:self.publish_result(job,note)
                    time.sleep(min(5+attempt*2,20));continue
                results={x.get('path'):str(x.get('fs_id','')) for x in status.get('list',[])
                         if not x.get('errno') and not x.get('error_code')}
                if status.get('total')==len(batch) and set(results)==set(batch):
                    completed_ids={p:results[p] for p in batch if results[p]==self.expected_ids.get(p)}
                self.audit(job)
            checked=check_paths(cloud,batch,self.expected_ids,
                self.state_dir/('check-'+job['id'])/f"batch-{job['events'].index(event)+1}",
                self.progress(job['id'],'正在定向核实已受理批次'),completed_ids=completed_ids)
            removed=[x['path'] for x in checked['states'] if x['state']=='absent']
            pending=[x['path'] for x in checked['states'] if x['state']!='absent']
            known=self.known_state();all_removed=sorted(set(known.get('removed',[])+removed))
            atomic_json(self.state_dir/'known-deletions.json',{**known,'jobId':job['id'],'checkedAt':stamp(),
                'removed':all_removed,'pending':[p for p in known.get('pending',[]) if p not in all_removed]})
            event.update(verifiedRemoved=removed,remaining=pending,lastCheckedAt=stamp())
            if not pending:
                event.update(status='completed',completedAt=stamp())
                with self.lock:self.publish_result(job,f"已核实移除 {len(set(all_removed)&{p['path'] for p in job['plan']['remove']})} / {job['plan']['folderCount']} 个；正在接续后续批次")
                return True
            with self.lock:self.publish_result(job,f"本批已核实移除 {len(removed)} 个、等待 {len(pending)} 个；尚未提交后续批次")
            if not task_id:return False
            time.sleep(min(5+attempt*2,20))
        return False

    def execute(self,job_id):
        require_deletion_enabled()
        job=self.jobs[job_id];plan=job['plan'];cloud=None
        try:
            cloud=self.cloud_factory()
            # Resume only the already-confirmed scope. Accepted requests are
            # reconciled or polled, never reposted, including after a restart.
            for event in job.get('events',[]):
                known=set(self.known_state().get('removed',[]))
                if all(p in known for p in event['paths']):
                    event.update(status='completed');continue
                if not self.complete_batch(cloud,job,event):
                    with self.lock:self.publish_result(job,'已受理批次结果尚未确认，后续未提交；不会重复提交。','partial')
                    return
            submitted={p for e in job['events'] for p in e['paths']}
            known=set(self.known_state().get('removed',[]))
            remaining=[p for p in plan['remove'] if p['path'] not in submitted and p['path'] not in known]
            if remaining:
                current_plan=copy.deepcopy(plan);paths={p['path'] for p in remaining}
                for group in current_plan['groups']:group['remove']=[p for p in group['remove'] if p['path'] in paths]
                current_plan['groups']=[g for g in current_plan['groups'] if g['remove']]
                current_plan['remove']=remaining
                tree=cloud.read_tree(self.progress(job_id,'正在核对尚未提交的原确认范围'))
                changes=self.verify(current_plan,tree)
                if changes:
                    job['mismatches']=changes
                    with self.lock:self.publish_result(job,'待提交目录有变化，已保留原完成结果；未扩大删除范围。','changed')
                    return
                with self.lock:job.update(validatedAt=stamp());self.audit(job)
                for start in range(0,len(remaining),100):
                    batch=[p['path'] for p in remaining[start:start+100]]
                    with self.lock:
                        event={'paths':batch,'submittedAt':stamp(),'status':'submitting'}
                        job['events'].append(event)
                        self.publish_result(job,f"正在提交下一批 {len(batch)} 个；只处理原确认范围")
                    try:result=cloud.delete(batch)
                    except CloudError as exc:
                        event.update(status='unconfirmed',message=str(exc));self.audit(job)
                        self.complete_batch(cloud,job,event)
                        raise
                    event.update(status='async' if result.get('taskId') else 'submitted',response=result)
                    self.audit(job)
                    if any(x.get('errno')!=0 for x in result.get('info',[])):
                        raise CloudError('部分目录操作返回错误，已保存响应，未重复提交。')
                    if not self.complete_batch(cloud,job,event):
                        with self.lock:self.publish_result(job,'已提交批次仍待确认，尚未提交后续；不会重复提交。','partial')
                        return
            keep_checked=check_paths(cloud,list(dict.fromkeys(g['keep']['path'] for g in plan['groups'])),
                self.expected_ids,self.state_dir/('keepers-'+job_id),self.progress(job_id,'核对保留目录身份'))
            retained=[x['path'] for x in keep_checked['states'] if x['state']!='present']
            with self.lock:
                self.publish_result(job)
                job['result']['retainedChanges']=retained
                left=len(job['result']['remaining']);count=len(job['result']['removed'])
                self.publish_result(job,f"已核实移除 {count} / {plan['folderCount']} 个重复目录；待确认 {len(job['result']['pending'])} 个，尚未提交 {len(job['result']['notSubmitted'])} 个。",'done' if not left and not retained else 'partial')
                job.pop('requestMessage',None)
        except Exception as exc:
            message=str(exc) if isinstance(exc,(CloudError,ValueError)) else '接续暂时中断，执行记录已保留；不会重复提交已受理批次。'
            with self.lock:self.publish_result(job,message,'partial' if job.get('events') else 'failed')
        finally:
            with self.lock:self.active_commit=None;self.audit(job)

    def public(self,job_id):
        with self.lock:
            if job_id not in self.jobs:raise ValueError('未找到本次核对任务。')
            job=copy.deepcopy(self.jobs[job_id]);job.pop('expires',None)
            return job


def server(port=8765,manager=None,resume_job=None):
    if DELETE_LOCK.exists() and not package_deletion_mode() and manager is None:
        if resume_job:require_deletion_enabled()
        from package_review_server import create_server
        return create_server(port)
    if package_deletion_mode() and manager is None:
        from package_delete_service import PackageReviewManager
        manager=PackageReviewManager()
    current=OUT/'文件夹去重清单_当前.json'
    dataset=json.loads((current if current.exists() else OUT/'文件夹去重清单.json').read_text(encoding='utf-8'))
    if manager is None:
        con=sqlite3.connect(f'file:{(OUT/"inventory.sqlite").as_posix()}?mode=ro',uri=True)
        ids=dict(con.execute('select path,fsid from nodes where isdir=1'));con.close()
        manager=ReviewManager(dataset,expected_ids=ids)
    if resume_job:manager.resume_confirmed(resume_job)
    csrf=secrets.token_urlsafe(36);origin=f'http://127.0.0.1:{port}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,body,content_type='application/json; charset=utf-8'):
            if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
            self.end_headers();self.wfile.write(body)
        def host_ok(self):return self.headers.get('Host')==f'127.0.0.1:{port}'
        def do_GET(self):
            if not self.host_ok():return self.send(403,{'error':'仅允许本机访问。'})
            path=unquote(urlsplit(self.path).path)
            if path=='/api/health':return self.send(200,{'service':'folder-dedup-review','confirmationRequired':True,'version':2})
            if path=='/api/state':
                if not secrets.compare_digest(self.headers.get('X-Dedup-CSRF',''),csrf):return self.send(403,{'error':'无效的页面会话。'})
                return self.send(200,manager.state())
            if path=='/api/candidates':
                if not secrets.compare_digest(self.headers.get('X-Dedup-CSRF',''),csrf):return self.send(403,{'error':'无效的页面会话。'})
                return self.send(200,getattr(manager,'package_data',manager.dataset))
            if path.startswith('/api/jobs/'):
                if not secrets.compare_digest(self.headers.get('X-Dedup-CSRF',''),csrf):return self.send(403,{'error':'无效的页面会话。'})
                try:return self.send(200,manager.public(path.rsplit('/',1)[-1]))
                except ValueError as exc:return self.send(404,{'error':str(exc)})
            name=path.lstrip('/') or '文件夹去重清单.html'
            if '/' in name or '\\' in name or Path(name).suffix.lower() not in ['.html','.md','.json','.txt','.png','.jpg']:
                return self.send(404,{'error':'未找到文件。'})
            target=(OUT/name).resolve()
            if target.parent!=OUT.resolve() or not target.is_file():return self.send(404,{'error':'未找到文件。'})
            body=target.read_bytes()
            if name in ('文件夹去重清单.html','整包去重清单.html'):
                config=json.dumps({'csrf':csrf,'base':origin},separators=(',',':'))
                body=body.replace(b'<script id="dedup-data"',('<script>window.DEDUP_REVIEW_SERVICE='+config+';</script><script id="dedup-data"').encode(),1)
                body=body.replace(b'<script id="package-data"',('<script>window.PACKAGE_REVIEW_SERVICE='+config+';</script><script id="package-data"').encode(),1)
            mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
            return self.send(200,body,mime+('; charset=utf-8' if mime.startswith('text/') or mime=='application/json' else ''))
        def do_POST(self):
            if not self.host_ok() or self.headers.get('Origin')!=origin or not secrets.compare_digest(self.headers.get('X-Dedup-CSRF',''),csrf):
                return self.send(403,{'error':'请求必须来自当前本机核对页面。'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(415,{'error':'请求格式不支持。'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024*1024:raise ValueError('请求大小不符合要求。')
                data=json.loads(self.rfile.read(length))
                if self.path=='/api/prepare':
                    job_id=manager.start_prepare(data.get('decisions'))
                    return self.send(202,{'jobId':job_id})
                if self.path=='/api/confirm-delete':
                    manager.confirm(data.get('jobId'),data.get('confirmationToken'),data.get('confirmedFolderCount'))
                    return self.send(202,{'jobId':data['jobId']})
                return self.send(404,{'error':'未知操作。'})
            except (ValueError,TypeError) as exc:return self.send(400,{'error':str(exc)})
            except Exception:return self.send(500,{'error':'操作未能启动，请保留选择后重新核对。'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--resume-confirmed')
    args=parser.parse_args()
    instance=server(args.port,resume_job=args.resume_confirmed)
    print(f'Local folder review service listening on 127.0.0.1:{args.port}; mode: '+('package roots only, final confirmation required' if package_deletion_mode() else 'read-only, deletion disabled' if DELETE_LOCK.exists() else 'confirmation required'),flush=True)
    instance.serve_forever()
