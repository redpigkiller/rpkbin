"""Display interactions preserve data identity and stay independent of controls."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
SOURCE = Path(__file__).parents[2] / "rpkbin/wave/reading.js"
ICONS_SOURCE = Path(__file__).parents[2] / "rpkbin/wave/icons.js"
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def run(checks):
    harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const stored=new Map(),nodes=new Map();let vertical=true,changes=0;
function node(id){if(!nodes.has(id))nodes.set(id,{id,dataset:{},style:{},listeners:{},attrs:{},hidden:false,innerHTML:'',
 addEventListener(k,v){this.listeners[k]=v},setAttribute(k,v){this.attrs[k]=v},getAttribute(k){return this.attrs[k]},
 setPointerCapture(){this.captured=true},hasPointerCapture(){return !!this.captured},releasePointerCapture(){this.captured=false},
 getBoundingClientRect:()=>({left:0,top:0,width:1000,height:500,right:1000,bottom:500}),
 });return nodes.get(id)}
const pane=node('analysisContext'),divider=node('analysisContextDivider'),root=node('flowWorkspaceView');
pane.parentElement=node('readingArea');
const window={innerWidth:1280,innerHeight:800,localStorage:{getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,v)}};
const sandbox=vm.createContext({document:{getElementById:node},window,Map,Set,JSON,Number,setTimeout,clearTimeout,
 getComputedStyle:()=>({flexDirection:vertical?'row':'column'})});
vm.runInContext(ICONS_SOURCE,sandbox);
vm.runInContext(SOURCE,sandbox);let api=vm.runInContext('ReadingLayout',sandbox),icons=vm.runInContext('WaveIcons',sandbox);
api.initialize(root,()=>changes++);
function event(target,extra={}){return {target,button:0,pointerId:1,preventDefault(){},stopPropagation(){},...extra}}
const key='["flow","1","parsed",""]';
function column(name,left=96,width=160){const th={dataset:{readingField:name},textContent:name,
 getBoundingClientRect:()=>({left,width,right:left+width}),querySelector:()=>null,
 setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]}};
 th.closest=selector=>selector==='table'?table:(selector==='th'||selector==='th[data-reading-field]')?th:null;
 return th;}
const a=column('__proto__',96,160),b=column('value',256,160);
const cols=[{style:{width:'96px'}},{style:{width:'160px'}},{style:{width:'160px'}}];
const table={dataset:{readingScope:key},style:{},querySelectorAll:s=>s==='thead th'?[{dataset:{}},a,b]:s==='col'?cols:[]};
const handle={closest:s=>s==='[data-column-resize]'?handle:s==='th'?a:s==='table'?table:null,setPointerCapture(){}};
"""
    code = (
        harness + checks
    ).replace("ICONS_SOURCE", json.dumps(ICONS_SOURCE.read_text(encoding="utf-8"))).replace(
        "SOURCE", json.dumps(SOURCE.read_text(encoding="utf-8"))
    )
    result = subprocess.run([NODE, "-"], input=code.encode(), capture_output=True, timeout=10)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


def test_context_drag_keeps_independent_orientation_sizes_and_bounds():
    run(r"""
assert.equal(pane.style.flexBasis,'44%');
divider.listeners.pointerdown(event(divider));divider.listeners.pointermove(event(divider,{clientX:300}));
divider.listeners.pointerup(event(divider));assert.equal(pane.style.flexBasis,'70%');
vertical=false;api.context();assert.equal(pane.style.flexBasis,'40%');
assert.equal(divider.attrs['aria-orientation'],'horizontal');
divider.listeners.keydown(event(divider,{key:'ArrowUp'}));assert.equal(pane.style.flexBasis,'43%');
vertical=true;api.context();assert.equal(pane.style.flexBasis,'70%');
divider.listeners.pointerdown(event(divider));divider.listeners.pointermove(event(divider,{clientX:-100}));
assert.equal(pane.style.flexBasis,'85%');
""")


