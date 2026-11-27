"""Flow publication identity, bounded windows and source navigation."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).parents[2] / "rpkbin/wave/flow.js"
JOBS_SOURCE = SOURCE.with_name("jobs.js")
READING_SOURCE = SOURCE.with_name("reading.js")
ICONS_SOURCE = SOURCE.with_name("icons.js")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def run(checks, with_reading=False):
    harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const nodes=new Map(),handlers={},stored=new Map();
function element(id){
 if(!nodes.has(id))nodes.set(id,{id,dataset:{},hidden:false,value:'',checked:false,attributes:{},listeners:{},
  innerHTML:'',textContent:'',scrollTop:0,scrollHeight:10000,clientHeight:300,offsetTop:0,
  parentElement:null,classList:{toggle(){},contains(){return false}},style:{},
  setAttribute(k,v){this.attributes[k]=v},getAttribute(k){return this.attributes[k]},
  addEventListener(k,fn){this.listeners[k]=fn},querySelector(){return element('context-home')},
  querySelectorAll(){return []},replaceChildren(){this.innerHTML=''},
  appendChild(child){child.parentElement=this},focus(){document.activeElement=this},
  select(){this.selectionStart=0;this.selectionEnd=this.value.length},
 });return nodes.get(id);
}
element('flowPriorityHome').parentElement=element('priority-home');
element('analysisContext').parentElement=element('context-home');
const window={innerWidth:1280,innerHeight:800,localStorage:{getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,v)}};
const document={getElementById:id=>id==='waveColumnMenu'&&!nodes.has(id)?null:element(id),
 createElement:tag=>{const el=element(tag==='div'?'waveColumnMenu':'created_'+tag);return el;},
 body:{appendChild(el){nodes.set(el.id,el);}},
 addEventListener(k,fn){handlers[k]=fn}};
// Keep the execution host's child identities until its markup is assigned again.
const executionFacts=element('flowExecutionFacts');
let executionMarkup='', executionValues=[];
Object.defineProperty(executionFacts,'innerHTML',{
 get(){return executionMarkup},
 set(html){
   executionMarkup=html;
   for(const id of ['flowCopyPath','flowCopyFallback','flowCopyField','flowCopyStatus']){
     if(document.activeElement && document.activeElement===nodes.get(id))document.activeElement=null;
     nodes.delete(id);
   }
   for(const match of html.matchAll(/<(\w+) [^>]*id="(flowCopy\w+)"[^>]*>/g)){
     const field=element(match[2]);
     field.hidden=/\bhidden\b/.test(match[0]);
     field.readOnly=/\breadonly\b/.test(match[0]);
   }
   executionValues=Array.from(html.matchAll(/<td[^>]*data-flow-live-fact="(\d+)"[^>]*>([^<]*)<\/td>/g),match=>({
     dataset:{flowLiveFact:match[1]},textContent:match[2],
   }));
 },
});
executionFacts.querySelectorAll=selector=>selector==='[data-flow-live-fact]'?executionValues:[];
const STATE={workspace_id:'workspace',archive:{run_id:'run',enabled:true}};
let respond=async()=>({rows:[],has_more:false,next_seq:0});const queries=[],jumps=[];
const RunHistory={selection:null,healthText:()=> 'Saved',query:(q,d)=>{queries.push({...q,job_id:d.id,attempt_id:d.attempt_id});return respond(q,d)}};
const AnalysisWorkspace={openContext:async (d,n)=>jumps.push(n)};
const escHtml=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const navigator={};
const sandbox=vm.createContext({document,window,navigator,STATE,RunHistory,AnalysisWorkspace,escHtml,Map,Set,JSON,Number,
 DETAIL_VIEW:'parsed',WORKSPACE_LAYOUT:'split',badge:s=>s||'Undecided',displayState:s=>s,
 AbortController,setTimeout,clearTimeout,console,getComputedStyle:()=>({flexDirection:'row'}),
 openModal(id){element(id).hidden=false},closeModal(id){element(id).hidden=true},setDetailView(v){sandbox.DETAIL_VIEW=v;api.display(v)}});
EXTRA_SCRIPTS
vm.runInContext(JOBS_SOURCE,sandbox);
vm.runInContext(SOURCE,sandbox);const api=vm.runInContext('FlowWorkspace',sandbox);
function detail(id='a',count=2){return {id,name:id,attempt_id:id,attempt_number:1,status:'RUNNING',total_lines:10,
 flow:{name:'flow',version:'1',parsers:[{name:'sample',outputs:{voltage:{label:'Voltage',unit:'V'}},count,latest:{voltage:1.3}}],
 analyzers:[],automations:[],diagnostics:[]}}}
function publication(seq,line,value=1.3){return {seq,producer:'sample',stage:'parser',kind:'record',data:{voltage:value},evidence:{line_no:line}}}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function click(data){handlers.click({target:{closest:()=>({dataset:data})}})}
function clickButton(button){handlers.click({target:{closest:()=>button}})}
""".replace(
        "EXTRA_SCRIPTS",
        (f"vm.runInContext({json.dumps(ICONS_SOURCE.read_text(encoding='utf-8'))},sandbox);\n"
         f"vm.runInContext({json.dumps(READING_SOURCE.read_text(encoding='utf-8'))},sandbox);") if with_reading else ""
    ).replace("JOBS_SOURCE", json.dumps(JOBS_SOURCE.read_text(encoding="utf-8"))).replace(
        "vm.runInContext(SOURCE,sandbox)", f"vm.runInContext({json.dumps(SOURCE.read_text(encoding='utf-8'))},sandbox)"
    )
    result = subprocess.run(
        [NODE, "-"],
        input=(harness + "\n(async()=>{\n" + checks +
               "\n})().catch(e=>{console.error(e);process.exitCode=1});").encode(),
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


def test_parser_record_is_one_row_and_uses_raw_evidence_not_publication_sequence():
    run(r"""
respond=async()=>({rows:[publication(900,7),publication(901,8)],has_more:false,next_seq:901});
api.refresh(detail());await settle();
element('flowHistoryMode').listeners.click();await settle();
assert.ok(element('flowParsedRows').innerHTML.includes('Voltage (V)'));
assert.equal((element('flowParsedRows').innerHTML.match(/data-flow-row=/g)||[]).length,2);
assert.ok(element('flowParsedRows').innerHTML.includes('data-flow-evidence="7"'));
await api.evidence(7);assert.deepEqual(jumps,[7]);
""")


def test_stale_result_cannot_replace_selected_job_outputs():
    run(r"""
const held=[];respond=(q,d)=>new Promise(resolve=>held.push({q,d,resolve}));
api.refresh(detail('old'));api.refresh(detail('new'));
held.find(x=>x.d.id==='new').resolve({rows:[publication(2,22,2.2)],has_more:false,next_seq:2});await settle();
held.find(x=>x.d.id==='old').resolve({rows:[publication(1,11,1.1)],has_more:false,next_seq:1});await settle();
assert.ok(element('flowParsedRows').innerHTML.includes('data-flow-evidence="22"'));
assert.ok(!element('flowParsedRows').innerHTML.includes('data-flow-evidence="11"'));
""")


def test_sparse_search_continues_from_scanned_sequence_without_offset_pages():
    run(r"""
element('flowParsedScroll').scrollHeight=200;
respond=async q=>Number(q.after)===0?{rows:[],has_more:true,next_seq:1000}:
 {rows:[publication(1001,9)],has_more:false,next_seq:1001};
api.refresh(detail());await new Promise(resolve=>setTimeout(resolve,15));
assert.equal(queries[1].after,1000);
assert.ok(element('flowParsedRows').innerHTML.includes('data-flow-evidence="9"'));
""")


def test_output_reader_has_no_advanced_condition_requests():
    run(r"""
api.refresh(detail());await settle();
assert.ok(queries.every(q=>!Object.hasOwn(q,'conditions')));
assert.equal(element('flowConditionField').listeners.change,undefined);
""")


def test_bounded_window_and_previous_edge_restore_after_eviction():
    run(r"""
respond=async q=>({rows:Array.from({length:100},(_,i)=>publication(Number(q.after||0)+i+1,i+1)),has_more:true,next_seq:Number(q.after||0)+100});
api.refresh(detail('a',1000));await settle();
element('flowHistoryMode').listeners.click();await settle();
for(let n=0;n<8;n++){element('flowParsedScroll').scrollTop=9900;element('flowParsedScroll').listeners.scroll();await settle();}
assert.ok((element('flowParsedRows').innerHTML.match(/data-flow-row=/g)||[]).length<=600);
element('flowParsedScroll').scrollTop=0;element('flowParsedScroll').listeners.scroll();await settle();
assert.ok(queries.at(-1).before>0);
""")


def test_layout_restores_scroll_and_analysis_outputs_stay_out_of_parsed():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'guard',inputs:['sample'],count:0,report_count:1,result:'FAIL',reason:'over limit',issues:[],events:[],event_count:3}];
api.refresh(d);await settle();element('flowParsedScroll').scrollTop=240;api.rememberLayout();
sandbox.WORKSPACE_LAYOUT='jobs';element('flowParsedScroll').scrollTop=0;api.rememberLayout();
sandbox.WORKSPACE_LAYOUT='split';api.layoutChanged();assert.equal(element('flowParsedScroll').scrollTop,240);
respond=async()=>({rows:[{seq:7,producer:'guard',kind:'report',data:{name:'voltage',result:'HIGH',reason:'over limit',values:{voltage_max:1.3}}}],has_more:false,next_seq:7});
api.selectAnalyzer('guard');api.refresh(d);await settle();assert.ok(element('flowAnalysisRows').innerHTML.includes('voltage_max'));
assert.ok(!element('flowParsedRows').innerHTML.includes('voltage_max'));
""")


def test_latest_is_queried_as_whole_output_or_explicit_field_projection():
    run(r"""
