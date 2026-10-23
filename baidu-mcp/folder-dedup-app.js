'use strict';
const DATA=JSON.parse(document.getElementById('dedup-data').textContent),$=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>Number(n).toLocaleString('zh-CN');
const size=n=>n>=1024**4?(n/1024**4).toFixed(2)+' TiB':n>=1024**3?(n/1024**3).toFixed(2)+' GiB':n>=1024**2?(n/1024**2).toFixed(1)+' MiB':n>=1024?(n/1024).toFixed(1)+' KiB':n+' B';
const inside=(p,root)=>p===root||p.startsWith(root+'/');
const names={exact:'完整重复 · 最高层',contains:'严格包含',content_equal:'内容相同 · 目录不同',content_contains:'内容包含 · 目录不同'};
const caseMap=new Map(DATA.cases.map(c=>[c.id,c]));
const allPaths=c=>c.type==='exact'?[...c.copies,...(c.allReferenceCopies||[])]:[...c.smaller,...c.larger];
const protectedPath=p=>DATA.meta.protectedPaths.some(r=>inside(p,r))||(DATA.meta.preservedRootEntries||[]).includes(p);
const storageKey='baidu-folder-dedup-v2-'+DATA.meta.snapshotFinishedAt;
let chosen={},resolved=new Map(),type='strict',page=0,filtered=[],toastTimer,deleteJob=null,pollGeneration=0;
let knownRemoved=DATA.meta.knownRemovedPaths||[],knownPending=[],liveStateLoaded=!window.DEDUP_REVIEW_SERVICE,lastJobId=null,lastRenderedPlan='';
const isRemoved=path=>knownRemoved.some(root=>inside(path,root));
function caseState(c){
 if(c.protected)return 'protected';
 if(c.reviewState==='completed')return 'completed';
 if(c.type!=='exact'&&c.smaller.every(p=>isRemoved(p.path)))return 'completed';
 if(c.type==='exact'){const keep=defaultKeep(c);return c.copies.some(p=>p.path!==keep.path&&!isRemoved(p.path))?'pending':'completed';}
 return c.type==='contains'&&c.reviewState!=='review'?'pending':'review';
}
const actionable=c=>caseState(c)==='pending'&&['exact','contains'].includes(c.type);
function chooseType(next){type=next;$('#query').value='';history.replaceState(null,'','#'+type);update();}
function pendingCounts(){const out={exact:0,contains:0,review:0,completed:0,protected:0};for(const c of DATA.cases){const state=caseState(c);out[state==='pending'?c.type:state]++;}return out;}
function updateCounts(){
 const n=pendingCounts();
 for(const [id,value] of Object.entries({'stat-exact':n.exact,'stat-contains':n.contains,'stat-review':n.review,'stat-primary':n.completed,'count-strict':n.exact+n.contains,'count-exact':n.exact,'count-contains':n.contains,'count-review':n.review,'count-completed':n.completed,'count-protected':n.protected}))$('#'+id).textContent=fmt(value);
 $('#next-step-title').textContent=n.exact?'还有 '+fmt(n.exact)+' 组完全重复，可以继续核对':n.contains?'完全重复已完成，下一步核对严格包含':'自动判断的候选已处理，接下来人工复核';
 $('#next-step-copy').textContent=n.contains?'剩余 '+fmt(n.contains)+' 组严格包含：勾选较少份，查看保留与删除路径，再确认执行。另有 '+fmt(n.review)+' 组目录差异待人工复核。':'剩余 '+fmt(n.review)+' 组需逐组核对目录与独有内容。已完成项可以在“已完成”中查看。';
 $('#next-exact').textContent='核对完全重复（'+fmt(n.exact)+' 组）';$('#next-exact').hidden=!n.exact;
 $('#next-contains').textContent='继续核对严格包含（'+fmt(n.contains)+' 组）';$('#next-contains').hidden=!n.contains;
 $('#next-review').textContent='查看人工复核（'+fmt(n.review)+' 组）';$('#next-review').hidden=!n.review;
}
const keepChoices=new Map(),pageSize=16;
function notify(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);}
function orderedCases(ids){return ids.map(id=>caseMap.get(id)).filter(Boolean).sort((a,b)=>((b.copies?.[0]||b.smaller[0]).files+(b.copies?.[0]||b.smaller[0]).folders)-((a.copies?.[0]||a.smaller[0]).files+(a.copies?.[0]||a.smaller[0]).folders)||b.bytes-a.bytes||a.id.localeCompare(b.id));}
function defaultKeep(c,removed=[]){
 if(c.type==='exact'&&c.allReferenceCopies?.length){const refs=c.allReferenceCopies.filter(p=>!isRemoved(p.path)&&!removed.some(r=>inside(p.path,r)));refs.sort((a,b)=>Number(a.path.startsWith('/我的资源/'))-Number(b.path.startsWith('/我的资源/'))||Number(!a.path.startsWith('/01恩恩学习资料/'))-Number(!b.path.startsWith('/01恩恩学习资料/'))||a.path.split('/').length-b.path.split('/').length||a.path.length-b.path.length||a.path.localeCompare(b.path));if(!refs.length)throw Error('保留父包的选择使参照路径失效，请调整选择。');return refs[0];}
 const paths=(c.type==='exact'?c.copies:c.larger).filter(p=>!isRemoved(p.path)),desired=chosen[c.id]?.keep||keepChoices.get(c.id)||c.defaultKeep;return paths.find(p=>p.path===desired)||paths[0];
}
function computePlans(selection){const plans=new Map(),removedExact=[];for(const c of orderedCases(Object.keys(selection))){if(!actionable(c))continue;const paths=(c.type==='exact'?c.copies:c.larger).filter(p=>!isRemoved(p.path));let keep;if(c.type==='exact'&&c.allReferenceCopies?.length)keep=defaultKeep(c,removedExact);else keep=paths.find(p=>p.path===(selection[c.id]?.keep||keepChoices.get(c.id)||c.defaultKeep))||paths[0];const remove=(c.type==='exact'?c.copies:c.smaller).filter(p=>p.path!==keep.path&&!isRemoved(p.path));if(!remove.length)continue;if(remove.some(p=>protectedPath(p.path)))throw Error('涉及受保护目录，未加入删除选择。');plans.set(c.id,{id:c.id,type:c.type,title:c.title,keep:keep.path,keepItem:keep,remove});if(c.type==='exact')removedExact.push(...remove.map(p=>p.path));}
 const exactPlans=[...plans.values()];const keepPaths=exactPlans.map(p=>p.keep);for(const p of exactPlans.flatMap(p=>p.remove))if(keepPaths.some(k=>inside(k,p.path)))throw Error('删除目录覆盖了某个保留目录，请调整选择。');return plans;}
