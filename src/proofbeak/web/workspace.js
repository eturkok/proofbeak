'use strict';

let historySequence = 0;
let historyTimer = null;
const detailRequests = new Set();

function historyUrl(before=null){
  const params=new URLSearchParams({kind:state.historyKind,limit:'25',q:state.query});
  for(const [name,value] of Object.entries({state:state.filter==='all'?'':state.filter,
    outcome:state.outcome,provider:state.provider,since:state.since,until:state.until,before})){
    if(value)params.set(name,value);
  }
  return '/workspace/history?'+params;
}

async function loadHistory(append=false){
  const sequence=++historySequence;
  const session=state.session;
  try{
    const data=await api(historyUrl(append?state.historyCursor:null));
    if(sequence!==historySequence||state.session!==session||!session)return;
    const items=data[state.historyKind==='suite'?'suites':'runs'];
    state.historyItems=append?[...state.historyItems,...items]:items;
    state.historyCursor=data.next_cursor;
    if(state.view==='history')updateHistoryResults();
  }catch(error){if(sequence===historySequence)toast(errorText(error),'error');}
}

function updateHistoryResults(){
  const target=document.querySelector('#history-results');
  if(!target)return;
  target.innerHTML=state.historyItems.length?historyTable(state.historyItems,state.historyKind):
    empty('No matching results','Try another name, service, date or outcome.',null);
  document.querySelector('#history-more').innerHTML=state.historyCursor?
    '<button class="btn" data-action="load-more">Load older results</button>':'';
  document.querySelector('#history-count').textContent=state.historyItems.length+' matching results loaded';
}

function workspaceHistory(){
  const options=(values,current)=>values.map(([value,text])=>`<option value="${esc(value)}" ${value===current?'selected':''}>${esc(text)}</option>`).join('');
  return `<div class="page-heading"><div><p class="eyebrow">YOUR OBSERVATION RECORD</p><h1>Every run, a clearer answer.</h1><p>Search all retained records in this workspace, including older pages.</p></div><button class="btn" data-action="refresh-history">${icon('refresh')}Refresh</button></div>
    <div class="segmented"><button data-history-kind="suite" class="${state.historyKind==='suite'?'selected':''}">Test packages</button><button data-history-kind="trial" class="${state.historyKind==='trial'?'selected':''}">Individual trials</button></div>
    <div class="history-filters panel pad">
      <label class="field">Search history<input id="history-search" maxlength="120" value="${esc(state.query)}" placeholder="Name or reference"></label>
      <label class="field">Execution status<select id="history-filter">${options([['all','All statuses'],...['queued','running','completed','interrupted','cancelled'].map(s=>[s,label(s)])],state.filter)}</select></label>
      <label class="field">Recorded acceptance<select data-history-filter="outcome">${options([['','All outcomes'],['pass','Pass'],['fail','Fail'],['inconclusive','Inconclusive'],['running','Running']],state.outcome)}</select></label>
      <label class="field">Service<select data-history-filter="provider">${options([['','All services'],...Object.keys(state.catalog.provider_capabilities).map(p=>[p,human(p)])],state.provider)}</select></label>
      <label class="field">From date (UTC)<input type="date" data-history-filter="since" value="${esc(state.since)}"></label>
      <label class="field">Through date (UTC)<input type="date" data-history-filter="until" value="${esc(state.until)}"></label>
    </div><p class="field-help">Recorded acceptance is historical. Inspect the current evidence gate, freshness and cleanup before relying on a pass.</p>
    <p id="history-count" class="small muted" role="status"></p><div id="history-results" class="panel">${historyTable(state.historyItems,state.historyKind)}</div><div id="history-more" class="section-head"></div>`;
}

function openDefinition(definition){
  if(state.pending)throw new Error('Resolve the pending submission before starting another package.');
  if(!definition||definition.id!=='suite.v1'||!Array.isArray(definition.cases))throw new Error('The original definition is no longer retained. Import your saved JSON instead.');
  if(state.dirty&&!confirm('Replace the current draft? Save it first if you want to keep it.'))return;
  state.builder=clone(definition);state.caseIndex=0;
  state.mode=definition.cases.every(visualCaseShape)?'visual':'json';
  state.jsonDraft=JSON.stringify(state.builder,null,2);state.preview=null;state.previewDefinition=null;
  state.dirty=true;navigation('builder');
  toast('Definition copied. Review and validate before starting a new execution.');
}