respond=async q=>({rows:[{...publication(2,8),data:{x:3},known_fields:['x','y']}],has_more:false,next_seq:2});
api.refresh(detail());await settle();element('flowLatestMode').listeners.click();await settle();
assert.equal(queries.at(-1).view,'flow_latest');assert.equal(queries.at(-1).mode,'record');
assert.ok(element('flowParsedRows').innerHTML.includes('data-reading-field="y"'));
assert.ok(element('flowParsedRows').innerHTML.includes('>—</td>'));
element('flowLatestPolicy').value='fields';element('flowLatestPolicy').listeners.change();await settle();
assert.equal(queries.at(-1).mode,'fields');assert.ok(element('flowStatus').textContent.includes('earlier outputs'));
""")


def test_returning_to_a_job_reloads_near_the_visible_publication_not_zero():
    run(r"""
respond=async q=>({rows:[publication(q.after?Number(q.after)+1:700,7)],has_more:false,next_seq:q.after?Number(q.after)+1:700});
api.refresh(detail('a'));await settle();
element('flowParsedScroll').getBoundingClientRect=()=>({top:0});
element('flowParsedRows').querySelectorAll=()=>[{dataset:{flowRow:'700'},getBoundingClientRect:()=>({top:30,bottom:54})}];
api.rememberLayout();api.refresh(detail('b'));await settle();api.refresh(detail('a'));await settle();
assert.equal(queries.at(-1).job_id,'a');assert.equal(queries.at(-1).after,699);
""")


def test_jobs_overview_does_not_scan_hidden_parser_history():
    run(r"""
sandbox.WORKSPACE_LAYOUT='jobs';api.refresh(detail());await settle();
assert.equal(queries.length,0);
sandbox.WORKSPACE_LAYOUT='split';api.layoutChanged();await settle();
assert.equal(queries.length,1);
""")


def test_empty_live_parser_history_reopens_when_first_outputs_arrive():
    run(r"""
respond=async()=>({rows:[],has_more:false,next_seq:0});
api.refresh(detail('a',0));await settle();
respond=async()=>({rows:[publication(1,1)],has_more:false,next_seq:1});
api.refresh(detail('a',1));await settle();
assert.equal(queries.length,2);
assert.ok(element('flowParsedRows').innerHTML.includes('data-flow-row="1"'));
""")


def test_count_change_during_empty_request_is_not_lost_at_response():
    run(r"""
let resolve;respond=()=>new Promise(done=>resolve=done);
api.refresh(detail('a',0));api.refresh(detail('a',1));
resolve({rows:[],has_more:false,next_seq:0});await settle();
respond=async()=>({rows:[publication(1,1)],has_more:false,next_seq:1});
api.refresh(detail('a',1));await settle();
assert.equal(queries.length,2);
assert.ok(element('flowParsedRows').innerHTML.includes('data-flow-row="1"'));
""")


def test_automation_latest_includes_never_acted_and_uses_literal_receipt_status():
    run(r"""
const d=detail();d.flow.automations=[
 {name:'stop_failed',inputs:['guard'],enabled:true,processed:8,action_count:1,publication_count:2,
  last:{seq:123,data:{version:1,request_id:122,action:'stop',phase:'returned',status:'rejected',receipt:{accepted:true}}}},
 {name:'disabled',inputs:['guard'],enabled:false,processed:0,action_count:0},
 {name:'faulted',inputs:['guard'],enabled:true,processed:2,action_count:0,error:{message:'callback failed'}}
];
sandbox.DETAIL_VIEW='automation';api.refresh(d);await settle();
assert.equal(element('flowAutomationScroll').hidden,false);
assert.equal(element('flowAnalysisScroll').hidden,true);
assert.equal(element('flowDetailsScroll').hidden,true);
const html=element('flowAutomationRows').innerHTML;
assert.ok(html.includes('data-reading-fixed="automation"'));assert.ok(html.includes('data-reading-field="inputs"'));
assert.ok(html.includes('Disabled'));assert.ok(html.includes('callback failed'));
assert.ok(html.includes('>stop</button>'));assert.ok(html.includes('rejected'));
assert.ok(!html.includes('Once'));assert.ok(!html.includes('Callback invoked'));
assert.equal((html.match(/No operation/g)||[]).length,2);
assert.ok(html.includes('data-flow-full="123"'));
assert.equal(element('flowOutputActions').parentElement.id,'flowAutomationTools');
assert.equal(element('flowLatestPolicy').disabled,true);
assert.equal(queries.length,0,'Latest uses attached Automation snapshot without running callbacks');
""")


def test_automation_specification_and_saved_action_are_inspection_only():
    run(r"""
