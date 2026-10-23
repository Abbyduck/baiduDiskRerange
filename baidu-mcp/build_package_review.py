"""Publish ONLY complete inferred package roots after verified rollback."""
import collections
import html
import itertools
import json
import re
from pathlib import Path

from dedup_review_server import OUT,Tree,stamp,package_deletion_mode
from dedup_live_index import atomic_json
from analyze_folder_duplicates import normalized_title
from package_boundaries import CONTAINERS,infer_roots,package_owner,exact_groups,deletion_candidate_boundary
from package_containment import find_package_coverage


def main(quiet=False):
    recovery=json.loads((OUT/'恢复记录/完整恢复核验.json').read_text(encoding='utf-8'))
    if recovery['status']!='verified':raise ValueError('Recovery must be fully verified before publishing candidates.')
    scan=json.loads((OUT/'实时索引/latest-scan.json').read_text(encoding='utf-8'))
    if scan['status']!='complete' or not re.fullmatch('scan-[a-f0-9]+',scan['directory']):raise ValueError('Incomplete live index.')
    known=json.loads((OUT/'实时索引/known-deletions.json').read_text(encoding='utf-8'))
    removed=set(known.get('removed',[]))
    records=[]
    for page in range(1,scan['pages']+1):
        records.extend(json.loads((OUT/'实时索引'/scan['directory']/f'page-{page:05d}.json').read_text(encoding='utf-8'))['items'])
    assert len(records)==scan['entries']
    records=[r for r in records if not any('/'.join(r['path'].split('/')[:n]) in removed
             for n in range(2,len(r['path'].split('/'))+1))]
    tree=Tree(records)
    for p in sorted(tree.nodes,key=lambda p:p.count('/') if p!='/' else 0,reverse=True):
        n=tree.nodes[p];n.update(files=0,folders=0,bytes=0)
        for x in n['children'].values():
            if x['isdir']:
                ch=tree.nodes[x['path']];n['files']+=ch['files'];n['folders']+=1+ch['folders'];n['bytes']+=ch['bytes']
            else:n['files']+=1;n['bytes']+=x['size']
    # Self-made topical directory groups are not verified independent package roots.
    roots={p:b for p,b in infer_roots(tree.nodes).items() if deletion_candidate_boundary(b)}
    assert not any(a!=b and a.startswith(b+'/') for a in roots for b in roots)
    stats=tree.nodes
    def unit(p):
        n=stats[p]
        return {**roots[p],**{k:n[k] for k in ('files','folders','bytes','fingerprint','fsid')},
            'title':n['name'],'children':[{'name':x['name'],'isdir':x['isdir']} for x in n['children'].values()]}
    exact=[]
    for ps in exact_groups(tree,roots,stats):
        ps.sort(key=lambda p:(p.startswith('/我的资源/'),not p.startswith('/01恩恩学习资料/'),len(p),p))
        exact.append({'type':'exact','title':stats[ps[0]]['name'],'copies':[unit(p) for p in ps],
            'reason':'两边均为独立整包边界，全部相对路径、子目录、文件大小和内容指纹一致。'})
    exact.sort(key=lambda c:-c['copies'][0]['bytes'])
    contains=[]
    for found in find_package_coverage(tree,roots,stats):
        small=found['source'];ref=found['reference'];big=ref['keeper']
        # Whole-root equality belongs to the exact tab, not both tabs.
        if ref['matchPath']==big and stats[small]['fingerprint']==stats[big]['fingerprint']:continue
        match={'path':ref['matchPath'],'fingerprint':ref['matchFingerprint'],
               'fsid':ref['matchFsid'],'files':ref['matchFiles'],'folders':ref['matchFolders'],
               'bytes':ref['matchBytes'],'exactAtMatch':ref['exactAtMatch']}
        contains.append({'type':'contains','title':stats[small]['name'],'copies':[unit(big),unit(small)],
            'matchWithin':match,'referenceCount':len(found['references']),
            'references':found['references'],
            'addedFiles':ref['matchFiles']-stats[small]['files'],
            'reason':'这个独立整包的全部相对路径、文件大小和内容指纹，均已在保留包的对应位置找到；包括空目录。仅可删除独立整包，保留包及包内对应目录不作改动。'})
    previous=OUT/'整包去重清单.json'
    old=json.loads(previous.read_text(encoding='utf-8')) if previous.exists() else {'exact':[],'contains':[]}
    key=lambda c:(c['type'],tuple(x['path'] for x in c['copies']))
    ids={key(c):c['id'] for c in old['exact']+old['contains']}
    from analyze_folder_duplicates import digest
    for c in exact+contains:c['id']=ids.get(key(c)) or ('P' if c['type']=='exact' else 'S')+digest(key(c))[:10]
    original=json.loads((OUT/'文件夹去重清单.json').read_text(encoding='utf-8'))
    hidden=[];affected=collections.defaultdict(lambda:{'paths':set(),'caseIds':set()})
    for c in original['cases']:
        copies=c.get('originalCopies',c.get('copies',[])) if c['type']=='exact' else c.get('smaller',[])+c.get('larger',[])
        for x in copies:
            owner=package_owner(x['path'],roots)
            if owner and owner!=x['path']:
                hidden.append({'oldCase':c['id'],'path':x['path'],'packageRoot':owner})
                affected[owner]['paths'].add(x['path']);affected[owner]['caseIds'].add(c['id'])
    review=[{**unit(p),'internalDuplicateFolders':len(a['paths']),'oldCaseCount':len(a['caseIds']),
        'explanation':'发现重复的是包内子目录，已取消这些子目录候选；不能据此删除这个整包。'}
        for p,a in affected.items() if not roots[p]['protected']]
    review.sort(key=lambda x:-x['internalDuplicateFolders'])
    data={'meta':{'createdAt':stamp(),'sourceScan':scan,'deletionDisabled':not package_deletion_mode(),
        'knownRemovedPaths':sorted(removed),
        'boundaryRule':'先识别分类容器，遇到首个完整资源目录即停止向下拆分，再比较这些整包根目录。',
        'boundaryEvidence':'现有目录名称与父子结构推断；未取得历史转存批次记录，均需人工核对边界。',
        'packageRoots':len(roots),'exactGroups':len(exact),'containmentPairs':len(contains),
        'embeddedContainmentGroups':sum(c['matchWithin']['path']!=c['copies'][0]['path'] for c in contains),
        'containedFiles':sum(c['copies'][1]['files'] for c in contains),
        'containedBytes':sum(c['copies'][1]['bytes'] for c in contains),
        'hiddenInternalOccurrences':len(hidden),'affectedPackages':len(review)},
        'recovery':recovery,'exact':exact,'contains':contains,'boundaryReview':review,
        'packages':[unit(p) for p in roots if stats[p]['files']],
        'classificationContainers':[{'path':p,'reason':r} for p,r in CONTAINERS.items() if p in tree.nodes]}
    atomic_json(OUT/'整包去重清单.json',data)
    atomic_json(OUT/'包内子目录候选撤销记录.json',hidden)
    template=(Path(__file__).parent/'package-review-template.html').read_text(encoding='utf-8')
    body=template.replace('__DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
    body=body.replace('__ACTIONS__',(Path(__file__).parent/'package-actions.js').read_text(encoding='utf-8'))
    for name in ('整包去重清单.html','文件夹去重清单.html'):(OUT/name).write_text(body,encoding='utf-8')
    if not quiet:print(json.dumps(data['meta'],ensure_ascii=False))
    return data


if __name__=='__main__':main()
