"""Reader identity, bounded windows and filter semantics without a browser."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).parents[2] / "rpkbin/wave/analysis.js"
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def run(checks):
    harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const nodes=new Map();
function element(id){
 if(!nodes.has(id))nodes.set(id,{id,dataset:{},value:'',checked:false,hidden:false,disabled:false,attributes:{},listeners:{},
  textContent:'',innerHTML:'',scrollTop:0,scrollHeight:10000,clientHeight:300,children:[],
  classList:{toggle(){}},addEventListener(event,fn){(this.listeners[event]??=[]).push(fn)},
  setAttribute(name,value){this.attributes[name]=value},focus(){document.activeElement=this},querySelector(selector){return this.id==='flowSpecMore'?element(selector):null},
  querySelectorAll(){return []},getBoundingClientRect(){return {top:0,bottom:300}},
  appendChild(child){child.parentElement=this},replaceChildren(){this.innerHTML=''}});
 return nodes.get(id);
}
const document={getElementById:element,activeElement:null};
const STATE={workspace_id:'workspace',archive:{run_id:'run',enabled:true}};
const RunHistory={selection:null,query:(options,d)=>fetchJson('/api/archive?'+new URLSearchParams({...options,job_id:d.id,attempt_id:d.attempt_id}))};
let historical=false;function isHistory(){return historical||!!RunHistory.selection}
let responder;const queries=[];
function fetchJson(url,options){const q=Object.fromEntries(new URL(url,'http://localhost').searchParams);queries.push(q);return responder(q,options);}
function escHtml(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');}
function d(id='a',lines=200){return {id,attempt_id:id,total_lines:lines,data:{x:1},status:'DONE'}}
function row(seq,text='line '+seq){return {seq,text,elapsed:seq/10,source:'stdout'}}
function page(rows,extra={}){return {rows,first:rows.length?'c'+rows[0].seq:null,last:rows.length?'c'+rows.at(-1).seq:null,
 next:rows.length?'c'+rows.at(-1).seq:null,has_more:false,scanned:rows.length,scanned_seq:rows.at(-1)?.seq||0,end_seq:200,...extra}}
const sandbox=vm.createContext({document,STATE,RunHistory,isHistory,fetchJson,escHtml,URLSearchParams,AbortController,DETAIL_VIEW:'log',
 openModal(id){element(id).hidden=false},closeModal(id){element(id).hidden=true},Date,console,setTimeout,clearTimeout,requestAnimationFrame:fn=>fn(),setDetailView(value){sandbox.DETAIL_VIEW=value}});
vm.runInContext(SOURCE,sandbox);const api=vm.runInContext('AnalysisWorkspace',sandbox);
""".replace("SOURCE", json.dumps(SOURCE.read_text(encoding="utf-8")))
    result = subprocess.run([NODE, "-"], input=(harness + "\n(async()=>{\n" + checks +
        "\n})().catch(e=>{console.error(e);process.exitCode=1});").encode(), capture_output=True, timeout=15)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


def test_old_identity_and_cancelled_response_never_replace_current_rows():
    run(r"""
const held=[];responder=(q,o)=>new Promise(resolve=>held.push({q,o,resolve}));
const old=api.refresh(d('old'));const fresh=api.refresh(d('fresh'));
held.find(x=>x.q.job_id==='fresh').resolve(page([row(2,'FRESH')]));await fresh;
held.find(x=>x.q.job_id==='old').resolve(page([row(1,'OLD')]));await old;
assert.ok(element('analysisRows').innerHTML.includes('FRESH'));
assert.ok(!element('analysisRows').innerHTML.includes('OLD'));
const pending=api.apply();api.stop();assert.ok(held.at(-1).o.signal.aborted);
held.at(-1).resolve(page([row(9,'CANCELLED')]));await pending;
assert.ok(!element('analysisRows').innerHTML.includes('CANCELLED'));
assert.equal(element('analysisFollow').checked,false);
""")


def test_double_click_context_only_for_filtered_logs():
    run(r"""
const record={dataset:{seq:'5'}};
element('analysisRows').querySelectorAll=selector=>selector==='[data-seq]'?[record]:[];
responder=async()=>page([row(5)]);
await api.refresh(d());
assert.equal(record.ondblclick,null,'plain Log keeps native double-click selection');
element('analysisRegex').checked=true;await api.apply();
assert.equal(record.ondblclick,null,'regex without query is not a filter');
element('analysisQuery').value='line';await api.apply();
assert.equal(typeof record.ondblclick,'function');
await record.ondblclick({target:{closest:()=>null}});
assert.equal(element('analysisContext').hidden,false);
element('analysisQuery').value='';element('analysisStart').value='0';await api.apply();
assert.equal(typeof record.ondblclick,'function','time range is a Log filter');
element('analysisStart').value='';await api.apply();
assert.equal(record.ondblclick,null,'clearing filters removes the old handler');
element('analysisQuery').value='line';await api.apply();
const count=queries.length;
await record.ondblclick({target:{closest:()=>({tagName:'BUTTON'})}});
assert.equal(queries.length,count,'double-click on a value/button must not also jump');
""")