const d=detail();d.flow.automations=[{name:'stop_failed',inputs:['guard'],enabled:true,on_report:true,on_job_event:false,
 processed:9,action_count:1,publication_count:2,code:'class StopFailed(Automation): pass',last:{seq:321,data:{action:'stop',status:'accepted'}}}];
sandbox.DETAIL_VIEW='automation';api.refresh(d);
click({flowSpec:'automation:stop_failed'});
assert.equal(element('flowSpecTitle').textContent,'stop_failed');
assert.ok(element('flowSpecFacts').innerHTML.includes('Processed callbacks'));
assert.ok(element('flowSpecFacts').innerHTML.includes('Control operations'));
assert.ok(!element('flowSpecFacts').innerHTML.includes('Once'));
assert.ok(element('flowSpecCode').textContent.includes('class StopFailed'));
respond=async()=>({text:'saved receipt',total:13,next_offset:13});
click({flowFull:'321'});await settle();
assert.equal(queries.at(-1).view,'flow_text');assert.equal(queries.at(-1).seq,321);
assert.equal(queries.at(-1).job_id,'a');assert.equal(queries.at(-1).attempt_id,'a');
assert.equal(element('flowSpecCode').textContent,'saved receipt');
""")


def test_automation_scroll_is_preserved_and_no_flow_does_not_retain_old_operations():
    run(r"""
sandbox.DETAIL_VIEW='automation';api.refresh(detail('a'));await settle();
element('flowAutomationScroll').scrollTop=240;api.rememberLayout();
sandbox.WORKSPACE_LAYOUT='jobs';element('flowAutomationScroll').scrollTop=0;
sandbox.WORKSPACE_LAYOUT='split';api.layoutChanged();
assert.equal(element('flowAutomationScroll').scrollTop,240);
api.refresh(detail('b'));api.refresh(detail('a'));
assert.equal(element('flowAutomationScroll').scrollTop,240);
const plain=detail('plain');delete plain.flow;api.refresh(plain);
assert.ok(element('flowAutomationRows').innerHTML.includes('No Flow attached. No Automation definitions.'));
assert.equal(element('flowAutomationScroll').hidden,false);
""")


def test_report_result_is_literal_text_and_name_filter_is_explicit():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'limits',report_count:2,result:'FAIL'}];
sandbox.DETAIL_VIEW='analysis';sandbox.badge=()=>{throw Error('Report text is not an enum badge')};
respond=async q=>q.view==='flow_report_names'?{rows:['upper','lower']}:
 {rows:[{seq:8,producer:'limits',kind:'report',data:{version:1,name:'upper',result:'RETRYING',reason:'user text',values:{x:12,flag:false,empty:null}},evidence:{line_no:120}}],has_more:false,next_seq:8};
api.refresh(d);await settle();
const html=element('flowAnalysisRows').innerHTML;
assert.ok(html.includes('RETRYING'));assert.ok(html.includes('user text'));
assert.ok(html.includes('>false</td>'));assert.ok(html.includes('>null</td>'));
assert.ok(!html.includes('Current notices'));assert.ok(!html.includes('Occurrences'));
assert.ok(html.includes('data-flow-evidence="120"'));
element('flowReportSelect').value='name:upper';element('flowReportSelect').listeners.change();await settle();
assert.equal(queries.filter(q=>q.view==='flow_latest').at(-1).report_name,'upper');
element('flowHistoryMode').listeners.click();await settle();
const q=queries.filter(q=>q.view==='flow').at(-1);
assert.equal(q.kind,'report');assert.equal(q.report_name,'upper');assert.equal(q.stage,'analyzer');
""")


def test_latest_projection_updates_do_not_duplicate_a_report_group():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'limits',report_count:1}];sandbox.DETAIL_VIEW='analysis';
let revision=1;respond=async q=>q.view==='flow_report_names'?{rows:[]}:
 {rows:[{seq:revision,producer:'limits',kind:'report',report_name:'same',data:{version:1,name:'same',result:'R'+revision,reason:'',values:{}}}],has_more:true,next_seq:revision};
api.refresh(d);await settle();
revision=2;element('flowAnalysisScroll').scrollTop=9900;element('flowAnalysisScroll').listeners.scroll();await settle();
const html=element('flowAnalysisRows').innerHTML;
assert.equal((html.match(/data-flow-row=/g)||[]).length,1);assert.ok(html.includes('R2'));assert.ok(!html.includes('>R1<'));
""")


def test_merged_full_output_uses_projection_chunk_reader():
    run(r"""
api.refresh(detail());await settle();
respond=async()=>({text:'{"data":{"x":3,"y":2},"field_sources":{"x":9,"y":8}}',next_offset:60,total:60});
click({flowFull:'9',flowProjection:'fields'});await settle();
assert.equal(queries.at(-1).view,'flow_latest_text');assert.equal(queries.at(-1).mode,'fields');
assert.ok(element('flowSpecCode').textContent.includes('field_sources'));
""")


def test_unknown_report_format_is_shown_as_unsupported_not_interpreted():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'future',report_count:1}];sandbox.DETAIL_VIEW='analysis';
respond=async q=>q.view==='flow_report_names'?{rows:[]}:
 {rows:[{seq:1,producer:'future',kind:'report',data:{version:99,name:'item',result:'PASS',values:{}}}],has_more:false};
api.refresh(d);await settle();assert.ok(element('flowAnalysisRows').innerHTML.includes('Unsupported Report format'));
assert.ok(!element('flowAnalysisRows').innerHTML.includes('>PASS<'));
""")


def test_latest_omitted_prototype_named_field_is_missing_not_a_javascript_property():
    run(r"""
respond=async()=>({rows:[{...publication(2,8),data:{x:3},known_fields:['x','constructor','toString']}],has_more:false,next_seq:2});
api.refresh(detail());await settle();element('flowLatestMode').listeners.click();await settle();
const html=element('flowParsedRows').innerHTML;
assert.ok(html.includes('data-reading-field="constructor"'));assert.ok(!html.includes('function Object'));
assert.ok(!html.includes('function toString'));assert.equal((html.match(/>—<\/td>/g)||[]).length,3);
""")


