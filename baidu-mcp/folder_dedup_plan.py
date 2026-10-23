"""Highest-folder duplicate projection and server-authoritative exact-only plans."""
from copy import deepcopy


def inside(path, root):
    return path == root or path.startswith(root + '/')


def preference(item):
    path = item['path']
    return (path.startswith('/我的资源/'), not path.startswith('/01恩恩学习资料/'),
            path.count('/'), len(path), path)


def collapse_exact_folders(cases):
    exact = [c for c in cases if c['type'] == 'exact']
    occurrence = {p['path']: c for c in exact for p in c['copies']}

    def highest_ancestor(path):
        parts = path.split('/')
        for length in range(2, len(parts)):
            parent = '/'.join(parts[:length])
            if parent in occurrence:
                return parent
        return None

    # Every parent has strictly more tree entries than its descendants, even
    # when the parent contains only one directory and no additional files.
    ordered = sorted(exact, key=lambda c: (-(c['copies'][0]['files'] + c['copies'][0]['folders']), -c['bytes'], c['id']))
    removals, output, covered_ids = [], [], []
    for original in ordered:
        free = [deepcopy(p) for p in original['copies'] if not highest_ancestor(p['path'])]
        covered = [deepcopy(p) for p in original['copies'] if highest_ancestor(p['path'])]
        if not free:
            covered_ids.append(original['id'])
            continue
        retained_references = [p for p in covered if not any(inside(p['path'], r) for r in removals)]
        assert not covered or retained_references, original['id']
        case = deepcopy(original)
        case['copies'] = sorted(free, key=preference)
        case['referenceCopies'] = sorted(retained_references, key=preference)
        case['allReferenceCopies'] = covered
        case['originalCopies'] = deepcopy(original['copies'])
        case['allCopyCount'] = len(original['copies'])
        case['coveredCopyCount'] = len(covered)
        case['nestedUnder'] = None
        case['title'] = case['copies'][0]['path'].rsplit('/', 1)[-1]
        keeper = (case['referenceCopies'] or case['copies'])[0]
        case['defaultKeep'] = keeper['path']
        if case['referenceCopies']:
            case['referenceExplanation'] = '相同内容已在更高层保留包内；这里只处理其他分支的独立副本，不单列父包内部的子目录删除。'
        if not case['protected']:
            removals.extend(p['path'] for p in case['copies'] if p['path'] != keeper['path'])
        output.append(case)
    # Distinct deletion candidates cannot be descendants of other candidates.
    assert not any(a != b and inside(a, b) for a in removals for b in removals)
    return output, covered_ids


def make_exact_plan(dataset, decisions):
    """Ignore all client-supplied paths and resolve IDs/keepers from trusted data."""
    if not isinstance(decisions, list) or not decisions:
        raise ValueError('请至少选择一组完全重复文件夹。')
    available = {c['id']: c for c in dataset['cases'] if c['type'] == 'exact'}
    decision_map = {}
    for item in decisions:
        if not isinstance(item, dict) or item.get('id') not in available:
            raise ValueError('选择中含有未知候选或非完全重复项目。')
        if item['id'] in decision_map:
            raise ValueError('同一候选不能重复提交。')
        case = available[item['id']]
        if case['protected']:
            raise ValueError('受保护来源不能加入批量删除。')
        decision_map[item['id']] = item
    ordered = sorted((available[key] for key in decision_map),
                     key=lambda c: (-(c['copies'][0]['files'] + c['copies'][0]['folders']), -c['bytes'], c['id']))
    plans, removes = [], []
    protected_paths = dataset['meta']['protectedPaths']
    protected_entries = dataset['meta'].get('preservedRootEntries', [])
    for case in ordered:
        request = decision_map[case['id']]
        refs = case.get('allReferenceCopies', [])
        if refs:
            live_refs = [p for p in refs if not any(inside(p['path'], r['path']) for r in removes)]
            if not live_refs:
                raise ValueError('较高层的保留选择导致下层参照失效，请调整选择。')
            keeper = sorted(live_refs, key=preference)[0]
        else:
            requested = request.get('keep') or case['defaultKeep']
            keeper = next((p for p in case['copies'] if p['path'] == requested), None)
            if keeper is None:
                raise ValueError('保留路径不是这组允许选择的文件夹。')
        deleted = [deepcopy(p) for p in case['copies'] if p['path'] != keeper['path']]
        if not deleted:
            continue
        for p in deleted:
            if p['path'] in protected_entries or any(inside(p['path'], root) for root in protected_paths):
                raise ValueError('删除范围涉及必须保留的目录。')
        plans.append({'id': case['id'], 'title': case['title'], 'type': 'exact',
                      'keep': deepcopy(keeper), 'remove': deleted})
        removes.extend(deleted)
    if not plans:
        raise ValueError('所选项目没有需要删除的独立副本。')
    keepers = [p['keep']['path'] for p in plans]
    for p in removes:
        if any(inside(keep, p['path']) for keep in keepers):
            raise ValueError('删除目录覆盖了某个保留目录，请调整选择。')
    # Even if a stale client submitted overlapping rows, never expand the action.
    ordered_removes = sorted({p['path']: p for p in removes}.values(), key=lambda p: len(p['path']))
    roots = []
    for item in ordered_removes:
        if not any(inside(item['path'], p['path']) for p in roots):
            roots.append(item)
    return {'groups': plans, 'remove': roots, 'groupCount': len(plans),
            'folderCount': len(roots), 'fileCount': sum(p['files'] for p in roots),
            'bytes': sum(p['bytes'] for p in roots),
            'myResourcesFolderCount': sum(p['path'].startswith('/我的资源/') for p in roots),
            'snapshotFinishedAt': dataset['meta']['snapshotFinishedAt']}


