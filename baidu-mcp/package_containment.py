"""Find independent complete packages covered at a position in another package.

Only the independent source root can become a removal candidate. An internal
matching directory is evidence within the retained complete package, never a
removal target. All names, empty directories, sizes and content hashes matter.
"""
from collections import defaultdict
from package_boundaries import package_owner


def find_package_coverage(tree,roots,stats):
    owners={p:package_owner(p,roots) for p in tree.nodes}
    index=defaultdict(set)
    for p,n in tree.nodes.items():
        owner=owners[p]
        if not owner or roots[owner]['protected']:continue
        for name,x in n['children'].items():index[(name,x['isdir'])].add(p)
    output=[]
    for source,boundary in roots.items():
        if boundary['protected'] or not stats[source]['files']:continue
        candidates=None
        # Intersection of immediate relative names is a lossless prefilter.
        keys=sorted(((name,x['isdir']) for name,x in tree.nodes[source]['children'].items()),
                    key=lambda k:len(index[k]))
        for key in keys:
            candidates=set(index[key]) if candidates is None else candidates & index[key]
            if not candidates:break
        refs=[]
        for match in candidates or ():
            keeper=owners[match]
            if keeper==source:continue
            if any(stats[source][k]>stats[match][k] for k in ('files','folders','bytes')):continue
            if not tree.contains(source,match):continue
            refs.append({'keeper':keeper,'matchPath':match,
                'matchFingerprint':tree.nodes[match]['fingerprint'],'matchFsid':str(tree.nodes[match]['fsid']),
                'matchFiles':stats[match]['files'],'matchFolders':stats[match]['folders'],
                'matchBytes':stats[match]['bytes'],
                'exactAtMatch':tree.nodes[source]['fingerprint']==tree.nodes[match]['fingerprint']})
        if not refs:continue
        # Prefer a large retained collection to avoid chains of removal advice.
        refs.sort(key=lambda r:(-stats[r['keeper']]['files'],-stats[r['keeper']]['folders'],
            r['keeper'].startswith('/我的资源/'),not r['exactAtMatch'],
            len(r['matchPath']),r['keeper'],r['matchPath']))
        output.append({'source':source,'reference':refs[0],'references':refs})
    output.sort(key=lambda x:-stats[x['source']]['bytes'])
    return output