def test_closing_original_log_preserves_placement_for_next_source_jump():
    run(r"""
responder=async()=>page([row(5)]);await api.refresh(d());
await api.jump(5);api.context('below');
assert.equal(element('analysisPanel').dataset.context,'below');
assert.equal(element('btnContextPosition').attributes['aria-label'],'Move original Log right');
api.context('hidden');assert.equal(element('analysisContext').hidden,true);
assert.equal(api.contextPosition,'below');
await api.jump(7);
assert.equal(element('analysisContext').hidden,false);
assert.equal(element('analysisPanel').dataset.context,'below');
api.context('right');
assert.equal(element('btnContextPosition').attributes['aria-label'],'Move original Log below');
""")


def test_charts_context_preserves_main_reader_and_discards_a_stale_request():
    run(r"""
const held=[];responder=async()=>page([row(5,'MAIN')]);
await api.refresh(d());
const log=element('analysisRows');
log.scrollTop=42;
element('analysisQuery').value='current search';
element('analysisFollow').checked=true;
const count=queries.length;
responder=(q,o)=>new Promise(resolve=>held.push({resolve,signal:o.signal}));
api.setContextView('charts',d());
const pending=api.openContext(d(),81);
assert.equal(queries.length,count+1);
assert.equal(queries.at(-1).query,'');
assert.equal(queries.at(-1).anchor,'71');
assert.equal(log.scrollTop,42);
assert.equal(element('analysisFollow').checked,true);
assert.equal(element('analysisQuery').value,'current search');
assert.ok(!log.innerHTML.includes('analysis-selected'));
api.setContextView('analysis',d());
assert.equal(held[0].signal.aborted,true);
held[0].resolve(page([row(81,'STALE')]));
await pending;
assert.equal(element('analysisContext').hidden,true);
assert.ok(!element('analysisContextRows').innerHTML.includes('STALE'));
""")


def test_sparse_search_continues_and_bounded_windows_can_read_backwards():
    run(r"""
let batch=0;responder=async q=>{
 if(batch++===0)return page([],{next:'scan1000',has_more:true,scanned:1000,scanned_seq:1000,end_seq:10000});
 const start=q.direction==='backward'?1:batch*100;
 return page(Array.from({length:100},(_,i)=>row(start+i)),{has_more:true});
};
await api.refresh(d());assert.equal(queries[1].cursor,'scan1000');
for(let i=0;i<10;i++)await api.edge('forward');
assert.ok((element('analysisRows').innerHTML.match(/class="analysis-row/g)||[]).length<=600);
await api.edge('backward');assert.equal(queries.at(-1).direction,'backward');
assert.ok(queries.at(-1).cursor);
""")


def test_reading_old_data_does_not_follow_live_and_raw_text_is_escaped():
    run(r"""
responder=async q=>page([row(5,'  <state>\tRUN    ')]);
await api.refresh(d());const count=queries.length;
await api.refresh(d('a',999999));assert.equal(queries.length,count);
assert.ok(element('analysisRows').innerHTML.includes('  &lt;state>\tRUN    '));
element('analysisRegex').checked=true;STATE.archive.enabled=false;
await api.refresh(d('fallback'));assert.ok(element('analysisStatus').textContent.includes('retained'));
assert.equal(queries.length,count);
""")


def test_sparse_nonempty_pages_fill_viewport_and_keep_all_early_matches():
    run(r"""
const box=element('analysisRows');Object.defineProperty(box,'scrollHeight',{get(){return (this.innerHTML.match(/class="analysis-row/g)||[]).length*20}});
box.clientHeight=300;let batch=0;
responder=async q=>{batch++;return page([row(batch*1000)],{next:'scan'+batch,has_more:batch<25,scanned:1000,scanned_seq:batch*1000,end_seq:25000})};
await api.refresh(d());assert.ok(batch>=16&&batch<25);
assert.ok(box.innerHTML.includes('line 1000'));assert.ok(box.scrollHeight>box.clientHeight);
await api.edge('forward');assert.equal(queries.at(-1).cursor,'scan'+(batch-1));
""")


def test_cancelled_sparse_scan_resumes_exact_continuation_without_starting_over():
    run(r"""
let finish;let batch=0;
responder=async q=>{
 if(++batch===1)return page([],{next:'scan1000',has_more:true,scanned:1000,scanned_seq:1000,end_seq:5000});
 return new Promise(resolve=>finish=resolve);
};
const pending=api.refresh(d());
while(!finish)await new Promise(resolve=>setTimeout(resolve,1));
api.stop();assert.equal(element('analysisCancel').textContent,'Continue');
finish(page([row(1001,'STALE')]));await pending;
responder=async q=>page([row(2000,'RESUMED')]);
await api.edge('continue');assert.equal(queries.at(-1).cursor,'scan1000');
assert.ok(element('analysisRows').innerHTML.includes('RESUMED'));assert.ok(!element('analysisRows').innerHTML.includes('STALE'));
""")