def test_data_column_resize_and_reorder_persist_without_changing_field_identity():
    run(r"""
api.register(['__proto__','value'],key);
root.listeners.pointerdown(event(handle,{clientX:100}));root.listeners.pointermove(event(handle,{clientX:196}));
root.listeners.pointerup(event(handle));
assert.equal(cols[1].style.width,'256px');assert.equal(table.style.width,'512px');assert.equal(table.style.minWidth,'512px');
sandbox.document.elementFromPoint=()=>b;
root.listeners.pointerdown(event(a,{clientX:120}));
root.listeners.pointermove(event(a,{clientX:360,clientY:10}));
root.listeners.pointerup(event(a,{clientX:360,clientY:10,type:'pointerup'}));
assert.deepEqual(Array.from(api.order(['__proto__','value'],key)),['value','__proto__']);
assert.equal(changes,2);
assert.deepEqual(Array.from(api.order(['__proto__','value'],'different')),['__proto__','value']);
const second=vm.createContext({document:{getElementById:node},window,Map,Set,JSON,Number,
 getComputedStyle:()=>({flexDirection:'row'})});
vm.runInContext(SOURCE,second);const restored=vm.runInContext('ReadingLayout',second);
assert.equal(restored.width(key,'__proto__'),256);
assert.deepEqual(Array.from(restored.order(['__proto__','value'],key)),['value','__proto__']);
""")


def test_keyboard_resize_is_bounded_and_alt_arrows_reorder_only_data_columns():
    run(r"""
api.register(['__proto__','value'],key);
const target={closest:s=>s==='th[data-reading-field]'?a:s==='[data-column-resize]'?handle:null};
for(let n=0;n<20;n++)root.listeners.keydown(event(target,{key:'ArrowLeft'}));
assert.equal(api.width(key,'__proto__'),56);
root.listeners.keydown(event(target,{key:'ArrowRight',altKey:true}));
assert.deepEqual(Array.from(api.order(['__proto__','value'],key)),['value','__proto__']);
root.listeners.keydown(event({closest:()=>null},{key:'ArrowRight',altKey:true}));
assert.deepEqual(Array.from(api.order(['__proto__','value'],key)),['value','__proto__']);
""")


def test_fixed_columns_resize_without_colliding_with_user_fields_or_reordering():
    run(r"""
api.register(['__proto__','value'],key);
const fixed={dataset:{readingFixed:'source'},getBoundingClientRect:()=>({width:96}),querySelector:()=>null,closest:()=>table};
table.querySelectorAll=s=>s==='thead th'?[fixed,a,b]:cols;
const target={closest:s=>s==='th[data-reading-fixed]'?fixed:s==='[data-column-resize]'?{}:null};
root.listeners.keydown(event(target,{key:'ArrowRight'}));
assert.equal(cols[0].style.width,'112px');assert.equal(api.width(key,'source',true),112);
assert.equal(api.width(key,'source'),160,'same user key is a different column');
root.listeners.keydown(event(target,{key:'ArrowRight',altKey:true}));
assert.deepEqual(Array.from(api.order(['__proto__','value'],key)),['__proto__','value']);
const sizes=api.columns(['source'],key,[{key:'source',width:96}]);
assert.equal(sizes.size,272);assert.ok(sizes.html.includes('112px'));
assert.ok(sizes.html.includes('<col class="col-filler">'));
""")


