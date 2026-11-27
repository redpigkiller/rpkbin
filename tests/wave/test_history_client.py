"""Async view identity and bounded-page behavior; native UI is tested separately."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).parents[2] / "rpkbin/wave/history.js"
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def run(checks):
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const nodes = new Map();
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {id, dataset:{}, value:'', checked:true, hidden:false,
    disabled:false, textContent:'', selectedOptions:[], html:'', setAttribute(){}, contains(){return false},
    get innerHTML(){return this.html},
    set innerHTML(html){
      this.html=html;
      const options=[...html.matchAll(/<option value="([^"]+)"(?: data-job="([^"]+)")?>/g)];
      this.value=options[0]?.[1] || '';
      this.selectedOptions=options.length ? [{value:this.value,dataset:{job:options[0][2]}}] : [];
    },
    querySelector(selector){return element(id+' '+selector)}, querySelectorAll(){return []},
    replaceChildren(){this.innerHTML='';this.textContent=''}, scrollIntoView(){}});
  return nodes.get(id);
}
const document = {getElementById:element, activeElement:null};
const STATE = {workspace_id:'workspace', archive:{run_id:'run',enabled:true,status:'recording'}};
let DETAIL_VIEW = 'details';
const queries = [];
let responder;
function fetchJson(url) {const q = Object.fromEntries(new URL(url,'http://localhost').searchParams);queries.push(q);return responder(q);}
function escHtml(value) {return String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');}
function page(rows) {return {rows,first:'first',last:'last',has_more:true,health:{enabled:true,status:'recording'}};}
function d(id='a',lines=200) {return {id,attempt_id:id,total_lines:lines,data:{x:1}};}
function runs(ids, cursor=null) {
  return {rows:ids.map(run_id=>({run_id,created:0,closed_cleanly:true})),
    cursor,has_more:cursor!==null,unreadable_files:0};
}
function attempts(ids, before=null) {
  return {...page(ids.map(id=>({...d(id),name:id,attempt_number:1,status:'SUCCEEDED'}))),
    before,has_more:before!==null};
}
function deferred() {
  let resolve,reject;
  const promise=new Promise((done,fail)=>{resolve=done;reject=fail;});
  return {promise,resolve,reject};
}
const layouts=[];
const feedback=[];
const context = vm.createContext({document,STATE,DETAIL_VIEW,fetchJson,escHtml,URLSearchParams,Date,console,
  recordFeedback:(...args)=>feedback.push(args),
  openModal:id=>{element(id).hidden=false;}, closeModal:id=>{element(id).hidden=true;},
  setWorkspaceLayout:layout=>layouts.push(layout),
  setDetailView(){}, renderDetail(){}, updateControls(){}});
vm.runInContext(SOURCE,context);
const api = vm.runInContext('RunHistory',context);
""".replace("SOURCE", json.dumps(SOURCE.read_text(encoding="utf-8")))
    script = harness + "\n(async()=>{\n" + checks + "\n})().catch(e=>{console.error(e);process.exitCode=1});"
    result = subprocess.run([NODE, "-"], input=script.encode(), capture_output=True, timeout=10)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


@pytest.mark.parametrize("late", ["response", "error"])
def test_reopening_history_clears_choices_and_ignores_stale_run_catalog(late):
    run(r"""
const runSelect=element('archiveRun'), attemptSelect=element('archiveAttempt');
const view=element('archiveViewAttempt'), error=element('archiveError');
responder=async q=>q.view==='runs' ? runs(['old-workspace']) : attempts(['old-attempt']);
await api.openRuns();
assert.equal(view.disabled,false);

const old=deferred(), fresh=deferred();
let requested=0;
responder=q=>q.view==='runs' ? [old,fresh][requested++].promise : Promise.resolve(attempts(['new-attempt']));
const staleOpen=api.openRuns();
assert.equal(runSelect.value,'');
assert.equal(attemptSelect.value,'');
assert.equal(view.disabled,true);
const currentOpen=api.openRuns();
fresh.resolve(runs(['current-run']));
await currentOpen;
if(LATE==='response')old.resolve(runs(['outdated-run']));
else old.reject(new Error('outdated run catalog'));
await staleOpen;
assert.equal(runSelect.value,'current-run');
assert.equal(attemptSelect.value,'new-attempt');
assert.equal(view.disabled,false);
assert.equal(error.textContent,'');
""".replace("LATE", json.dumps(late)))


