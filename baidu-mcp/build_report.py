"""Render the completed inventory into a proposal and a local searchable index."""
import collections
import html
import json
import sqlite3
from pathlib import Path

from analyze_inventory import OUT, ROOT, classify


def read_json(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))


def read_lines(name):
    return [json.loads(line) for line in (OUT/name).read_text(encoding='utf-8').splitlines()]


def fmt_size(value):
    for power,unit in [(4,'TiB'),(3,'GiB'),(2,'MiB'),(1,'KiB')]:
        if value>=1024**power:return f'{value/1024**power:.2f} {unit}'
    return f'{value} B'


def cell(text):
    return str(text).replace('|','\\|').replace('\n',' ')


def table(headers,rows):
    return '| '+' | '.join(headers)+' |\n| '+' | '.join('---' for _ in headers)+' |\n'+''.join('| '+' | '.join(cell(x) for x in row)+' |\n' for row in rows)


def main():
    summary=read_json('summary.json')
    if not summary.get('complete'):
        raise SystemExit('Refuse to publish an incomplete inventory.')
    units=read_lines('resource-units.jsonl')
    nonphoto=read_lines('file-understanding.jsonl')
    photos=read_json('photo-groups.json')
    zero=read_json('zero-byte-files.json')
    stats=read_json('directory-statistics.json')
    by_series=collections.defaultdict(lambda:{'files':0,'sources':set()})
    for row in nonphoto:
        group=by_series[row['suggested']]
        group['files']+=1;group['sources'].add(row['sourceUnit'])
    series_rows=[]
    for key in ['牛津树','波西和皮普','女巫温妮','培生朗文迪士尼','培生幼儿英语','悠游成长计划','比利和小怪兽','数字积木与奇幻积木','自然拼读','Pete the Cat','方块兔Super Rabbit Boy']:
        matches=[(dest,g) for dest,g in by_series.items() if dest.endswith('/'+key)]
        if not matches:continue
        dest,g=matches[0]
        sources=sorted(g['sources'],key=lambda p:(len(p),p))
        examples='；'.join('`'+p+'`' for p in sources[:3])
        if len(sources)>3:examples+=f'；另有 {len(sources)-3} 个来源单元'
        series_rows.append([key,examples,f"{g['files']:,}"])
    roots=summary['roots']
    root_rows=[]
    for root,s in sorted(roots.items()):
        destinations=collections.Counter()
        for row in nonphoto:
            if row['path'].startswith(root+'/'):
                destinations[row['suggested'].split('/')[0]]+=1
        if root=='/06照片' or root=='/来自：iPhone':
            destination='04_照片与家庭视频（来源组整体保留）'
        elif destinations:
            destination='；'.join(f'{d}（{n:,}）' for d,n in destinations.most_common())
        else:
            destination=classify(root,True)[0]+'（当前为空）'
        root_rows.append([f'`{root}`',f"{s['files']:,}",fmt_size(s['bytes']),destination])
    c=sqlite3.connect(OUT/'inventory.sqlite')
    pair_rows=[]
    pairs=[('/01恩恩学习资料/小孩资源/悠游','/01恩恩学习资料/小孩资源/悠游成长计划'),
           ('/我的资源/松果妈妈自然拼读课','/我的资源/松果妈妈自然拼读课(1)'),
           ('/我的资源/小小科学探索家 英语版','/我的资源/小小科学探索家 英语版(1)')]
    for a,b in pairs:
        def hashes(prefix):
            return {(m,n) for m,n in c.execute('select md5,size from nodes where isdir=0 and path like ?',(prefix+'/%',)) if m and n>0}
        x,y=hashes(a),hashes(b)
        note='唯一内容指纹集合重合，仍需保留文件名、版本及码号关系' if x and x==y else f'共有 {len(x&y):,} 项；B 另有 {len(y-x):,} 项唯一内容，A 另有 {len(x-y):,} 项'
        pair_rows.append([f'`{a}`',f'`{b}`',f'{len(x):,} / {len(y):,}',note])
    for name,path,size in c.execute("select name,path,size from nodes where parent='/' and isdir=0"):
        root_rows.append([f'`{path}`','1',fmt_size(size),classify(path)[0]])
    c.close()
    photo_rows=[[f"`{g['source']}`",f"{g['files']:,}",fmt_size(g['bytes']),f"`{g['suggested']}`"] for g in photos]
    low=[r for r in nonphoto if r['confidence']=='低']
    uncertain=collections.defaultdict(list)
    for row in low:uncertain[row['sourceUnit']].append(row['name'])
    uncertain_examples=[]
    for unit,names in sorted(uncertain.items(),key=lambda x:-len(x[1]))[:12]:
        uncertain_examples.append(f"- `{unit}`：{len(names)} 个低把握文件。示例："+'；'.join('`'+n+'`' for n in names[:3])+'。')
    template=(ROOT/'baidu-mcp/report-template.md').read_text(encoding='utf-8')
    replacements={
      'files':f"{summary['files']:,}",'folders':f"{summary['folders']:,}",
      'directories':f"{summary['directoriesIncludingRoot']:,}",'size':fmt_size(summary['bytes']),
      'photoGroups':str(summary['photoGroups']),'photoFiles':f"{summary['photoGroupFiles']:,}",
      'nonPhotoFiles':f"{summary['nonPhotoFiles']:,}",
      'seriesTable':table(['统一系列','实际来源示例','文件数'],series_rows),
      'photoTable':table(['原来源组','文件数','逻辑大小','建议位置'],photo_rows),
      'rootTable':table(['原根目录或文件','文件数','逻辑大小','建议分区及文件数'],root_rows),
      'pairTable':table(['来源 A','来源 B','A / B 唯一内容数','内容指纹对照'],pair_rows),
      'duplicateGroups':f"{summary['duplicateGroups']:,}",
      'duplicateCopies':f"{summary['duplicateExtraCopies']:,}",
      'duplicateSize':fmt_size(summary['duplicateCandidateExtraBytes']),
      'zeroFiles':str(summary['zeroByteFiles']),'archives':str(summary['archives']),
      'emptyDirs':str(summary['emptyLeafDirectories']),
      'zeroExamples':'\n'.join('- `'+z['path']+'`' for z in zero[:12]) or '- 无。',
      'uncertainExamples':'\n'.join(uncertain_examples) or '- 无低把握项目。',
    }
    for key,value in replacements.items():template=template.replace('{{'+key+'}}',str(value))
    if '{{' in template:raise RuntimeError('Unfilled report placeholder')
    (OUT/'目录重构建议.md').write_text(template,encoding='utf-8')
    dataset=[]
    for unit in units:
        dataset.append({'source':unit['source'],'files':unit['files'],'bytes':unit['bytes'],
                        'destinations':unit['destinations'],'examples':unit['examples'],
                        'lowConfidence':unit['lowConfidence']})
    payload=json.dumps(dataset,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    page=(ROOT/'baidu-mcp/index-template.html').read_text(encoding='utf-8')
    page=page.replace('%%DATA%%',payload).replace('%%SUMMARY%%',html.escape(f"普通文件区已完整遍历 · {summary['files']:,} 个文件 · {summary['folders']:,} 个文件夹 · {len(units):,} 个资源单元"))
    (OUT/'资源目录索引.html').write_text(page,encoding='utf-8')
    metadata={'files':summary['files'],'folders':summary['folders'],'units':len(units),
              'lowConfidenceFiles':len(low),'proposal':'目录重构建议.md','index':'资源目录索引.html'}
    (OUT/'report-metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metadata,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
