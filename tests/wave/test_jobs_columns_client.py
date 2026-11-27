"""Built-in Jobs columns keep display preferences separate from process data."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
WAVE = Path(__file__).parents[2] / "rpkbin" / "wave"
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def run(checks):
    harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const stored=new Map();let changes=0;
const window={innerWidth:1280,innerHeight:800,localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)}};
const created=new Map();
function makeEl(id){return {id,dataset:{},style:{},hidden:false,innerHTML:'',listeners:{},
 addEventListener(kind,handler){(this.listeners[kind]??=[]).push(handler)},
 setAttribute(k,v){this[k]=v},querySelector(){return null},querySelectorAll(){return []}}}
const document={
 getElementById:id=>created.get(id)||null,
 createElement:tag=>{const el=makeEl('waveColumnMenu');created.set('waveColumnMenu',el);return el;},
 addEventListener(){},
 body:{appendChild(el){created.set(el.id,el);}}
};
const sandbox=vm.createContext({window,Map,Set,JSON,Number,setTimeout,clearTimeout,
 document,displayState:s=>s==='DONE'?'FINISHED':s,WORKSPACE_LAYOUT:'jobs'});
function load(){vm.runInContext(ICONS,sandbox);vm.runInContext(READING,sandbox);vm.runInContext(JOBS,sandbox);return vm.runInContext('JobsColumns',sandbox);}
const api=load(),reading=vm.runInContext('ReadingLayout',sandbox);
function root(){return {listeners:{},addEventListener(kind,handler){(this.listeners[kind]??=[]).push(handler)}}}
const host=root();reading.initialize(host,()=>changes++);
const event=(target,extra={})=>({target,button:0,pointerId:1,preventDefault(){},stopPropagation(){},...extra});
function restored(layout='jobs'){const copy=vm.createContext({window,Map,Set,JSON,Number,WORKSPACE_LAYOUT:layout,document:{getElementById:()=>null}});vm.runInContext(ICONS,copy);vm.runInContext(READING,copy);vm.runInContext(JOBS,copy);return vm.runInContext('JobsColumns',copy);}
"""
    code = harness.replace("ICONS", json.dumps((WAVE / "icons.js").read_text(encoding="utf-8"))).replace(
        "READING", json.dumps((WAVE / "reading.js").read_text(encoding="utf-8"))
    ).replace(
        "JOBS", json.dumps((WAVE / "jobs.js").read_text(encoding="utf-8"))
    ) + checks
    result = subprocess.run([NODE, "-"], input=code.encode(), capture_output=True, timeout=10)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


def test_visibility_persists_required_name_and_rejects_unknown_fields():
    run(r"""
assert.deepEqual(Array.from(api.columns()),['name','status','result','warning_count','elapsed','priority']);
api.change('elapsed',false);api.change('pid',true);api.change('name',false);
api.change('parser.output',true);api.change('__proto__',true);api.change('resources','yes');
assert.deepEqual(Array.from(api.columns()),['name','status','result','warning_count','priority','pid']);
assert.deepEqual(Array.from(restored().columns()),Array.from(api.columns()));
assert.equal(api.header('parser.output'),'');assert.equal(api.cell({},'__proto__'),'');
const preferences=JSON.parse(stored.get('wave_jobs_columns_v1'));
assert.equal(preferences.version,1);assert.ok(preferences.visible.includes('name'));
assert.ok(!preferences.visible.includes('parser.output'));
""")


@pytest.mark.parametrize("saved", ["{broken", "null", '{"version":2,"visible":[]}', '{"version":1,"visible":{}}', "x" * 9000])
def test_bad_or_future_preferences_do_not_block_default_columns(saved):
    run("stored.set('wave_jobs_columns_v1'," + json.dumps(saved) + ");\n" + r"""
assert.deepEqual(Array.from(api.columns()),['name','status','result','warning_count','elapsed','priority']);
""")


def test_empty_and_unknown_saved_visibility_preserve_only_required_identity():
    run(r"""
stored.set('wave_jobs_columns_v1',JSON.stringify({version:1,visible:['unknown','__proto__']}));
assert.deepEqual(Array.from(api.columns()),['name']);
assert.match(api.choices(),/data-jobs-visible="name" checked disabled/);
assert.ok(!api.choices().includes('data-jobs-visible="unknown"'));
api.reset();assert.ok(api.columns().includes('priority'));
""")