def make_deletion_plan(dataset, decisions):
    """Resolve only exact or current strict-containment cases from trusted data."""
    if not isinstance(decisions,list) or not decisions:raise ValueError('请至少选择一组待处理项目。')
    cases={c['id']:c for c in dataset['cases']};seen=set();exact=[];contained=[]
    for decision in decisions:
        if not isinstance(decision,dict) or decision.get('id') not in cases:raise ValueError('选择中含有未知候选。')
        key=decision['id'];case=cases[key]
        if key in seen:raise ValueError('同一候选不能重复提交。')
        seen.add(key)
        if case.get('protected') or case.get('reviewState') in ('review','completed','protected'):
            raise ValueError('已完成、待人工复核或受保护项目不能执行删除。')
        if case['type']=='exact':exact.append(decision)
        elif case['type']=='contains':contained.append(decision)
        else:raise ValueError('目录差异项目需要人工复核，不能加入批量删除。')
    groups=make_exact_plan(dataset,exact)['groups'] if exact else []
    for decision in contained:
        case=cases[decision['id']];keep_path=decision.get('keep') or case.get('defaultKeep') or case['larger'][0]['path']
        keep=next((p for p in case['larger'] if p['path']==keep_path),None)
        if keep is None:raise ValueError('包含关系必须保留已核实覆盖较少份的完整目录。')
        removes=deepcopy(case['smaller'])
        if not removes or any(p['path']==keep_path or p['files']<=0 for p in removes):
            raise ValueError('较少份没有需要清理的文件。')
        group={'id':case['id'],'title':case['title'],'type':'contains','keep':deepcopy(keep),'remove':removes}
        if case.get('matchWithin'):group['matchWithin']=deepcopy(case['matchWithin'])
        groups.append(group)
    all_removes=[p for group in groups for p in group['remove']]
    keepers=[g['keep']['path'] for g in groups]
    for p in all_removes:
        if p['path'] in dataset['meta'].get('preservedRootEntries',[]) or any(inside(p['path'],root) for root in dataset['meta']['protectedPaths']):
            raise ValueError('删除范围涉及必须保留的目录。')
        if any(inside(keeper,p['path']) for keeper in keepers):
            raise ValueError('某个删除目录包含另一组的保留目录，请取消其中一组后继续。')
    roots=[]
    for p in sorted({p['path']:p for p in all_removes}.values(),key=lambda p:len(p['path'])):
        if not any(inside(p['path'],r['path']) for r in roots):roots.append(p)
    return {'groups':groups,'remove':roots,'groupCount':len(groups),'folderCount':len(roots),
            'fileCount':sum(p['files'] for p in roots),'bytes':sum(p['bytes'] for p in roots),
            'myResourcesFolderCount':sum(p['path'].startswith('/我的资源/') for p in roots),
            'exactGroupCount':sum(g['type']=='exact' for g in groups),
            'containmentGroupCount':sum(g['type']=='contains' for g in groups),
            'snapshotFinishedAt':dataset['meta']['snapshotFinishedAt']}
