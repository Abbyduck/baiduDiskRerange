"""Compare whole folder trees from the read-only Netdisk inventory snapshot.

No network writes, moves, or deletes. Root folder names are not part of a tree's
identity; all descendant names, empty folders, sizes, and server MD5 values are.
"""
import collections
import datetime as dt
import functools
import hashlib
import itertools
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from folder_dedup_plan import collapse_exact_folders, make_exact_plan

ROOT = Path(__file__).resolve().parents[1]
from project_paths import DATA_DIR

OUT = DATA_DIR
PROTECTED = ['/00熏听', '/06照片', '/来自：iPhone', '/apps']
PRESERVED_ENTRIES = ['/我的资源', '/01恩恩学习资料']


def inside(path, parent):
    return path == parent or path.startswith(parent + '/')


def normalized_title(name):
    # Candidate discovery only. Descendant names are compared without changes.
    name = unicodedata.normalize('NFKC', name).strip().casefold()
    name = re.sub(r'(?:\s*\(\d+\)|\s*[-_ ]?(?:副本|copy)(?:\s*\d+)?)$', '', name)
    return re.sub(r'\s+', '', name)


def protected(path):
    if path in PRESERVED_ENTRIES: return path
    return next((p for p in PROTECTED if inside(path, p)), '')


def preference(path):
    return (0 if path.startswith('/01恩恩学习资料/') else
            3 if path.startswith('/我的资源/') else 1,
            bool(re.search(r'\(\d+\)|（\d+）|副本', path.rsplit('/', 1)[-1])),
            path.count('/'), len(path), path)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def main():
    con = sqlite3.connect(f'file:{(OUT / "inventory.sqlite").as_posix()}?mode=ro', uri=True)
    assert not con.execute("select count(*) from directories where status!='done'").fetchone()[0]
    dirs = {'/': {'name': '/', 'children': {}, 'files': 0, 'folders': 0, 'bytes': 0, 'missing': 0}}
    for path, name in con.execute('select path,name from nodes where isdir=1'):
        dirs[path] = {'name': name, 'children': {}, 'files': 0, 'folders': 0, 'bytes': 0, 'missing': 0}
    file_count = 0
    for path, parent, name, isdir, size, md5 in con.execute('select path,parent,name,isdir,size,md5 from nodes'):
        assert parent in dirs
        if isdir:
            dirs[parent]['children'][name] = (1, path)
        else:
            dirs[parent]['children'][name] = (0, (size, md5 or ''))
            file_count += 1
    scan_finished = dict(con.execute('select * from run_info'))['finished_at']
    con.close()
    by_hash = collections.defaultdict(list)
    for path in sorted(dirs, key=lambda p: p.count('/') if p!='/' else 0, reverse=True):
        node = dirs[path]
        values = []
        node['codedAudio'] = False
        for name, (isdir, value) in sorted(node['children'].items()):
            if isdir:
                child = dirs[value]
                node['files'] += child['files']; node['folders'] += 1 + child['folders']
                node['bytes'] += child['bytes']; node['missing'] += child['missing']
                node['codedAudio'] |= child['codedAudio']
                values.append(('D', name, child['hash']))
            else:
                size, md5 = value
                node['files'] += 1; node['bytes'] += size; node['missing'] += not bool(md5)
                node['codedAudio'] |= bool(re.fullmatch(r'\d+\.(?:mp3|wav|m4a)',name,re.I))
                values.append(('F', name, size, md5))
        title = normalized_title(node['name'])
        code = re.match(r'^(\d{4,9})(?:[_\-]|$)',title)
        node['codeIdentity'] = (title if title.isdecimal() else code[1] if code else None) if node['codedAudio'] and any(t in path for t in ['小蝌蚪','点读','MP3格式mp4格式']) else None
        if node['codeIdentity'] is not None:
            values.append(('POINT_READING_DIRECTORY_CODE',node['codeIdentity']))
        node['hash'] = digest(values)
        if path != '/' and node['files']:
            by_hash[node['hash']].append(path)
    assert dirs['/']['files'] == file_count == 284034
    print(json.dumps({'stage': 'tree_fingerprints', 'folders': len(dirs)-1, 'files': file_count}), flush=True)

    @functools.lru_cache(maxsize=None)
    def subset(small, big):
        a, b = dirs[small], dirs[big]
        if a['codeIdentity'] != b['codeIdentity']: return False
        if a['files'] > b['files'] or a['folders'] > b['folders'] or a['bytes'] > b['bytes']:
            return False
        for name, (isdir, value) in a['children'].items():
            other = b['children'].get(name)
            if other is None or other[0] != isdir:
                return False
            if isdir:
                if not subset(value, other[1]): return False
            elif value != other[1]:
                return False
        return True

    @functools.lru_cache(maxsize=None)
    def content_counter(path):
        result = collections.Counter()
        for isdir, value in dirs[path]['children'].values():
            if isdir: result.update(content_counter(value))
            else: result[value] += 1
        return result

    def entries(path, prefix=''):
        for name, (isdir, value) in sorted(dirs[path]['children'].items()):
            relative = prefix + name
            if isdir:
                yield {'type': 'folder', 'relativePath': relative}
                yield from entries(value, relative + '/')
            else:
                yield {'type': 'file', 'relativePath': relative, 'bytes': value[0], 'serverMd5': value[1]}

    def stat(path):
        n = dirs[path]
        return {'path': path, 'files': n['files'], 'folders': n['folders'], 'bytes': n['bytes'],
                'protected': protected(path), 'fingerprint': n['hash']}

    def alternatives(paths):
        return [stat(p) for p in sorted(paths, key=preference)]

    cases = []
    exact_by_path = {}
    exact_groups = sorted((v for v in by_hash.values() if len(v)>1),
                          key=lambda v: (-dirs[v[0]]['bytes'], -dirs[v[0]]['files'], v[0]))
    for index, paths in enumerate(exact_groups, 1):
        paths = sorted(paths, key=preference)
        # Recheck actual tree entries after hashing; a hash alone never decides a case.
        assert all(subset(paths[0], p) and subset(p, paths[0]) for p in paths[1:])
        if any(dirs[p]['missing'] for p in paths): continue
        case_id = f'E{index:04d}'
        case = {'id': case_id, 'type': 'exact', 'title': dirs[paths[0]]['name'],
                'copies': alternatives(paths), 'files': dirs[paths[0]]['files'],
                'bytes': dirs[paths[0]]['bytes'], 'nestedUnder': None,
                'protected': any(protected(p) for p in paths),
                'reason': '全部相对路径、子目录（含空目录）、文件大小与服务端内容指纹完全一致。'}
        cases.append(case)
        for path in paths: exact_by_path[path] = case_id

    name_groups = collections.defaultdict(list)
    for path, node in dirs.items():
        if path != '/' and node['files']: name_groups[normalized_title(node['name'])].append(path)
    relations, content_relations, partials = {}, {}, []
    compared = 0
    for title, paths in name_groups.items():
        if len(paths)<2: continue
        for a, b in itertools.combinations(paths, 2):
            if inside(a,b) or inside(b,a): continue
            x,y=dirs[a],dirs[b]
            if x['hash']==y['hash']: continue
            compared += 1
            if (x['files'], x['folders'], x['bytes']) > (y['files'], y['folders'], y['bytes']): a,b=b,a; x,y=y,x
            key=(x['hash'],y['hash'])
            if key in relations or key in content_relations: continue
            if subset(a,b) and not x['missing'] and not y['missing']:
                relations[key]=(a,b)
            elif min(x['files'],y['files'])>=10 and len(title)>=6:
                ca,cb=content_counter(a),content_counter(b)
                if not (ca-cb):
                    content_relations[key]=(a,b,'content_equal' if ca==cb else 'content_contains')
                elif not (cb-ca):
                    content_relations[(y['hash'],x['hash'])]=(b,a,'content_contains')
                elif len(partials)<300:
                    partials.append({'a':stat(a),'b':stat(b),'commonFiles':sum((ca&cb).values()),
                                     'onlyA':sum((ca-cb).values()),'onlyB':sum((cb-ca).values())})
    print(json.dumps({'stage':'folder_comparisons','sameNamePairsCompared':compared,
                      'exactGroups':len(exact_groups),'strictRelations':len(relations),
                      'contentOnlyRelations':len(content_relations)}),flush=True)

    for index, ((small_hash,big_hash),(a,b)) in enumerate(sorted(relations.items(),key=lambda kv:-dirs[kv[1][0]]['bytes']),1):
        smaller=by_hash[small_hash];larger=by_hash[big_hash]
        # A containment is proven on the whole structure, including file multiplicity.
        assert subset(a,b) and not subset(b,a)
        small_entries={e['relativePath'] for e in entries(a)}
        extras=[e for e in entries(b) if e['relativePath'] not in small_entries]
        added_files=sum(e['type']=='file' for e in extras)
        added_folders=sum(e['type']=='folder' for e in extras)
        assert added_files==dirs[b]['files']-dirs[a]['files']
        assert added_folders==dirs[b]['folders']-dirs[a]['folders']
        important='玩出数感超能力' in dirs[a]['name'] and 'L1' in dirs[a]['name']
        cases.append({'id':f'I{index:04d}','type':'contains','title':dirs[a]['name'],
                      'smaller':alternatives(smaller),'larger':alternatives(larger),
                      'files':dirs[a]['files'],'bytes':dirs[a]['bytes'],'nestedUnder':None,
                      'protected':any(protected(p) for p in smaller+larger),
                      'addedFiles':added_files,'addedFolders':added_folders,
                      'addedBytes':dirs[b]['bytes']-dirs[a]['bytes'],
                      'extraExamples':extras if important else extras[:16],
                      'extraExamplesComplete':important or len(extras)<=16,
                      'reason':'较少一份的全部相对子目录、文件名、大小和服务端内容指纹，都在较多一份的对应位置找到；建议保留完整份、删除较少份。'})
        if important:
            evidence={'smaller':stat(a),'larger':stat(b),'strictContainment':True,
                      'matchedEntries':list(entries(a)),'extraEntries':extras}
            (OUT/'重点核对_超妈数感L1.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')

    # Hide subordinate cases only if a parent comparison covers every copy at
    # exactly the same relative location. Full records remain available in the UI.
    by_id={c['id']:c for c in cases}
    def ancestors(path):
        parent=path.rsplit('/',1)[0]
        while parent:
            yield parent,path[len(parent):]
            parent=parent.rsplit('/',1)[0]
    containment_pairs={}
    containment_small=collections.defaultdict(set)
    containment_large=collections.defaultdict(set)
    for c in cases:
        if c['type']=='contains':
            for s in c['smaller']:
                containment_small[s['path']].add(c['id'])
                for b in c['larger']: containment_pairs[(s['path'],b['path'])]=c['id']
            for b in c['larger']:containment_large[b['path']].add(c['id'])
    for c in cases:
        cover=None
        if c['type']=='exact':
            common=None
            for item in c['copies']:
                keys={(exact_by_path[p],suffix) for p,suffix in ancestors(item['path']) if p in exact_by_path}
                common=keys if common is None else common & keys
            if common:
                cover=max((k[0] for k in common),key=lambda key:by_id[key]['bytes'])
        else:
            common=None
            for role,items in [(containment_small,c['smaller']),(containment_large,c['larger'])]:
                for item in items:
                    keys=set()
                    for parent,suffix in ancestors(item['path']):
                        if parent in exact_by_path:keys.add((exact_by_path[parent],suffix))
                        keys.update((key,suffix) for key in role.get(parent,()))
                    common=keys if common is None else common & keys
            if common:cover=max((key[0] for key in common),key=lambda key:by_id[key]['bytes'])
        if cover and cover!=c['id']: c['nestedUnder']=cover

    for index, ((small_hash,big_hash),(a,b,kind)) in enumerate(sorted(content_relations.items(),key=lambda kv:-dirs[kv[1][0]]['bytes']),1):
        ca,cb=content_counter(a),content_counter(b)
        assert not (ca-cb)
        small_entries={e['relativePath']:(e['type'],e.get('bytes'),e.get('serverMd5')) for e in entries(a)}
        big_entries={e['relativePath']:(e['type'],e.get('bytes'),e.get('serverMd5')) for e in entries(b)}
        examples=[path for path,value in small_entries.items() if big_entries.get(path)!=value]
        cases.append({'id':f'R{index:04d}','type':kind,'title':dirs[a]['name'],
                      'smaller':alternatives(by_hash[small_hash]),'larger':alternatives(by_hash[big_hash]),
                      'files':dirs[a]['files'],'bytes':dirs[a]['bytes'],'nestedUnder':None,
                      'protected':any(protected(p) for p in by_hash[small_hash]+by_hash[big_hash]),
                      'addedFiles':sum((cb-ca).values()),'differentPathExamples':examples[:16],
                      'reason':'按文件大小与内容指纹逐份计数可完整覆盖，但相对目录或名称存在差异；先核对目录用途、顺序与点读关系，再决定是否删除较少份。'})

    raw_cases=cases
    highest_exact,covered_exact_ids=collapse_exact_folders(raw_cases)
    cases=highest_exact+[c for c in raw_cases if c['type']!='exact' and not c['nestedUnder']]
    cases.sort(key=lambda c:(bool(c['nestedUnder']),c['protected'],c['type'].startswith('content'),-c['bytes'],c['id']))
    primary=[c for c in cases if not c['nestedUnder'] and not c['protected'] and c['type'] in ('exact','contains')]
    counts=collections.Counter(c['type'] for c in cases)
    meta={'snapshotFinishedAt':scan_finished,'createdAt':dt.datetime.now().astimezone().isoformat(timespec='seconds'),
          'scannedFolders':len(dirs)-1,'scannedFiles':file_count,'missingFileFingerprints':dirs['/']['missing'],
          'exactGroups':counts['exact'],'strictContainmentGroups':counts['contains'],
          'rawExactGroups':sum(c['type']=='exact' for c in raw_cases),'coveredExactGroups':len(covered_exact_ids),
          'contentOnlyReviewGroups':counts['content_equal']+counts['content_contains'],
          'primaryCases':len(primary),'nestedCases':sum(bool(c['nestedUnder']) for c in raw_cases),
          'protectedCases':sum(c['protected'] for c in cases),'emptyFoldersExcluded':sum(n['files']==0 for p,n in dirs.items() if p!='/'),
          'sameNamePairsCompared':compared,'protectedPaths':PROTECTED,'preservedRootEntries':PRESERVED_ENTRIES,
          'scope':'全部非空目录检查完整结构重复；同名、空格差异及常见副本后缀进一步检查结构包含和内容覆盖。压缩包不展开。',
          'matchRule':'普通目录自身名称不参与，码号命名且含数字音频的点读目录保留自身码号身份；其下全部相对路径、文件名、空目录、大小、服务端MD5逐项对应。内容覆盖另按多重集合计数。',
          'pointReadingCodeDirectories':sum(n['codeIdentity'] is not None for n in dirs.values()),
          'cloudMutations':0,'warnings':['统计为重叠候选，不能直接相加为可释放空间。','基于盘点快照，实际删除前应重新核对云端状态。']}
    dataset={'meta':meta,'cases':cases,'partialExamples':partials}
    exact_default=make_exact_plan(dataset,[{'id':c['id'],'keep':c['defaultKeep']} for c in highest_exact if not c['protected']])
    meta['exactBulkPreview']={key:exact_default[key] for key in ['groupCount','folderCount','fileCount','bytes','myResourcesFolderCount']}
    (OUT/'文件夹去重证据_全部层级.json').write_text(json.dumps({'meta':meta,'cases':raw_cases},ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'完全重复_默认删除预览.json').write_text(json.dumps(exact_default,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'文件夹去重清单.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    payload=json.dumps(dataset,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    template=(ROOT/'baidu-mcp/folder-dedup-template.html').read_text(encoding='utf-8')
    app=(ROOT/'baidu-mcp/folder-dedup-app.js').read_text(encoding='utf-8')
    (OUT/'文件夹去重清单.html').write_text(template.replace('%%DATA%%',payload).replace('%%APP%%',app),encoding='utf-8')
    lines=['# 文件夹去重建议','',f"基于 {scan_finished} 的完整盘点快照，未删除任何网盘文件。",'',
           f"扫描 {meta['scannedFolders']:,} 个文件夹、{file_count:,} 个文件。完整重复 {counts['exact']:,} 组；严格包含 {counts['contains']:,} 组；目录差异需复核 {meta['contentOnlyReviewGroups']:,} 组。",'',
           f"优先复核 {len(primary):,} 组未被上层比较覆盖、且不涉及受保护来源的严格候选。父子候选可能重叠，不直接相加空间。",'',
           '00熏听、照片来源组与 apps 仅展示比对，不默认建议删除。空目录不单独作为删除候选。','',
           '全部交互筛选、逐项路径与选择请打开：文件夹去重清单.html。','', '## 优先候选（前 100 组，完整清单见 HTML / JSON）','']
    for c in primary[:100]:
        lines.extend([f"### {c['id']} · {'完整重复' if c['type']=='exact' else '完整包含'} · {c['title']}",''])
        if c['type']=='exact':
            lines.extend([f"每份 {c['files']:,} 个文件，建议保留：`{c['copies'][0]['path']}`。",'', '其余相同副本：'])
            lines.extend('- `'+x['path']+'`' for x in c['copies'][1:])
        else:
            lines.extend([f"建议保留：`{c['larger'][0]['path']}`（{c['larger'][0]['files']:,} 个文件）。",'',
                          f"建议删除较少份：`{c['smaller'][0]['path']}`（{c['files']:,} 个文件）。",'',
                          f"完整份额外有 {c['addedFiles']:,} 个文件、{c['addedFolders']:,} 个子目录。"])
        lines.append('')
    (OUT/'文件夹去重建议.md').write_text('\n'.join(lines),encoding='utf-8')
    validation={'allFilesAccountedFor':file_count==284034,'allCasesRechecked':True,
                'fingerprintsPresentForAllFiles':dirs['/']['missing']==0,
                'cloudMutations':0,'caseCounts':dict(counts),'primaryCases':len(primary),
                'importantMathCase':next((c['id'] for c in cases if c['type']=='contains' and '玩出数感超能力' in c['title'] and 'L1' in c['title']),None)}
    (OUT/'folder-dedup-validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(meta,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