def test_returning_to_analysis_latest_restores_its_reader_position():
    run(r"""
const a=detail('a');a.flow.analyzers=[{name:'limits',report_count:1}];
const b=detail('b');b.flow.analyzers=[{name:'limits',report_count:1}];sandbox.DETAIL_VIEW='analysis';
respond=async q=>q.view==='flow_report_names'?{rows:[]}:
 {rows:[{seq:7,producer:'limits',kind:'report',data:{version:1,name:'item',result:'READY',reason:'',values:{}}}],has_more:false,next_seq:7};
api.refresh(a);await settle();element('flowAnalysisScroll').scrollTop=240;api.rememberLayout();
api.refresh(b);await settle();element('flowAnalysisScroll').scrollTop=0;
api.refresh(a);await settle();assert.equal(element('flowAnalysisScroll').scrollTop,240);
""")


def test_background_poll_does_not_overwrite_uncommitted_report_name_input():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'a',report_count:1}];sandbox.DETAIL_VIEW='analysis';
respond=async()=>({rows:[],has_more:false});api.refresh(d);await settle();
element('flowReportName').value='typing-report-name';api.refresh(d);await settle();
assert.equal(element('flowReportName').value,'typing-report-name');
element('flowReportName').listeners.keydown({key:'Enter'});await settle();
assert.equal(queries.filter(q=>q.view==='flow_latest').at(-1).report_name,'typing-report-name');
""")


def test_full_output_late_response_cannot_replace_newer_inspection():
    run(r"""
api.refresh(detail());await settle();const held=[];
respond=q=>new Promise(resolve=>held.push({q,resolve}));
click({flowFull:'1'});click({flowFull:'2'});
held[1].resolve({text:'second',total:6,next_offset:6});await settle();
held[0].resolve({text:'first',total:5,next_offset:5});await settle();
assert.equal(element('flowSpecTitle').textContent,'Saved publication 2');
assert.equal(element('flowSpecCode').textContent,'second');
""")


def test_leaving_parsed_rejects_pending_response_from_hidden_reader():
    run(r"""
let done;respond=()=>new Promise(resolve=>done=resolve);api.refresh(detail());
sandbox.DETAIL_VIEW='details';api.display('details');element('flowStatus').textContent='Current details';
done({rows:[publication(1,1)],has_more:false,next_seq:1});await settle();
assert.equal(element('flowStatus').textContent,'Current details');
""")


def test_automation_history_uses_request_identity_pending_rows_and_terminal_refresh():
    run(r"""
const d=detail();d.flow.automations=[{name:'guard',enabled:true,publication_count:1}];
sandbox.DETAIL_VIEW='automation';api.refresh(d);let terminal=false;
respond=async q=>({rows:[{request_id:11,seq:terminal?22:11,producer:'guard',stage:'automation',kind:'action',
 data:{version:1,request_id:11,action:'stop',phase:terminal?'returned':'requested',status:terminal?'accepted':'requested',receipt:null}}],has_more:false,next_seq:11});
element('flowHistoryMode').listeners.click();await settle();
assert.equal(queries.at(-1).view,'automation_history');
assert.match(element('flowAutomationRows').innerHTML.replace(/<[^>]+>/g,' '),/\brequested\b/);
terminal=true;d.flow.automations[0].publication_count=2;api.refresh(d);await settle();
const html=element('flowAutomationRows').innerHTML;
assert.equal((html.match(/data-flow-position="11"/g)||[]).length,1);
assert.match(html.replace(/<[^>]+>/g,' '),/\baccepted\b/);assert.ok(html.includes('data-flow-full="22"'));
assert.doesNotMatch(html.replace(/<[^>]+>/g,' '),/\brequested\b/);
assert.ok(html.includes('data-reading-field="source"')&&html.includes('data-reading-field="reason"'));
""")


def test_automation_history_cursor_is_request_id_not_phase_publication_sequence():
    run(r"""
const d=detail();d.flow.automations=[{name:'guard',enabled:true,publication_count:10}];sandbox.DETAIL_VIEW='automation';api.refresh(d);
respond=async q=>({rows:[{request_id:Number(q.after||0)+1,seq:900+Number(q.after||0),producer:'guard',data:{version:1,action:'stop',phase:'returned',status:'accepted'}}],has_more:true,next_seq:Number(q.after||0)+1});
element('flowHistoryMode').listeners.click();await settle();
element('flowAutomationScroll').scrollTop=9900;element('flowAutomationScroll').listeners.scroll();await settle();
assert.equal(queries.at(-1).after,1);assert.ok(element('flowAutomationRows').innerHTML.includes('data-flow-full="900"'));
""")


def test_automation_source_opens_publication_with_actual_evidence_and_native_event_name():
    run(r"""
const d=detail();d.flow.automations=[{name:'guard',enabled:true,last:{seq:8,data:{version:1,request_id:7,action:'rerun',status:'accepted',phase:'returned',source:{seq:5,producer:'job',stage:'job',kind:'event',name:'job.finished'}}}}];
sandbox.DETAIL_VIEW='automation';api.refresh(d);
const html=element('flowAutomationRows').innerHTML;assert.ok(html.includes('job.finished'));
assert.ok(html.includes('data-flow-publication="5"'));assert.ok(!html.includes('data-flow-evidence='));
respond=async q=>q.view==='flow'?{rows:[{seq:5,producer:'job',stage:'job',kind:'event',data:{name:'job.finished'},evidence:null}]}:{text:'{"name":"job.finished"}',total:23,next_offset:23};
click({flowPublication:'5'});await settle();await settle();
assert.equal(queries.at(-2).view,'flow');assert.equal(queries.at(-2).after,4);assert.equal(queries.at(-1).view,'flow_text');
assert.ok(element('flowSpecCode').textContent.includes('job.finished'));
assert.ok(element('flowSpecFacts').innerHTML.includes('Producer'));
assert.ok(!element('flowSpecFacts').innerHTML.includes('data-flow-evidence='));
""")


def test_automation_history_stale_response_cannot_replace_new_job_and_window_bounded():
    run(r"""