def test_latest_sparse_pages_fill_backwards_and_remain_ascending():
    run(r"""
const box=element('analysisRows');Object.defineProperty(box,'scrollHeight',{get(){return (this.innerHTML.match(/class="analysis-row/g)||[]).length*20}});
box.clientHeight=100;element('analysisFollow').checked=true;let seq=100;
responder=async q=>{assert.equal(q.direction,'backward');return page([row(seq--)],{next:'scan'+seq,has_more:seq>0,scanned_seq:seq,end_seq:100})};
await api.refresh(d());assert.equal(queries.length,6);
assert.ok(box.innerHTML.indexOf('line 95')<box.innerHTML.indexOf('line 100'));
assert.equal(box.scrollTop,box.scrollHeight);
""")


def test_history_follow_disabled_and_backward_status_is_not_end():
    run(r"""
historical=true;element('analysisFollow').checked=true;
responder=async q=>page([row(3)]);
await api.refresh(d());assert.equal(element('analysisFollow').checked,false);
assert.equal(element('analysisFollow').disabled,true);

await api.edge('latest');assert.ok(element('analysisStatus').textContent.includes('beginning reached'));
assert.ok(!element('analysisStatus').textContent.includes('end of matches'));
""")


def test_invalid_search_clears_old_results():
    run(r"""
responder=async q=>page([row(1,'OLD SEARCH RESULT')]);await api.refresh(d());
responder=async q=>{throw new Error('Invalid regular expression')};
element('analysisQuery').value='[';element('analysisRegex').checked=true;await api.apply();
assert.ok(!element('analysisRows').innerHTML.includes('OLD SEARCH RESULT'));
assert.ok(element('analysisStatus').textContent.includes('Invalid regular expression'));

""")


def test_paused_reader_can_scroll_into_new_live_tail_without_enabling_follow():
    run(r"""
responder=async q=>page([row(1,'OLD')],{next:'scan200',end_seq:200});await api.refresh(d());
const count=queries.length;await api.refresh(d('a',201));assert.equal(queries.length,count);
responder=async q=>page([row(201,'NEW')],{next:'scan201',end_seq:201});await api.edge('forward');
assert.equal(queries.at(-1).cursor,'scan200');assert.ok(element('analysisRows').innerHTML.includes('NEW'));
assert.ok(element('analysisRows').innerHTML.includes('OLD'));assert.equal(element('analysisFollow').checked,false);
""")


def test_underfilled_filtered_tail_probes_each_new_head_once_even_with_no_matches():
    run(r"""
const box=element('analysisRows');Object.defineProperty(box,'scrollHeight',{get(){return (this.innerHTML.match(/class="analysis-row/g)||[]).length*20}});
element('analysisFilterMode').checked=true;element('analysisQuery').value='needle';let head=200;
responder=async q=>page([],{next:'scan'+head,end_seq:head,scanned_seq:head});
await api.refresh(d());const count=queries.length;
head=201;await api.refresh(d('a',201));assert.equal(queries.length,count+1);
assert.equal(queries.at(-1).cursor,'scan200');assert.equal(queries.at(-1).query,'needle');
await api.refresh(d('a',201));assert.equal(queries.length,count+1);
head=202;await api.refresh(d('a',202));assert.equal(queries.length,count+2);
""")


def test_empty_pending_attempt_reads_new_output_once_without_resetting_filter():
    run(r"""
const box=element('analysisRows');Object.defineProperty(box,'scrollHeight',{get(){return (this.innerHTML.match(/class="analysis-row/g)||[]).length*20}});
element('analysisFilterMode').checked=true;element('analysisQuery').value='needle';responder=async q=>page([],{next:null,end_seq:0});
await api.refresh(d('a',0));const count=queries.length;
responder=async q=>page([row(1,'needle')],{end_seq:1});await api.refresh(d('a',1));
assert.equal(queries.length,count+1);assert.equal(queries.at(-1).query,'needle');
assert.equal(queries.at(-1).cursor,undefined);assert.ok(box.innerHTML.includes('needle'));
await api.refresh(d('a',1));assert.equal(queries.length,count+1);
""")


def test_control_home_end_reach_source_edges_in_both_readers():
    run(r"""
responder=async q=>page([row(200)]);
await api.refresh(d());
const fire=async(id,key,modifier='ctrlKey')=>{
 let prevented=false;
 element(id).listeners.keydown[0]({key,[modifier]:true,target:element(id),preventDefault(){prevented=true}});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(prevented,true);
};
await fire('analysisRows','End');
assert.equal(queries.at(-1).direction,'backward');
assert.equal(queries.at(-1).cursor,undefined);assert.equal(element('analysisFollow').checked,true);
await fire('analysisRows','Home');
assert.equal(queries.at(-1).direction,'forward');assert.equal(element('analysisFollow').checked,false);
await api.jump(200);element('analysisQuery').value='FILTER';
await fire('analysisContextRows','End','metaKey');
assert.equal(queries.at(-1).query,'');assert.equal(queries.at(-1).kind,undefined);
assert.equal(queries.at(-1).direction,'backward');assert.equal(element('analysisFollow').checked,false);
""")