@pytest.mark.parametrize("late", ["response", "error"])
def test_switching_back_to_same_run_ignores_stale_attempt_catalog(late):
    run(r"""
const runSelect=element('archiveRun'), attemptSelect=element('archiveAttempt');
const view=element('archiveViewAttempt'), error=element('archiveError');
responder=async q=>q.view==='runs' ? runs(['a','b']) : attempts(['a-original']);
await api.openRuns();
const old=deferred();
responder=q=>old.promise;
const stale=api.listAttempts();
assert.equal(attemptSelect.value,'');
assert.equal(view.disabled,true);
responder=async q=>attempts([q.run_id+'-latest']);
runSelect.value='b';
await api.listAttempts();
assert.equal(attemptSelect.value,'b-latest');
runSelect.value='a';
await api.listAttempts();
if(LATE==='response')old.resolve(attempts(['outdated-attempt']));
else old.reject(new Error('outdated attempt catalog'));
await stale;
assert.equal(attemptSelect.value,'a-latest');
assert.equal(view.disabled,false);
assert.equal(error.textContent,'');
""".replace("LATE", json.dumps(late)))


def test_history_pagination_and_empty_pages_disable_unavailable_choices():
    run(r"""
const runSelect=element('archiveRun'), attemptSelect=element('archiveAttempt');
const earlierRuns=element('archiveEarlierRuns'), earlierAttempts=element('archiveEarlierAttempts');
const view=element('archiveViewAttempt');
responder=async q=>{
  if(q.view==='runs')return q.cursor ? runs([]) : runs(['run'],'runs-cursor');
  return q.before ? attempts([]) : attempts(['attempt'],'attempts-before');
};
await api.openRuns();
assert.equal(earlierRuns.disabled,false);
assert.equal(earlierAttempts.disabled,false);
await api.listAttempts(true);
assert.equal(queries.at(-1).before,'attempts-before');
assert.equal(attemptSelect.disabled,true);
assert.equal(earlierAttempts.disabled,true);
assert.equal(view.disabled,true);
await api.listRuns(true);
assert.equal(queries.at(-1).cursor,'runs-cursor');
assert.equal(runSelect.disabled,true);
assert.equal(earlierRuns.disabled,true);
assert.equal(view.disabled,true);
assert.match(element('archiveError').textContent,/No saved runs/);
""")


@pytest.mark.parametrize("late", ["response", "error"])
def test_view_attempt_ignores_prior_picker_and_opens_current_selection_in_split(late):
    run(r"""
const overlay=element('archiveOverlay'), error=element('archiveError');
const old=deferred();
responder=q=>{
  if(q.view==='runs')return Promise.resolve(runs(['saved-run']));
  if(q.view==='attempts')return Promise.resolve(attempts(['saved']));
  return old.promise;
};
await api.openRuns();
const stale=api.viewAttempt();
overlay.hidden=true;
await api.openRuns();
if(LATE==='response')old.resolve({attempt:d('saved'),health:{enabled:true}});
else old.reject(new Error('outdated attempt'));
await stale;
assert.equal(overlay.hidden,false);
assert.equal(error.textContent,'');
assert.equal(api.selection,null);
assert.deepEqual(layouts,[]);

responder=async()=>({attempt:d('saved'),health:{enabled:true}});
await api.viewAttempt();
assert.equal(api.selection.detail.id,'saved');
assert.equal(overlay.hidden,true);
assert.deepEqual(layouts,['split']);
""".replace("LATE", json.dumps(late)))


@pytest.mark.parametrize("view", ["runs", "attempts"])
def test_catalog_can_retry_failed_earlier_page_without_reenabling_stale_choices(view):
    run(r"""
const scope = VIEW;
const button = element(scope === 'runs' ? 'archiveEarlierRuns' : 'archiveEarlierAttempts');
const view = element('archiveViewAttempt'), attempt = element('archiveAttempt');
const paginate = scope === 'runs' ? api.listRuns : api.listAttempts;
const cursorField = scope === 'runs' ? 'cursor' : 'before';
responder=async q=>q.view==='runs' ? runs(['recent-run'],'first') : attempts(['recent-attempt'],'first');
await api.openRuns();
assert.equal(button.disabled,false);
responder=async q=>{throw new Error('temporary archive error')};
await paginate(true);
assert.equal(queries.at(-1)[cursorField],'first');
assert.equal(button.disabled,false);
assert.equal(view.disabled,true);
assert.equal(attempt.value,'');
responder=async q=>q.view==='runs' ? runs(['earlier-run']) : attempts(['earlier-attempt']);
const start = queries.length;
await paginate(true);
assert.equal(queries[start][cursorField],'first');
assert.equal(attempt.value,'earlier-attempt');
assert.equal(view.disabled,false);
""".replace("VIEW", json.dumps(view)))