def test_pointer_column_handle_reorders_and_cancel_or_outside_drop_does_not():
    run(r"""
const c=column('unit',416,160);
api.register(['__proto__','value','unit'],key);
let hit=c;
sandbox.document.elementFromPoint=()=>hit;
root.listeners.pointerdown(event(a,{clientX:110}));
assert.equal(api.isResizing(table),true);
// Small movement <= 5px does not start drag or reorder
root.listeners.pointermove(event(a,{clientX:113,clientY:10}));
assert.equal(c['data-column-drop'],undefined);
root.listeners.pointerup(event(a,{clientX:113,clientY:10,type:'pointerup'}));
assert.deepEqual(Array.from(api.order(['__proto__','value','unit'],key)),['__proto__','value','unit']);
// Dragging > 5px onto left half of c inserts before c
root.listeners.pointerdown(event(a,{clientX:110}));
root.listeners.pointermove(event(a,{clientX:440,clientY:10}));
assert.equal(c['data-column-drop'],'left');
root.listeners.pointerup(event(a,{clientX:440,clientY:10,type:'pointerup'}));
assert.deepEqual(Array.from(api.order(['__proto__','value','unit'],key)),['value','__proto__','unit']);
assert.equal(api.isResizing(table),false);assert.equal(c['data-column-drop'],undefined);
// Dragging onto right half of c inserts after c
root.listeners.pointerdown(event(a,{clientX:260}));
root.listeners.pointermove(event(a,{clientX:550,clientY:10}));
assert.equal(c['data-column-drop'],'right');
root.listeners.pointerup(event(a,{clientX:550,clientY:10,type:'pointerup'}));
assert.deepEqual(Array.from(api.order(['__proto__','value','unit'],key)),['value','unit','__proto__']);
// Pointercancel aborts reorder
hit=b;
root.listeners.pointerdown(event(a,{clientX:420}));
root.listeners.pointermove(event(a,{clientX:120,clientY:10}));
root.listeners.pointercancel(event(a,{clientX:120,clientY:10,type:'pointercancel'}));
assert.deepEqual(Array.from(api.order(['__proto__','value','unit'],key)),['value','unit','__proto__']);
hit=null;root.listeners.pointerdown(event(a,{clientX:0}));
root.listeners.pointerup(event(a,{clientX:200,clientY:100,type:'pointerup'}));
assert.equal(api.isResizing(table),false);
""")


def test_double_click_autofits_column_and_open_column_menu_manages_visibility():
    run(r"""
const rows=[
  {children:[{textContent:'L1'},{textContent:'short'},{textContent:'a much longer cell value for autofit testing'}]}
];
table.querySelectorAll=s=>s==='thead th'?[{dataset:{readingFixed:'source'},textContent:'Source'},a,b]:
  s==='col'?cols:s==='tbody tr'?rows:[];
root.listeners.dblclick(event(handle));
const fittedA=api.width(key,'__proto__');
assert.ok(fittedA>=56 && fittedA<=160);
api.autoFitAll(table,key);
const fittedB=api.width(key,'value');
assert.ok(fittedB>fittedA && fittedB<=480);

// Per-scope visibility & universal right-click column menu
const allCols=Array.from({length:14},(_,i)=>({key:'f'+i,label:'Field '+i,fixed:i===0}));
api.register(allCols.filter(c=>!c.fixed).map(c=>c.key),key,12);
assert.equal(api.isVisible(key,'f1'),true);
assert.equal(api.isVisible(key,'f12'),true);
assert.equal(api.isVisible(key,'f13'),false);

const menu=api.openColumnMenu({clientX:200,clientY:150,preventDefault(){},stopPropagation(){}},{
  table,key,clickedField:'f2',clickedLabel:'Field 2',clickedFixed:false,allColumns:allCols
});
assert.equal(menu.hidden,false);
assert.ok(menu.innerHTML.includes('Auto-fit "Field 2"'));
assert.ok(menu.innerHTML.includes('Auto-fit all columns'));
assert.ok(menu.innerHTML.includes('Hide "Field 2"'));
assert.ok(menu.innerHTML.includes('aria-label="Move Left"'));
assert.ok(menu.innerHTML.includes('aria-label="Move Right"'));
assert.ok(menu.innerHTML.includes(icons.svg('arrow-left')));
assert.ok(menu.innerHTML.includes(icons.svg('arrow-right')));
assert.ok(menu.innerHTML.includes('data-menu-filter'));
assert.ok(menu.innerHTML.includes('Reset columns to default'));

const emptyHeaderMenu=api.openColumnMenu({clientX:900,clientY:20,preventDefault(){},stopPropagation(){}},{
  table,key,allColumns:allCols
});
assert.equal(emptyHeaderMenu.hidden,false);
assert.ok(emptyHeaderMenu.innerHTML.includes('Auto-fit all columns'));
assert.ok(emptyHeaderMenu.innerHTML.includes('Reset columns to default'));
assert.ok(!emptyHeaderMenu.innerHTML.includes('Auto-fit "Column"'));

// Re-open on Field 2 before checking checkbox and hide actions
api.openColumnMenu({clientX:200,clientY:150,preventDefault(){},stopPropagation(){}},{
  table,key,clickedField:'f2',clickedLabel:'Field 2',clickedFixed:false,allColumns:allCols
});

// Toggling a checkbox updates visibility immediately without closing menu
const cb={dataset:{menuColumn:'f13'},checked:true,disabled:false,closest:s=>s==='[data-menu-column]'?cb:null};
menu.listeners.change({target:cb});
assert.equal(api.isVisible(key,'f13'),true);
assert.equal(menu.hidden,false);

// Hide button hides clicked column and closes menu
const hideBtn={dataset:{menuAction:'hide-column'},disabled:false,closest:s=>s==='[data-menu-action]'?hideBtn:null};
menu.listeners.click({target:hideBtn});
assert.equal(api.isVisible(key,'f2'),false);
assert.equal(menu.hidden,true);
""")