def test_explicit_stop_at_end_prevents_new_output_from_restarting_reading():
    run(r"""
const box=element('analysisRows');Object.defineProperty(box,'scrollHeight',{get(){return 20}});
responder=async()=>page([row(1)],{end_seq:1});await api.refresh(d('a',1));
api.stop();const count=queries.length;
await api.refresh(d('a',2));assert.equal(queries.length,count);
""")


def test_follow_keeps_reading_visible_until_response_and_does_not_repaint_equal_rows():
    run(r"""
const box=element('analysisRows');let html='',writes=0;
Object.defineProperty(box,'innerHTML',{get(){return html},set(value){html=value;writes++}});
responder=async()=>page([row(200,'VISIBLE LOG')]);
await api.refresh(d());const original=html,painted=writes,originalStatus=element('analysisStatus').textContent;
let finish;responder=()=>new Promise(resolve=>finish=resolve);
const update=api.edge('latest');
assert.equal(box.innerHTML,original,'Keep the old reading visible during network latency');
assert.equal(writes,painted,'Do not clear the live reader before its response');
assert.equal(element('analysisStatus').textContent,originalStatus,'Background Follow does not flash a loading label');
finish(page([row(200,'VISIBLE LOG')]));await update;
assert.equal(writes,painted,'Equal rows and the final render must not repaint');
responder=async()=>page([row(200,'VISIBLE LOG')]);
await api.edge('start');
assert.ok(box.innerHTML.includes('VISIBLE LOG'),'Explicit reset invalidates the render cache');
""")


def test_applied_time_filter_chip_removal_keeps_text_search():
    run(r"""
responder=async()=>page([row(1)]);await api.refresh(d());
element('analysisFilterMode').checked=true;element('analysisQuery').value='shared query';element('analysisRegex').checked=true;
element('analysisStart').value='1.25';element('analysisEnd').value='9';await api.apply();
assert.equal(element('analysisFilterCount').textContent,'1');
assert.equal(queries.at(-1).start,'1.25');
await api.removeFilter('time');assert.equal(queries.at(-1).start,undefined);
assert.equal(queries.at(-1).end,undefined);assert.equal(queries.at(-1).query,'shared query');
""")


def test_active_reading_tab_keeps_source_position_and_details_can_return_to_same_tab():
    run(r"""
responder=async()=>page([row(200)]);await api.refresh(d());
const box=element('analysisRows');box.scrollTop=75;
const count=queries.length;
api.setContextView('details',d());
api.setContextView('log',d());
await api.refresh(d());
assert.equal(queries.length,count);
assert.equal(box.scrollTop,75);
""")


def test_cancel_control_hidden_when_idle_and_short_background_follow_refreshes():
    run(r"""
responder=async()=>page([row(200)]);await api.refresh(d());
assert.equal(element('analysisCancel').hidden,true);
let finish;responder=()=>new Promise(resolve=>finish=resolve);
const background=api.edge('latest');assert.equal(element('analysisCancel').hidden,true);
finish(page([row(200)]));await background;assert.equal(element('analysisCancel').hidden,true);
const explicit=api.apply();assert.equal(element('analysisCancel').hidden,false);
api.stop();assert.equal(element('analysisCancel').hidden,false);
assert.equal(element('analysisCancel').textContent,'Continue');
finish(page([row(1)]));await explicit;assert.equal(element('analysisCancel').textContent,'Continue');
""")


def test_cancelled_latest_replacement_continues_as_replacement_not_an_append():
    run(r"""
responder=async()=>page([row(200,'OLD')]);await api.refresh(d());
let finish;responder=()=>new Promise(resolve=>finish=resolve);
const background=api.edge('latest');api.stop();
responder=async()=>page([row(200,'UPDATED')]);await api.edge('continue');
assert.equal((element('analysisRows').innerHTML.match(/data-key="200:/g)||[]).length,1);
assert.ok(!element('analysisRows').innerHTML.includes('OLD'));
finish(page([row(200,'STALE')]));await background;
assert.ok(element('analysisRows').innerHTML.includes('UPDATED'));
""")


def test_sparse_latest_replacement_keeps_old_reading_until_a_hit_arrives():
    run(r"""
responder=async()=>page([row(200,'OLD READING')]);await api.refresh(d());
let finish,batch=0;
responder=async()=>++batch===1?page([],{has_more:true,next:'scan1000'}):new Promise(resolve=>finish=resolve);
const background=api.edge('latest');
while(!finish)await new Promise(resolve=>setTimeout(resolve,1));
assert.ok(element('analysisRows').innerHTML.includes('OLD READING'));
assert.equal(element('analysisCancel').hidden,false,'Sparse background search remains cancellable');
finish(page([row(199,'FOUND')]));await background;
assert.ok(element('analysisRows').innerHTML.includes('FOUND'));
assert.ok(!element('analysisRows').innerHTML.includes('OLD READING'));
""")