def test_header_drag_and_chooser_move_share_persisted_order_without_row_changes():
    run(r"""
api.columns();
const table={dataset:{readingScope:'jobs-overview'}};
function header(field,left,width=100){
 const th={dataset:{readingField:field},getBoundingClientRect:()=>({left,width,right:left+width}),
  setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},
  closest:s=>s==='table'?table:(s==='th'||s==='th[data-reading-field]')?headerCache[field]:null};
 return th;
}
const headerCache={status:header('status',248,120),priority:header('priority',624,88)};
sandbox.document.elementFromPoint=()=>headerCache.status;
host.listeners.pointerdown[0](event(headerCache.priority,{clientX:640}));
host.listeners.pointermove[0](event(headerCache.priority,{clientX:260,clientY:10}));
host.listeners.pointerup[0](event(headerCache.priority,{clientX:260,clientY:10,type:'pointerup'}));
assert.deepEqual(Array.from(api.columns()),['name','priority','status','result','warning_count','elapsed']);
assert.equal(changes,1);
api.move('priority',1);
assert.deepEqual(Array.from(api.columns()),['name','status','priority','result','warning_count','elapsed']);
api.change('priority',false);api.change('priority',true);
assert.deepEqual(Array.from(restored().columns()),Array.from(api.columns()));
api.move('name',1);api.move('status',-1);
assert.equal(api.columns()[0],'name');assert.equal(api.columns()[1],'status');
assert.match(api.header('name'),/data-reading-fixed="name"/);
assert.ok(!api.header('name').includes('draggable'));
assert.ok(!api.header('priority').includes('draggable'));
assert.ok(!api.header('priority').includes('reading-column-move'));
assert.match(api.header('priority'),/tabindex="0"/);
assert.ok(api.layout().html.includes('<col class="col-filler">'));
""")


def test_keyboard_resize_and_order_keep_job_column_identity_and_other_profiles():
    run(r"""
api.columns();reading.register(['x','y'],'another-flow');reading.reorder('another-flow','y','x');
const th={dataset:{readingField:'priority'},getBoundingClientRect:()=>({width:88}),querySelector:()=>null};
const fixed={dataset:{readingFixed:'name'},querySelector:()=>null};
const cols=[{style:{width:'28px'}},{style:{width:'220px'}},{style:{width:'88px'}},{classList:{contains:c=>c==='col-filler'},style:{}}];
const table={dataset:{readingScope:'jobs-overview'},style:{},querySelectorAll:selector=>selector==='thead th'?[{dataset:{}},fixed,th]:cols};
const handle={closest:selector=>selector==='th[data-reading-field]'?th:selector==='[data-column-resize]'?handle:null};
th.closest=selector=>selector==='table'?table:th;
host.listeners.keydown[0](event(handle,{key:'ArrowRight'}));
assert.equal(reading.width('jobs-overview','priority'),104);
assert.match(api.layout().html,/width:104px/);
host.listeners.keydown[0](event(handle,{key:'ArrowLeft',altKey:true}));
assert.deepEqual(Array.from(api.columns()),['name','status','result','warning_count','priority','elapsed']);
assert.equal(reading.width('jobs-overview','priority'),104);
api.reset();
assert.deepEqual(Array.from(api.columns()),['name','status','result','warning_count','elapsed','priority']);
assert.equal(reading.width('jobs-overview','priority',false,88),88);
assert.deepEqual(Array.from(reading.order(['x','y'],'another-flow')),['y','x']);
""")


def test_split_and_overview_scopes_are_independent_and_context_menu_works():
    run(r"""
assert.equal(api.scope(),'jobs-overview');
assert.deepEqual(Array.from(api.columns()),['name','status','result','warning_count','elapsed','priority']);
sandbox.WORKSPACE_LAYOUT='split';
assert.equal(api.scope(),'jobs-split');
assert.deepEqual(Array.from(api.columns()),['name','status','result']);
api.change('pid',true);
assert.deepEqual(Array.from(api.columns()),['name','status','result','pid']);
sandbox.WORKSPACE_LAYOUT='jobs';
assert.deepEqual(Array.from(api.columns()),['name','status','result','warning_count','elapsed','priority']);
assert.deepEqual(Array.from(restored('split').columns()),['name','status','result','pid']);

const th={dataset:{readingField:'status'},closest:()=>null};
const tbl={dataset:{readingScope:'jobs-overview'},querySelectorAll:()=>[]};
const menu=api.openContextMenu({clientX:100,clientY:80,preventDefault(){},stopPropagation(){}},th,tbl);
assert.ok(menu && menu.hidden===false);
assert.ok(menu.innerHTML.includes('Hide "State"'));
assert.ok(menu.innerHTML.includes('data-menu-column="pid"'));
""")