def test_saved_selection_cannot_arrive_after_return_to_current_or_new_choice():
    run(r"""
const held=[];
responder=q=>new Promise(resolve=>held.push({q,resolve}));
const old=api.chooseAttempt('run-old','old','old-attempt');
const fresh=api.chooseAttempt('run-new','fresh','fresh-attempt');
held[1].resolve({attempt:d('fresh'),health:{enabled:true}});
await fresh;
held[0].resolve({attempt:d('old'),health:{enabled:true}});
await old;
assert.equal(api.selection.run_id,'run-new');
assert.equal(api.selection.detail.id,'fresh');
const late=api.chooseAttempt('run-old','old','old-attempt');
api.clearSelection();
held[2].resolve({attempt:d('old'),health:{enabled:true}});
await late;
assert.equal(api.selection,null);
""")


def test_dismissed_picker_or_replaced_workspace_cannot_apply_saved_attempt():
    run(r"""
responder=async()=>({attempt:d('old'),health:{enabled:true}});
assert.equal(await api.chooseAttempt('run','old','old',()=>false),undefined);
assert.equal(api.selection,null);
let finish;
responder=q=>new Promise(resolve=>finish=resolve);
const late=api.chooseAttempt('run','old','old');
STATE.workspace_id='replacement';
finish({attempt:d('old'),health:{enabled:true}});
await late;
assert.equal(api.selection,null);
""")


@pytest.mark.parametrize("phase", ["attempts", "attempt"])
@pytest.mark.parametrize("selection_change", ["none", "clear", "workspace"])
def test_initial_saved_attempt_failure_is_visible_only_for_current_request(phase, selection_change):
    run(r"""
let rejectRequest,reachedRequest;
const reached=new Promise(resolve=>reachedRequest=resolve);
responder=q=>{
  if(q.view==='attempts'&&PHASE==='attempt')return Promise.resolve({rows:[{id:'saved',attempt_id:'saved-attempt'}]});
  const pending=new Promise((resolve,reject)=>rejectRequest=reject);reachedRequest();return pending;
};
const request=api.openInitial('saved-run');await reached;
assert.equal(typeof rejectRequest,'function');
if(SELECTION_CHANGE==='clear')api.clearSelection();
if(SELECTION_CHANGE==='workspace')STATE.workspace_id='replacement';
rejectRequest(new Error('ARCHIVE_LOOKUP_FAILURE'));await request;
if(SELECTION_CHANGE==='none'){
  assert.equal(feedback.length,1);assert.equal(feedback[0][1],'error');
  assert.ok(feedback[0][0].includes('Could not open saved attempt'));
  assert.ok(feedback[0][0].includes('ARCHIVE_LOOKUP_FAILURE'));
}else assert.equal(feedback.length,0,'a stale request must not report failure in the new selection');
assert.equal(api.selection,null);
""".replace("PHASE", json.dumps(phase)).replace("SELECTION_CHANGE", json.dumps(selection_change)))


def test_saved_selection_queries_saved_attempt_and_exposes_actual_save_health():
    run(r"""
const health={enabled:true,status:'unclosed'};
responder=async()=>({attempt:d('saved'),health});await api.chooseAttempt('saved-run','saved','saved');api.refresh(d('live'));
responder=async()=>page([]);await api.query({view:'logs'});
assert.equal(queries.at(-1).run_id,'saved-run');assert.equal(queries.at(-1).job_id,'saved');assert.equal(queries.at(-1).attempt_id,'saved');
assert.equal(api.healthText(api.selection.health),'Unverified archive — committed records only');
assert.ok(api.healthText({error:{message:'disk full'}}).includes('save failed'));
""")


def test_tail_follow_pauses_recovers_and_finishes_without_reselection():
    run(r"""
let time=100, heartbeat=100, finalized=false, closed=false, lines=1;
responder=async()=>({attempt:{...d('tail',lines),source_kind:'tail',finalized},
  health:{enabled:true,closed_cleanly:closed,writer_heartbeat:heartbeat}});
await api.chooseAttempt('run','tail','tail');
await api.pollFollow(time);
assert.equal(api.selection.follow,'following');
const selection=api.selection;
api.toggleFollow(time);
lines=3;heartbeat=101;
await api.pollFollow(101);
assert.equal(api.selection,selection);
assert.equal(api.selection.detail.total_lines,3);
assert.equal(api.selection.follow,'paused');
api.toggleFollow(time);
assert.equal(api.selection.follow,'following');
await api.pollFollow(130);
assert.equal(api.selection.follow,'interrupted');
heartbeat=130;
await api.pollFollow(130);
assert.equal(api.selection.follow,'following');
lines=4; finalized=true;
await api.pollFollow(131);
assert.equal(api.selection.follow,'finished');
assert.equal(api.selection.detail.total_lines,4);
assert.equal(api.selection.health.closed_cleanly,false);
closed=true;
await api.pollFollow(132);
assert.equal(api.selection.health.closed_cleanly,true);
const count=queries.length;
await api.pollFollow(133);
assert.equal(queries.length,count);
""")
