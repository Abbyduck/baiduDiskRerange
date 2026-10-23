"""Durable, read-only directory checks. No cloud mutations live in this module."""
import hashlib
import json
import time
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows indexers can briefly hold the old checkpoint after replacement.
    # Retry only this local file operation; never retry a cloud mutation here.
    for attempt in range(7):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt==6:raise
            time.sleep(min(.05*2**attempt,.8))


def metadata(item):
    """Keep inventory fields only, never download URLs or session credentials."""
    return {'path': item['path'], 'fsid': str(item.get('fs_id', item.get('fsid', ''))),
            'name': item.get('server_filename', item.get('name', item['path'].rsplit('/', 1)[-1])),
            'isdir': item.get('isdir'), 'size': item.get('size', 0), 'md5': item.get('md5', ''),
            'mtime': item.get('server_mtime', item.get('mtime')),
            'ctime': item.get('server_ctime', item.get('ctime'))}


def check_paths(cloud, paths, expected_ids, cache_dir, progress=None, completed_ids=None):
    """Prove path absence using complete immediate-parent listings, not file counts.

    A successful asynchronous delete with matching original path and ID, plus an
    omitted original ID in a successful metadata response, also proves removal
    of that original copy. A newly created item at the same name is not targeted.
    This checks existence/identity only. It is not a substitute for a recursive
    fingerprint comparison before deletion. A missing parent must be established
    recursively from its own parent; read errors never count as absent paths.
    Every completed listing is durable for auditing, but a later check refreshes it.
    """
    cache_dir = Path(cache_dir)
    listings, states = {}, {}
    reads = 0
    metadata_ids=list(dict.fromkeys(expected_ids[p] for p in paths if expected_ids.get(p)))
    meta=cloud.metadata_by_id(metadata_ids) if hasattr(cloud,'metadata_by_id') and metadata_ids else {}
    if meta:atomic_json(cache_dir/'metadata-response.json',{'receivedAt':cloud.timestamp(),'items':list(meta.values())})

    def parent_of(path):
        return path.rsplit('/', 1)[0] or '/'

    def listing(path):
        nonlocal reads
        if path in listings:
            return listings[path]
        if path != '/':
            ancestor = listing(parent_of(path))
            node = ancestor.get(path)
            if not node or node['isdir'] != 1:
                listings[path] = {}
                return listings[path]
        items = cloud.list_directory(path)
        children = {x['path']: x for x in items}
        if len(children) != len(items) or any(parent_of(x) != path for x in children):
            raise ValueError('目录列表的路径或分页不完整，不能判断删除结果。')
        listings[path] = children
        reads += 1
        record = {'path': path, 'checkedAt': cloud.timestamp(), 'complete': True, 'items': items}
        atomic_json(cache_dir / ('parent-' + hashlib.sha256(path.encode()).hexdigest() + '.json'), record)
        if progress:
            progress(reads, len(states), f'按父目录核对结果：已完成 {reads} 个目录、{len(states)} 个目标')
        return children

    for path in dict.fromkeys(paths):
        by_id=meta.get(expected_ids.get(path))
        # Returned IDs prove current existence. Omitted IDs alone do not prove
        # deletion; complete parent listings provide the absence evidence.
        proved_original_removed=bool(expected_ids.get(path)) and hasattr(cloud,'metadata_by_id') and (completed_ids or {}).get(path)==expected_ids[path] and expected_ids[path] not in meta
        current = None if proved_original_removed else by_id if by_id and by_id.get('path')==path else listing(parent_of(path)).get(path)
        if current is None:
            state = {'path': path, 'state': 'absent', 'checkedAt': cloud.timestamp()}
            if proved_original_removed:state['evidence']='task-success-for-original-id-and-original-id-not-returned'
        else:
            same_id = not expected_ids.get(path) or current['fsid'] == expected_ids[path]
            state = {'path': path, 'state': 'present' if same_id else 'replaced',
                     'fsid': current['fsid'], 'checkedAt': cloud.timestamp()}
        states[path] = state
        atomic_json(cache_dir / 'path-check-progress.json',
                    {'updatedAt': cloud.timestamp(), 'checked': len(states), 'targetCount': len(set(paths)),
                     'parentReads': reads, 'states': list(states.values())})
    return {'checkedAt': cloud.timestamp(), 'parentReads': reads, 'states': list(states.values()),
            'verification': 'task-success-and-original-id-check-or-complete-parent-listing; existence-and-identity-only'}