function workspaceLibrary(){
  const rows=(items)=>items.map(item=>`<div class="service-row"><div class="service-text"><strong>${esc(item.name)}</strong><span>${date(item.created_at)}${item.kind==='baseline'?' · '+esc(item.source_state || 'Source no longer retained'):''}</span></div>
    ${item.kind==='template'?`<button class="btn compact" data-action="load-template" data-item="${esc(item.id)}">Use template</button>`:
      `<button class="btn compact" data-open="${esc(item.source_id)}" data-kind="suite" ${item.source_state?'':'disabled'}>Inspect baseline</button>`}
    <button class="btn danger compact" data-action="delete-workspace-item" data-item="${esc(item.id)}" aria-label="Delete ${esc(item.name)}">${icon('trash')}</button></div>`).join('') || '<p class="muted">Nothing saved yet.</p>';
  return `<div class="page-heading"><div><p class="eyebrow">REUSE YOUR WORK</p><h1>Your templates and baselines.</h1><p>Start from a saved definition or a recorded reference. No provider work happens when opening either.</p></div></div>
    <div class="callout">Templates are saved only on your explicit request and can contain test data. They stay in this private workspace until you delete them. Baselines reference retained results and do not extend evidence retention or freshness.</div>
    <section class="panel pad"><h2>Saved templates</h2>${rows(state.templates)}</section><br><section class="panel pad"><h2>Named baselines</h2>${rows(state.baselines)}</section>`;
}

function operationsView(){
  const m=state.metrics;
  return `<div class="page-heading"><div><p class="eyebrow">WORKSPACE OPERATIONS</p><h1>Understand the workload.</h1><p>Aggregate metadata for this authenticated workspace. Refresh to take a new snapshot.</p></div><button class="btn" data-action="refresh-metrics">${icon('refresh')}Refresh metrics</button></div>${!m?'<p>Metrics unavailable.</p>':`
    <div class="metrics">${metric('Queued trials',m.jobs.queued||0,'Plus '+m.queued_suites+' queued packages','clock')}
    ${metric('Running trials',m.jobs.running||0,'Current durable queue','play')}
    ${metric('Oldest queue wait',m.oldest_queue_seconds.toFixed(1)+' s','Oldest queued trial or package','clock')}
    ${metric('Cleanup pending',m.cleanup_pending_runs,'Runs with unresolved recorded cleanup','shield')}</div>
    <div class="metrics">${metric('Queue + execution p95',m.p95_queue_and_run_seconds===null?'—':m.p95_queue_and_run_seconds.toFixed(2)+' s','Includes queue time and cleanup','clock')}
    ${metric('Recent timeouts',m.recent_timeout_runs,'Among the sampled terminal trials','shield')}
    ${metric('Retained report data',(m.retained_report_bytes/1024).toFixed(1)+' KiB','Queue report payloads only','file')}
    ${metric('Maintenance',m.maintenance,'Coordinator retention maintenance','database')}</div>
    <p class="field-help">Snapshot ${date(m.generated_at)}. Duration and timeout sample: latest ${m.sample_size} completed/interrupted trials, at most ${m.sample_limit}. Storage excludes ledgers, exports and backups. Cleanup reflects retained reports and recorded recovery.</p>`}`;
}

function needsAttention(entry){return !entry.report||(entry.report.candidates||[]).some(c=>c.verdict!=='pass'||c.cleanup_status!=='cleaned'||c.validity?.reasons?.length);}
function attentionSummary(candidates,entries=[]){
  const failing=candidates.reduce((n,c)=>n+(c.checks||[]).filter(x=>x.passed!==true&&x.available!==false).length,0);
  const missing=candidates.reduce((n,c)=>n+(c.checks||[]).filter(x=>x.available===false).length,0);
  const unavailable=entries.filter(e=>!e.report).length;
  const cleanup=candidates.filter(c=>c.cleanup_status!=='cleaned').length;
  return `<div class="callout ${failing||missing||cleanup?'warning':''}"><strong>What needs attention</strong><p>${failing} failing conditions · ${missing} unavailable observations · ${unavailable} cases without a result · ${cleanup} cleanup issues.</p><button class="btn compact" data-action="toggle-attention" aria-pressed="${!!state.attentionOnly}">${state.attentionOnly?'Show all cases':'Show cases needing attention'}</button></div>`;
}

function patchDetail(){
  const holder=document.createElement('div');holder.innerHTML=detail();
  for(const region of holder.children){
    const previous=region.id&&document.getElementById(region.id);
    if(!previous){document.querySelector('#main').innerHTML=holder.innerHTML;return;}
    if(previous.innerHTML!==region.innerHTML)previous.replaceWith(region.cloneNode(true));
  }
}