const a=detail('a'),b=detail('b');a.flow.automations=b.flow.automations=[{name:'guard',enabled:true,publication_count:1000}];
sandbox.DETAIL_VIEW='automation';api.refresh(a);const held=[];respond=(q,d)=>new Promise(resolve=>held.push({q,d,resolve}));
element('flowHistoryMode').listeners.click();api.refresh(b);element('flowHistoryMode').listeners.click();
held.find(h=>h.d.id==='b').resolve({rows:[{request_id:1,seq:2,producer:'guard',data:{version:1,action:'NEW',status:'accepted'}}],has_more:true,next_seq:1});await settle();
held.find(h=>h.d.id==='a').resolve({rows:[{request_id:1,seq:2,producer:'guard',data:{version:1,action:'OLD',status:'accepted'}}],has_more:false,next_seq:1});await settle();
assert.ok(element('flowAutomationRows').innerHTML.includes('NEW'));assert.ok(!element('flowAutomationRows').innerHTML.includes('OLD'));
respond=async q=>({rows:Array.from({length:100},(_,i)=>({request_id:Number(q.after||0)+i+1,seq:10000+Number(q.after||0)+i,producer:'guard',data:{version:1,action:'stop',status:'accepted'}})),has_more:true,next_seq:Number(q.after||0)+100});
for(let i=0;i<8;i++){element('flowAutomationScroll').scrollTop=9900;element('flowAutomationScroll').listeners.scroll();await settle();}
assert.ok((element('flowAutomationRows').innerHTML.match(/data-flow-row=/g)||[]).length<=600);
element('flowAutomationScroll').scrollTop=0;element('flowAutomationScroll').listeners.scroll();await settle();assert.ok(queries.at(-1).before>0);
""")


def test_automation_source_real_evidence_opens_raw_context_and_filter_is_explicit():
    run(r"""
const d=detail();d.flow.automations=[{name:'guard',enabled:true,publication_count:2}];sandbox.DETAIL_VIEW='automation';api.refresh(d);
respond=async q=>q.view==='flow'?{rows:[{seq:5,producer:'parser',stage:'parser',kind:'record',evidence:{line_no:42},data:{x:1}}]}:{text:'{"x":1}',total:7,next_offset:7};
click({flowPublication:'5'});await settle();await settle();
assert.ok(element('flowSpecFacts').innerHTML.includes('data-flow-evidence="42"'));
click({flowEvidence:'42'});await settle();assert.deepEqual(jumps,[42]);
element('flowAutomation').value='guard';element('flowAutomation').listeners.change();
respond=async()=>({rows:[],has_more:false,next_seq:0});element('flowHistoryMode').listeners.click();await settle();
assert.equal(queries.at(-1).view,'automation_history');assert.equal(queries.at(-1).producer,'guard');
assert.equal(queries.at(-1).mode,undefined);
""")


def test_late_pending_refresh_cannot_replace_new_job_automation_snapshot():
    run(r"""
const a=detail('a');a.flow.automations=[{name:'guard',enabled:true,publication_count:1}];sandbox.DETAIL_VIEW='automation';api.refresh(a);
respond=async()=>({rows:[{seq:1,request_id:1,producer:'guard',data:{version:1,action:'stop',phase:'requested',status:'requested'}}],has_more:false,next_seq:1});
element('flowHistoryMode').listeners.click();await settle();
let finish;respond=()=>new Promise(resolve=>finish=resolve);a.flow.automations[0].publication_count=2;api.refresh(a);
const b=detail('b');b.flow.automations=[{name:'NEW JOB',enabled:true}];api.refresh(b);
finish({rows:[{seq:2,request_id:1,producer:'guard',data:{version:1,action:'OLD TERMINAL',phase:'returned',status:'accepted'}}],has_more:false,next_seq:1});await settle();
assert.ok(element('flowAutomationRows').innerHTML.includes('NEW JOB'));
assert.ok(!element('flowAutomationRows').innerHTML.includes('OLD TERMINAL'));
""")


def test_parser_and_analyzer_default_all_specs_include_never_emitted_definitions():
    run(r"""
const d=detail('a',0);d.flow.parsers.push({name:'quiet_parser',count:0,inputs:[],code:'def quiet_parser(): pass'});
d.flow.analyzers=[{name:'quiet_analyzer',report_count:0,inputs:['quiet_parser'],code:'class QuietAnalyzer: pass'}];
api.refresh(d);await settle();assert.equal(element('flowParser').value,'');
assert.equal(queries.find(q=>q.view==='flow_latest').producer,undefined);
element('flowHistoryMode').listeners.click();await settle();
assert.equal(queries.find(q=>q.view==='flow').producer,undefined);
click({flowSpec:'parser'});assert.equal(element('flowSpecTitle').textContent,'All Parsers');
assert.ok(element('flowSpecCode').textContent.includes('def quiet_parser'));
assert.ok(!element('flowParsedRows').innerHTML.includes('quiet_parser'));
sandbox.DETAIL_VIEW='analysis';api.refresh(d);await settle();assert.equal(element('flowAnalyzer').value,'');
assert.equal(element('flowAnalyzerSpec').disabled,false);
click({flowSpec:'analyzer'});assert.equal(element('flowSpecTitle').textContent,'All Analyzers');
assert.ok(element('flowSpecCode').textContent.includes('class QuietAnalyzer'));
assert.ok(!element('flowAnalysisRows').innerHTML.includes('quiet_analyzer'));
""")


def test_shared_mode_controls_change_active_page_and_keep_modes_independent():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'limits',report_count:1}];d.flow.automations=[{name:'guard',enabled:true}];
api.refresh(d);await settle();
assert.equal(element('flowLatestMode').attributes['aria-pressed'],'true');
assert.equal(element('flowHistoryMode').attributes['aria-pressed'],'false');
assert.equal(element('flowLatestPolicy').hidden,false);
sandbox.DETAIL_VIEW='analysis';api.display('analysis');
element('flowHistoryMode').listeners.click();await settle();assert.equal(queries.filter(q=>q.view==='flow').at(-1).stage,'analyzer');
sandbox.DETAIL_VIEW='parsed';api.refresh(d);await settle();assert.equal(element('flowLatestMode').attributes['aria-pressed'],'true');
element('flowHistoryMode').listeners.click();await settle();assert.equal(queries.filter(q=>q.view==='flow').at(-1).stage,'parser');
sandbox.DETAIL_VIEW='analysis';api.refresh(d);await settle();assert.equal(element('flowHistoryMode').attributes['aria-pressed'],'true');
sandbox.DETAIL_VIEW='automation';api.refresh(d);await settle();assert.equal(element('flowLatestMode').attributes['aria-pressed'],'true');
sandbox.DETAIL_VIEW='parsed';api.refresh(d);await settle();assert.equal(element('flowHistoryMode').attributes['aria-pressed'],'true');
element('flowLatestMode').listeners.click();await settle();assert.equal(queries.at(-1).view,'flow_latest');
assert.equal(element('flowLatestPolicy').hidden,false);
""")


