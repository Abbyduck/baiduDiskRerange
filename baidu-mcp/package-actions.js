// Only package case IDs and approved keeper choices are sent to the server.
const service=window.PACKAGE_REVIEW_SERVICE;
const selected=new Set(),keepers=new Map();
let busy=false,currentJob=null,liveLoaded=false,knownRemoved=[],pending=[],lastDataset=D.meta.createdAt,pollKey=0;
const allCases=()=>[...D.exact,...D.contains];
const gone=p=>knownRemoved.some(r=>p===r||p.startsWith(r+'/'));
function keeper(c){return keepers.get(c.id)||c.copies[0].path}
function eligible(c){return c.copies.every(x=>!gone(x.path))}
function selectedPlan(){
 const groups=allCases().filter(c=>selected.has(c.id)&&eligible(c)).map(c=>{
  const keep=c.copies.find(x=>x.path===keeper(c));if(!keep)throw Error('保留路径不在本组。');
  return {id:c.id,title:c.title,type:c.type,keep,remove:c.type==='exact'?c.copies.filter(x=>x.path!==keep.path):[c.copies[1]]};
 });
 const remove=[...new Map(groups.flatMap(g=>g.remove).map(x=>[x.path,x])).values()];
 if(remove.some(x=>groups.some(g=>g.keep.path===x.path||g.keep.path.startsWith(x.path+'/'))))
  throw Error('所选建议的保留与删除路径冲突，请保留同一份完整包，或取消冲突的包含项。');
 return {groups,remove};
}
function packageControls(c){
 const keep=keeper(c);
 return `<div class="package-controls"><label><input type="checkbox" data-pick="${esc(c.id)}" ${selected.has(c.id)?'checked':''} ${busy||!liveLoaded||!eligible(c)?'disabled':''}>选择${c.type==='exact'?'这组整包副本':'这个独立整包'}</label>${c.type==='exact'?`<label>选择保留：<select data-keep="${esc(c.id)}" ${busy?'disabled':''}>${c.copies.map(x=>`<option value="${esc(x.path)}" ${keep===x.path?'selected':''}>${esc(x.path)}</option>`).join('')}</select></label>`:`<small>保留整个收录包：${esc(c.copies[0].path)}</small>`}<small>仅删除独立整包根目录；包内对应位置全部保留。</small></div>`;
}
function matchEvidence(c){
 if(!c.matchWithin)return '';
 const m=c.matchWithin,s=c.copies[1];
 return `<div class="path match-evidence"><strong>保留包中的对应位置 · 仅作比对证据</strong><code>${esc(m.path)}</code><small>${m.exactAtMatch?'对应位置与独立整包完全一致':'对应位置完整包含独立整包'} · 独立包 ${s.files.toLocaleString()} 个文件 → 对应位置 ${m.files.toLocaleString()} 个文件${c.addedFiles?'，另多 '+c.addedFiles+' 个文件':''}。此位置没有删除选项。</small>${c.referenceCount>1?`<details><summary>另有 ${c.referenceCount-1} 处收录位置（仅参考）</summary>${c.references.slice(1).map(r=>`<p><small>所属整包：${esc(r.keeper)}</small><code>${esc(r.matchPath)}</code></p>`).join('')}</details>`:''}</div>`;
}
function updateActions(rows=[]){
 const active=['exact','contains'].includes(tab);
 $('#batch-actions').hidden=!active;$('#select-packages').disabled=busy||!liveLoaded||!rows.length;
 const available=rows.filter(eligible);$('#select-packages').checked=!!available.length&&available.every(c=>selected.has(c.id));
 $('#select-packages').indeterminate=available.some(c=>selected.has(c.id))&&!available.every(c=>selected.has(c.id));
 $('#batch-label').textContent='全选当前筛选的 '+available.length+' 组整包（跨所有页）';
 let plan;try{plan=selectedPlan()}catch(e){$('#selection-summary').textContent=e.message;$('#review-packages').disabled=true;return}
 $('#selection-summary').textContent=plan.groups.length?'已选 '+plan.groups.length+' 组 · 删除 '+plan.remove.length+' 个整包根目录 · '+bytes(plan.remove.reduce((n,x)=>n+x.bytes,0)):'勾选整包，再核对保留和删除路径';
 $('#review-packages').disabled=busy||!liveLoaded||pending.length>0||!plan.remove.length;
}
async function api(path,data){
 if(!service)throw Error('请通过本地服务页面打开，才能确认删除。');
 const options={headers:{'X-Dedup-CSRF':service.csrf}};
 if(data!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}
 const response=await fetch(path,options);const result=await response.json();if(!response.ok)throw Error(result.error||'操作未能完成。');return result;
}
async function loadState(){
 const state=await api('/api/state');knownRemoved=state.removed||[];pending=state.pending||[];
 if(state.datasetVersion!==lastDataset){D=await api('/api/candidates');lastDataset=state.datasetVersion;
  const ids=new Set(allCases().map(x=>x.id));for(const id of selected)if(!ids.has(id))selected.delete(id);
  $('#exact-count').textContent=D.exact.length;$('#contains-count').textContent=D.contains.length;
 }
 liveLoaded=true;$('#service-status').textContent=pending.length?'上次提交仍在核验，暂缓新提交':knownRemoved.length?'已核实删除 '+knownRemoved.length+' 个整包 · 可继续核对':'整包删除可用 · 确认后执行';
 return state;
}
function showJob(job){
 currentJob=job;const p=job.plan;
 $('#confirmation-summary').textContent=p.folderCount+' 个整包根目录 · '+p.fileCount.toLocaleString()+' 个文件 · '+bytes(p.bytes);
 $('#confirmation-phase').textContent=job.phase+(job.progress?.entries?' · 已核对 '+job.progress.entries.toLocaleString()+' 个条目':'');
 $('#confirmation-paths').innerHTML=p.groups.map(g=>`<article class="confirm-group"><h3>${esc(g.id+' · '+g.title)}</h3><div class="confirm-keep"><b>保留整个包：</b><code>${esc(g.keep.path)}</code></div>${g.matchWithin?`<div class="confirm-keep"><b>包内对应位置（保留）：</b><code>${esc(g.matchWithin.path)}</code></div>`:''}${g.remove.map(x=>`<div class="confirm-remove"><b>删除独立整包：</b><code>${esc(x.path)}</code></div>`).join('')}</article>`).join('');
 $('#execute-packages').disabled=job.status!=='ready';
 $('#execute-packages').textContent=job.status==='ready'?'确认并删除 '+p.folderCount+' 个整包':job.status==='committing'?'正在执行与核验':'本次已结束';
 $('#confirmation-result').textContent=job.result?'已核实删除 '+job.result.removed.length+' 个；待核验 '+job.result.pending.length+' 个；尚未提交 '+job.result.notSubmitted.length+' 个。':'';
 if(job.mismatches)$('#confirmation-result').textContent=job.mismatches.map(x=>x.path+'：'+x.reason).join('\n');
}
async function pollJob(id,key){
 if(key!==pollKey)return;
 try{
  const job=await api('/api/jobs/'+id);showJob(job);
  if(['validating','committing'].includes(job.status)){setTimeout(()=>pollJob(id,key),1800);return}
  busy=false;await loadState();render();
 }catch(e){$('#confirmation-phase').textContent=e.message+'；已提交的请求不会重复提交。';setTimeout(()=>pollJob(id,key),5000)}
}
$('#list').addEventListener('change',e=>{
 if(e.target.dataset.pick){const id=e.target.dataset.pick;e.target.checked?selected.add(id):selected.delete(id);render()}
 if(e.target.dataset.keep){keepers.set(e.target.dataset.keep,e.target.value);render()}
});
$('#select-packages').onchange=e=>{const q=$('#search').value.trim().toLowerCase();for(const c of D[tab].filter(c=>JSON.stringify(c).toLowerCase().includes(q)&&eligible(c)))e.target.checked?selected.add(c.id):selected.delete(c.id);render()};
$('#clear-packages').onclick=()=>{selected.clear();render()};
$('#review-packages').onclick=async()=>{
 try{
  const p=selectedPlan();if(!p.remove.length)return;busy=true;render();
  const result=await api('/api/prepare',{decisions:p.groups.map(g=>({id:g.id,keep:g.keep.path}))});
  $('#package-confirm').showModal();$('#confirmation-phase').textContent='正在生成整包确认清单';
  $('#execute-packages').disabled=true;pollKey++;await pollJob(result.jobId,pollKey);
 }catch(e){busy=false;$('#service-status').textContent=e.message;render()}
};
$('#execute-packages').onclick=async()=>{
 if(currentJob?.status!=='ready')return;
 const job=currentJob;$('#execute-packages').disabled=true;busy=true;
 try{
  await api('/api/confirm-delete',{jobId:job.id,confirmationToken:job.confirmationToken,confirmedFolderCount:job.plan.folderCount});
  pollKey++;await pollJob(job.id,pollKey);
 }catch(e){busy=false;$('#confirmation-phase').textContent=e.message}
};
$('#close-confirmation').onclick=()=>$('#package-confirm').close();
async function initPackageActions(){
 if(!service){$('#service-status').textContent='静态预览；请通过本地服务页面打开以删除';return}
 try{const s=await loadState();render();if(s.activeJobId){busy=true;$('#package-confirm').showModal();pollKey++;pollJob(s.activeJobId,pollKey)}}
 catch(e){$('#service-status').textContent=e.message;render()}
}
initPackageActions();