def test_raw_full_text_chunks_preserve_utf8_offsets_and_exact_text():
    run(r"""
responder=async()=>page([row(1)]);await api.refresh(d());
responder=async q=>({text:'中😀9007199254740993',offset:Number(q.offset||0),next_offset:24,total:48,offset_unit:'utf8-bytes',has_more:true});
await api.readFull(1);assert.equal(element('flowSpecCode').textContent,'中😀9007199254740993');
assert.equal(queries.at(-1).field,undefined);assert.equal(queries.at(-1).view,'text');
await element('[data-log-next]').onclick();assert.equal(queries.at(-1).offset,'24');
assert.ok(element('flowSpecFacts').textContent.includes('utf8-bytes'));
""")


def test_raw_full_text_late_response_cannot_overwrite_new_selection_or_inspection():
    run(r"""
responder=async()=>page([row(1)]);await api.refresh(d());
const held=[];responder=q=>new Promise(resolve=>held.push({q,resolve}));
const old=api.readFull(1),fresh=api.readFull(2);
held[1].resolve({text:'NEW',offset:0,next_offset:3,total:3,has_more:false});await fresh;
held[0].resolve({text:'OLD',offset:0,next_offset:3,total:3,has_more:false});await old;
assert.equal(element('flowSpecCode').textContent,'NEW');
const closing=api.readFull(3);api.closeInspection();element('flowSpecCode').textContent='CLOSED';
held[2].resolve({text:'CLOSED OLD'});await closing;assert.equal(element('flowSpecCode').textContent,'CLOSED');
const changing=api.readFull(4);responder=async()=>page([row(1)]);await api.refresh(d('new'));
held[3].resolve({text:'WRONG ATTEMPT'});await changing;assert.equal(element('flowSpecCode').textContent,'Reading saved text…');
""")


def test_archive_follow_retains_log_cursor_across_pause_and_final_update():
    run(r"""
RunHistory.selection={run_id:'tail',health:{enabled:true},follow:'following'};
let end=1;
responder=async q=>{
  const start=q.cursor?Number(q.cursor.slice(1)):0;
  return page(Array.from({length:end-start},(_,i)=>row(i+start+1)),{end_seq:end});
};
await api.refresh(d('a',1));
RunHistory.selection.follow='paused';
end=3;await api.refresh(d('a',3));
assert.equal(queries.length,1);
RunHistory.selection.follow='following';
await api.refresh(d('a',3));
assert.equal(queries.at(-1).cursor,'c1');
RunHistory.selection.follow='finished';
RunHistory.selection.catchUp=true;
end=4;await api.refresh(d('a',4));
assert.equal(queries.at(-1).cursor,'c3');
await api.refresh(d('a',4));
assert.equal(queries.length,3);
assert.deepEqual(Array.from(element('analysisRows').innerHTML.matchAll(/data-seq="(\d+)"/g),m=>m[1]),['1','2','3','4']);
""")


def test_vscode_find_in_context_mode_b_highlights_and_steps_without_filtering_rows():
    run(r"""
responder=async()=>page([
  row(1,'init start'),
  row(2,'Error: first error here'),
  row(3,'normal status line'),
  row(4,'ERROR: final check')
],{has_more:false,end_seq:4});
await api.refresh(d('a',4));
const initialQueries=queries.length;
element('analysisQuery').value='error';
await api.apply();
assert.equal(queries.length,initialQueries,'Mode B highlights in-memory rows without reloading when unfiltered');
const html=element('analysisRows').innerHTML;
assert.ok(html.includes('init start')&&html.includes('normal status line'),'All context lines remain visible in Mode B');
assert.equal((html.match(/<mark class="log-hit/g)||[]).length,3);
assert.ok(html.includes('<mark class="log-hit active">Error</mark>'));
assert.equal(element('analysisMatchCount').textContent,'1 of 3');

await api.nextMatch();
assert.equal(element('analysisMatchCount').textContent,'2 of 3');
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">error</mark>'));

await api.nextMatch();
assert.equal(element('analysisMatchCount').textContent,'3 of 3');
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">ERROR</mark>'));

await api.nextMatch();
assert.equal(element('analysisMatchCount').textContent,'1 of 3','Wraps around at end when no more pages');
await api.prevMatch();
assert.equal(element('analysisMatchCount').textContent,'3 of 3','Wraps backward at start when no more pages');
""")