def test_cell_formatting_uses_only_builtin_data_and_preserves_zero_and_escaping():
    run(r"""
const job={id:'id"<',name:'A<script>long-name',status:'PENDING',priority:0,elapsed:0,attempt_number:1,
 returncode:0,pid:null,resources:{'slot<script>':2},warning_count:0,data:{priority:999,derived:'hidden'},
 pending_reason:'Waiting <slot>',flow_summary:{reason:'derived note'}};
assert.equal(api.cell(job,'priority').replace(/<[^>]*>/g,''),'0');
assert.ok(!api.cell(job,'priority').includes('disabled'));
assert.ok(api.cell(job,'priority').includes('data-job-priority="id&quot;&lt;"'));
assert.ok(!api.cell(job,'priority').includes('999'));
assert.match(api.cell(job,'returncode'),/>0<\/td>/);
assert.match(api.cell(job,'pid'),/>—<\/td>/);
assert.match(api.cell(job,'elapsed'),/>0s<\/td>/);
assert.ok(api.cell(job,'resources').includes('slot&lt;script&gt;: 2'));
assert.ok(api.cell(job,'name').includes('&lt;script&gt;'));
assert.ok(!api.cell(job,'name').includes('<script>'));
assert.ok(!api.cell(job,'status').includes('pending-reason'));
assert.ok(api.cell({...job,held:true},'status').includes('HELD'));
assert.ok(api.cell({...job,held:true},'status').includes('<svg '));
assert.ok(api.cell(job,'pending_reason').includes('Waiting &lt;slot&gt;'));
assert.ok(!api.cell(job,'pending_reason').includes('job-reason'));
assert.ok(api.cell({...job,status:'RUNNING'},'priority').includes(' disabled'));
assert.ok(api.cell(job,'priority').includes('job-priority-edit-icon'));
assert.ok(api.cell(job,'priority').includes('aria-label="Edit priority for '));
assert.ok(!api.cell({...job,status:'RUNNING'},'priority').includes('job-priority-edit-icon'));
assert.ok(api.cell({...job,status:'DONE'},'status').includes('FINISHED'));
const result=api.cell({...job,result:'FAIL',error_count:2,log_file_error:{path:'"<log>'}},'result');
assert.ok(result.includes('error-badge-btn'));assert.ok(result.includes('log-save-error'));
assert.ok(result.includes('&quot;&lt;log&gt;'));assert.ok(!result.includes('path="<'));
assert.ok(api.cell({...job,warning_count:2},'warning_count').includes('warning-count'));
""")


def test_chooser_initialization_is_idempotent_and_retains_focus_after_rebuild():
    run(r"""
const first=root(),second=root();let updates=0,focused='';
first.querySelector=selector=>({focus:()=>focused=selector});
api.initialize(first,()=>updates++);api.initialize(first,()=>updates++);api.initialize(second,()=>updates++);
assert.equal(first.listeners.change.length,1);assert.equal(second.listeners.change.length,1);
const input={dataset:{jobsVisible:'elapsed'},checked:false,closest:()=>input};
first.listeners.change[0](event(input));
assert.equal(updates,1);assert.ok(!api.columns().includes('elapsed'));
assert.equal(focused,'[data-jobs-visible="elapsed"]');
const button={dataset:{jobsMove:'priority',direction:'-1'},closest:()=>button};
first.listeners.click[0](event(button));
assert.equal(updates,2);assert.equal(focused,'[data-jobs-move="priority"][data-direction="-1"]');
const before=Array.from(api.columns());button.disabled=true;first.listeners.click[0](event(button));
assert.deepEqual(Array.from(api.columns()),before);
""")


def test_numeric_cells_keep_external_pid_labels_left_aligned():
    run(r"""
const job={warning_count:0,elapsed:0,priority:0,attempt_number:1,returncode:0,pid:123};
for(const field of ['warning_count','elapsed','priority','attempt_number','returncode','pid']){
  assert.match(api.header(field),/<th[^>]*class="job-numeric"/);
  assert.match(api.cell(job,field),/<td[^>]*class="[^"]*\bjob-numeric\b/);
}
assert.match(api.cell(job,'warning_count'),/\bjob-warning-cell\b/);
const external=api.cell({...job,pid:null,origin_label:'External (tail)'},'pid');
assert.match(external,/>External \(tail\)<\/td>/);
assert.doesNotMatch(external,/job-numeric/);
""")


def test_jobs_state_explains_a_stopped_source_without_changing_status():
    run(r"""
const cell=api.cell({status:'FAILED',source_kind:'tail',completion_reason:'user_stopped'},'status');
assert.match(cell,/st-FAILED/);
assert.match(cell,/>Stopped by user<\/span>/);
assert.match(cell,/title="Stopped by user · incomplete"/);
""")