function scheduleDetail(delay=1500){
  clearTimeout(pollTimer);
  if(state.view!=='detail'||!state.session||document.hidden)return;
  pollTimer=setTimeout(()=>refreshDetail().catch(error=>{
    if(state.view!=='detail')return;
    if(!state.pollErrors)toast(errorText(error),'error');
    state.pollErrors=(state.pollErrors||0)+1;scheduleDetail(Math.min(15000,1500*2**state.pollErrors));
  }),delay);
}

async function refreshWorkspaceDetail(force=false){
  clearTimeout(pollTimer);pollTimer=null;
  if(state.view!=='detail'||!state.session||(!force&&document.hidden))return;
  const id=state.detailId,kind=state.detailKind,session=state.session,key=kind+':'+id;
  if(detailRequests.has(key))return;
  detailRequests.add(key);
  const current=()=>state.view==='detail'&&state.detailId===id&&state.detailKind===kind&&state.session===session;
  try{
    const progress=await api(`/${kind==='suite'?'suites':'runs'}/${id}/progress`);
    if(!current())return;
    if(force||!state.detail||state.progressRevision!==progress.revision){
      let data=await api(`/${kind==='suite'?'suites':'runs'}/${id}`);
      if(kind==='trial'&&data.state==='completed')data={...data,...await api(`/runs/${id}/comparison`)};
      if(!current())return;
      const initial=!state.detail;state.detail=data;state.progressRevision=progress.revision;
      if(initial)render();else patchDetail();
    }
    state.pollErrors=0;
    if(['queued','running'].includes(progress.state))scheduleDetail();
    else if(state.finishedRevision!==progress.revision){state.finishedRevision=progress.revision;await loadData();}
  }finally{detailRequests.delete(key);}
}

async function workspaceAction(button){
  const action=button.dataset.action;
  if(action==='refresh-history'){await loadHistory();return true;}
  if(action==='refresh-metrics'){state.metrics=await api('/workspace/metrics');render();return true;}
  if(action==='load-more'){await loadHistory(true);return true;}
  if(action==='toggle-attention'){state.attentionOnly=!state.attentionOnly;patchDetail();return true;}
  if(action==='clone-definition'){
    const d=state.detail;
    openDefinition(state.detailKind==='suite'?d.definition:{id:'suite.v1',name:(d.test.name||'Trial').slice(0,120),cases:[{id:'case-1',test:d.test,candidates:d.candidates.map(c=>c.provider)}]});return true;
  }
  if(action==='save-template'||action==='pin-baseline'){
    if(action==='save-template')state.templateDefinition=serializeBuilder();
    showModal(action==='save-template'?'Save a reusable template':'Name this baseline',
      `<label class="field">${action==='save-template'?'Template name':'Baseline name'}<input id="workspace-item-name" maxlength="80" value="${esc((action==='save-template'?state.builder.name:state.detail.name||'Baseline').slice(0,80))}"></label><p class="field-help">${action==='save-template'?'Explicitly saves this definition and its test data in your private workspace. It can be deleted from Templates & baselines.':'Stores a reference to this passing result. Existing evidence retention and freshness rules still apply.'}</p>`,
      `<button class="btn" data-action="close-modal">Cancel</button><button class="btn primary" data-action="confirm-${action}">Save ${action==='save-template'?'template':'baseline'}</button>`);
    document.querySelector('#workspace-item-name').focus();return true;
  }
  if(action==='confirm-save-template'||action==='confirm-pin-baseline'){
    const template=action==='confirm-save-template',name=document.querySelector('#workspace-item-name').value.trim();
    await api('/workspace/'+(template?'templates':'baselines'),template?{name,suite:state.templateDefinition}:{name,suite_id:state.detailId});
    state.templateDefinition=null;modal.close();await loadData();toast(template?'Template saved.':'Named baseline saved.');return true;
  }
  if(action==='load-template'){const item=await api('/workspace/items/'+button.dataset.item);openDefinition(item.definition);return true;}
  if(action==='delete-workspace-item'){
    if(!confirm('Delete this saved template or baseline reference? Historical runs are preserved.'))return true;
    await api('/workspace/items/'+button.dataset.item,undefined,'DELETE');await loadData();render();toast('Saved item deleted.');return true;
  }
  return false;
}

document.addEventListener('visibilitychange',()=>{
  clearTimeout(pollTimer);pollTimer=null;
  if(!document.hidden&&state.view==='detail'&&state.session)refreshDetail(true).catch(error=>{toast(errorText(error),'error');scheduleDetail(5000);});
});