def test_vscode_find_case_sensitive_whole_word_and_regex_toggles_in_both_modes():
    run(r"""
responder=async()=>page([
  row(1,'Error err error_code ERR42 err7 my_err7_x'),
  row(2,'plain line')
],{has_more:true,next:'c2',end_seq:10});
await api.refresh(d('a',10));

// Case sensitive (Aa)
element('analysisQuery').value='Error';
element('analysisCase').checked=true;
await api.apply();
assert.equal(element('analysisMatchCount').textContent,'1 of 1+');
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">Error</mark>'));

// Whole word (ab) + Case insensitive
element('analysisQuery').value='err';
element('analysisCase').checked=false;
element('analysisWord').checked=true;
await api.apply();
assert.equal(element('analysisMatchCount').textContent,'1 of 1+');
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">err</mark>'));

// Regex (.*) + Whole word (ab) + Case sensitive (Aa)
element('analysisQuery').value='err\\d+';
element('analysisRegex').checked=true;
element('analysisCase').checked=true;
element('analysisWord').checked=true;
await api.apply();
assert.equal(element('analysisMatchCount').textContent,'1 of 1+');
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">err7</mark>'));

// Mode A (filterLines === true) passes case_sensitive, whole_word, regex, query to backend
responder=async q=>page([row(1,'err7')],{has_more:false,end_seq:10});
element('analysisFilterMode').checked=true;
await api.apply();
const lastQ=queries.at(-1);
assert.equal(lastQ.query,'err\\d+');
assert.equal(lastQ.regex,'true');
assert.equal(lastQ.case_sensitive,'true');
assert.equal(lastQ.whole_word,'true');
assert.equal(element('analysisMatchCount').textContent,'1 of 1');
assert.ok(!element('analysisRows').innerHTML.includes('plain line'));

// Toggling filterLines back off reloads full unfiltered log while preserving highlights
responder=async q=>page([row(1,'Error err7'),row(2,'plain line')],{has_more:false,end_seq:2});
element('analysisFilterMode').checked=false;
await api.apply();
assert.equal(queries.at(-1).query,'');
assert.ok(element('analysisRows').innerHTML.includes('plain line'));
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">err7</mark>'));
""")


def test_vscode_find_mode_b_steps_past_window_edge_via_backend_anchor():
    run(r"""
responder=async()=>page([row(1,'first line'),row(2,'needle here')],{has_more:true,next:'c2',end_seq:100});
await api.refresh(d('a',100));
element('analysisQuery').value='needle';
element('analysisCase').checked=true;
element('analysisWord').checked=true;
await api.apply();
assert.equal(element('analysisMatchCount').textContent,'1 of 1+');

responder=async q=>{
  if(q.query==='needle'){
    assert.equal(q.direction,'forward');
    assert.equal(q.anchor,'3');
    assert.equal(q.case_sensitive,'true');
    assert.equal(q.whole_word,'true');
    return page([row(50,'distant needle match')],{has_more:false,end_seq:100});
  }
  assert.equal(q.anchor,'40');
  assert.equal(q.query,'');
  return page([row(49,'context before'),row(50,'distant needle match'),row(51,'context after')],{has_more:false,end_seq:100});
};
await api.nextMatch();
assert.ok(element('analysisRows').innerHTML.includes('context before'));
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">needle</mark>'));
assert.equal(element('analysisMatchCount').textContent,'1 of 1+');

responder=async q=>{
  if(q.query==='needle'){
    assert.equal(q.direction,'backward');
    assert.equal(q.anchor,'48');
    return page([row(2,'needle here')],{has_more:false,end_seq:100});
  }
  return page([row(1,'first line'),row(2,'needle here')],{has_more:true,next:'c2',end_seq:100});
};
await api.prevMatch();
assert.ok(element('analysisRows').innerHTML.includes('first line'));
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">needle</mark>'));
""")


def test_clearing_analysis_query_input_immediately_clears_highlights_and_filters():
    run(r"""
responder=async()=>page([row(1,'alpha needle'),row(2,'beta')]);
await api.refresh(d('a',2));
element('analysisQuery').value='needle';
element('analysisQuery').listeners.input[0]();
await new Promise(resolve=>setTimeout(resolve,180));
assert.ok(element('analysisRows').innerHTML.includes('<mark class="log-hit active">needle</mark>'));
assert.equal(element('analysisMatchCount').textContent,'1 of 1');

// Immediate clear when empty in Mode B
element('analysisQuery').value='';
element('analysisQuery').listeners.input[0]();
assert.ok(!element('analysisRows').innerHTML.includes('<mark class="log-hit'));
assert.equal(element('analysisMatchCount').textContent,'');

// Immediate clear when empty in Mode A reloads unfiltered stream
element('analysisFilterMode').checked=true;
element('analysisQuery').value='needle';
responder=async q=>page(q.query?[row(1,'alpha needle')]:[row(1,'alpha needle'),row(2,'beta')]);
await api.apply();
assert.ok(!element('analysisRows').innerHTML.includes('beta'));
element('analysisQuery').value='';
element('analysisQuery').listeners.input[0]();
await new Promise(resolve=>setTimeout(resolve,10));
assert.equal(queries.at(-1).query,'');
assert.ok(element('analysisRows').innerHTML.includes('beta'));
""")


