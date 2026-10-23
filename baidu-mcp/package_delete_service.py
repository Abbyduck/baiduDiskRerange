"""Deletion service restricted to the new atomic package candidates."""
import copy
import json

from dedup_review_server import OUT,ReviewManager,Cloud
from folder_dedup_plan import make_deletion_plan
from folder_dedup_plan import inside
from package_boundaries import PROTECTED,infer_roots


def service_dataset(data):
    roots={x['path']:x for x in data['packages'] if not x['protected']}
    cases=[]
    for original in data['exact']+data['contains']:
        c=copy.deepcopy(original)
        if any(x['path'] not in roots for x in c['copies']):raise ValueError('候选涉及包内子目录，不能启用。')
        c.update(protected=False,reviewState='pending',defaultKeep=c['copies'][0]['path'],
                 files=c['copies'][0]['files'],bytes=c['copies'][0]['bytes'])
        if c['type']=='contains':c.update(larger=[c['copies'][0]],smaller=[c['copies'][1]])
        cases.append(c)
    return {'meta':{'planScope':'whole-package-v1','protectedPaths':list(PROTECTED),
        'preservedRootEntries':['/我的资源','/01恩恩学习资料'],
        'snapshotFinishedAt':data['meta']['sourceScan']['completedAt'],
        'remainingUpdatedAt':data['meta']['createdAt']},'cases':cases}


def validate_package_plan(plan,data):
    roots={x['path']:x for x in data['packages'] if not x['protected']}
    trusted={c['id']:c for c in service_dataset(data)['cases']}
    for g in plan['groups']:
        c=trusted.get(g['id'])
        if not c:raise ValueError('此候选不在当前整包清单中。')
        if g['type']!=c['type']:raise ValueError('候选类型与可信证据不符。')
        if c['type']=='contains' and (g['keep']['path'] not in {x['path'] for x in c['larger']} or
            any(x['path'] not in {r['path'] for r in c['smaller']} for x in g['remove'])):
            raise ValueError('包含关系只能删除被收录的独立整包，必须保留收录包。')
        if g.get('matchWithin')!=c.get('matchWithin'):raise ValueError('包内对应位置与可信证据不符。')
        if g.get('matchWithin') and not inside(g['matchWithin']['path'],g['keep']['path']):
            raise ValueError('包内对应位置必须位于保留整包内。')
        allowed={x['path'] for x in c['copies']}
        for x in [g['keep'],*g['remove']]:
            root=roots.get(x['path'])
            if not root or x['path'] not in allowed or x['fingerprint']!=root['fingerprint']:
                raise ValueError('只允许新清单中的完整资源包，不能提交包内子目录。')


class PackageReviewManager(ReviewManager):
    def __init__(self,data=None,cloud_factory=Cloud,audit_dir=None):
        self.package_data=data or json.loads((OUT/'整包去重清单.json').read_text(encoding='utf-8'))
        super().__init__(service_dataset(self.package_data),cloud_factory=cloud_factory,
            expected_ids={x['path']:str(x['fsid']) for x in self.package_data['packages']},audit_dir=audit_dir)
        # Rolled-back legacy jobs are historical evidence, never pending work.
        self.jobs={key:j for key,j in self.jobs.items() if j.get('scope')=='whole-package-v1'}

    def start_prepare(self,decisions,run=True):
        validate_package_plan(make_deletion_plan(self.dataset,decisions),self.package_data)
        return super().start_prepare(decisions,run=run)

    def confirm(self,job_id,token,confirmed_count,run=True):
        job=self.jobs.get(job_id)
        if job:validate_package_plan(job['plan'],self.package_data)
        return super().confirm(job_id,token,confirmed_count,run=run)

    def verify(self,plan,tree):
        validate_package_plan(plan,self.package_data)
        current=infer_roots(tree.nodes)
        if any(x['path'] not in current for g in plan['groups'] for x in [g['keep'],*g['remove']]):
            return [{'path':x['path'],'reason':'实时目录不再是整包边界，未执行删除'}
                    for g in plan['groups'] for x in [g['keep'],*g['remove']] if x['path'] not in current]
        return super().verify(plan,tree)

    def state(self):
        with self.lock:
            known=self.known_state()
            if (not self.active_commit and not known.get('pending') and self.state_dir==OUT/'实时索引'
                and set(self.package_data['meta'].get('knownRemovedPaths',[]))!=set(known.get('removed',[]))):
                from build_package_review import main
                self.package_data=main(quiet=True)
                self.dataset=service_dataset(self.package_data)
                self.expected_ids={x['path']:str(x['fsid']) for x in self.package_data['packages']}
            latest=max((j for j in self.jobs.values() if j.get('confirmedAt')),key=lambda j:j['confirmedAt'],default=None)
            return {**known,'deletionDisabled':False,'mode':'package-roots-only',
                'datasetVersion':self.package_data['meta']['createdAt'],
                'lastJobId':latest['id'] if latest else None,'activeJobId':self.active_commit,
                'snapshotFinishedAt':self.dataset['meta']['snapshotFinishedAt']}