def test_native_report_selector_catalog_all_exact_name_and_draft_are_preserved():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'limits',report_count:1}];sandbox.DETAIL_VIEW='analysis';
respond=async q=>q.view==='flow_report_names'?{rows:['alpha','beta'],has_more:true}:{rows:[],has_more:false};
api.refresh(d);await settle();assert.ok(element('flowReportSelect').innerHTML.includes('value="name:alpha"'));
element('flowReportSelect').value='find';element('flowReportSelect').listeners.change();
assert.equal(element('flowReportOverlay').hidden,false);element('flowReportName').value='typed-outside-first-100';
api.refresh(d);await settle();assert.equal(element('flowReportName').value,'typed-outside-first-100');
element('flowReportApply').listeners.click();await settle();
assert.equal(queries.filter(q=>q.view==='flow_latest').at(-1).report_name,'typed-outside-first-100');
assert.equal(element('flowReportSelect').value,'name:typed-outside-first-100');
assert.equal(element('flowReportOverlay').hidden,true);
element('flowReportSelect').value='';element('flowReportSelect').listeners.change();await settle();assert.equal(element('flowReportSelect').value,'');
assert.equal(queries.filter(q=>q.view==='flow_latest').at(-1).report_name,undefined);
""")


def test_history_follow_is_per_page_jumps_to_tail_and_pauses_on_upward_input():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'limits',report_count:1}];d.flow.automations=[{name:'guard',enabled:true,publication_count:1}];
respond=async q=>({rows:[publication(q.before?100:1,q.before?100:1)],has_more:false,next_seq:q.before?100:1});
api.refresh(d);await settle();element('flowHistoryMode').listeners.click();await settle();
assert.equal(element('flowFollow').checked,false);
element('flowFollow').checked=true;element('flowFollow').listeners.change();await settle();
assert.equal(queries.at(-1).before,Number.MAX_SAFE_INTEGER);assert.equal(element('flowParsedScroll').scrollTop,10000);
sandbox.DETAIL_VIEW='analysis';api.refresh(d);await settle();element('flowHistoryMode').listeners.click();await settle();
assert.equal(element('flowFollow').checked,false);
sandbox.DETAIL_VIEW='automation';api.refresh(d);await settle();element('flowHistoryMode').listeners.click();await settle();assert.equal(element('flowFollow').checked,false);
sandbox.DETAIL_VIEW='parsed';api.refresh(d);await settle();assert.equal(element('flowFollow').checked,true);
element('flowParsedScroll').listeners.wheel({deltaY:-1});assert.equal(element('flowFollow').checked,false);
d.flow.parsers[0].count++;api.refresh(d);await settle();assert.ok(queries.at(-1).before!==Number.MAX_SAFE_INTEGER||queries.at(-1).stage!=='parser');
""")


def test_fixed_column_resize_is_available_in_analysis_and_automation():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'a',report_count:1}];
d.flow.automations=[{name:'control',enabled:true}];sandbox.DETAIL_VIEW='analysis';
respond=async q=>({rows:[{seq:1,producer:'a',kind:'report',data:{version:1,name:'r',result:'CUSTOM',reason:'literal',values:{result:3}}}],has_more:false,next_seq:1});
api.refresh(d);await settle();
const analysis=element('flowAnalysisRows').innerHTML;
assert.ok(analysis.includes('data-reading-fixed="reason"'));
assert.ok(analysis.includes('aria-label="Resize Reason"'));
assert.ok(analysis.includes('data-reading-field="result"'),'user data and fixed Result keep separate identities');
sandbox.DETAIL_VIEW='automation';api.refresh(d);
const auto=element('flowAutomationRows').innerHTML;
assert.ok(auto.includes('data-reading-fixed="automation"'));
for(const key of ['inputs','state','operation','receipt'])assert.ok(auto.includes('data-reading-field="'+key+'"'));
assert.ok(!auto.includes('draggable="true"'),'pointer drag replaces HTML5 draggable');
assert.equal(element('flowFollowLabel').hidden,true);
assert.equal(element('flowFollow').disabled,true);
""")


def test_report_name_find_is_data_and_not_the_search_action():
    run(r"""
const d=detail();d.flow.analyzers=[{name:'a',report_count:1}];sandbox.DETAIL_VIEW='analysis';
respond=async q=>q.view==='flow_report_names'?{rows:['find','name:x']}:{rows:[],has_more:false};
api.refresh(d);await settle();
assert.ok(element('flowReportSelect').innerHTML.includes('value="name:find"'));
element('flowReportSelect').value='name:find';element('flowReportSelect').listeners.change();await settle();
assert.equal(queries.filter(q=>q.view==='flow_latest').at(-1).report_name,'find');
assert.equal(element('flowReportOverlay').hidden,true);
""")


@pytest.mark.parametrize("page, producer", [("parsed", "Parser"), ("analysis", "Analyzer"), ("automation", "Automation")])
def test_plain_job_reader_has_explicit_empty_state_without_archive_requests(page, producer):
    run(r"""
sandbox.DETAIL_VIEW=PAGE;const plain=detail('plain');delete plain.flow;api.refresh(plain);api.layoutChanged();await settle();
assert.equal(queries.length,0);assert.ok(element('flow'+PAGE[0].toUpperCase()+PAGE.slice(1)+'Rows').innerHTML.includes('No '+PRODUCER+' definitions.'));
assert.equal(element('flowWorkspaceView').hidden,false);assert.equal(element('flowStatus').textContent.includes('No Flow attached'),true);
""".replace("PAGE", json.dumps(page)).replace("PRODUCER", json.dumps(producer)))


def test_plain_job_details_retains_execution_settings_diagnostics_and_priority_widget():
    run(r"""
sandbox.DETAIL_VIEW='details';const plain={...detail('plain'),cmd:['python','<unsafe>'],env:{EXACT:'9007199254740993'},stop_policy:'command',stop_cmd:'quit',errors:[{message:'failed'}],priority:9};delete plain.flow;
api.refresh(plain);assert.ok(element('flowExecutionFacts').innerHTML.includes('&lt;unsafe>'));
assert.ok(element('flowDefinitionFacts').innerHTML.includes('No Flow attached'));
assert.equal(element('priorityEditor').parentElement.id,'flowPrioritySlot');
click({flowSpec:'execution'});assert.ok(element('flowSpecCode').textContent.includes('9007199254740993'));
assert.ok(element('flowSpecCode').textContent.includes('quit'));
api.diagnostics();assert.ok(element('flowSpecCode').textContent.includes('failed'));
assert.equal(queries.length,0);
""")


def test_external_execution_details_show_exact_path_and_source_facts():
    run(r"""
sandbox.DETAIL_VIEW='details';
sandbox.displayState=(status,kind)=>kind==='tail'?'WATCHING':status;
const path='D:/'+('long-name/'.repeat(30))+'<file>.log';
const source={...detail(),source_kind:'tail',origin_label:'External (tail)',
  completion_reason:'user_stopped',encoding:'utf-8',source_descriptor:{path,end_offset:0,generation:2}};
api.refresh(source);
const facts=element('flowExecutionFacts').innerHTML;
for(const label of ['File','Origin','Run','State','Completion','Source file','Offset','Generation','Elapsed','Encoding','Log save']){
 assert.ok(facts.includes('>'+label+'</th>'), label);
}
assert.ok(facts.includes(escHtml(path)));
assert.match(facts,/Offset<\/th><td[^>]*class="flow-fact-number"[^>]*>0/);
assert.match(facts,/WATCHING.*Stopped by user · incomplete/);
assert.doesNotMatch(facts,/Working directory|Priority|Resources|PTY|Retries|Stop policy|data-flow-spec="execution"/);
""")


@pytest.mark.parametrize("clipboard_available", [True, False])
def test_copy_source_path_keeps_focus_and_manual_selection_across_refresh(clipboard_available):
    run(r"""