def test_find_widget_open_close_toggle_and_close_button_cancels_active_scan():
    run(r"""
element('vscodeFindWidget').hidden=true;
responder=async()=>page([row(1,'hello')]);
await api.refresh(d('a',1));
api.toggleFind();
assert.equal(element('vscodeFindWidget').hidden,false);
assert.equal(document.activeElement,element('analysisQuery'));
assert.equal(element('btnToggleFind').attributes['aria-pressed'],'true');

let finish;
responder=()=>new Promise(resolve=>finish=resolve);
element('analysisFilterMode').checked=true;
element('analysisQuery').value='slow';
const pending=api.apply();
// Clicking close while a scan is active cancels the scan first without hiding widget
element('btnCloseFind').listeners.click[0]();
assert.equal(element('vscodeFindWidget').hidden,false);
finish(page([]));await pending;
// Clicking close again when idle hides the find widget
element('btnCloseFind').listeners.click[0]();
assert.equal(element('vscodeFindWidget').hidden,true);
assert.equal(element('btnToggleFind').attributes['aria-pressed'],'false');
""")


def test_ansi_sgr_rendering_and_stripping_with_search_highlights_and_xss_safety():
    run(r"""
responder=async()=>page([
  row(1,'\x1b[1;31mERROR\x1b[0m: \x1b[32m<script>\x1b[m \x1b[2K\x1b]0;title\x07\x1b[38;5;12mblue')
]);
await api.refresh(d('a',1));
const html=element('analysisRows').innerHTML;
assert.ok(!html.includes('\x1b'),'All raw escape characters are stripped');
assert.ok(!html.includes('title'),'OSC sequences are stripped');
assert.ok(!html.includes('<script>'),'HTML inside ANSI is escaped');
assert.ok(html.includes('<span class="ansi-bold ansi-fg-red">ERROR</span>'));
assert.ok(html.includes('<span class="ansi-fg-green">&lt;script></span>'));
assert.ok(html.includes('<span class="ansi-fg-bright-blue">blue</span>'));

// Search match across ANSI span boundary
element('analysisQuery').value='ERROR: <script>';
await api.apply();
const highlighted=element('analysisRows').innerHTML;
assert.ok(highlighted.includes('<mark class="log-hit active"><span class="ansi-bold ansi-fg-red">ERROR</span>: <span class="ansi-fg-green">&lt;script></span></mark>'));

// Toggling ansiColor off strips ANSI without color spans
api.ansiColor=false;
const plainHtml=element('analysisRows').innerHTML;
assert.ok(!plainHtml.includes('ansi-fg-'));
assert.ok(plainHtml.includes('<mark class="log-hit active">ERROR: &lt;script></mark>'));
api.ansiColor=true;
""")


def test_hidden_layout_guard_and_incremental_forward_tailing():
    run(r"""
sandbox.WORKSPACE_LAYOUT='jobs';
responder=async()=>page([row(1,'hidden')]);
await api.refresh(d('a',1));
assert.equal(queries.length,0,'Do not poll archive when layout is jobs');
sandbox.WORKSPACE_LAYOUT='charts';
await api.refresh(d('a',1));
assert.equal(queries.length,0,'Do not poll archive when layout is charts');

sandbox.WORKSPACE_LAYOUT='split';
element('analysisFollow').checked=true;
responder=async q=>page([row(199,'line 199'),row(200,'line 200')],{end_seq:200});
await api.refresh(d('a',200));
assert.equal(queries.length,1);
assert.equal(queries[0].direction,'backward');

// When total_lines grows while following, tail forward incrementally from c200
responder=async q=>page([row(201,'line 201'),row(202,'line 202')],{end_seq:202});
await api.refresh(d('a',202));
assert.equal(queries.length,2);
assert.equal(queries[1].direction,'forward');
assert.equal(queries[1].cursor,'c200');
assert.ok(element('analysisRows').innerHTML.includes('line 199'));
assert.ok(element('analysisRows').innerHTML.includes('line 202'));
""")


def test_archive_disabled_guards_find_handlers_and_updates_log_options_menu():
    run(r"""
responder=async()=>page([row(1,'alpha'),row(2,'alpha beta')]);
await api.refresh(d('a',2));

// #logOptionsMenu receives data-active when elapsed time filter is active
element('analysisStart').value='1';
await api.apply();
assert.equal(element('logOptionsMenu').attributes['data-active'],'true');
await api.removeFilter('time');
assert.equal(element('logOptionsMenu').attributes['data-active'],'false');

// Switching to an archive-disabled workspace prevents AnalysisWorkspace DOM find handlers from double-firing
STATE.archive.enabled=false;
element('vscodeFindWidget').hidden=true;
element('analysisQuery').value='alpha';
element('analysisMatchCount').textContent='retained';
element('btnToggleFind').listeners.click[0]();
assert.equal(element('vscodeFindWidget').hidden,true,'AnalysisWorkspace must not toggle widget when archive disabled');

element('vscodeFindWidget').hidden=false;
element('btnCloseFind').listeners.click[0]();
assert.equal(element('vscodeFindWidget').hidden,false,'AnalysisWorkspace must not close widget when archive disabled');

const qCount=queries.length;
element('analysisFindNext').listeners.click[0]();
element('analysisFindPrev').listeners.click[0]();
element('analysisQuery').listeners.input[0]();
let prevented=false;
element('analysisQuery').listeners.keydown[0]({key:'Enter',preventDefault(){prevented=true}});
assert.equal(prevented,false);
for (const id of ['analysisCase','analysisWord','analysisRegex','analysisFilterMode']) {
  element(id).checked=true;
  element(id).listeners.change[0]();
}
await new Promise(resolve=>setTimeout(resolve,180));
assert.equal(queries.length,qCount);
assert.equal(element('analysisMatchCount').textContent,'retained');
""")


