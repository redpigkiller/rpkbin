"""Execute priority UI handlers against a small DOM and transport boundary."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = Path(__file__).parents[2] / "rpkbin" / "wave" / "static" / "app.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


SETUP = r"""
const nodes = new Map(), calls = [], feedback = [], opened = [], closed = [];
const storage = new Map();
window.localStorage = {
 getItem(key){return storage.get(key) ?? null}, setItem(key,value){storage.set(key,value)}
};
let refreshes = 0;
document.getElementById = id => {
  if (!nodes.has(id)) nodes.set(id, {id, value:'', hidden:true, disabled:false, open:false,
    checked:false, textContent:'', innerHTML:'', placeholder:'', title:'', dataset:{}, attrs:{},
    style:{setProperty(){}},
    isConnected:true, getClientRects:()=>[{}], querySelectorAll:()=>[], querySelector:()=>null, contains:()=>false,
    classList:{toggle(){}},
    replaceChildren(){this.innerHTML=''}, setAttribute(k,v){this[k]=v;this.attrs[k]=v},
    removeAttribute(k){delete this[k];delete this.attrs[k]}, getAttribute(k){return this.attrs[k]},
    focus(){this.focused=true;document.activeElement=this}});
  return nodes.get(id);
};
document.querySelectorAll = () => [];
openModal = (id, focus) => { el(id).hidden=false; opened.push(id); };
closeModal = id => { el(id).hidden=true; closed.push(id); };
recordFeedback = (...args) => feedback.push(args);
fetchState = async () => { refreshes++; };
fetchJson = async (path, options) => {
  calls.push({path, body:JSON.parse(options.body)});
  return {action:'priority', priority:10, affected:1, results:[]};
};
const job = (id, overrides={}) => ({id, name:id, attempt_id:id+'-attempt-1',
  submission_id:id+'-submission-1', attempt_number:1, status:'PENDING',
  priority:0, can_rerun:false, ...overrides});
const a=job('alpha'), b=job('beta'), c=job('gamma');
CONNECTED=true;
STATE={workspace_id:'workspace-1', jobs:[a,b,c], detail:a};
SELECTED_JOB_ID=a.id;
"""


def run_checks(checks, *, with_columns=False):
    source = APP_JS.read_text(encoding="utf-8")
    script, removed = re.subn(
        r"\nsetupMonitorLayout\(\);\s*\npollState\(\);\s*$", "\n", source
    )
    assert removed == 1, "Harness must disable only automatic page startup"
    if with_columns:
        script = APP_JS.parent.parent.joinpath("jobs.js").read_text(encoding="utf-8") + "\n" + script
    code = script + SETUP + "\n(async () => {\n" + checks + r"""
})().catch(error => { console.error(error); process.exitCode=1; });
"""
    harness = (
        "const vm=require('node:vm'), assert=require('node:assert/strict');\n"
        "const document={addEventListener(){}}, getComputedStyle=()=>({visibility:'visible'});\n"
        "vm.runInNewContext("
        + json.dumps(code)
        + ",{assert,document,getComputedStyle,window:{},console,process});\n"
    )
    result = subprocess.run(
        [NODE, "-"], input=harness.encode("utf-8"), capture_output=True,
        timeout=10, check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode(
        "utf-8", errors="replace"
    )


def test_blank_resource_capacity_is_rejected_but_explicit_zero_is_allowed():
    run_checks(r"""
const quota0=el('resQuota1');
quota0.dataset.resQuota='slot';
el('resMaxWorkers').value='2';
el('resourcesTableBody').querySelectorAll=sel=>sel==='input[data-res-quota]'?[quota0]:[];
await saveResourcesPanel();
assert.equal(calls.length,0);
assert.equal(quota0.focused,true);
assert.match(el('resourcesFeedback').textContent,/nonnegative integer/);
quota0.value='0';
await saveResourcesPanel();
assert.equal(calls.length,1);
assert.equal(calls[0].path,'/api/settings/save');
assert.equal(calls[0].body.resources.slot,0);
assert.equal(calls[0].body.max_workers,2);
""")


def test_ready_empty_preview_can_start_watching():
    run_checks(r"""
STATE={ready:true, started:false, paused:true, jobs:[], detail:null,
  resources:{}, resources_in_use:{}, active_workers:0, max_workers:4};
SELECTED_JOB_ID=null;
el('statusDot').classList={toggle(){}};
setMarkup=()=>{};
syncQuickFilters=()=>{};
renderStatusBanner=()=>{};
renderTopBar();
updateControls();
assert.equal(el('btnStart').hidden,false);
assert.equal(el('btnStart').disabled,false);
assert.equal(el('btnStart').textContent,'Start Watching');
STATE.ready=false;
renderTopBar();
updateControls();
assert.equal(el('btnStart').hidden,true);
assert.equal(el('btnStart').disabled,true);
""")


def test_external_source_labels_watcher_status_and_jobs_columns():
    run_checks(r"""
el('statusDot').classList={toggle(){}};
setMarkup=()=>{};
syncQuickFilters=()=>{};
Object.assign(STATE,{ready:true, started:false, resources:{}, resources_in_use:{},
 active_workers:0,max_workers:4,sources:{configured_watchers:0},launch_cwd:'D:/cwd'});