sandbox.DETAIL_VIEW='details';
const path='D:\\Logs\\large & "quoted" <archive>.log';
const source={...detail(),source_kind:'tail',elapsed_str:'2s',source_descriptor:{path,end_offset:17}};
const copied=[];
if (CLIPBOARD_AVAILABLE) navigator.clipboard={writeText:async path=>{
  copied.push(path);
  if(copied.length===2)throw new Error('Permission denied');
}};
api.refresh(source);
assert.match(executionFacts.innerHTML,/Copy Path/);
const button=element('flowCopyPath');
button.focus();
if (CLIPBOARD_AVAILABLE) {
  clickButton(button);await settle();
  assert.deepEqual(copied,[path]);
  api.refresh({...source,elapsed_str:'3s'});
  assert.equal(document.activeElement,button);
  assert.match(element('flowCopyStatus').textContent,/copied/i);
}
clickButton(button);await settle();
const field=element('flowCopyField');
assert.equal(element('flowCopyFallback').hidden,false);
assert.match(executionFacts.innerHTML,/Ctrl\+C/);
assert.ok(field.readOnly);
assert.equal(document.activeElement,field);
assert.equal(field.value,path);
assert.equal(field.selectionStart,0);
assert.equal(field.selectionEnd,path.length);

api.refresh({...source,elapsed_str:'4s',source_descriptor:{path,end_offset:29}});
assert.equal(document.activeElement,field);
assert.equal(field.selectionEnd,path.length);
assert.ok(executionValues.some(cell=>cell.textContent==='29'));
assert.ok(executionValues.some(cell=>cell.textContent==='4s'));

api.refresh({...source,source_descriptor:{path:'different.log',end_offset:0}});
assert.equal(element('flowCopyFallback').hidden,true);
assert.notEqual(element('flowCopyField'),field);
""".replace("CLIPBOARD_AVAILABLE", json.dumps(clipboard_available)))


def test_late_copy_failure_cannot_open_fallback_for_another_source():
    run(r"""
sandbox.DETAIL_VIEW='details';
let fail;
navigator.clipboard={writeText:()=>new Promise((resolve,reject)=>{fail=reject})};
const source={...detail(),source_kind:'tail',source_descriptor:{path:'shared.log'}};
api.refresh(source);
assert.match(executionFacts.innerHTML,/Copy Path/);
clickButton(element('flowCopyPath'));
api.refresh({...source,id:'different-job'});
fail(new Error('Permission denied'));await settle();
assert.equal(element('flowCopyFallback').hidden,true);
assert.notEqual(document.activeElement,element('flowCopyField'));
""")


def test_execution_preview_bounds_large_structures_without_losing_saved_settings():
    run(r"""
sandbox.DETAIL_VIEW='details';const job={...detail(),resources:Object.fromEntries(Array.from({length:10000},(_,i)=>['r'+i,1])),env:{LONG:'value'.repeat(20000)}};
api.refresh(job);assert.ok(element('flowExecutionFacts').innerHTML.length<20000);
assert.ok(!element('flowExecutionFacts').innerHTML.includes('r9999'));
click({flowSpec:'execution'});assert.equal(JSON.parse(element('flowSpecCode').textContent).env.LONG.length,100000);
""")


def test_execution_details_reading_position_survives_switching_and_attempt_identity():
    run(r"""
sandbox.DETAIL_VIEW='details';api.refresh(detail('a'));element('flowDetailsScroll').scrollTop=230;api.rememberLayout();
api.refresh(detail('b'));assert.equal(element('flowDetailsScroll').scrollTop,0);
api.refresh(detail('a'));assert.equal(element('flowDetailsScroll').scrollTop,230);
api.refresh(detail('a'));assert.equal(element('flowDetailsScroll').scrollTop,230);
""")


def test_archive_follow_preserves_flow_sequence_across_pause_and_finish():
    run(r"""
RunHistory.selection={run_id:'tail',follow:'following'};
let end=1;
respond=async q=>{
  const start=Number(q.after||0);
  return {rows:Array.from({length:end-start},(_,i)=>publication(start+i+1,start+i+1)),
          has_more:false,next_seq:end};
};
api.refresh(detail('a',1));await settle();
element('flowHistoryMode').listeners.click();await settle();
const baseQueries=queries.length;
RunHistory.selection.follow='paused';end=3;
api.refresh(detail('a',3));await settle();
assert.equal(queries.length,baseQueries);
RunHistory.selection.follow='following';
api.refresh(detail('a',3));await settle();
assert.equal(queries.at(-1).after,1);
RunHistory.selection.follow='finished';RunHistory.selection.catchUp=true;end=4;
api.refresh(detail('a',4));await settle();
assert.equal(queries.at(-1).after,3);
api.refresh(detail('a',4));await settle();
assert.equal(queries.length,baseQueries+2);
assert.deepEqual(Array.from(element('flowParsedRows').innerHTML.matchAll(/data-flow-row="(\d+)"/g),m=>m[1]),['1','2','3','4']);
""")


def test_latest_mode_updates_even_when_scrolled_away_from_bottom_and_charts_layout_skips_poll():
    run(r"""
let rev=1;
respond=async()=>({rows:[publication(rev,rev,rev*10)],has_more:false,next_seq:rev});
api.refresh(detail('a',1));await settle();
element('flowLatestMode').listeners.click();await settle();
assert.ok(element('flowParsedRows').innerHTML.includes('>10</td>'));
// Scrolled to top (scrollTop=0, scrollHeight=10000, clientHeight=300 -> far from bottom)
element('flowParsedScroll').scrollTop=0;
rev=2;const d2=detail('a',2);
api.refresh(d2);await settle();
assert.ok(element('flowParsedRows').innerHTML.includes('>20</td>'),'Latest mode updates regardless of scroll position');
const countBefore=queries.length;
sandbox.WORKSPACE_LAYOUT='charts';
rev=3;api.refresh(detail('a',3));await settle();
assert.equal(queries.length,countBefore,'charts layout stops background polling');
""")


def test_inline_segmented_policy_control_and_over_24_columns_with_context_menu():
    run(r"""
