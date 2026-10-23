"""Refresh remaining candidates from the saved live index and verified deletions.

No network access or cloud mutations. The original inventory/evidence is retained.
"""
import collections
import copy
import json
import re
from pathlib import Path

from dedup_review_server import OUT, Tree, stamp
from dedup_live_index import atomic_json
from folder_dedup_plan import preference


def main(quiet=False):
    data=json.loads((OUT/'文件夹去重清单.json').read_text(encoding='utf-8'))
    known=json.loads((OUT/'实时索引/known-deletions.json').read_text(encoding='utf-8'))
    scan=json.loads((OUT/'实时索引/latest-scan.json').read_text(encoding='utf-8'))
    if scan['status']!='complete' or not re.fullmatch('scan-[a-f0-9]+',scan['directory']):
        raise ValueError('没有完整的已保存实时索引。')
    removed=set(known['removed'])
    def gone(path):
        parts=path.split('/')
        return any('/'.join(parts[:n]) in removed for n in range(2,len(parts)+1))
    records=[];received=0
    for page in range(1,scan['pages']+1):
        chunk=json.loads((OUT/'实时索引'/scan['directory']/f'page-{page:05d}.json').read_text(encoding='utf-8'))
        received+=len(chunk['items']);records.extend(x for x in chunk['items'] if not gone(x['path']))
    if received!=scan['entries']:raise ValueError('已保存索引的条目数量不完整。')
    tree=Tree(records)
    for path in sorted(tree.nodes,key=lambda x:x.count('/') if x!='/' else 0,reverse=True):
        node=tree.nodes[path];node.update(files=0,folders=0,bytes=0)
        for item in node['children'].values():
            if item['isdir']:
                child=tree.nodes[item['path']]
                node['files']+=child['files'];node['folders']+=1+child['folders'];node['bytes']+=child['bytes']
            else:node['files']+=1;node['bytes']+=item['size']
    def stat(old):
        node=tree.nodes[old['path']]
        return {**old,**{k:node[k] for k in ['files','folders','bytes','fingerprint']}}
    def entries(path,prefix=''):
        for name,item in sorted(tree.nodes[path]['children'].items()):
            rel=prefix+name
            if item['isdir']:
                yield rel,('folder',0,'');yield from entries(item['path'],rel+'/')
            else:yield rel,('file',item['size'],item['md5'])
    def subset(small,big):
        return tree.contains(small,big)
    counters={}
    def content(path):
        if path not in counters:
            counters[path]=collections.Counter((v[1],v[2]) for _,v in entries(path) if v[0]=='file')
        return counters[path]
    counts=collections.Counter();changes=[]
    for c in data['cases']:
        if c['type']=='exact':
            c['reviewState']='protected' if c['protected'] else 'completed' if all(
                gone(p['path']) for p in c['copies'] if p['path']!=c['defaultKeep']) else 'pending'
            counts[c['reviewState']]+=1
            continue
        c['originalType']=c['type']
        small=[stat(p) for p in c['smaller'] if p['path'] in tree.nodes]
        large=[stat(p) for p in c['larger'] if p['path'] in tree.nodes]
        original_small=c['smaller'];original_large=c['larger']
        c['smaller']=small;c['larger']=large
        if not small or not any(p['files'] for p in small):
            c.update(reviewState='completed',currentNote='较少份中的文件已随完全重复清理；现有空目录不再作为内容去重候选。')
            # Keep original paths/stats in completed history; do not offer actions.
            c['smaller']=original_small;c['larger']=original_large
        elif not large:
            c.update(type='content_equal',reviewState='review',currentNote='原保留参照已变化，需要重新核对。')
        elif c['type']=='contains':
            good=[p for p in large if all(subset(s['path'],p['path']) for s in small)]
            if good:
                c['larger']=sorted(good,key=preference)
                c['reviewState']='pending';c['defaultKeep']=c['larger'][0]['path']
                base=small[0];keep=c['larger'][0]
                c.update(files=base['files'],bytes=base['bytes'],addedFiles=keep['files']-base['files'],
                         addedFolders=keep['folders']-base['folders'],addedBytes=keep['bytes']-base['bytes'])
                a=dict(entries(base['path']));b=dict(entries(keep['path']))
                extras=[{'relativePath':k,'type':v[0],**({'bytes':v[1]} if v[0]=='file' else {})}
                         for k,v in b.items() if k not in a]
                c.update(extraExamples=extras[:20],extraExamplesComplete=len(extras)<=20)
                c['currentNote']='已根据保存的实时目录及已确认删除结果重新核对包含关系。'
                if len({p['fingerprint'] for p in small+c['larger']})==1:
                    c.update(type='exact',copies=list({p['path']:p for p in c['larger']+small}.values()),
                             referenceCopies=[],allReferenceCopies=[],
                             currentNote='上一轮清理后，这两份父目录现已完全相同；已重新列为待处理的完全重复。')
            else:
                c.update(type='content_equal',reviewState='review',currentNote='完全重复清理改变了父包内容，旧包含关系已失效；先核对当前目录，暂不执行删除。')
                c['differentPathExamples']=['旧的严格包含建议已暂停，不能直接沿用。']
        else:
            c['reviewState']='protected' if c['protected'] else 'review'
            covers=any(all(not (content(s['path'])-content(b['path'])) for s in small) for b in large)
            c['currentNote']='目录结构不同，继续逐组核对；不加入批量删除。' if covers else '清理后原内容覆盖关系已变化；请核对两侧现有内容，暂不加入删除。'
        if c['protected']:c['reviewState']='protected'
        if c['reviewState']!='completed' and small:
            c.update(files=small[0]['files'],bytes=small[0]['bytes'])
        counts[c['reviewState']]+=1
        if c['reviewState']!='pending' or any(a['fingerprint']!=b['fingerprint'] for a,b in zip(original_small+original_large,small+large)):
            changes.append({'id':c['id'],'title':c['title'],'state':c['reviewState'],
                            'small':[{'path':p['path'],'files':p['files']} for p in small],
                            'large':[{'path':p['path'],'files':p['files']} for p in large]})
    data['meta'].update(remainingUpdatedAt=stamp(),remainingSourceScan=scan['completedAt'],
        knownRemovedPaths=known['removed'],remainingCounts=dict(counts),
        remainingScope='已保存的完整实时索引减去核实移除项；未重新读取网盘。执行前仍实时核对。')
    atomic_json(OUT/'文件夹去重清单_当前.json',data)
    source=Path(__file__).resolve().parent
    template=(source/'folder-dedup-template.html').read_text(encoding='utf-8')
    app=(source/'folder-dedup-app.js').read_text(encoding='utf-8')
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    (OUT/'文件夹去重清单.html').write_text(template.replace('%%DATA%%',payload).replace('%%APP%%',app),encoding='utf-8')
    proof={'updatedAt':stamp(),'sourceLiveScan':scan,'knownRemoved':len(removed),'currentEntries':len(records),
           'counts':dict(counts),'changedCandidates':changes,'cloudMutations':0,'cloudReads':0}
    atomic_json(OUT/'剩余去重建议核验.json',proof)
    if not quiet:print(json.dumps({'counts':dict(counts),'changedCandidates':len(changes)},ensure_ascii=False),flush=True)
    return data


if __name__=='__main__':main()
