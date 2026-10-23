"""Conservative package boundaries, determined BEFORE duplicate matching.

Only reviewed classification containers are traversed. A course/collection root
ends traversal even if a nested directory has a duplicate elsewhere. The result
is an inferred review boundary, not a claimed historical transfer record.
"""
CONTAINERS = {
    '/': '网盘根目录',
    '/我的资源': '用户说明：默认转存入口；其直接子目录按整包保留',
    '/01恩恩学习资料': '学习资料总分类',
    '/01恩恩学习资料/小孩资源': '多主题资源总分类',
    '/01恩恩学习资料/小孩资源/儿童动画片': '多部动画的分类容器',
    '/01恩恩学习资料/小孩资源/牛津树': '用户确认的自建分类容器；内部资料组单独识别',
    '/01恩恩学习资料/小孩资源/3岁': '按年龄归类的多套资源',
    '/01恩恩学习资料/小孩资源/3岁以后': '按年龄归类的多套资源',
    '/01恩恩学习资料/中文': '学科分类',
    '/01恩恩学习资料/数学': '学科分类',
    '/01恩恩学习资料/英文': '学科分类',
    '/01恩恩学习资料/点读包': '格式分类；内部编码目录继续受整包保护',
    '/01恩恩学习资料/粤语动画片': '多部动画的语言分类',
    '/01恩恩学习资料/tmp': '临时收纳分类',
    '/02英语学习': '成人英语资料分类',
    '/05技术': '技术资料分类',
    '/07软件': '软件资料分类',
    '/tmp': '临时收纳分类',
    '/00熏听': '受保护的使用入口',
    '/06照片': '受保护的照片分类',
    '/来自：iPhone': '受保护的手机来源',
    '/apps': '受保护的应用数据',
}
# Oxford's topical child groups are not independently verified transfer packages.
NON_ATOMIC_CONTAINERS = frozenset({'/01恩恩学习资料/小孩资源/牛津树'})


def deletion_candidate_boundary(boundary):
    return boundary['parentContainer'] not in NON_ATOMIC_CONTAINERS


PROTECTED = ('/00熏听','/06照片','/来自：iPhone','/apps')


def protected(path):
    return any(path == p or path.startswith(p+'/') for p in PROTECTED)


def infer_roots(directory_paths, containers=None):
    containers = CONTAINERS if containers is None else containers
    paths = set(directory_paths)
    roots = {}
    for path in sorted(paths, key=lambda p:(p.count('/'),p)):
        if path=='/' or path in containers:continue
        parent=path.rsplit('/',1)[0] or '/'
        # A nested container-looking name cannot cross an established package.
        if parent not in containers:continue
        ancestors=[p for p in roots if path.startswith(p+'/')]
        if ancestors:continue
        roots[path]={'path':path,'parentContainer':parent,'containerEvidence':containers[parent],
            'boundaryEvidence':'从分类容器进入首个资源目录后停止向下拆分；保留全部内部结构',
            'confidence':'推断，待核对','protected':protected(path)}
    return roots


def package_owner(path, roots):
    parts=path.split('/')
    for n in range(2,len(parts)+1):
        candidate='/'.join(parts[:n])
        if candidate in roots:return candidate
    return None


def exact_groups(tree, roots, stats):
    by_hash={}
    for p,boundary in roots.items():
        if stats[p]['files'] and not boundary['protected']:
            by_hash.setdefault(tree.nodes[p]['fingerprint'],[]).append(p)
    return [ps for ps in by_hash.values() if len(ps)>1]