const fields=Object.fromEntries(Array.from({length:30},(_,i)=>['col_'+i,i]));
const d=detail('wide',1);d.flow.parsers=[{name:'sample',outputs:fields,count:1,latest:fields}];
respond=async()=>({rows:[{seq:1,producer:'sample',stage:'parser',kind:'record',data:fields,known_fields:Object.keys(fields),evidence:{line_no:5}}],has_more:false,next_seq:1});
api.refresh(d);await settle();
// Parsed starts in Latest mode -> segmented control [Record | Merged] visible and Follow hidden
assert.equal(element('flowReadingSettings').hidden,true);
assert.equal(element('flowLatestPolicyGroup').hidden,false);
assert.equal(element('flowFollowLabel').hidden,true);
// Switch to History mode -> Latest policy segmented control hides and Follow appears
element('flowHistoryMode').listeners.click();await settle();
assert.equal(element('flowLatestPolicyGroup').hidden,true);
assert.equal(element('flowFollowLabel').hidden,false);
// Switch back to Latest mode -> segmented control [Record | Merged] appears and Follow hides
element('flowLatestMode').listeners.click();await settle();
assert.equal(element('flowLatestPolicyGroup').hidden,false);
assert.equal(element('flowFollowLabel').hidden,true);
api.changePolicy('fields');await settle();
assert.equal(queries.at(-1).mode,'fields');
assert.equal(element('flowPolicyFields').attributes['aria-pressed'],'true');
assert.equal(element('flowPolicyRecord').attributes['aria-pressed'],'false');
// 30 fields discovered: 12 default visible, all 30 selectable in column menu, col-filler present, no draggable
const html=element('flowParsedRows').innerHTML;
assert.equal((html.match(/data-reading-field="/g)||[]).length,12);
assert.ok(html.includes('<th class="col-filler"'));
assert.ok(!html.includes('draggable="true"'));
assert.ok(!html.includes('reading-column-move'));
const menu=api.openColumnMenu({clientX:100,clientY:100,preventDefault(){},stopPropagation(){}},{dataset:{readingField:'col_0'},closest:()=>null},null);
assert.ok(menu.innerHTML.includes('data-menu-column="col_29"'));
assert.ok(menu.innerHTML.includes('data-menu-filter'));
// Toggle col_29 visible -> 13 visible columns
const cb={dataset:{menuColumn:'col_29'},checked:true,disabled:false,closest:s=>s==='[data-menu-column]'?cb:null};
menu.listeners.change({target:cb});
assert.ok(element('flowParsedRows').innerHTML.includes('data-reading-field="col_29"'));
""", with_reading=True)


def test_analysis_badges_row_selection_log_icon_and_details_facts_with_completion():
    run(r"""
const d=detail('proc',1);
d.completion_reason='user_stopped';d.returncode=143;d.cmd=['python','run.py'];d.cwd='D:/work';
d.flow.analyzers=[{name:'guard',report_count:1,result:'FAIL'}];
sandbox.DETAIL_VIEW='analysis';
respond=async q=>q.view==='flow_report_names'?{rows:['check']}:
 {rows:[{seq:10,producer:'guard',kind:'report',data:{version:1,name:'check',result:'PASS',reason:'ok',values:{v:1}},evidence:{line_no:42}},
        {seq:11,producer:'guard',kind:'report',data:{version:1,name:'check2',result:'FAIL',reason:'bad',values:{v:2}},evidence:{line_no:43}}],has_more:false,next_seq:11};
api.refresh(d);await settle();
const html=element('flowAnalysisRows').innerHTML;
assert.ok(html.includes('status-badge result-PASS'));
assert.ok(html.includes('status-badge result-FAIL'));
assert.ok(html.includes('flow-log-btn'));
assert.ok(html.includes('data-wave-icon="external-link"'));
// Clicking row toggles .selected
const rowEl={dataset:{flowRow:'11'},classList:{toggle(){}},closest:s=>s==='[data-flow-row]'?rowEl:null};
handlers.click({target:rowEl});
assert.ok(element('flowAnalysisRows').innerHTML.includes('<tr class="selected" data-flow-row="11"'));
// Details view renders structured table + completion_reason + collapsible raw JSON
sandbox.DETAIL_VIEW='details';api.refresh(d);
const execHtml=element('flowExecutionFacts').innerHTML;
assert.ok(execHtml.includes('Stopped by user · incomplete'));
assert.ok(execHtml.includes('<details class="flow-raw-details">'));
const defHtml=element('flowDefinitionFacts').innerHTML;
assert.ok(defHtml.includes('<details class="flow-raw-details">'));
""", with_reading=True)


def test_flow_tables_use_width_100_min_width_and_truncated_zero_span_row_matches_colgroup():
    run(r"""
const d=detail('t',1);
d.flow.parsers=[{name:'sample',outputs:{},count:1}];
d.flow.automations=[{name:'guard',enabled:true}];
// Select single parser with 0 fields and a truncated row -> only source + col-filler (2 cols)
respond=async()=>({rows:[{seq:1,producer:'sample',stage:'parser',kind:'record',truncated:true,data_preview:'Saved'}],has_more:false,next_seq:1});
api.refresh(d);await settle();
element('flowParser').value='sample';element('flowParser').listeners.change();await settle();
const parsedHtml=element('flowParsedRows').innerHTML;
assert.ok(parsedHtml.includes('style="width:100%;min-width:96px"'));
assert.equal((parsedHtml.match(/<col\b/g)||[]).length,2);
assert.equal((parsedHtml.match(/<td\b/g)||[]).length,2);
// Automation table also uses width:100%;min-width
sandbox.DETAIL_VIEW='automation';api.refresh(d);
assert.ok(element('flowAutomationRows').innerHTML.includes('style="width:100%;min-width:'));
""", with_reading=True)


def test_phase21_parsed_default_latest_and_resize_guard():
    run(r"""
respond=async()=>({rows:[publication(10,5,3.1)],has_more:false,next_seq:10});
api.refresh(detail('p21',1));await settle();
assert.equal(queries.at(-1).view,'flow_latest');
assert.equal(queries.at(-1).mode,'record');
assert.equal(element('flowLatestMode').attributes['aria-pressed'],'true');

// ReadingLayout.isResizing prevents overwriting active table during resize drag
const activeTable={dataset:{readingScope:'s'}};
element('flowParsedRows').querySelector=sel=>sel==='table'?activeTable:null;
const rl=vm.runInContext('ReadingLayout',sandbox);
const origIsResizing=rl.isResizing;
rl.isResizing=t=>t===activeTable;
element('flowParsedRows').innerHTML='KEEP_ACTIVE_TABLE';
api.refresh(detail('p21',2));
assert.equal(element('flowParsedRows').innerHTML,'KEEP_ACTIVE_TABLE');
rl.isResizing=origIsResizing;
""", with_reading=True)


def test_flow_aborted_request_does_not_surface_abort_reason():
    run(r"""
respond=async(_q,_d,opts)=>{
  if(opts?.signal)assert.ok('aborted' in opts.signal);
  throw Object.assign(new Error('Request cancelled'), {name:'AbortError', isAborted:true});
};
api.refresh(detail('abort-test',1));await settle();
assert.doesNotMatch(element('flowParsedRows').innerHTML,/signal is aborted without reason|Request cancelled/);
assert.doesNotMatch(element('flowStatus').textContent,/signal is aborted without reason|Request cancelled/);
""")