for(const [kind,single,multiple,stateWord,badgeWord] of [
 ['process','Start (1)','Start (2)','RUNNING','ACTIVE'],
 ['tail','Watch 1 Log','Watch 2 Logs','WATCHING','WATCHING'],
 ['file','Analyze 1 Log','Analyze 2 Logs','ANALYZING','WATCHING'],
 ['reanalysis','Reanalyze 1 Run','Reanalyze 2 Runs','REPLAYING','WATCHING']]){
 const row={...a,source_kind:kind,status:'RUNNING'};
 STATE.jobs=[row];
 renderTopBar();
 assert.equal(el('btnStart').textContent,single);
 assert.equal(displayState('RUNNING',kind),stateWord);
 assert.match(JobsColumns.cell(row,'status'), new RegExp('st-RUNNING">' + stateWord));
 STATE.jobs=[row,{...row,id:b.id}];
 renderTopBar();
 assert.equal(el('btnStart').textContent,multiple);
 Object.assign(STATE.sources,{active_readers:kind==='process'?0:1,
  stoppable_jobs:[row.id],watching:kind!=='process'});
 STATE.started=true; STATE.paused=false;
 renderTopBar();
 assert.equal(el('workspaceBadge').textContent,badgeWord);
 STATE.started=false;
}
STATE.jobs=[{...a,source_kind:'process'},{...b,source_kind:'tail'}];
renderTopBar();
assert.equal(el('btnStart').textContent,'Start (2)');
STATE.jobs=[];
STATE.started=true;
STATE.source_path='D:/'+('long-folder/'.repeat(20));
STATE.sources={configured_watchers:1,active_watchers:1,active_readers:0,watching:true};
renderTopBar();
assert.equal(el('workspaceBadge').textContent,'WATCHING');
assert.match(el('workspaceInfo').textContent,/^Folder watch active — waiting for logs/);
assert.ok(el('workspaceInfo').title.includes(STATE.source_path));
assert.ok(el('workspaceInfo').title.includes('cwd: D:/cwd'));
STATE.watcher_error='OSError: source <unavailable>';
renderStatusBanner();
assert.equal(el('connectionNotice').hidden,false);
assert.match(el('statusWarningText').textContent,/OSError: source <unavailable>/);
for(const kind of ['tail','file','replay']){
 const row={...a,pid:null,origin_label:'External ('+kind+')'};
 assert.match(JobsColumns.cell(row,'pid'), /External \(/);
}
assert.match(JobsColumns.cell({pid:42},'pid'), />42</);
assert.match(JobsColumns.layout(['pid']).html,/width:120px/);
const nameJobs=JobsColumns.cell({name:'folder/long.log',source_descriptor:{path:'D:/logs/<folder>/long.log'}},'name');
assert.match(nameJobs,/title="[^"]*D:\/logs\/&lt;folder&gt;\/long.log"/);
assert.match(nameJobs,/class="job-name-end">der\/long\.log</);
WORKSPACE_LAYOUT='split';
const nameSplit=JobsColumns.cell({name:'folder/long.log',source_descriptor:{path:'D:/logs/<folder>/long.log'}},'name');
assert.match(nameSplit,/class="job-name-end">g\.log</);
WORKSPACE_LAYOUT='jobs';
""", with_columns=True)


def test_stop_source_controls_use_the_source_endpoint_and_guard_history():
    run_checks(r"""
WORKSPACE_LAYOUT='split';
const source={...a,source_kind:'tail',status:'RUNNING',pid:null,origin_label:'External (tail)',
 capabilities:{has_process:false}};
STATE.jobs=[source];
STATE.detail=source;
STATE.sources={watching:true,stoppable_jobs:[source.id]};
el('detailMeta').dataset={};
el('detailTitle').parentElement={};
el('attemptSelect').dataset={};
el('btnStopSource').setAttribute=(name,value)=>{el('btnStopSource')[name]=value};
renderLogs=()=>{};
renderDetail();
assert.match(el('detailMeta').innerHTML,/st-RUNNING">WATCHING/);
assert.match(el('detailMeta').innerHTML,/· External \(tail\)/);
updateControls();
assert.equal(el('attemptControls').hidden,true);
assert.equal(el('btnStopWatching').hidden,false);
assert.equal(el('btnStopSource').hidden,false);
assert.equal(el('btnStopSource').textContent,'Stop Watching');
assert.equal(el('btnStopSource')['aria-label'],'Stop Watching '+source.name);
await stopWatching(true);
await stopWatching();
assert.deepEqual(calls,[{path:'/api/sources/stop',body:{workspace_id:'workspace-1',job_id:source.id}},
 {path:'/api/sources/stop',body:{workspace_id:'workspace-1',job_id:null}}]);
assert.equal(refreshes,2);
source.source_kind='file';
updateControls();
assert.equal(el('btnStopSource').textContent,'Stop Reading');
assert.equal(el('btnStopWatching').textContent,'Stop Reading');
SELECTED_ATTEMPT_ID='history';
updateControls();
assert.equal(el('btnStopSource').hidden,true);
await stopWatching(true);
assert.equal(calls.length,2);
SELECTED_ATTEMPT_ID=null;
STATE.opened_run_id='saved';
updateControls();
assert.equal(el('btnStopWatching').hidden,true);
assert.equal(el('btnStopSource').hidden,true);
await stopWatching();
assert.equal(calls.length,2);
STATE.opened_run_id=null;
STATE.archive={error:{message:'disk full'}};
updateControls();
assert.equal(el('btnStopWatching').disabled,false);
await stopWatching();
assert.equal(calls.length,3);
STATE.sources={watching:false,stoppable_jobs:[]};
updateControls();
assert.equal(el('btnStopWatching').hidden,true);
assert.equal(el('btnStopSource').hidden,true);
""")


def test_open_modes_browse_and_submit_a_static_log_preview():
    run_checks(r"""
const history={clears:0,clearSelection(){this.clears++}};
globalThis.RunHistory=history;
for (const id of ['openWorkTab','openLogsTab']) {
 const button=el(id);
 button.setAttribute=(k,v)=>{button[k]=v};
 button.focus=()=>{button.focused=true};
}
const buttons=[{dataset:{entry:'0'}},{dataset:{entry:'1'}}];
el('fileList').querySelectorAll=()=>buttons;
const browse=[];
fetchJson=async (url,options)=>{
 if(url.startsWith('/api/files?')){
  browse.push(url);
  if (url.includes('path=' + encodeURIComponent('D:/missing') + '&')) {
   throw Object.assign(new Error('Directory does not exist'), {field:'browse_path'});
  }
  return {path:'D:/logs',parent:'D:/',entries:[
   {name:'nested',path:'D:/logs/nested',directory:true},
   {name:'one.log',path:'D:/logs/one.log',directory:false}]};
 }
 assert.equal(url,'/api/logs/load');
 calls.push(JSON.parse(options.body));
 return {ok:true,message:'Preview ready. Analyze the logs.',
   warning:{kind:'previous_workspace_cleanup',message:'Archive close error from the prior workspace'}};
};
openWorkFile();
assert.equal(el('openWorkPanel').hidden,false);
assert.equal(el('openLogsPanel').hidden,true);
switchOpenTab({key:'ArrowRight',preventDefault(){}});
assert.equal(el('openWorkPanel').hidden,true);
assert.equal(el('openLogsPanel').hidden,false);
assert.equal(el('openLogsTab')['aria-selected'],'true');
assert.equal(el('openLogsTab').focused,true);
await browseFiles();
assert.match(browse.at(-1),/mode=logs/);
assert.equal(el('btnAddFolder').hidden,false);
await browseFiles('D:/missing');
assert.equal(el('fileBrowser').hidden,false);
assert.equal(el('fileList').innerHTML,'');
assert.equal(el('btnAddFolder').hidden,true);
addBrowsedFolder();
assert.equal(el('logTargets').value,'');
assert.match(el('browsePathError').textContent,/Directory does not exist/);
assert.equal(el('browsePath').attrs['aria-invalid'],'true');
assert.equal(document.activeElement,el('browsePath'));
await browseFiles('D:/logs');
assert.equal(el('loadError').textContent,'');
el('browsePath').value='D:/unsent-path';
addBrowsedFolder();
buttons[1].onclick();
assert.equal(el('logTargets').value,'D:/logs\nD:/logs/one.log');
assert.match(el('logTargetList').innerHTML,/D:\/logs\/one.log/);
removeLogTarget(1);
assert.equal(el('logTargets').value,'D:/logs');
await browseFiles('', 'flow');
assert.match(browse.at(-1),/mode=work/);
assert.equal(el('btnAddFolder').hidden,true);
el('logsMode').value='static';
updateLogOptions();
assert.equal(el('logStart').disabled,true);
el('logStart').value='end';
el('logGlobs').value='*.log, *.trace';
el('logFlowPath').value='D:/flow.py';
el('logRecursive').checked=true;
CHECKED_JOBS.add(a.id);
SELECTED_ATTEMPT_ID='old';
await loadLogsSource();
assert.deepEqual(calls,[{workspace_id:'workspace-1',targets:['D:/logs'],once:true,
 globs:['*.log','*.trace'],recursive:true,flow_path:'D:/flow.py'}]);
assert.equal(SELECTED_JOB_ID,null);
assert.equal(SELECTED_ATTEMPT_ID,null);
assert.equal(CHECKED_JOBS.size,0);
assert.equal(history.clears,1);
assert.equal(el('workOverlay').hidden,true);
assert.equal(refreshes,1);
assert.match(feedback.at(-1)[0],/Analyze/);
assert.match(el('statusWarningText').textContent,/prior workspace/);
const saved = JSON.parse(storage.get(OPEN_DRAFT_KEY));
assert.deepEqual(saved.logs, ['D:/logs']);
assert.equal(saved.directory, 'D:/logs');
assert.deepEqual(saved.recent, [{mode:'logs',path:'D:/logs'}]);
""")


def test_open_field_errors_keep_draft_and_recent_paths_reuse_only_confirmed_inputs():
    run_checks(r"""
storage.set(OPEN_DRAFT_KEY, JSON.stringify({work:'D:/old.py', logs:['D:/old.log'],
  recent:[{mode:'work', path:'D:/old.py'}]}));
openWorkFile('work');
assert.equal(el('workPath').value,'D:/old.py');
assert.equal(calls.length,0);
el('workPath').value='bad.py';
fetchJson=async (path, options)=>{
 calls.push(JSON.parse(options.body));
 throw Object.assign(new Error('File not found'), {field:'path',status:400});
};
await loadWorkFile();
assert.equal(el('workPath').value,'bad.py');
assert.equal(el('workPath').getAttribute('aria-invalid'),'true');
assert.equal(el('workPathError').textContent,'File not found');
assert.equal(document.activeElement,el('workPath'));
assert.equal(el('loadError').textContent,'');
assert.equal(JSON.parse(storage.get(OPEN_DRAFT_KEY)).work,'D:/old.py');
el('recentWorkPaths').value='D:/old.py';
useRecentPath('work');
assert.equal(el('workPath').value,'D:/old.py');
assert.equal(el('workPath').getAttribute('aria-invalid'),undefined);
assert.equal(el('workPathError').hidden,true);
assert.equal(calls.length,1);

openWorkFile('logs'); el('logTargets').value=''; el('logGlobs').value='';
await loadLogsSource();
assert.equal(document.activeElement,el('logTargetAddInput'));
assert.equal(el('logTargetsError').hidden,false);
assert.equal(el('logGlobsError').hidden,false);
assert.equal(el('logFolderOptions').hidden,false);
for(let i=0;i<7;i++) rememberLoadedPaths('logs',['D:/'+i+'.log']);
rememberLoadedPaths('logs',['D:/5.log']);
const saved = JSON.parse(storage.get(OPEN_DRAFT_KEY));
assert.deepEqual(saved.recent.map(entry=>entry.path), ['D:/5.log','D:/6.log','D:/4.log','D:/3.log','D:/2.log']);
assert.deepEqual(saved.logs, ['D:/5.log']);
openDraft=undefined; window.localStorage={
 getItem(){throw new Error('Unavailable')},
 setItem(){throw new Error('Unavailable')}
};
rememberLoadedPaths('work', ['D:/in-session.py']);
assert.equal(getOpenDraft().work,'D:/in-session.py');
assert.equal(calls.length,1);
""")


def test_settings_http_field_error_keeps_value_and_ungrouped_error_stays_modal():
    run_checks(r"""
el('settingsOverlay').hidden=false;
const hist=el('maxHistoryInput');
hist.value='7';
fetchJson=async()=>{throw Object.assign(new Error('History limit rejected'),{field:'max_history',status:400})};
await saveHistoryLimit();
assert.equal(hist.value,'7');
assert.equal(hist.getAttribute('aria-invalid'),'true');
assert.equal(el('maxHistoryInputError').textContent,'History limit rejected');
assert.equal(document.activeElement,hist);
assert.equal(el('settingsFeedback').textContent,'');
fetchJson=async()=>{throw Object.assign(new Error('Settings unavailable'),{status:400})};
await saveHistoryLimit();
assert.equal(el('settingsFeedback').textContent,'Settings unavailable');
""")


def test_late_settings_error_has_report_and_does_not_focus_reopened_form():
    run_checks(r"""
el('settingsOverlay').hidden=false;
const hist=el('maxHistoryInput');
hist.value='7';
let reject;
fetchJson=()=>new Promise((_,fail)=>{reject=fail});
const saving=saveHistoryLimit();
el('settingsOverlay').hidden=true;
++SETTINGS_DIALOG_EPOCH;
el('settingsOverlay').hidden=false;
el('logRetentionInput').focus();
reject(Object.assign(new Error('History limit rejected'),{field:'max_history',status:400}));
await saving;
assert.equal(document.activeElement,el('logRetentionInput'));
assert.equal(hist.getAttribute('aria-invalid'),undefined);
assert.equal(el('settingsFeedback').textContent,'');
assert.match(feedback.at(-1)[2],/History limit rejected/);
""")


def test_jobs_toolbar_targets_selected_job_and_keeps_checked_jobs_separate():
    run_checks(r"""
CHECKED_JOBS=new Set([b.id]);
openJobsPriority();
assert.deepEqual(PRIORITY_REQUEST.targets.map(t=>t.job_id),['alpha']);
assert.ok(el('priorityTargets').textContent.includes('alpha'));
SELECTED_JOB_ID=b.id;
await applyPriority();
assert.equal(calls.length,1);
assert.equal(calls[0].body.targets[0].job_id,'alpha');
""")


def test_single_editor_does_not_retarget_when_selected_job_changes():
    run_checks(r"""
openSelectedPriority();
assert.equal(el('priorityOverlay').hidden,false);
assert.equal(el('priorityTargets').textContent,'alpha · Attempt 1');
assert.deepEqual(targets(PRIORITY_REQUEST.targets),[{
  job_id:'alpha',attempt_id:'alpha-attempt-1',submission_id:'alpha-submission-1'}]);
SELECTED_JOB_ID=b.id; STATE.detail=b;
CHECKED_JOBS=new Set([c.id]);
el('priorityValue').value='17';
updateControls();
await applyPriority();
assert.equal(calls.length,1);
assert.deepEqual(calls[0],{path:'/api/job/priority',body:{workspace_id:'workspace-1',
  priority:17,targets:[{job_id:'alpha',attempt_id:'alpha-attempt-1',submission_id:'alpha-submission-1'}]}});
assert.equal(PRIORITY_REQUEST,null);
assert.equal(BUSY,false);
assert.equal(el('priorityOverlay').hidden,true);
""")


def test_checked_editor_reports_original_mixed_targets_after_checkboxes_and_attempt_change():
    run_checks(r"""
a.priority=2;b.priority=5;
CHECKED_JOBS=new Set([a.id,b.id]);
openPriorityEditor('checked');
assert.equal(el('priorityValue').value,'');
assert.equal(el('priorityValue').placeholder,'Mixed priorities');
assert.match(el('priorityTargets').textContent,/2 checked jobs: alpha, beta/);
// A rerun replaces this execution while the user is editing. The request
// retains its original identity, even if the live object is mutated in place.
a.attempt_id='alpha-attempt-2';a.submission_id='alpha-submission-2';a.attempt_number=2;
CHECKED_JOBS=new Set([c.id]);SELECTED_JOB_ID=c.id;STATE.detail=c;
el('priorityValue').value='8';
updateControls();
assert.match(el('priorityAvailability').textContent,/1\/2 original targets still pending/);
assert.equal(el('priorityApply').disabled,false);
fetchJson=async(path,options)=>{
  calls.push({path,body:JSON.parse(options.body)});
  return {action:'priority',priority:8,affected:1,results:[
    {job_id:'alpha',name:'alpha',status:'rejected',ok:false,reason:'stale attempt',message:'wrong text'},
    {job_id:'beta',name:'beta',status:'accepted',ok:true,reason:'Priority updated'}]};
};
await applyPriority();
assert.deepEqual(calls[0].body.targets,[
  {job_id:'alpha',attempt_id:'alpha-attempt-1',submission_id:'alpha-submission-1'},
  {job_id:'beta',attempt_id:'beta-attempt-1',submission_id:'beta-submission-1'}]);
assert.equal(el('reportOverlay').hidden,true);
const detail = feedback.at(-1)[2];
assert.match(detail,/alpha/);
assert.match(detail,/stale attempt/);
assert.match(detail,/beta/);
assert.match(detail,/Priority updated/);
assert.doesNotMatch(detail,/wrong text/);
assert.match(feedback.at(-1)[0],/1 succeeded · 1 skipped · 0 failed/);
""")


def test_workspace_replacement_blocks_old_editor_submission():
    run_checks(r"""
openPriorityEditor(a.id);
el('priorityValue').value='21';
STATE.workspace_id='workspace-2';
updateControls();
assert.equal(el('priorityApply').disabled,true);
assert.match(el('priorityAvailability').textContent,/Work file changed/);
await applyPriority();
assert.equal(calls.length,0);
assert.equal(el('priorityValue').value,'21');
assert.equal(el('priorityOverlay').hidden,false);
assert.equal(BUSY,false);
""")


@pytest.mark.parametrize("text", ["", "  ", "1.5", "9007199254740992", "-9007199254740992", "Infinity"])
def test_invalid_priority_input_is_retained_and_never_sent(text):
    run_checks("""
openPriorityEditor(a.id);
const typed=""" + json.dumps(text) + r""";
el('priorityValue').value=typed;
await applyPriority();
assert.equal(calls.length,0);
assert.equal(el('priorityValue').value,typed);
assert.match(el('priorityError').textContent,/integer/);
assert.equal(el('priorityOverlay').hidden,false);
assert.equal(BUSY,false);
""")


def test_live_state_refresh_preserves_typed_priority_and_updates_original_availability():
    run_checks(r"""
CHECKED_JOBS=new Set([a.id,b.id]);openPriorityEditor('checked');
el('priorityValue').value='-13';
const label=el('priorityTargets').textContent;
// Simulate poll results reaching the real control refresh without rebuilding
// the whole page DOM. Editing belongs to the action, not the selected job.
a.priority=99;a.status='RUNNING';b.priority=31;
SELECTED_JOB_ID=c.id;STATE.detail=c;
updateControls();
assert.equal(el('priorityValue').value,'-13');
assert.equal(el('priorityTargets').textContent,label);
assert.match(el('priorityAvailability').textContent,/1\/2/);
b.status='DONE';updateControls();
assert.equal(el('priorityValue').value,'-13');
assert.equal(el('priorityApply').disabled,true);
assert.match(el('priorityAvailability').textContent,/0\/2/);
""")


def test_transport_failure_releases_busy_state_and_retains_original_input_and_targets():
    run_checks(r"""
openPriorityEditor(a.id);el('priorityValue').value='19';
let fail;
fetchJson=(path,options)=>{
  calls.push({path,body:JSON.parse(options.body)});
  return new Promise((_,reject)=>{fail=reject;});
};
const first=applyPriority();
assert.equal(BUSY,true);
assert.equal(el('priorityApply').disabled,true);
await applyPriority();
assert.equal(calls.length,1,'A second click must not issue a duplicate request');
fail(new Error('connection lost'));
await first;
assert.equal(BUSY,false);
assert.equal(el('priorityApply').disabled,false);
assert.equal(el('priorityOverlay').hidden,false);
assert.equal(el('priorityValue').value,'19');
assert.equal(PRIORITY_REQUEST.targets[0].job_id,'alpha');
assert.match(el('priorityError').textContent,/Could not confirm/);
assert.match(el('priorityError').textContent,/Check current state/);
assert.ok(feedback.some(([message, tone, detail])=>message === 'Action unconfirmed' && tone === 'error' && detail.includes('Could not confirm action')));
assert.equal(refreshes,1);
""")


def test_selected_history_or_mismatched_detail_cannot_open_priority_editor():
    run_checks(r"""
SELECTED_ATTEMPT_ID='old-attempt';openSelectedPriority();
assert.equal(opened.length,0);
SELECTED_ATTEMPT_ID=null;STATE.detail=b;openSelectedPriority();
assert.equal(opened.length,0);
STATE.detail=a;a.status='RUNNING';openSelectedPriority();
assert.equal(opened.length,0);
""")


def test_source_capabilities_drive_control_visibility():
    run_checks(r"""
const buttons = ['stop', 'force_stop', 'rerun', 'interrupt'].map(singleAction => ({dataset: {singleAction}}));
const interrupt = buttons.find(b => b.dataset.singleAction === 'interrupt');
const rerun = buttons.find(b => b.dataset.singleAction === 'rerun');
const stop = buttons.find(b => b.dataset.singleAction === 'stop');
document.querySelectorAll = selector => selector === '[data-single-action]' ? buttons : [];
const all = {has_process:true, can_send:true, can_interrupt:true, can_stop:true, can_kill:true, can_rerun:true};
for (const source_kind of ['process', 'tail', 'file', 'reanalysis']) {
  const process = source_kind === 'process';
  const capabilities = Object.fromEntries(Object.keys(all).map(key => [key, process]));
  STATE.detail = STATE.jobs[0] = {...a, source_kind, capabilities, status:'RUNNING'};
  updateControls();
  assert.equal(allowed(STATE.detail, 'stop'), process);
  assert.equal(el('attemptControls').hidden, !process);
  assert.equal(el('attemptConsole').hidden, !process);
  assert.equal(interrupt.disabled, !process);
  assert.equal(el('btnSend').disabled, !process);
  assert.equal(stop.hidden, !process);
  assert.equal(rerun.hidden, true);
}
STATE.detail = STATE.jobs[0] = {...a, source_kind:'process',
  capabilities:{...all, can_send:false}, status:'RUNNING'};
STATE.detail.capabilities.can_kill = false;
updateControls();
assert.equal(el('btnSend').hidden, true);
assert.equal(el('attemptConsole').hidden, true);
assert.equal(el('attemptMore').hidden, false);
assert.equal(interrupt.disabled, false);
assert.match(interrupt.title, /may handle or ignore it.*will not trigger a retry.*Use Stop/);
// Contextual action slot: FINISHED shows Rerun and hides Stop; WAITING_INPUT shows quick Ctrl+C; PENDING hides stdin bar
STATE.detail = STATE.jobs[0] = {...a, source_kind:'process', capabilities:all, status:'DONE', can_rerun:true};
updateControls();
assert.equal(stop.hidden, true);
assert.equal(rerun.hidden, false);
assert.equal(rerun.disabled, false);
assert.equal(el('attemptConsole').hidden, true);
STATE.detail = STATE.jobs[0] = {...a, source_kind:'process', capabilities:all, status:'WAITING_INPUT'};
const quickInt = {id:'btnQuickInterrupt', dataset:{singleAction:'interrupt'}};
document.querySelectorAll = selector => selector === '[data-single-action]' ? [...buttons, quickInt] : [];
updateControls();
assert.equal(quickInt.hidden, false);
assert.equal(el('attemptConsole').hidden, false);
assert.match(sourceBadges({source_kind:'tail', completion_reason:'idle_timeout', verified:false}),
  /tail.*idle_timeout.*Unverified/);
assert.doesNotMatch(sourceBadges({source_kind:'process', verified:true}), /process/);
assert.doesNotMatch(sourceBadges({source_kind:'file', verified:true}), /Unverified/);
""")


def test_filtering_preserves_checked_jobs_and_bare_enter_sends_empty_stdin():
    run_checks(r"""
CHECKED_JOBS = new Set([a.id, b.id]);
el('jobSearch').value = 'beta';
updateControls();
assert.deepEqual([...CHECKED_JOBS], ['alpha', 'beta']);
assert.equal(el('checkedCount').textContent, '2 checked');
STATE.jobs = [b];
updateControls();
assert.deepEqual([...CHECKED_JOBS], ['beta']);
// Bare empty Enter sends empty string to stdin
STATE.jobs = [a]; STATE.detail = a; a.status = 'RUNNING';
a.capabilities = {has_process:true, can_send:true};
SELECTED_JOB_ID = a.id;
el('btnSend').disabled = false;
el('stdinInput').value = '';
fetchJson = async (path, options) => {
  calls.push({path, body:JSON.parse(options.body)});
  return {action:'send', affected:1, results:[{job_id:'alpha', ok:true, status:'accepted', reason:'ok'}]};
};
await sendStdinCommand();
assert.equal(calls.length, 1);
assert.equal(calls[0].body.action, 'send');
assert.equal(calls[0].body.text, '');
""")


def test_stop_all_two_step_force_stop_confirm_and_settings_tabs():
    run_checks(r"""
STATE.started = true; STATE.paused = false;
STATE.jobs = [{...a, status:'RUNNING', capabilities:{has_process:true, can_stop:true, can_kill:true}}];
renderTopBar();
updateControls();
assert.equal(el('btnStart').hidden, true);
assert.equal(el('btnPauseResume').hidden, false);
assert.equal(el('stopAllGroup').hidden, false);
assert.equal(el('btnForceAll').disabled, false);

// Step 1: clicking Force Stop All opens inline confirmation without issuing request
el('stopAllMenu').open = true;
promptForceStopAll();
assert.equal(el('forceAllConfirm').hidden, false);
assert.equal(calls.length, 0);
cancelForceStopAll();
assert.equal(el('forceAllConfirm').hidden, true);
assert.equal(calls.length, 0);

// Step 2: Confirm Force Stop fires force_stop and closes menu
promptForceStopAll();
await confirmForceStopAll();
assert.equal(el('forceAllConfirm').hidden, true);
assert.equal(el('stopAllMenu').open, false);
assert.equal(calls.length, 1);
assert.equal(calls[0].body.action, 'force_stop');

// Settings 2 tabs (Retention / Display)
const attrs = node => ({
  hidden: false, attrs: {},
  classList: {toggle(){}},
  setAttribute(k, v) { this[k] = v; this.attrs[k] = v; },
  focus() { this.focused = true; }
});
const retTab = attrs(), dispTab = attrs(), retSec = attrs(), dispSec = attrs();
const origGet = document.getElementById;
document.getElementById = id => ({btnSecRetention:retTab, btnSecDisplay:dispTab, settingsSectionRetention:retSec, settingsSectionDisplay:dispSec}[id] || origGet(id));
focusSettingsSection('display');
assert.equal(dispTab.attrs['aria-selected'], 'true');
assert.equal(retTab.attrs['aria-selected'], 'false');
assert.equal(retSec.hidden, true);
assert.equal(dispSec.hidden, false);
settingsTabKey({key:'ArrowLeft', preventDefault(){}});
assert.equal(retTab.attrs['aria-selected'], 'true');
assert.equal(dispSec.hidden, true);
assert.equal(retSec.hidden, false);
""")


def test_batch_summary_counts_receipt_outcomes_without_opening_report():
    run_checks(r"""
CHECKED_JOBS = new Set([a.id, b.id, c.id]);
fetchJson = async () => ({action:'stop', affected:1, results:[
  {name:'alpha', status:'accepted', ok:true, reason:'Stop requested'},
  {name:'beta', status:'rejected', ok:false, reason:'Already finished'},
  {name:'gamma', status:'failed', ok:false, reason:'<signal error>', message:'wrong text'}
]});
await batchJobAction('stop');
assert.match(feedback.at(-1)[0], /1 succeeded · 1 skipped · 1 failed/);
assert.equal(el('reportOverlay').hidden, true);
const detail = feedback.at(-1)[2];
assert.match(detail, /Stop requested/);
assert.match(detail, /Already finished/);
assert.match(detail, /&lt;signal error&gt;/);
assert.doesNotMatch(detail, /wrong text/);
""")


def test_persistent_status_and_controls_follow_connection_and_archive_health():
    run_checks(r"""
STATE.ready = true;
const stop = {dataset:{singleAction:'stop'}};
document.querySelectorAll = selector => selector === '[data-single-action]' ? [stop] : [];
setConnection(false);
assert.match(el('connectionNotice').textContent, /Disconnected/);
assert.equal(stop.disabled, true);
STATE.archive = {enabled:false, error:{message:'disk full'}};
setConnection(true);
assert.match(el('connectionNotice').textContent, /Archive error/);
assert.match(el('statusWarningText').textContent, /History save failed.*disk full/);
assert.doesNotMatch(el('connectionNotice').textContent, /Disconnected/);
assert.equal(stop.disabled, true);
assert.equal(el('btnStart').disabled, true);
await singleJobAction('stop');
assert.equal(calls.length, 0);
STATE.archive = {enabled:true, status:'failed'};
updateControls();
assert.equal(el('connectionNotice').hidden, false);
assert.equal(stop.disabled, true);
STATE.archive = {enabled:true, status:'recording'};
const saved = {detail:{...a, verified:false}, health:{status:'unclosed'}, follow:'interrupted'};
globalThis.RunHistory = {selection:saved};
updateControls();
assert.match(el('connectionNotice').textContent, /Unverified archive/);
assert.match(el('connectionNotice').textContent, /Writer may have stopped/);
assert.equal(stop.disabled, true);
assert.equal(el('btnStart').disabled, false); // The separate live queue remains available.
RunHistory.selection = null;
updateControls();
assert.equal(el('connectionNotice').hidden, true);
assert.equal(stop.disabled, false);
""")


def test_open_3_tabs_clean_logs_modern_file_browser_and_archive_mode():
    run_checks(r"""
for (const id of ['openWorkTab', 'openLogsTab', 'openArchiveTab']) {
  const tab = el(id);
  tab.setAttribute = (k, v) => { tab[k] = v; };
  tab.focus = () => { tab.focused = true; document.activeElement = tab; };
}
let navCalls = 0, sortCalls = 0;
el('browseBreadcrumbs').querySelectorAll = () => {
  navCalls++;
  return [{ dataset: { crumb: '0' } }, { dataset: { crumb: '1' } }];
};
el('browseShortcuts').querySelectorAll = () => [];
const listButtons = [{ dataset: { entry: '0' } }, { dataset: { entry: '1' } }, { dataset: { entry: '2' } }];
const checkBoxes = [{ dataset: { check: '0' }, checked: false }, { dataset: { check: '1' }, checked: false }];
el('fileList').querySelectorAll = selector =>
  selector === 'button' ? listButtons :
  selector === 'input[data-check]' ? checkBoxes : [];
el('browseSortBar').querySelectorAll = () => {
  sortCalls++;
  return [{ dataset: { sort: 'name' }, classList: { toggle(){} } },
          { dataset: { sort: 'mtime' }, classList: { toggle(){} } },
          { dataset: { sort: 'size' }, classList: { toggle(){} } }];
};

fetchJson = async (url, options) => {
  if (url.startsWith('/api/archive?')) {
    return {
      rows: [{
        run_id: 'abcd' + '0'.repeat(28),
        filename: '20261121_120000_work_abcd0000.sqlite3',
        path: 'D:/archives/20261121_120000_work_abcd0000.sqlite3',
        source_path: 'D:/proj/work.py',
        created: 1700000000,
        run_outcome: 'PASS',
        closed_cleanly: true,
      }],
    };
  }
  if (url.startsWith('/api/files?')) {
    return {
      path: 'D:/proj',
      parent: 'D:/',
      shortcuts: [
        { id: 'cwd', label: 'Launch Dir', path: 'D:/proj', exists: true },
        { id: 'archives', label: 'Archives', path: 'D:/archives', exists: true },
      ],
      entries: [
        { name: 'logs_dir', path: 'D:/proj/logs_dir', directory: true, size: null, mtime: 200 },
        { name: 'b.log', path: 'D:/proj/b.log', directory: false, size: 2048, mtime: 300 },
        { name: 'a.log', path: 'D:/proj/a.log', directory: false, size: 512, mtime: 100 },
      ],
    };
  }
  calls.push({ path: url, body: JSON.parse(options.body) });
  return { ok: true, message: 'Opened read-only archive.' };
};

// 3-tab Open keyboard navigation (work -> logs -> archive -> home)
openWorkFile('work');
assert.equal(OPEN_MODE, 'work');
switchOpenTab({ key: 'ArrowRight', preventDefault(){} });
assert.equal(OPEN_MODE, 'logs');
assert.equal(el('openLogsPanel').hidden, false);
switchOpenTab({ key: 'ArrowRight', preventDefault(){} });
assert.equal(OPEN_MODE, 'archive');
assert.equal(el('openArchivePanel').hidden, false);
switchOpenTab({ key: 'Home', preventDefault(){} });
assert.equal(OPEN_MODE, 'work');
switchOpenTab({ key: 'End', preventDefault(){} });
assert.equal(OPEN_MODE, 'archive');

// Logs: file targets hide folder globs; folder target shows globs; Static hides Start position
setOpenMode('logs');
el('logTargetAddInput').value = 'D:/proj/only.log';
addLogTargetFromInput();
assert.equal(el('logTargets').value, 'D:/proj/only.log');
assert.equal(el('logTargetEmpty').hidden, true);
assert.equal(el('logFolderOptions').hidden, true);
setLogsMode('static');
assert.equal(el('logStartLabel').hidden, true);
assert.equal(el('logStart').disabled, true);
el('logTargetAddInput').value = 'D:/proj/logs_dir';
addLogTargetFromInput();
assert.equal(el('logFolderOptions').hidden, false);

// Modern File Browser: filter, sort (folders first), multi-select addSelectedBrowsedEntries
await browseFiles('D:/proj', 'logs');
assert.equal(el('fileBrowser').hidden, false);
assert.ok(navCalls >= 1 && sortCalls >= 1);
assert.equal(BROWSE_VISIBLE.map(e => e.name).join(','), 'logs_dir,a.log,b.log');
setBrowseSort('size');
assert.equal(BROWSE_VISIBLE[0].name, 'logs_dir', 'directories stay first');
assert.equal(BROWSE_VISIBLE[1].name, 'b.log', 'first click sorts largest first');
setBrowseSort('size');
assert.equal(BROWSE_VISIBLE[1].name, 'a.log', 'second click sorts smallest first');
setBrowseFilter('b.');
assert.equal(BROWSE_VISIBLE.map(e => e.name).join(','), 'b.log');
setBrowseFilter('');
BROWSE_CHECKED.add('D:/proj/a.log');
BROWSE_CHECKED.add('D:/proj/logs_dir');
addSelectedBrowsedEntries();
assert.ok(readLogTargets().includes('D:/proj/a.log'));
assert.ok(readLogTargets().includes('D:/proj/logs_dir'));

// Archive tab: busy batch disables direct open and surfaces copyable CLI command
STATE.started = true;
STATE.jobs = [{ ...a, status: 'PENDING', held: true }];
setOpenMode('archive');
await Promise.resolve();
assert.equal(el('archiveBusyHint').hidden, false);
assert.equal(el('btnOpenArchive').disabled, true);
assert.match(el('archiveOpenCliCmd').textContent, /rpk-wave --archive/);

// When batch finishes, direct /api/archive/open succeeds
STATE.jobs = [{ ...a, status: 'DONE', held: false }];
updateArchiveBusyUi();
assert.equal(el('archiveBusyHint').hidden, true);
assert.equal(el('btnOpenArchive').disabled, false);
el('archivePath').value = 'D:/archives/20261121_120000_work_abcd0000.sqlite3';
await openArchiveFile();
assert.equal(calls.at(-1).path, '/api/archive/open');
assert.equal(calls.at(-1).body.path, 'D:/archives/20261121_120000_work_abcd0000.sqlite3');

// Explorer Back/Forward history, non-selecting Linux clipboard edit, and G1 archive delete + size
const visitedPaths = [];
fetchJson = async (url, options) => {
  if (url.startsWith('/api/files?')) {
    const match = /[?&]path=([^&]*)/.exec(url);
    const p = match ? decodeURIComponent(match[1]) : 'D:/proj';
    visitedPaths.push(p);
    return {
      path: p || 'D:/proj',
      parent: 'D:/',
      shortcuts: [
        { id: 'cwd', label: 'Launch Dir', path: 'D:/proj', exists: true },
        { id: 'work_dir', label: 'Work File Dir', path: 'D:/other_work', exists: true },
        { id: 'archives', label: 'Archives', path: 'D:/archives', exists: true },
      ],
      entries: [{ name: 'logs_dir', path: 'D:/proj/logs_dir', directory: true, size: null, mtime: 200 }],
    };
  }
  if (url.startsWith('/api/archive?')) {
    return {
      rows: [{
        run_id: 'abcd' + '0'.repeat(28),
        filename: '20261121_120000_work_abcd0000.sqlite3',
        path: 'D:/archives/20261121_120000_work_abcd0000.sqlite3',
        source_path: 'D:/proj/work.py',
        size: 4096,
        created: 1700000000,
        run_outcome: 'PASS',
        closed_cleanly: true,
      }],
    };
  }
  calls.push({ path: url, body: JSON.parse(options.body) });
  return { ok: true, message: 'Deleted archive.' };
};

openWorkFile('work');
await browseFiles('D:/dirA', 'work');
assert.equal(el('fileBrowserOverlay').hidden, false);
assert.match(el('fileList').innerHTML, /fb-entry-name-cell/);
assert.equal(el('btnBrowseBack').disabled, true);
assert.ok(!el('browseShortcuts').innerHTML.includes('Archives'), 'Archives hidden in work mode');
await browseFiles('D:/dirB', 'work');
assert.equal(el('btnBrowseBack').disabled, false);
assert.equal(el('btnBrowseForward').disabled, true);
await browseBack();
assert.equal(BROWSE_DIRECTORY, 'D:/dirA');
assert.equal(el('btnBrowseForward').disabled, false);
await browseForward();
assert.equal(BROWSE_DIRECTORY, 'D:/dirB');

let selectCalled = false, caretRange = null;
el('browsePath').select = () => { selectCalled = true; };
el('browsePath').setSelectionRange = (s, e) => { caretRange = [s, e]; };
toggleBrowsePathEdit(true);
assert.equal(selectCalled, false, 'Must not select() and overwrite X11 PRIMARY selection');
assert.deepEqual(caretRange, ['D:/dirB'.length, 'D:/dirB'.length]);
clearBrowsePathInput();
assert.equal(el('browsePath').value, '');
assert.equal(document.activeElement, el('browsePath'));
const beforeClickCount = visitedPaths.length;
listButtons[0].onclick();
assert.equal(visitedPaths.length, beforeClickCount, 'Single-clicking a folder must only select it');
assert.equal(BROWSE_DIRECTORY, 'D:/dirB');
assert.equal(BROWSE_SELECTED_INDEX, 0);
assert.equal(el('btnSelectBrowseFile').hidden, false);
assert.equal(el('btnSelectBrowseFile').textContent, 'Open Folder');
await listButtons[0].ondblclick();
assert.equal(BROWSE_DIRECTORY, 'D:/proj/logs_dir', 'Double-clicking a folder navigates into it');
await browseFiles('D:/dirB', 'work');
listButtons[0].onclick();
await confirmBrowseSelection();
assert.equal(BROWSE_DIRECTORY, 'D:/proj/logs_dir', 'Open Folder button opens the selected folder');
hideFileBrowser();
assert.equal(el('fileBrowserOverlay').hidden, true);
assert.equal(el('fileBrowser').hidden, true);

await browseFiles('D:/archives', 'archive');
assert.ok(el('browseShortcuts').innerHTML.includes('Archives'), 'Archives shortcut visible in archive mode');
hideFileBrowser();

setOpenMode('archive');
await Promise.resolve();
assert.match(el('archiveRecentList').innerHTML, /4\.0 KB/);
await deleteRecentArchive({ run_id: 'abcd' + '0'.repeat(28), path: 'D:/archives/20261121_120000_work_abcd0000.sqlite3', filename: '20261121_120000_work_abcd0000.sqlite3' });
assert.equal(calls.at(-1).path, '/api/archive/delete');
assert.equal(calls.at(-1).body.run_id, 'abcd' + '0'.repeat(28));
""")