function save(){try{localStorage.setItem(storageKey,JSON.stringify(chosen));}catch{notify('浏览器无法保存选择，可使用导出功能。');}}
function applySelection(next){try{const newPlans=computePlans(next);chosen=next;resolved=newPlans;save();updateDraft();return true;}catch(e){notify(e.message);return false;}}
function currentKeep(c){return resolved.get(c.id)?.keepItem||defaultKeep(c,[...resolved.values()].filter(p=>p.type==='exact').flatMap(p=>p.remove.map(x=>x.path)));}
function roots(plans){const values=[...new Map(plans.flatMap(p=>p.remove).map(p=>[p.path,p])).values()].sort((a,b)=>a.path.length-b.path.length);return values.filter((p,i)=>!values.slice(0,i).some(r=>inside(p.path,r.path)));}
function updateDraft(){
 const selected=[...resolved.values()],exact=selected.filter(p=>p.type==='exact'),contained=selected.filter(p=>p.type==='contains'),remove=roots(selected);
 $('#draft-summary').textContent=selected.length?'已选 '+fmt(exact.length)+' 组完全重复、'+fmt(contained.length)+' 组严格包含 · 合并为 '+fmt(remove.length)+' 个删除目录 · '+size(remove.reduce((n,p)=>n+p.bytes,0)):'勾选待处理目录，再核对保留与删除路径';
 $('#count-selected').textContent=selected.length;$('#review-delete').disabled=!selected.length||!liveStateLoaded||knownPending.length>0;
 $('#review-delete').textContent=contained.length&&!exact.length?'核对并删除所选较少份':'核对并删除所选副本';
}
function toggleChoice(id){const c=caseMap.get(id);if(!c||c.protected||!['exact','contains'].includes(c.type))return;const next={...chosen};if(next[id])delete next[id];else next[id]={id,keep:currentKeep(c).path};if(applySelection(next))update();}
const searches=new Map();function searchText(c){if(!searches.has(c.id))searches.set(c.id,[c.id,c.title,...allPaths(c).map(p=>p.path)].join(' ').toLocaleLowerCase());return searches.get(c.id);}
function update(){
 updateCounts();
 const q=$('#query').value.trim().toLocaleLowerCase(),protectedOn=$('#include-protected').checked,sort=$('#sort').value,onlyPending=$('#only-pending').checked;
 filtered=DATA.cases.filter(c=>{const state=caseState(c);if(type==='completed')return state==='completed'&&(!q||searchText(c).includes(q));if(type==='selected')return resolved.has(c.id)&&(!q||searchText(c).includes(q));if(type==='protected'){if(!c.protected)return false;}else{if(!protectedOn&&c.protected)return false;if(onlyPending&&state==='completed')return false;}if(type==='strict'&&!['exact','contains'].includes(c.type))return false;if(['exact','contains'].includes(type)&&c.type!==type)return false;if(type==='review'&&state!=='review')return false;return !q||searchText(c).includes(q);});
 filtered.sort(sort==='name'?(a,b)=>a.title.localeCompare(b.title,'zh-CN'):sort==='files'?(a,b)=>b.files-a.files:(a,b)=>b.bytes-a.bytes);page=0;render();
}
function keepSelect(c,paths){return `<label><span class="sr">${esc(c.id)} 选择保留的路径</span><select data-keep="${c.id}">${paths.filter(p=>!isRemoved(p.path)).map(p=>`<option value="${esc(p.path)}" ${currentKeep(c).path===p.path?'selected':''}>保留：${esc(p.path)}</option>`).join('')}</select></label>`;}
function evidence(c){if(c.type==='contains')return `<p>完整份额外有 <b>${fmt(c.addedFiles)} 个文件、${fmt(c.addedFolders)} 个子目录</b>，多出 ${size(c.addedBytes)}。</p><p>${c.extraExamplesComplete?'全部新增条目：':'新增条目示例：'}</p><ul>${c.extraExamples.map(x=>`<li><code>${esc(x.relativePath)}${x.type==='folder'?'/':''}</code>${x.type==='file'?' · '+size(x.bytes):''}</li>`).join('')}</ul>`;if(c.type.startsWith('content'))return `<p>${esc(c.currentNote||'目录结构不同，需要人工核对，暂不加入批量删除。')}</p><ul>${c.differentPathExamples.map(p=>'<li>'+esc(p)+'</li>').join('')}</ul>`;return '<p>整棵子树已逐项比较：相对路径、文件名、空目录、文件大小、服务端内容指纹和份数均相同。子目录的重复已向上合并，不再单列。'+(c.coveredCopyCount?'同一组另有 '+c.coveredCopyCount+' 处副本在更高层重复包内，已由父包处理。':'')+'</p>';}
function renderCase(c){
 const state=caseState(c),done=state==='completed',review=state==='review',keep=currentKeep(c),selected=resolved.has(c.id);let compare,meta;
 if(c.type==='exact'){
  const remove=c.copies.filter(p=>p.path!==keep.path);meta=done?'已完成':c.copies.length+' 处副本';
  compare=`<div class="compare"><div class="folder"><div class="folder-label">保留一份</div><div class="path">${esc(keep.path)}</div><div class="num">${fmt(keep.files)} 个文件 · ${size(keep.bytes)}</div>${!done&&!c.allReferenceCopies?.length?keepSelect(c,c.copies):''}</div><div class="folder remove"><div class="folder-label">${done?'已核实移除':'待处理'} ${remove.length} 处</div><div class="same-paths">${remove.map(p=>'<div class="path">'+(isRemoved(p.path)?'✓ 已移除：':'待处理：')+esc(p.path)+'</div>').join('')}</div></div></div>`;
 }else if(review||c.protected){
  meta='两侧目录需核对';compare=`<div class="compare"><div class="folder"><div class="folder-label">对照目录 A</div>${c.smaller.map(p=>'<div class="path">'+esc(p.path)+'</div><div class="num">'+fmt(p.files)+' 个文件 · '+size(p.bytes)+'</div>').join('')}</div><div class="folder remove"><div class="folder-label">对照目录 B</div>${c.larger.map(p=>'<div class="path">'+esc(p.path)+'</div><div class="num">'+fmt(p.files)+' 个文件 · '+size(p.bytes)+'</div>').join('')}</div></div>`;
 }else{
  meta=fmt(c.smaller[0].files)+' → '+fmt(keep.files)+' 个文件';compare=`<div class="compare"><div class="folder"><div class="folder-label">保留 · 已核实覆盖较少份</div><div class="path">${esc(keep.path)}</div><div class="num">${fmt(keep.files)} 个文件 · ${size(keep.bytes)}</div>${!done&&c.larger.length>1?keepSelect(c,c.larger):''}</div><div class="folder remove"><div class="folder-label">${done?'已处理':'建议删除 · 较少份'}</div>${c.smaller.map(p=>'<div class="path">'+esc(p.path)+'</div><div class="num">'+fmt(p.files)+' 个文件 · '+size(p.bytes)+'</div>').join('')}</div></div>`;
 }
 const action=done?'<span class="subtle">已完成，默认不再显示；可在此查看历史。</span>':c.protected?'<span class="subtle">受保护来源保留，不加入删除选择。</span>':review?'<span class="subtle">先核对目录及独有内容，暂不加入删除。</span>':`<label class="pick-case"><input type="checkbox" data-pick="${c.id}" ${selected?'checked':''}>${c.type==='contains'?'选中较少份，保留完整份':'选中这组重复副本'}</label><small>先查看路径，最后确认后才执行</small>`;
 return `<article class="card ${selected?'selected':''}" data-case="${c.id}"><div class="card-top"><div><span class="case-id">${c.id}</span><span class="badge ${review?'amber':''}">${done?'已完成':names[c.type]}</span>${c.protected?' <span class="badge gray">受保护 · 保留</span>':''}</div><span class="meta">${meta}</span></div><h2>${esc(c.title)}</h2><p class="reason">${esc(c.currentNote||c.referenceExplanation||c.reason)}</p>${compare}<details><summary>${c.type==='contains'?'展开多出的内容与比对证据':'展开比对说明'}</summary><div class="evidence">${evidence(c)}</div></details><div class="actions">${action}</div></article>`;
}
function render(){
 const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.min(page,pages-1);
 $('#cases').innerHTML=filtered.slice(page*pageSize,(page+1)*pageSize).map(renderCase).join('')||'<div class="empty">这一类已没有待处理项目。<br><button class="button secondary" type="button" data-type="contains">继续查看严格包含</button> <button class="text-button" type="button" data-type="completed">查看已完成记录</button></div>';
 $('#result-count').textContent='匹配 '+fmt(filtered.length)+' 组 · '+(type==='completed'?'已完成记录':$('#only-pending').checked?'只显示尚未处理的建议':'包含已完成记录');
 $('#page-number').textContent=(page+1)+' / '+pages;$('#prev').disabled=page===0;$('#next').disabled=page===pages-1;
 for(const b of $$('[data-type]'))b.classList.toggle('active',b.dataset.type===type);
 const eligible=filtered.filter(actionable);$('#batchbar').hidden=!['exact','contains','strict'].includes(type)||!eligible.length;
 $('#select-all-exact').disabled=!eligible.length;$('#select-all-exact').checked=!!eligible.length&&eligible.every(c=>!!chosen[c.id]);$('#select-all-exact').indeterminate=eligible.some(c=>chosen[c.id])&&!eligible.every(c=>chosen[c.id]);
 $('#batch-scope').textContent='全选当前筛选的 '+fmt(eligible.length)+' 组待处理建议（跨所有页）';
 $('#batch-note').textContent=type==='contains'?'只删除被覆盖的较少份，执行前实时核对':'保留一份完整副本，执行前实时核对';
}
function showCase(id){const c=caseMap.get(id);if(!c){if(/^E\d+$/.test(id))notify('这个子目录已合并到最高层文件夹，不再单列。');return;}type=c.type.startsWith('content')?'review':c.type;$('#include-protected').checked=c.protected;if(caseState(c)==='completed')type='completed';$('#query').value=id;history.replaceState(null,'','#'+id);update();$('#result-count').scrollIntoView({block:'start'});}
function showDraft(){const plans=[...resolved.values()];$('#draft-content').innerHTML=plans.length?plans.map(p=>`<article class="draft-case"><h3>${esc(p.id+' · '+p.title)}</h3><p>保留：${esc(p.keep)}</p>${p.remove.map(r=>'<p>建议删除：'+esc(r.path)+'</p>').join('')}<button type="button" class="text-button" data-remove-choice="${p.id}">撤销这组选择</button></article>`).join(''):'<p class="subtle">还没有选择删除副本。</p>';$('#draft-dialog').showModal();}
function exportDraft(){const plans=[...resolved.values()];if(!plans.length){notify('请先选择重复副本。');return;}const output={kind:'folder-dedup-review-draft',executionAuthorized:false,cloudMutations:0,requiresLiveRevalidation:true,snapshotFinishedAt:DATA.meta.snapshotFinishedAt,exportedAt:new Date().toISOString(),decisions:plans,nonOverlappingSuggestedDeletions:roots(plans)};const url=URL.createObjectURL(new Blob(['\ufeff'+JSON.stringify(output,null,2)],{type:'application/json;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='文件夹去重_我的选择.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('已导出选择，导出本身不执行删除。');}
const service=window.DEDUP_REVIEW_SERVICE;
async function api(path,data){const options={headers:{'X-Dedup-CSRF':service.csrf}};if(data!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}const response=await fetch(path,options);const result=await response.json();if(!response.ok)throw Error(result.error||'操作未完成。');return result;}
function renderDeleteJob(job){
 deleteJob=job;const p=job.plan;$('#delete-title').textContent=p.groups.every(g=>g.type==='contains')?'确认删除被完整包含的较少份':'确认删除重复副本';
 $('#delete-phase').textContent=job.phase+(!job.result&&job.progress?.entries?' · 本轮已检查 '+fmt(job.progress.entries)+' 个条目':'');
 $('#delete-summary').textContent=(job.result?'原确认范围：':'本次待处理：')+fmt(p.groupCount)+' 组'+(p.groups.every(g=>g.type==='contains')?'严格包含':'去重建议')+' · '+fmt(p.folderCount)+' 个最高层目录 · '+fmt(p.fileCount)+' 个文件 · '+size(p.bytes)+' 逻辑体积';
 $('#delete-my-resources').textContent='其中 '+fmt(p.myResourcesFolderCount)+' 个目录位于“我的资源”。熏听、照片和应用目录保持保留。';
 const planKey=job.id+JSON.stringify(job.result||null);
 if(lastRenderedPlan!==planKey){
  lastRenderedPlan=planKey;
  const removed=job.result?.removed||[],submitted=new Set((job.events||[]).flatMap(e=>e.paths));
  $('#delete-paths').innerHTML=p.groups.map(g=>`<details class="confirm-case"><summary>${esc(g.id+' · '+g.title)} <small>${job.result?'原计划':'删除'} ${g.remove.length} 处</small></summary><div class="confirm-keep">保留：${esc(g.keep.path)}</div>${g.remove.map(r=>'<div class="confirm-remove">'+(removed.includes(r.path)?'✓ 已核实移除：':job.result&&!submitted.has(r.path)?'尚未提交：':job.result?'已提交，待确认：':'删除：')+esc(r.path)+'</div>').join('')}</details>`).join('');
 }
 $('#confirm-delete').disabled=job.status!=='ready';
 $('#confirm-delete').textContent=job.status==='ready'?'确认后复核并删除 '+fmt(p.folderCount)+' 个文件夹':'正在核对';
 $('#delete-result').innerHTML=job.mismatches?job.mismatches.map(x=>'<p>'+esc(x.path+'：'+x.reason)+'</p>').join(''):job.result?'<p>已核实移除 '+fmt(job.result.removed.length)+' 个目录；'+fmt(job.result.notSubmitted?.length||0)+' 个尚未提交；'+fmt(job.result.pending?.length||0)+' 个已提交待确认。</p>'+(job.result.retainedChanges.length?'<p>以下保留目录状态变化，需核对：'+job.result.retainedChanges.map(esc).join('；')+'</p>':'')+(job.requestMessage?'<p>'+esc(job.requestMessage)+'</p>':''):'';
 if(['done','partial','failed','changed'].includes(job.status))$('#confirm-delete').textContent=job.status==='done'?'本次删除完成':'已显示实际执行结果';
}
async function pollJob(id){const generation=++pollGeneration;while(generation===pollGeneration){try{const job=await api('/api/jobs/'+id);renderDeleteJob(job);if(['done','partial','failed','changed'].includes(job.status)){localStorage.removeItem(storageKey+'-active-job');if(job.result?.removed.length){const removed=job.result.removed;const next={...chosen};for(const group of job.plan.groups)if(group.remove.every(r=>removed.includes(r.path)))delete next[group.id];applySelection(next);update();}refreshLiveState();return;}if(job.status==='ready')return;}catch(e){$('#delete-phase').textContent=e.message;return;}await new Promise(resolve=>setTimeout(resolve,1500));}}
async function reviewDelete(){if(!$('#delete-dialog').open)$('#delete-dialog').showModal();$('#confirm-delete').disabled=true;$('#delete-result').textContent='';if(!service){$('#delete-phase').textContent='离线文件可以查看和导出。执行删除请打开本机运行版。';$('#delete-summary').innerHTML='<a href="http://127.0.0.1:8765/文件夹去重清单.html#exact">打开本机运行版</a>';return;}if(deleteJob&&['committing','validating'].includes(deleteJob.status)){pollJob(deleteJob.id);return;}const decisions=[...resolved.values()].filter(p=>['exact','contains'].includes(p.type)).map(p=>({id:p.id,keep:p.keep}));if(!decisions.length){$('#delete-phase').textContent='请先勾选一组待处理的完全重复或严格包含建议。';return;}$('#delete-phase').textContent='正在复用本地完整盘点生成清单；已核实删除的副本会跳过。';$('#delete-summary').textContent='已选 '+decisions.length+' 组待处理建议';$('#delete-paths').replaceChildren();try{const result=await api('/api/prepare',{decisions});await pollJob(result.jobId);}catch(e){$('#delete-phase').textContent=e.message;}}
async function commitDelete(){if(!deleteJob||deleteJob.status!=='ready')return;const job=deleteJob;$('#confirm-delete').disabled=true;$('#delete-phase').textContent='已确认，正在执行前实时复核；通过后才会提交删除。';try{const result=await api('/api/confirm-delete',{jobId:job.id,confirmationToken:job.confirmationToken,confirmedFolderCount:job.plan.folderCount});localStorage.setItem(storageKey+'-active-job',result.jobId);deleteJob.status='committing';pollJob(result.jobId);}catch(e){$('#delete-phase').textContent=e.message;}}
document.addEventListener('click',e=>{const filter=e.target.closest('[data-type]');if(filter){chooseType(filter.dataset.type);return;}const choose=e.target.closest('[data-choose]');if(choose){toggleChoice(choose.dataset.choose);return;}const remove=e.target.closest('[data-remove-choice]');if(remove){const next={...chosen};delete next[remove.dataset.removeChoice];applySelection(next);update();$('#draft-dialog').close();showDraft();}});
document.addEventListener('change',e=>{if(e.target.matches('[data-pick]')){toggleChoice(e.target.dataset.pick);return;}if(e.target.matches('[data-keep]')){const id=e.target.dataset.keep;keepChoices.set(id,e.target.value);if(chosen[id]){const next={...chosen,[id]:{id,keep:e.target.value}};if(!applySelection(next))keepChoices.delete(id);}render();}});
$('#select-all-exact').addEventListener('change',e=>{const next={...chosen};for(const c of filtered.filter(actionable)){if(e.target.checked)next[c.id]={id:c.id,keep:keepChoices.get(c.id)||c.defaultKeep};else delete next[c.id];}if(applySelection(next)){update();notify(e.target.checked?'已选中当前筛选的待处理建议，确认前可核对路径。':'已取消当前筛选的选择。');}});
for(const selector of ['#include-protected','#only-pending','#sort'])$(selector).addEventListener('change',update);$('#query').addEventListener('input',update);$('#reset').addEventListener('click',()=>{type='strict';$('#query').value='';$('#include-protected').checked=false;history.replaceState(null,'','#strict');update();});$('#prev').addEventListener('click',()=>{page--;render();$('#result-count').scrollIntoView({block:'start'});});$('#next').addEventListener('click',()=>{page++;render();$('#result-count').scrollIntoView({block:'start'});});$('#show-math').addEventListener('click',()=>showCase(mathCase?.id));$('#view-draft').addEventListener('click',showDraft);$('#close-draft').addEventListener('click',()=>$('#draft-dialog').close());$('#export').addEventListener('click',exportDraft);$('#review-delete').addEventListener('click',reviewDelete);$('#cancel-delete').addEventListener('click',()=>{$('#delete-dialog').close();if(deleteJob?.status==='ready')pollGeneration++;});$('#confirm-delete').addEventListener('click',commitDelete);
const mathCase=DATA.cases.find(c=>['contains','exact'].includes(c.type)&&c.title.includes('玩出数感超能力')&&c.title.includes('L1'));
const m=DATA.meta;$('#stat-exact').textContent=fmt(m.exactGroups);$('#stat-contains').textContent=fmt(m.strictContainmentGroups);$('#stat-review').textContent=fmt(m.contentOnlyReviewGroups);$('#stat-primary').textContent=fmt(m.primaryCases);$('#count-strict').textContent=fmt(m.exactGroups+m.strictContainmentGroups);$('#count-exact').textContent=fmt(m.exactGroups);$('#count-contains').textContent=fmt(m.strictContainmentGroups);$('#count-review').textContent=fmt(m.contentOnlyReviewGroups);$('#count-protected').textContent=fmt(m.protectedCases);$('#snapshot-label').textContent='盘点完成：'+m.snapshotFinishedAt.replace('T',' ').replace('+08:00','')+' · '+fmt(m.scannedFolders)+' 个文件夹';$('#method-text').textContent='原始盘点从 '+fmt(m.rawExactGroups)+' 组完整重复中向上合并，隐藏 '+fmt(m.coveredExactGroups)+' 组父包内子目录比较，最高层保留 '+fmt(m.exactGroups)+' 组。页面剩余数量已按后续删除结果重算；新的完全重复可能由父包清理后形成。'+m.matchRule;$('#math-focus').hidden=!mathCase||caseState(mathCase)==='completed';
try{const saved=JSON.parse(localStorage.getItem(storageKey)||localStorage.getItem('baidu-folder-dedup-v1-'+m.snapshotFinishedAt)||'{}');const migrated={};for(const [id,item] of Object.entries(saved))if(caseMap.has(id)&&!caseMap.get(id).protected&&['exact','contains'].includes(caseMap.get(id).type))migrated[id]={id,keep:item.keep};applySelection(migrated);}catch{}
updateDraft();update();const hash=location.hash.slice(1);if(['strict','exact','contains','review','selected','protected','completed'].includes(hash)){type=hash;update();}else if(hash)showCase(hash);
try{const active=localStorage.getItem(storageKey+'-active-job');if(active&&service){$('#delete-dialog').showModal();pollJob(active);}}catch{}

async function refreshLiveState(){
 if(!service)return;
 try{
  const state=await api('/api/state');knownRemoved=state.removed||[];knownPending=state.pending||[];lastJobId=state.lastJobId;liveStateLoaded=true;
  $('#live-state-text').textContent='完整盘点已保存：'+fmt(state.inventoryFiles)+' 个文件、'+fmt(state.inventoryFolders)+' 个文件夹。已核实移除 '+fmt(knownRemoved.length)+' 个重复目录；'+fmt((state.notSubmitted||[]).length)+' 个尚未提交'+(knownPending.length?'；'+fmt(knownPending.length)+' 个提交结果待确认':'')+'。';
  $('#view-last-job').hidden=!lastJobId;
  if(state.datasetVersion&&state.datasetVersion!==DATA.meta.remainingUpdatedAt){const fresh=await api('/api/candidates');Object.assign(DATA,fresh);caseMap.clear();for(const c of DATA.cases)caseMap.set(c.id,c);searches.clear();}
  $('#math-focus').hidden=!mathCase||caseState(mathCase)==='completed';
  applySelection(chosen);if(type==='exact'&&!pendingCounts().exact&&!$('#query').value&&$('#only-pending').checked){type='contains';history.replaceState(null,'','#contains');}update();
  if(state.activeJobId&&deleteJob?.id!==state.activeJobId){localStorage.setItem(storageKey+'-active-job',state.activeJobId);$('#delete-dialog').showModal();pollJob(state.activeJobId);}
 }catch(e){$('#live-state-text').textContent='完整盘点已保存在本地。执行状态读取失败：'+e.message;}
}
$('#view-last-job').addEventListener('click',async()=>{if(!lastJobId)return;$('#delete-dialog').showModal();try{renderDeleteJob(await api('/api/jobs/'+lastJobId));}catch(e){$('#delete-phase').textContent=e.message;}});
refreshLiveState();

if(mathCase&&mathCase.reviewState==='pending'){
 $('#math-title').textContent=mathCase.type==='exact'?'超妈数感 L1：两份现已完全相同':'超妈数感 L1：查看当前保留与删除建议';
 $('#math-copy').textContent=mathCase.type==='exact'?'上一轮清理后，两份现在各有 '+fmt(mathCase.copies[0].files)+' 个文件，目录与内容完全相同。已归入待处理的完全重复。':'当前较少份 '+fmt(mathCase.smaller[0].files)+' 个文件均被保留份覆盖。保留份额外有 '+fmt(mathCase.addedFiles)+' 个文件、'+fmt(mathCase.addedFolders)+' 个子目录；已按上一轮删除结果更新。';
 $('#math-numbers').textContent=fmt(mathCase.larger[0].files)+' 个覆盖 '+fmt(mathCase.smaller[0].files)+' 个';
}