def test_unified_lucide_icons_keep_original_follow_and_include_all_u4_icons():
    run(r"""
assert.ok(icons.svg('follow').includes('<path d="M12 3v12m-5-5 5 5 5-5"/><path d="M5 19h14"/>'));
for (const name of [
  'columns','pane-right','pane-below','jobs','history','stop','rerun','filters','settings',
  'open','context','follow','search','enter','more','close','beginning','split',
  'panel-left-close','panel-left-open','panel-top-close','panel-top-open','reload',
  'play','pause','arrow-up','arrow-down','arrow-left','arrow-right','chevron-down','list-filter','external-link',
  'sliders','trash','check','grip-vertical','alert-triangle'
]) {
  const markup = icons.svg(name);
  assert.ok(markup.startsWith('<svg ') && markup.includes('viewBox="0 0 24 24"') && markup.includes('stroke-width="1.7"'), name);
}
assert.notEqual(icons.svg('reload'), icons.svg('rerun'));
""")


def test_shared_reading_roots_both_refresh_without_duplicate_divider_handlers():
    run(r"""
let otherChanges=0;
const other=node('jobs');api.initialize(other,()=>otherChanges++);
api.register(['__proto__','value'],key);
api.reorder(key,'__proto__','value');
assert.equal(changes,1);assert.equal(otherChanges,1);
api.initialize(other,()=>otherChanges+=2);
api.reset(key);
assert.equal(changes,2);assert.equal(otherChanges,3);
""")


def test_auto_fit_initial_caps_widths_respects_user_resizes_and_is_any_dragging():
    run(r"""
assert.equal(api.isAnyDragging(),false);
api.register(['__proto__','value'],key);
// Explicitly resize 'value' before autoFitInitial; autoFitInitial must keep the user width.
root.listeners.keydown(event({closest:s=>s==='th[data-reading-field]'?b:s==='[data-column-resize]'?{}:null},{key:'ArrowRight'}));
const userValWidth=api.width(key,'value');
assert.notEqual(userValWidth,160);

const rows=[
  {children:[
    {textContent:'short'},
    {textContent:'x'.repeat(100)},
    {textContent:'y'.repeat(100)}
  ]}
];
const scopeCols=[{style:{width:'96px'}},{style:{width:'160px'}},{style:{width:userValWidth+'px'}},{classList:{contains:c=>c==='col-filler'},style:{}}];
const sTable={
  dataset:{readingScope:key},
  style:{},
  querySelectorAll:s=>s==='thead th'?[{dataset:{readingFixed:'source'},textContent:'Source'},a,b]:
    s==='col'?scopeCols:s==='tbody tr'?rows:[]
};
assert.equal(api.autoFitInitial(sTable,key,{maxWidth:240,maxByField:{source:120}}),true);
assert.equal(api.width(key,'source',true),73);
assert.equal(api.width(key,'__proto__'),240,'capped to default maxWidth=240');
assert.equal(api.width(key,'value'),userValWidth,'explicit user width is preserved');
assert.equal(scopeCols[1].style.width,'240px');
// Running a second time for the same scope is a no-op
assert.equal(api.autoFitInitial(sTable,key,{maxWidth:240}),false);
assert.equal(api.isAnyDragging(),false);
""")