def test_phase21_follow_immediate_scroll_bottom_reenable_gutter_and_incremental_append():
    run(r"""
const box=element('analysisRows');
box.style={props:{},setProperty(k,v){this.props[k]=v;}};
box.scrollHeight=5000;box.clientHeight=300;
responder=async()=>page([row(5,'line 5')],{end_seq:5});
await api.refresh(d('a',5));

// #8: dynamic --log-gutter-w grows cleanly for large sequence numbers
assert.equal(box.style.props['--log-gutter-w'],'18px');

// #6: clicking Follow when already up-to-date scrolls straight to tail with zero extra requests
const countBeforeFollow=queries.length;
box.scrollTop=120;
element('analysisFollow').checked=true;
element('analysisFollow').listeners.change[0]();
assert.equal(box.scrollTop,5000);
assert.equal(queries.length,countBeforeFollow);

// #6: explicit Follow cancels a stale pending request rather than returning early
let releaseStale;
responder=()=>new Promise(resolve=>releaseStale=resolve);
const staleEdge=api.edge('start');
responder=async()=>page([row(123456,'LATEST ROW')],{end_seq:123456});
element('analysisFollow').checked=true;
element('analysisFollow').listeners.change[0]();
releaseStale(page([row(1,'STALE ROW')],{end_seq:10}));
await staleEdge;
await new Promise(resolve=>setTimeout(resolve,10));
assert.ok(box.innerHTML.includes('LATEST ROW'));
assert.ok(!box.innerHTML.includes('STALE ROW'));
assert.equal(box.style.props['--log-gutter-w'],'46px');

// #6: scrolling manually back to bottom after programmatic window re-enables Follow
await new Promise(resolve=>setTimeout(resolve,270));
element('analysisFollow').checked=false;
box.scrollTop=4700;
box.listeners.scroll[0]();
assert.equal(element('analysisFollow').checked,true);

// #13: incremental forward append + head trim uses insertAdjacentHTML without rewriting innerHTML
let htmlMarkup='', writes=0;
box.children=[];
Object.defineProperty(box,'firstElementChild',{get(){return box.children[0]||null;}});
Object.defineProperty(box,'innerHTML',{
  get(){return htmlMarkup;},
  set(v){
    writes++;
    htmlMarkup=String(v);
    box.children=Array.from(htmlMarkup.matchAll(/<div class="analysis-row"[^>]*data-seq="(\d+)"/g),m=>{
      const c={dataset:{seq:m[1]},classList:{contains:cls=>cls==='analysis-row'},remove(){const i=box.children.indexOf(c);if(i>=0)box.children.splice(i,1);}};
      return c;
    });
  }
});
box.insertAdjacentHTML=(pos,chunk)=>{
  assert.equal(pos,'beforeend');
  htmlMarkup+=chunk;
  for(const m of chunk.matchAll(/<div class="analysis-row"[^>]*data-seq="(\d+)"/g)){
    const c={dataset:{seq:m[1]},classList:{contains:cls=>cls==='analysis-row'},remove(){const i=box.children.indexOf(c);if(i>=0)box.children.splice(i,1);}};
    box.children.push(c);
  }
};
responder=async q=>{
  const after=q.cursor?Number(q.cursor.slice(1)):0;
  let rows;
  if(after===0)rows=Array.from({length:300},(_,i)=>row(i+1));
  else if(after===300)rows=Array.from({length:300},(_,i)=>row(301+i));
  else rows=[row(601),row(602)];
  return page(rows,{next:'c'+rows.at(-1).seq,end_seq:rows.at(-1).seq,scanned_seq:rows.at(-1).seq,has_more:after<300});
};
element('analysisFollow').checked=false;
await api.refresh(d('inc',600));
await api.edge('forward');
const initialWrites=writes;
element('analysisFollow').checked=true;
await api.refresh(d('inc',602));
assert.equal(writes,initialWrites);
assert.equal(box.children.length,302);
assert.equal(box.children[0].dataset.seq,'301');
assert.equal(box.children.at(-1).dataset.seq,'602');
""")
