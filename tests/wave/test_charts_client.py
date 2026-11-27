"""Run the production chart formatters and waveform geometry in Node."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
CHARTS = Path(__file__).parents[2] / "rpkbin/wave/charts.js"
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def run(source):
    result = subprocess.run(
        [NODE, "-"], input=("const assert=require('node:assert/strict');\n"
                            + CHARTS.read_text(encoding="utf-8") + "\n" + source).encode(),
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_bigint_radix_and_slice_preserve_exact_bits():
    run("""
const bus = {type:'signed_bus',bits:8,signed:true,frac_bits:4};
assert.equal(ChartMath.format('-1',bus,'hex'),'0xFF');
assert.equal(ChartMath.format('-1',bus,'bin'),'0b11111111');
assert.equal(ChartMath.format('-1',bus,'unsigned'),'255');
assert.equal(ChartMath.format('-1',bus,'signed'),'-1');
assert.equal(ChartMath.format('-1',bus,'fxp'),'-0.0625');
assert.equal(ChartMath.format('257',bus,'hex'),'Unknown');
assert.equal(ChartMath.format((2n**256n-1n).toString(),{type:'bus',bits:256},'hex'),
             '0x'+'F'.repeat(64));
assert.deepEqual(ChartMath.parseSlice('[7:4]',8),[7,4]);
assert.deepEqual(ChartMath.parseSlice('[0]',8),[0,0]);
assert.equal(ChartMath.parseSlice('[8:4]',8),null);
assert.equal(ChartMath.slice('-17',bus,[7,4]),'14');
assert.equal(ChartMath.slice(null,bus,[7,4]),null);
""")


def test_waveform_hold_uses_prior_event_and_stops_at_scan_frontier():
    run("""
const point=(x,flow_seq,value)=>({x,flow_seq,value,log_seq:flow_seq});
const data={left:{a:point(1,1,'0')}, points:[
  {...point(6,2,'16'),series_id:'a'}, {...point(7,3,'17'),series_id:'a'},
  {...point(9,4,'0'),series_id:'a'}], complete:false, coverage:[10,8,0], range:[5,20]};
const lane={id:'a',type:'bus',bits:8,bitSlice:[4,4]};
const spans=ChartMath.waveSegments(data,lane);
assert.deepEqual(spans.map(s=>[s.lo,s.hi,s.value,s.point?.log_seq]),
  [[5,6,'0',1],[6,9,'1',2],[9,10,'0',4]]);
assert.equal(ChartMath.holdAt(spans,6).point.log_seq,2);
assert.equal(ChartMath.holdAt(spans,11),null);
const pulse={left:{a:point(0,1,0)},buckets:{a:[{index:0,
  first:point(1,2,1),last:point(2,3,0),first_change:point(1,2,1),transitions:2}]},
  bucket_count:2, complete:true, range:[0,10]};
const pulsed=ChartMath.waveSegments(pulse,{id:'a',type:'bit'});
assert.ok(pulsed.some(s=>s.dense && s.point.log_seq===2));
assert.equal(ChartMath.holdAt(pulsed,8).value,0);
""")


def test_xy_overview_representatives_deduplicate_by_sample_identity():
    run("""
const point=(x,flow_seq,value)=>({x,flow_seq,value,series_id:'snr',log_seq:flow_seq});
const cell={
  first:point(5,1,12), min:point(5,1,12), max:point(6,2,23), last:point(7,3,18),
  violation:point(6,2,23)
};
const result=ChartMath.xyOverviewCell(cell);
assert.deepEqual(result.points.map(p=>[p.flow_seq,p.x]),[[1,5],[2,6],[3,7]]);
assert.equal(result.violation,null,'an already-rendered threshold sample is not emitted twice');
const sameX=ChartMath.xyOverviewCell({first:point(5,1,12),max:point(5,2,23)});
assert.deepEqual(sameX.points.map(p=>p.flow_seq),[1,2], 'distinct publications at one x remain distinct');
const extra=point(8,4,9);
assert.equal(ChartMath.xyOverviewCell({first:point(5,1,12),violation:extra}).violation,extra);
const tagged=ChartMath.xyOverviewCell({first:{x:5,flow_seq:1,value:12},violation:{x:8,flow_seq:4,value:9}},'snr');
assert.equal(tagged.points[0].series_id,'snr');
assert.equal(tagged.violation.series_id,'snr');
""")


def test_clearing_a_view_resets_find_controls():
    run("""
const operation={value:'eq'}, value={value:'0x1',hidden:false};
ChartControls.resetFind(operation,value);
assert.equal(operation.value,'any');
assert.equal(value.value,'');
assert.equal(value.hidden,true);
""")


def test_window_controls_and_keyboard_zoom_follow_chart_kind_and_range():
    run("""
const full=[0,100], narrowed=[20,80];
assert.deepEqual(ChartControls.windowActions('scatter',full,narrowed),
  {zoom:false,pan:false,reset:true});
assert.deepEqual(ChartControls.windowActions('histogram',full,full),
  {zoom:false,pan:false,reset:false});
assert.deepEqual(ChartControls.windowActions('xy_line',full,full),
  {zoom:true,pan:true,reset:false});
assert.deepEqual(ChartControls.windowActions('waveform',full,narrowed),
  {zoom:true,pan:true,reset:true});
""")


def test_toolbar_buttons_sync_to_allowed_chart_window_actions():
    run("""
const buttons={zoomIn:{},zoomOut:{},panLeft:{},panRight:{},reset:{}};
ChartControls.syncWindowButtons('scatter',[0,100],[20,80],buttons);
assert.deepEqual(Object.values(buttons).map(button=>button.disabled),[true,true,true,true,false]);
ChartControls.syncWindowButtons('histogram',[0,100],[0,100],buttons);
assert.deepEqual(Object.values(buttons).map(button=>button.disabled),[true,true,true,true,true]);
ChartControls.syncWindowButtons('xy_line',[0,100],[0,100],buttons);
assert.deepEqual(Object.values(buttons).map(button=>button.disabled),[false,false,false,false,true]);
ChartControls.syncWindowButtons('waveform',null,null,buttons);
assert.deepEqual(Object.values(buttons).map(button=>button.disabled),[true,true,true,true,true]);
""")


def test_selected_samples_match_view_axis_and_publication_identity():
    run("""
const picked={view_id:'iq',axis:'time',series_id:'points',x:4,flow_seq:9};
const point={series_id:'points',x:4,flow_seq:9,y:-.7};
assert.equal(ChartControls.matchesPicked(picked,point,'iq','time'),true);
assert.equal(ChartControls.matchesPicked(picked,point,'snr','time'),false);
assert.equal(ChartControls.matchesPicked(picked,point,'iq','elapsed'),false);
assert.equal(ChartControls.matchesPicked(picked,{...point,flow_seq:10},'iq','time'),false);
""")


def test_selected_sample_draws_a_double_outline_only_for_the_active_sample():
    run("""
const calls=[];
const ctx={save(){},restore(){},beginPath(){},stroke(){},arc(_x,_y,radius){calls.push(radius)}};
const picked={view_id:'iq',axis:'time',series_id:'points',x:4,flow_seq:9};
const point={series_id:'points',x:4,flow_seq:9,y:-.7};
assert.equal(ChartControls.drawPicked(ctx,picked,point,'iq','time',40,50),true);
assert.deepEqual(calls,[7,4.5]);
assert.equal(ChartControls.drawPicked(ctx,picked,point,'snr','time',40,50),false);
assert.deepEqual(calls,[7,4.5]);
""")


def test_c3_workspace_layout_guard_ignores_refresh_outside_charts():
    result = subprocess.run(
        [NODE, "-"],
        input=(
            "const assert=require('node:assert/strict');\n"
            "let WORKSPACE_LAYOUT='jobs', STATE={workspace_id:'w',archive:{enabled:true}}, queries=0;\n"
            "const RunHistory={selection:null, async query(){queries++; return {views:[]};}};\n"
            "const document={getElementById(){return null;}};\n"
            + CHARTS.read_text(encoding="utf-8")
            + "\nChartWorkspace.refresh({id:'j1',attempt_id:'a1',total_lines:1,status:'DONE'});\n"
            "assert.equal(queries, 0);\n"
            "assert.equal(typeof ChartWorkspace.openPickedLog, 'function');\n"
        ).encode(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_four_state_formatting_slicing_snap_transition_analog_and_lane_rows():
    run("""
const bus8 = {id: 'bus8', label: 'bus8', type: 'bus', bits: 8, radix: 'hex', frac_bits: 0};
assert.equal(ChartMath.format('X', bus8, 'hex'), 'X');
assert.equal(ChartMath.format('Z', bus8, 'unsigned'), 'Z');
assert.equal(ChartMath.format('4s:1010XZ01', bus8, 'bin'), '0b1010XZ01');
assert.equal(ChartMath.format('4s:1010XZ01', bus8, 'hex'), '0xAX');
assert.equal(ChartMath.format('4s:10101100', bus8, 'unsigned'), '172');

const bus4 = {type: 'bus', bits: 4};
assert.equal(ChartMath.slice('X', bus4, [3, 0]), 'X');
assert.equal(ChartMath.slice('Z', bus4, [2, 1]), 'Z');
assert.equal(ChartMath.slice('4s:10XZ', bus4, [3, 3]), '1');
assert.equal(ChartMath.slice('4s:10XZ', bus4, [1, 1]), 'X');
assert.equal(ChartMath.slice('4s:10XZ', bus4, [0, 0]), 'Z');
assert.equal(ChartMath.slice('4s:10XZ', bus4, [1, 0]), '4s:XZ');

const spans = ChartMath.waveSegments({
  range: [0, 30],
  complete: true,
  left: {b: {x: 0, flow_seq: 1, log_seq: 1, value: 0}},
  points: [
    {series_id: 'b', x: 10, flow_seq: 2, log_seq: 2, value: 1},
    {series_id: 'b', x: 20, flow_seq: 3, log_seq: 3, value: 'Z'},
  ],
}, {id: 'b', type: 'bit'});
assert.equal(spans.length, 3);
const snapped = ChartMath.snapToEdge(spans, 10.4, 1.0);
assert.equal(snapped.snapped, true);
assert.equal(snapped.x, 10);
assert.equal(ChartMath.snapToEdge(spans, 14.0, 1.0).snapped, false);

const fmtLane = (lane, v) => ChartMath.format(v, lane);
const atEdge = ChartMath.valueAtCursor(spans, 10.0, {id: 'b', type: 'bit'}, fmtLane, 0.2);
assert.equal(atEdge.isTransition, true);
assert.equal(atEdge.transitionText, '0 → 1');
const atZ = ChartMath.valueAtCursor(spans, 25.0, {id: 'b', type: 'bit'}, fmtLane, 0.2);
assert.equal(atZ.kind, 'z');
assert.equal(atZ.transitionText, 'Z');

const fxpDef = {id: 'g', label: 'g', type: 'fxp', bits: 8, signed: true, frac_bits: 4, radix: 'fxp'};
assert.equal(ChartMath.analogNumeric('-16', fxpDef), -1);
assert.equal(ChartMath.analogNumeric('X', fxpDef), null);
assert.equal(ChartMath.analogNumeric('Z', fxpDef), null);
assert.deepEqual(
  ChartMath.analogRange([{lo: 0, hi: 5, value: '-16'}, {lo: 5, hi: 10, value: '32'}, {lo: 10, hi: 15, value: 'X'}], fxpDef, [0, 20]),
  [-1.18, 2.18]
);

const unsignedBus8 = {id: 'ubus', label: 'ubus', type: 'bus', bits: 8, signed: false, radix: 'hex'};
assert.equal(ChartMath.format('255', unsignedBus8, 'signed'), '-1');
assert.equal(ChartMath.analogNumeric('255', unsignedBus8, {radix: 'signed'}), -1);
assert.equal(ChartMath.analogNumeric('255', unsignedBus8, {radix: 'fxp', frac: 4}), 15.9375);
assert.deepEqual(
  ChartMath.analogRange([{lo: 0, hi: 5, value: '255'}], unsignedBus8, [0, 10], {radix: 'signed'}),
  [-1.5, -0.5]
);

assert.equal(ChartMath.classifyDragStroke(80, 60), 'zoom_in');
assert.equal(ChartMath.classifyDragStroke(-80, -60), 'zoom_in');
assert.equal(ChartMath.classifyDragStroke(80, -10), 'zoom_in');
assert.equal(ChartMath.classifyDragStroke(50, -60), 'zoom_out');
assert.equal(ChartMath.classifyDragStroke(-50, 60), 'zoom_fit');

const defs = new Map([['bus4', {id: 'bus4', label: 'bus4', type: 'bus', bits: 4, radix: 'hex'}]]);
const settings = new Map([
  [JSON.stringify(['bus4', null]), {expanded: true, slices: ['[3:2]'], display: 'analog_step'}],
  [JSON.stringify(['bus4', [3, 3]]), {heightScale: 2}],
]);
const layout = ChartMath.laneRows([{type: 'divider', id: 'd1', label: 'Top'}, 'bus4'], defs, settings, 23);
assert.equal(layout.rows[0].kind, 'divider');
assert.equal(layout.rows[1].height, 46, 'analog display defaults to 2x height');
assert.deepEqual(layout.rows.slice(2, 6).map(r => r.label), ['[3]', '[2]', '[1]', '[0]']);
assert.equal(layout.rows[2].height, 46, 'expanded_bit honors heightScale');
assert.equal(layout.rows[6].label, 'bus4[3:2]');

const grpOpen = ChartMath.laneRows([{type: 'group', id: 'g1', label: 'G1', collapsed: false, signals: ['bus4']}], defs, settings, 23);
assert.equal(grpOpen.rows[0].kind, 'group');
assert.equal(grpOpen.rows[0].label, 'G1');
assert.equal(grpOpen.rows[0].signalCount, 1);
assert.equal(grpOpen.rows[1].kind, 'signal');
assert.equal(grpOpen.rows[1].groupId, 'g1');
assert.equal(grpOpen.rows[1].treePrefix, '└─ ');
const grpClosed = ChartMath.laneRows([{type: 'group', id: 'g1', label: 'G1', collapsed: true, signals: ['bus4']}], defs, settings, 23);
assert.equal(grpClosed.rows.length, 1);
assert.equal(grpClosed.rows[0].kind, 'group');
""")


def test_waveform_studio_cursors_dual_layer_no_click_log_and_context_reveal():
    result = subprocess.run(
        [NODE, "-"],
        input=(
            "const assert=require('node:assert/strict');\n"
            + CHARTS.read_text(encoding="utf-8")
            + "\n(async () => {\n"
            "let baseDraws = 0, overlayDraws = 0;\n"
            "function makeCtx(kind) {\n"
            "  const bump = () => { if (kind === 'base') baseDraws++; else overlayDraws++; };\n"
            "  return {\n"
            "    setTransform(){}, save(){}, restore(){}, beginPath(){}, rect(){}, clip(){},\n"
            "    moveTo(){}, lineTo(){}, setLineDash(){}, arc(){}, closePath(){}, clearRect(){},\n"
            "    measureText(s){ return { width: String(s).length * 6 }; },\n"
            "    fillRect(){ bump(); }, strokeRect(){ bump(); }, stroke(){ bump(); }, fill(){ bump(); }, fillText(){ bump(); },\n"
            "  };\n"
            "}\n"
            "function makeEl(tag, id = '') {\n"
            "  const classes = new Set();\n"
            "  return {\n"
            "    id, tagName: tag.toUpperCase(), hidden: false, disabled: false, value: '', textContent: '',\n"
            "    title: '', scrollTop: 0, clientWidth: 800, clientHeight: 300, dataset: {}, attributes: {},\n"
            "    children: [], listeners: {}, style: { setProperty(){}, getPropertyValue(){ return ''; } },\n"
            "    get options(){ return this.children; }, selectedOptions: [],\n"
            "    classList: {\n"
            "      toggle(c, force){ if (force === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); } else if (force) classes.add(c); else classes.delete(c); },\n"
            "      contains(c){ return classes.has(c); }, add(...cs){ cs.forEach(c => classes.add(c)); }, remove(...cs){ cs.forEach(c => classes.delete(c)); },\n"
            "    },\n"
            "    setAttribute(k, v){ this[k] = v; this.attributes[k] = v; },\n"
            "    addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "    dispatch(ev, e = {}){ const event = { preventDefault(){}, stopPropagation(){}, ...e, target: e.target || this }; for (const fn of this.listeners[ev] || []) fn(event); return event; },\n"
            "    replaceChildren(...kids){ this.children = []; this.append(...kids); },\n"
            "    append(...kids){ for (const k of kids) { if (k && k._isFrag) this.children.push(...k.children); else this.children.push(k); } },\n"
            "    getBoundingClientRect(){ return { left: 0, top: 0, width: 800, height: 300 }; },\n"
            "    focus(){}, contains(){ return false; }, querySelector(){ return null; }, setPointerCapture(){}, releasePointerCapture(){},\n"
            "    getContext(){ return makeCtx(id === 'chartOverlayCanvas' ? 'overlay' : 'base'); },\n"
            "  };\n"
            "}\n"
            "const elements = new Map();\n"
            "function node(id){ return elements.get(id) ?? (elements.set(id, makeEl('div', id)), elements.get(id)); }\n"
            "node('chartPlot').clientWidth = 800; node('chartPlot').clientHeight = 300;\n"
            "node('chartFindOp').replaceChildren(makeEl('option')); node('chartFindOp').value = 'any';\n"
            "globalThis.WORKSPACE_LAYOUT = 'charts';\n"
            "globalThis.STATE = { workspace_id: 'w1', archive: { enabled: true } };\n"
            "const openContextCalls = [];\n"
            "globalThis.AnalysisWorkspace = { openContext(d, seq){ openContextCalls.push({ d, seq }); }, context(){} };\n"
            "const seriesRequests = [];\n"
            "const windowRequests = [];\n"
            "globalThis.RunHistory = {\n"
            "  selection: null,\n"
            "  async query(opts) {\n"
            "    if (opts.view === 'chart_meta') {\n"
            "      return {\n"
            "        as_of_seq: 10, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },\n"
            "        views: [{\n"
            "          id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 2,\n"
            "          axes: ['time'], default_axis: 'time', domains: { time: [0, 100] },\n"
            "          axis: { label: 'Time', unit: 'ns' }, series: { mode: 'long', defaults: ['top.a'] }, options: { radix: 'hex' },\n"
            "        }],\n"
            "      };\n"
            "    }\n"
            "    if (opts.view === 'chart_series') {\n"
            "      seriesRequests.push(opts);\n"
            "      const all = [\n"
            "        { id: 'top.a', label: 'a', path: ['top', 'a'], type: 'bit' },\n"
            "        { id: 'top.sub.b', label: 'b', path: ['top', 'sub', 'b'], type: 'bit' },\n"
            "      ];\n"
            "      const pref = opts.prefix ? JSON.parse(opts.prefix) : [];\n"
            "      return { series: all.filter(s => pref.every((p, i) => s.path[i] === p)), complete: true, next_cursor: null };\n"
            "    }\n"
            "    if (opts.view === 'chart_window') {\n"
            "      windowRequests.push(opts);\n"
            "      return {\n"
            "        complete: true, scanned: 2, next_cursor: null, health: { closed_cleanly: true },\n"
            "        left: { 'top.a': { x: 0, flow_seq: 1, log_seq: 11, value: 0 } },\n"
            "        points: [{ series_id: 'top.a', x: 50, flow_seq: 2, log_seq: 22, value: 1 }],\n"
            "      };\n"
            "    }\n"
            "    return {};\n"
            "  },\n"
            "};\n"
            "globalThis.document = {\n"
            "  listeners: {},\n"
            "  getElementById: node,\n"
            "  createElement: tag => makeEl(tag),\n"
            "  createDocumentFragment(){ return { _isFrag: true, children: [], append(...k){ this.children.push(...k); } }; },\n"
            "  addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "};\n"
            "globalThis.window = {};\n"
            "ChartWorkspace.refresh({ id: 'j1', attempt_id: 'att1', total_lines: 10, status: 'DONE' });\n"
            "for (let i = 0; i < 12; i++) await Promise.resolve();\n"
            "assert.ok(baseDraws > 0, 'initial waveform should draw static base layer');\n"
            "const baseAfterInit = baseDraws, overlayAfterInit = overlayDraws;\n"
            "\n"
            "// 1. Single-click places Cursor A without calling AnalysisWorkspace.openContext and without redrawing base canvas\n"
            "const canvas = node('chartCanvas');\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 200, clientY: 34, shiftKey: false });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 1, clientX: 200, clientY: 34, shiftKey: false });\n"
            "assert.equal(baseDraws, baseAfterInit, 'cursor click must only redraw overlay canvas (FP4)');\n"
            "assert.ok(overlayDraws > overlayAfterInit);\n"
            "assert.equal(openContextCalls.length, 0, 'single click must NOT open Raw Log');\n"
            "assert.match(node('chartCursorAInput').value, /\\d+/);\n"
            "assert.equal(node('chartContinue').hidden, true);\n"
            "assert.equal(node('chartContinueFind').hidden, true);\n"
            "\n"
            "// 2. Shift+click places Cursor B and shows Δt\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 2, clientX: 600, clientY: 34, shiftKey: true });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 2, clientX: 600, clientY: 34, shiftKey: true });\n"
            "assert.match(node('chartCursorBInput').value, /\\d+/);\n"
            "assert.match(node('chartCursorSummary').textContent, /Δt: /);\n"
            "\n"
            "// 3. Context menu Reveal in Raw Log invokes AnalysisWorkspace.openContext explicitly\n"
            "canvas.dispatch('contextmenu', { clientX: 200, clientY: 34 });\n"
            "const menu = node('chartContextMenu');\n"
            "assert.equal(menu.hidden, false);\n"
            "const revBtn = menu.children.find(c => c.textContent && c.textContent.includes('Reveal in Raw Log'));\n"
            "assert.ok(revBtn && !revBtn.disabled);\n"
            "revBtn.dispatch('click');\n"
            "assert.equal(openContextCalls.length, 1);\n"
            "assert.equal(openContextCalls[0].seq, 11);\n"
            "\n"
            "// 4. Hierarchy prefix query & non-accidental Signal Browser selection\n"
            "const findHier = (root, pred) => {\n"
            "  if (!root || typeof root !== 'object') return null;\n"
            "  if (pred(root)) return root;\n"
            "  for (const ch of root.children || []) { const r = findHier(ch, pred); if (r) return r; }\n"
            "  return null;\n"
            "};\n"
            "const subScopeBtn = findHier(node('chartHierarchyTree'), el => el.tagName === 'BUTTON' && el.dataset?.scopeName === 'sub');\n"
            "assert.ok(subScopeBtn);\n"
            "subScopeBtn.dispatch('click');\n"
            "for (let i = 0; i < 6; i++) await Promise.resolve();\n"
            "assert.equal(seriesRequests.at(-1).prefix, JSON.stringify(['top', 'sub']));\n"
            "const winCountBefore = windowRequests.length;\n"
            "const browserRow = node('chartSignalTree').children[0];\n"
            "browserRow.dispatch('click');\n"
            "for (let i = 0; i < 4; i++) await Promise.resolve();\n"
            "assert.equal(windowRequests.length, winCountBefore, 'clicking signal in browser must not immediately mutate waveform');\n"
            "assert.equal(node('chartAddSignals').disabled, false);\n"
            "node('chartAddSignals').dispatch('click');\n"
            "for (let i = 0; i < 6; i++) await Promise.resolve();\n"
            "assert.ok(windowRequests.length > winCountBefore, 'Add Selected adds checked signal to waveform');\n"
            "assert.deepEqual(JSON.parse(windowRequests.at(-1).series), ['top.a', 'top.sub.b']);\n"
            "})().catch(err => { console.error(err); process.exitCode = 1; });\n"
        ).encode(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_verdi_hotkeys_gestures_layered_escape_and_analog_linear():
    result = subprocess.run(
        [NODE, "-"],
        input=(
            "const assert=require('node:assert/strict');\n"
            + CHARTS.read_text(encoding="utf-8")
            + "\n(async () => {\n"
            "const baseSegments = [];\n"
            "let curX = 0, curY = 0;\n"
            "function makeCtx(kind) {\n"
            "  return {\n"
            "    setTransform(){}, save(){}, restore(){}, beginPath(){}, rect(){}, clip(){},\n"
            "    setLineDash(){}, arc(){}, closePath(){}, clearRect(){},\n"
            "    measureText(s){ return { width: String(s).length * 6 }; },\n"
            "    fillRect(){}, strokeRect(){}, stroke(){}, fill(){}, fillText(){},\n"
            "    moveTo(x, y){ curX = x; curY = y; },\n"
            "    lineTo(x, y){ if (kind === 'base') baseSegments.push([curX, curY, x, y]); curX = x; curY = y; },\n"
            "  };\n"
            "}\n"
            "function makeEl(tag, id = '') {\n"
            "  const classes = new Set();\n"
            "  return {\n"
            "    id, tagName: tag.toUpperCase(), hidden: false, disabled: false, value: '', textContent: '',\n"
            "    title: '', scrollTop: 0, clientWidth: 800, clientHeight: 300, dataset: {}, attributes: {},\n"
            "    children: [], listeners: {}, style: { setProperty(){}, getPropertyValue(){ return ''; } },\n"
            "    get options(){ return this.children; }, selectedOptions: [],\n"
            "    classList: {\n"
            "      toggle(c, force){ if (force === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); } else if (force) classes.add(c); else classes.delete(c); },\n"
            "      contains(c){ return classes.has(c); }, add(...cs){ cs.forEach(c => classes.add(c)); }, remove(...cs){ cs.forEach(c => classes.delete(c)); },\n"
            "    },\n"
            "    setAttribute(k, v){ this[k] = v; this.attributes[k] = v; },\n"
            "    addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "    dispatch(ev, e = {}){\n"
            "      const event = { preventDefault(){}, stopPropagation(){}, checkValidity: () => true, ...e, target: e.target || this };\n"
            "      for (const fn of this.listeners[ev] || []) fn(event);\n"
            "      return event;\n"
            "    },\n"
            "    replaceChildren(...kids){ this.children = []; this.append(...kids); },\n"
            "    append(...kids){ for (const k of kids) { if (k && k._isFrag) this.children.push(...k.children); else this.children.push(k); } },\n"
            "    getBoundingClientRect(){ return { left: 0, top: 0, width: 800, height: 300 }; },\n"
            "    focus(){}, contains(){ return false; }, querySelector(){ return null; }, closest(){ return null; },\n"
            "    setPointerCapture(){}, releasePointerCapture(){}, checkValidity(){ return true; },\n"
            "    getContext(){ return makeCtx(id === 'chartOverlayCanvas' ? 'overlay' : 'base'); },\n"
            "  };\n"
            "}\n"
            "const elements = new Map();\n"
            "function node(id){ return elements.get(id) ?? (elements.set(id, makeEl('div', id)), elements.get(id)); }\n"
            "node('chartPlot').clientWidth = 800; node('chartPlot').clientHeight = 300;\n"
            "node('chartFindOp').replaceChildren(\n"
            "  Object.assign(makeEl('option'), { value: 'any', textContent: 'Any change' }),\n"
            "  Object.assign(makeEl('option'), { value: 'eq', textContent: 'Value equals' })\n"
            ");\n"
            "node('chartFindOp').value = 'any';\n"
            "const timers = [];\n"
            "let nextTimerId = 1;\n"
            "globalThis.setTimeout = (fn, ms) => { const id = nextTimerId++; timers.push({ id, fn, ms, active: true }); return id; };\n"
            "globalThis.clearTimeout = id => { const t = timers.find(item => item.id === id); if (t) t.active = false; };\n"
            "const flushAsync = async () => {\n"
            "  for (const t of timers.splice(0)) { if (t.active) t.fn(); }\n"
            "  for (let i = 0; i < 10; i++) await Promise.resolve();\n"
            "};\n"
            "globalThis.WORKSPACE_LAYOUT = 'charts';\n"
            "globalThis.STATE = { workspace_id: 'w1', archive: { enabled: true } };\n"
            "globalThis.AnalysisWorkspace = { openContext(){}, context(){} };\n"
            "let metaCalls = 0;\n"
            "const windowRequests = [], findRequests = [];\n"
            "globalThis.RunHistory = {\n"
            "  selection: null,\n"
            "  async query(opts) {\n"
            "    if (opts.view === 'chart_meta') {\n"
            "      metaCalls++;\n"
            "      return {\n"
            "        as_of_seq: 10, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },\n"
            "        views: [{\n"
            "          id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 1,\n"
            "          axes: ['time'], default_axis: 'time', domains: { time: [0, 100] },\n"
            "          axis: { label: 'Time', unit: 'ns' }, series: { mode: 'long', defaults: ['top.bus8'] },\n"
            "        }],\n"
            "      };\n"
            "    }\n"
            "    if (opts.view === 'chart_series') {\n"
            "      return {\n"
            "        series: [{ id: 'top.bus8', label: 'bus8', path: ['top', 'bus8'], type: 'bus', bits: 8, radix: 'hex' }],\n"
            "        complete: true, next_cursor: null,\n"
            "      };\n"
            "    }\n"
            "    if (opts.view === 'chart_window') {\n"
            "      windowRequests.push(opts);\n"
            "      return {\n"
            "        complete: true, scanned: 2, next_cursor: null, health: { closed_cleanly: true },\n"
            "        left: { 'top.bus8': { x: 0, flow_seq: 1, log_seq: 11, value: '0' } },\n"
            "        points: [{ series_id: 'top.bus8', x: 50, flow_seq: 2, log_seq: 22, value: '255' }],\n"
            "      };\n"
            "    }\n"
            "    if (opts.view === 'chart_find') {\n"
            "      findRequests.push(opts);\n"
            "      return { hit: null, complete: true, next_cursor: null };\n"
            "    }\n"
            "    return {};\n"
            "  },\n"
            "};\n"
            "globalThis.document = {\n"
            "  listeners: {},\n"
            "  getElementById: node,\n"
            "  createElement: tag => makeEl(tag),\n"
            "  createDocumentFragment(){ return { _isFrag: true, children: [], append(...k){ this.children.push(...k); } }; },\n"
            "  addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "  dispatch(ev, e = {}){\n"
            "    const event = { preventDefault(){}, stopPropagation(){}, ...e, target: e.target || this };\n"
            "    for (const fn of this.listeners[ev] || []) fn(event);\n"
            "    return event;\n"
            "  },\n"
            "};\n"
            "globalThis.window = {};\n"
            "ChartWorkspace.refresh({ id: 'j1', attempt_id: 'att1', total_lines: 10, status: 'DONE' });\n"
            "await flushAsync();\n"
            "const canvas = node('chartCanvas');\n"
            "\n"
            "// 1. Verdi hotkeys via document keydown (Z zoom in, z zoom out, l last view, f fit keeping cursors)\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 198, clientY: 34 });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 1, clientX: 198, clientY: 34 });\n"
            "const summaryWithA = node('chartCursorAInput').value;\n"
            "assert.equal(summaryWithA, '25');\n"
            "assert.equal(node('chartCursorSummary').textContent, 'Δt: —');\n"
            "assert.equal(node('chartLastView').disabled, true);\n"
            "document.dispatch('keydown', { key: 'Z' });\n"
            "await flushAsync();\n"
            "const zoomedIn = windowRequests.at(-1);\n"
            "assert.ok(zoomedIn.hi - zoomedIn.lo < 60, 'Verdi Shift+z (Z) must zoom IN');\n"
            "assert.ok(zoomedIn.lo > 5 && zoomedIn.hi < 70, 'zoom IN centers near Cursor A');\n"
            "assert.equal(node('chartLastView').disabled, false);\n"
            "document.dispatch('keydown', { key: 'z' });\n"
            "await flushAsync();\n"
            "const zoomedOut = windowRequests.at(-1);\n"
            "assert.ok(zoomedOut.hi - zoomedOut.lo > zoomedIn.hi - zoomedIn.lo, 'Verdi lowercase z must zoom OUT');\n"
            "document.dispatch('keydown', { key: 'l' });\n"
            "await flushAsync();\n"
            "const restored = windowRequests.at(-1);\n"
            "assert.ok(Math.abs(restored.lo - zoomedIn.lo) < 1e-6 && Math.abs(restored.hi - zoomedIn.hi) < 1e-6, 'l must restore Last View');\n"
            "document.dispatch('keydown', { key: 'f' });\n"
            "await flushAsync();\n"
            "const fitted = windowRequests.at(-1);\n"
            "assert.equal(fitted.lo, 0); assert.equal(fitted.hi, 100);\n"
            "assert.equal(node('chartCursorAInput').value, summaryWithA, 'f fit must preserve cursors');\n"
            "document.dispatch('keydown', { key: 'l' });\n"
            "await flushAsync();\n"
            "assert.ok(windowRequests.at(-1).hi - windowRequests.at(-1).lo < 60, 'l after f jumps back to zoomed view');\n"
            "node('chartResetWindow').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartCursorAInput').value, summaryWithA, 'Fit button also preserves cursors');\n"
            "\n"
            "// 2. Verdi h (hierarchy toggle), g (Signal Browser), Shift+G (Group), c (color cycle), Shift+L (reload)\n"
            "document.dispatch('keydown', { key: 'h' });\n"
            "assert.equal(node('chartSignalRows').children[0].children[0].children[1].textContent, 'top.bus8');\n"
            "assert.match(node('chartSelectedPath').textContent, /^top\\.bus8\\b/);\n"
            "document.dispatch('keydown', { key: 'h' });\n"
            "assert.equal(node('chartSignalRows').children[0].children[0].children[1].textContent, 'bus8');\n"
            "assert.equal(node('chartSignals').hidden, true);\n"
            "document.dispatch('keydown', { key: 'g' });\n"
            "assert.equal(node('chartSignals').hidden, false, 'g toggles Signal Browser open');\n"
            "document.dispatch('keydown', { key: 'G' });\n"
            "assert.equal(node('chartSignalRows').children[0].children[0].children[1].textContent, '= G1', 'Shift+G wraps signal into Group');\n"
            "const busRowEl = node('chartSignalRows').children[1];\n"
            "busRowEl.dispatch('click');\n"
            "document.dispatch('keydown', { key: 'c' });\n"
            "assert.equal(node('chartSignalRows').children[1].children[0].children[1].style.backgroundColor, '#facc15', 'c cycles signal color');\n"
            "const metaBefore = metaCalls;\n"
            "document.dispatch('keydown', { key: 'L' });\n"
            "await flushAsync();\n"
            "assert.equal(metaCalls, metaBefore + 1, 'Shift+L reloads chart metadata');\n"
            "\n"
            "// 3. Middle-click sets Cursor B, Alt+click does not clobber A, Shift+drag scrubs Cursor B, Δt click zooms A↔B\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 10, clientX: 198, clientY: 34 });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 10, clientX: 198, clientY: 34 });\n"
            "canvas.dispatch('pointerdown', { button: 1, pointerId: 11, clientX: 594, clientY: 34 });\n"
            "canvas.dispatch('pointerup', { button: 1, pointerId: 11, clientX: 594, clientY: 34 });\n"
            "assert.equal(node('chartCursorBInput').value, '75');\n"
            "assert.match(node('chartCursorSummary').textContent, /Δt: 50/);\n"
            "assert.equal(node('chartCursorSummary').disabled, false);\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 12, clientX: 100, clientY: 34, altKey: true });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 12, clientX: 100, clientY: 34, altKey: true });\n"
            "assert.equal(node('chartCursorAInput').value, '25', 'Alt+click must not overwrite Cursor A');\n"
            "const winBeforeShiftDrag = windowRequests.length;\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 13, clientX: 400, clientY: 34, shiftKey: true });\n"
            "canvas.dispatch('pointermove', { button: 0, pointerId: 13, clientX: 475.2, clientY: 34, shiftKey: true });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 13, clientX: 475.2, clientY: 34, shiftKey: true });\n"
            "assert.equal(windowRequests.length, winBeforeShiftDrag, 'Shift+drag scrubs Cursor B without zooming');\n"
            "assert.equal(node('chartCursorBInput').value, '60');\n"
            "assert.match(node('chartCursorSummary').textContent, /Δt: 35/);\n"
            "node('chartCursorSummary').dispatch('click');\n"
            "await flushAsync();\n"
            "const cursorZoom = windowRequests.at(-1);\n"
            "assert.ok(cursorZoom.lo >= 24 && cursorZoom.hi <= 61 && cursorZoom.hi - cursorZoom.lo < 37, 'Δt click zooms A..B');\n"
            "\n"
            "// 4. Verdi 4-direction drag strokes\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 14, clientX: 200, clientY: 150 });\n"
            "canvas.dispatch('pointermove', { button: 0, pointerId: 14, clientX: 260, clientY: 80 });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 14, clientX: 260, clientY: 80 });\n"
            "await flushAsync();\n"
            "assert.ok(windowRequests.at(-1).hi - windowRequests.at(-1).lo > cursorZoom.hi - cursorZoom.lo, 'Up-Right drag zooms OUT');\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 15, clientX: 260, clientY: 80 });\n"
            "canvas.dispatch('pointermove', { button: 0, pointerId: 15, clientX: 200, clientY: 150 });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 15, clientX: 200, clientY: 150 });\n"
            "await flushAsync();\n"
            "assert.equal(windowRequests.at(-1).lo, 0); assert.equal(windowRequests.at(-1).hi, 100);\n"
            "\n"
            "// 5. Layered Escape (context menu -> Signal Browser -> Cursor B)\n"
            "document.dispatch('keydown', { key: 'g' });\n"
            "canvas.dispatch('contextmenu', { clientX: 200, clientY: 34 });\n"
            "assert.equal(node('chartContextMenu').hidden, false);\n"
            "assert.equal(node('chartSignals').hidden, false);\n"
            "assert.equal(ChartWorkspace.hasEscapeTarget(), true);\n"
            "document.dispatch('keydown', { key: 'Escape' });\n"
            "assert.equal(node('chartContextMenu').hidden, true, '1st Escape closes context menu');\n"
            "assert.equal(node('chartSignals').hidden, false, '1st Escape leaves Signal Browser open');\n"
            "assert.equal(ChartWorkspace.hasEscapeTarget(), true);\n"
            "document.dispatch('keydown', { key: 'Escape' });\n"
            "assert.equal(node('chartSignals').hidden, true, '2nd Escape closes Signal Browser');\n"
            "assert.equal(node('chartCursorBInput').value, '60', '2nd Escape leaves Cursor B');\n"
            "document.dispatch('keydown', { key: 'Escape' });\n"
            "assert.equal(node('chartCursorBInput').value, '', '3rd Escape clears Cursor B');\n"
            "assert.match(node('chartCursorSummary').textContent, /Δt: —/, '3rd Escape clears Δt');\n"
            "assert.equal(ChartWorkspace.hasEscapeTarget(), false);\n"
            "\n"
            "// 6. Analog Linear diagonal slope + FXP panel (Signed/Q/Apply) in context menu + Digital resets height to 1x\n"
            "const findInTree = (root, pred) => {\n"
            "  if (!root || typeof root !== 'object') return null;\n"
            "  if (pred(root)) return root;\n"
            "  for (const ch of root.children || []) { const r = findInTree(ch, pred); if (r) return r; }\n"
            "  return null;\n"
            "};\n"
            "baseSegments.length = 0;\n"
            "canvas.dispatch('contextmenu', { clientX: 200, clientY: 56 });\n"
            "const analogLinearBtn = findInTree(node('chartContextMenu'), el => el.tagName === 'BUTTON' && el.textContent === 'Analog Linear');\n"
            "assert.ok(analogLinearBtn);\n"
            "analogLinearBtn.dispatch('click');\n"
            "assert.equal(node('chartSignalRows').children[1].style.height, '46px');\n"
            "assert.ok(baseSegments.some(([x1, y1, x2, y2]) => Math.abs(x2 - x1) > 20 && Math.abs(y2 - y1) > 10),\n"
            "  'Analog Linear must draw diagonal slopes between distinct sample values');\n"
            "canvas.dispatch('contextmenu', { clientX: 200, clientY: 56 });\n"
            "const fxpTrigger = node('chartContextMenu').children.find(c => c.textContent === 'Fixed-Point (FXP)…');\n"
            "assert.ok(fxpTrigger);\n"
            "fxpTrigger.dispatch('click');\n"
            "assert.equal(node('chartContextMenu').hidden, true, 'Clicking FXP closes context menu');\n"
            "assert.equal(node('chartParamModal').hidden, false, 'Clicking FXP opens #chartParamModal');\n"
            "const fxpSign = findInTree(node('chartParamBody'), el => el.tagName === 'SELECT');\n"
            "const fxpQ = findInTree(node('chartParamBody'), el => el.tagName === 'INPUT');\n"
            "assert.ok(fxpSign && fxpQ);\n"
            "fxpSign.value = 'signed';\n"
            "fxpSign.dispatch('change');\n"
            "fxpQ.value = '4';\n"
            "fxpQ.dispatch('input');\n"
            "node('chartParamApply').dispatch('click');\n"
            "assert.equal(node('chartParamModal').hidden, true, 'Apply closes #chartParamModal');\n"
            "assert.match(node('chartSelectedPath').textContent, /FXP \\(Signed, pt=4\\)/);\n"
            "canvas.dispatch('contextmenu', { clientX: 200, clientY: 56 });\n"
            "assert.equal(node('chartContextMenu').children.some(c => c.textContent && c.textContent.startsWith('Height ')), false, 'Row Height removed from context menu');\n"
            "const digitalBtn = findInTree(node('chartContextMenu'), el => el.tagName === 'BUTTON' && el.textContent === 'Digital Bus / Value');\n"
            "assert.ok(digitalBtn);\n"
            "digitalBtn.dispatch('click');\n"
            "assert.equal(node('chartSignalRows').children[1].style.height, '23px', 'Switching Analog -> Digital resets height to 1x');\n"
            "\n"
            "// 7. Collapsible Group (= G1) collapse/expand + Ungroup (Keep Signals) + Expand Bits in context menu\n"
            "node('chartSignalRows').children[0].dispatch('dblclick');\n"
            "assert.equal(node('chartSignalRows').children.length, 1, 'Double-clicking = G1 collapses group');\n"
            "node('chartSignalRows').children[0].dispatch('dblclick');\n"
            "assert.equal(node('chartSignalRows').children.length, 2, 'Double-clicking = G1 expands group');\n"
            "node('chartSignalRows').children[0].dispatch('contextmenu', { clientX: 50, clientY: 30 });\n"
            "node('chartContextMenu').children.find(c => c.textContent === 'Ungroup (Keep Signals)').dispatch('click');\n"
            "assert.equal(node('chartSignalRows').children.length, 1);\n"
            "assert.equal(node('chartSignalRows').children[0].children[0].children[1].textContent, 'bus8');\n"
            "\n"
            "// 8. Cursor A/B editable inputs + horizontal scrollbar sync & pan + stepSelectedEdge fallback\n"
            "node('chartCursorAInput').value = '30';\n"
            "node('chartCursorAInput').dispatch('keydown', { key: 'Enter' });\n"
            "assert.equal(node('chartCursorAInput').value, '30');\n"
            "node('chartCursorBInput').value = '70';\n"
            "node('chartCursorBInput').dispatch('keydown', { key: 'Enter' });\n"
            "assert.equal(node('chartCursorBInput').value, '70');\n"
            "assert.match(node('chartCursorSummary').textContent, /Δt: 40/);\n"
            "document.dispatch('keydown', { key: 'Z' });\n"
            "await flushAsync();\n"
            "assert.notEqual(node('chartHScrollThumb').style.width, '100%', 'Zoomed window shrinks horizontal thumb');\n"
            "const loBeforeScroll = windowRequests.at(-1).lo;\n"
            "node('chartHScrollRight').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.ok(windowRequests.at(-1).lo > loBeforeScroll, 'Horizontal scroll right shifts window forward');\n"
            "node('chartFindOp').value = 'eq';\n"
            "node('chartFindValue').value = '0xFF';\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 16, clientX: 600, clientY: 34 });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 16, clientX: 600, clientY: 34 });\n"
            "document.dispatch('keydown', { key: 'ArrowRight' });\n"
            "await flushAsync();\n"
            "assert.equal(findRequests.at(-1).op, 'any');\n"
            "assert.equal(node('chartFindOp').value, 'eq');\n"
            "assert.equal(node('chartFindValue').value, '0xFF');\n"
            "})().catch(err => { console.error(err); process.exitCode = 1; });\n"
        ).encode(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_axis_switch_preserves_cursors_and_updates_delta():
    result = subprocess.run(
        [NODE, "-"],
        input=(
            "const assert=require('node:assert/strict');\n"
            + CHARTS.read_text(encoding="utf-8")
            + "\n(async () => {\n"
            "function makeCtx(){ return { setTransform(){}, save(){}, restore(){}, beginPath(){}, rect(){}, clip(){}, moveTo(){}, lineTo(){}, setLineDash(){}, arc(){}, closePath(){}, clearRect(){}, measureText(s){ return { width: String(s).length * 6 }; }, fillRect(){}, strokeRect(){}, stroke(){}, fill(){}, fillText(){} }; }\n"
            "function makeEl(tag, id = '') {\n"
            "  const classes = new Set();\n"
            "  return {\n"
            "    id, tagName: tag.toUpperCase(), hidden: false, disabled: false, value: '', textContent: '', title: '',\n"
            "    scrollTop: 0, clientWidth: 800, clientHeight: 300, dataset: {}, attributes: {}, children: [], listeners: {},\n"
            "    style: { setProperty(){}, getPropertyValue(){ return ''; } }, get options(){ return this.children; }, selectedOptions: [],\n"
            "    classList: { toggle(c, f){ if (f === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); } else if (f) classes.add(c); else classes.delete(c); }, contains(c){ return classes.has(c); }, add(...cs){ cs.forEach(c => classes.add(c)); }, remove(...cs){ cs.forEach(c => classes.delete(c)); } },\n"
            "    setAttribute(k, v){ this[k] = v; this.attributes[k] = v; },\n"
            "    addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "    dispatch(ev, e = {}){ const event = { preventDefault(){}, stopPropagation(){}, ...e, target: e.target || this }; for (const fn of this.listeners[ev] || []) fn(event); return event; },\n"
            "    replaceChildren(...kids){ this.children = []; this.append(...kids); },\n"
            "    append(...kids){ for (const k of kids) { if (k && k._isFrag) this.children.push(...k.children); else this.children.push(k); } },\n"
            "    getBoundingClientRect(){ return { left: 0, top: 0, width: 800, height: 300 }; },\n"
            "    focus(){}, contains(){ return false; }, querySelector(){ return null; }, closest(){ return null; },\n"
            "    setPointerCapture(){}, releasePointerCapture(){}, checkValidity(){ return true; },\n"
            "    getContext(){ return makeCtx(); },\n"
            "  };\n"
            "}\n"
            "const elements = new Map();\n"
            "function node(id){ return elements.get(id) ?? (elements.set(id, makeEl('div', id)), elements.get(id)); }\n"
            "node('chartPlot').clientWidth = 800; node('chartPlot').clientHeight = 300;\n"
            "node('chartFindOp').replaceChildren(Object.assign(makeEl('option'), { value: 'any', textContent: 'Any change' }));\n"
            "node('chartFindOp').value = 'any';\n"
            "const timers = []; let nextTimerId = 1;\n"
            "globalThis.setTimeout = (fn, ms) => { const id = nextTimerId++; timers.push({ id, fn, ms, active: true }); return id; };\n"
            "globalThis.clearTimeout = id => { const t = timers.find(item => item.id === id); if (t) t.active = false; };\n"
            "const flushAsync = async () => { for (const t of timers.splice(0)) { if (t.active) t.fn(); } for (let i = 0; i < 10; i++) await Promise.resolve(); };\n"
            "globalThis.WORKSPACE_LAYOUT = 'charts';\n"
            "globalThis.STATE = { workspace_id: 'w1', archive: { enabled: true } };\n"
            "globalThis.AnalysisWorkspace = { openContext(){}, context(){} };\n"
            "globalThis.RunHistory = {\n"
            "  selection: null,\n"
            "  async query(opts) {\n"
            "    if (opts.view === 'chart_meta') {\n"
            "      return {\n"
            "        as_of_seq: 10, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },\n"
            "        views: [{\n"
            "          id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 1,\n"
            "          axes: ['time', 'seq'], default_axis: 'time', domains: { time: [0, 100], seq: [10, 30] },\n"
            "          axis: { label: 'Time', unit: 'ns' }, series: { mode: 'long', defaults: ['top.a'] },\n"
            "        }],\n"
            "      };\n"
            "    }\n"
            "    if (opts.view === 'chart_series') {\n"
            "      return { series: [{ id: 'top.a', label: 'a', path: ['top', 'a'], type: 'bit' }], complete: true, next_cursor: null };\n"
            "    }\n"
            "    if (opts.view === 'chart_window') {\n"
            "      const isSeq = opts.axis === 'seq';\n"
            "      return {\n"
            "        complete: true, scanned: 3, next_cursor: null, health: { closed_cleanly: true },\n"
            "        left: { 'top.a': { x: isSeq ? 10 : 0, flow_seq: 1, log_seq: 11, value: '0' } },\n"
            "        points: [\n"
            "          { series_id: 'top.a', x: isSeq ? 15 : 25, flow_seq: 2, log_seq: 22, value: '1' },\n"
            "          { series_id: 'top.a', x: isSeq ? 25 : 75, flow_seq: 3, log_seq: 33, value: '0' },\n"
            "        ],\n"
            "      };\n"
            "    }\n"
            "    return {};\n"
            "  },\n"
            "};\n"
            "globalThis.document = {\n"
            "  listeners: {}, getElementById: node, createElement: tag => makeEl(tag),\n"
            "  createDocumentFragment(){ return { _isFrag: true, children: [], append(...k){ this.children.push(...k); } }; },\n"
            "  addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "  dispatch(ev, e = {}){ const event = { preventDefault(){}, stopPropagation(){}, ...e, target: e.target || this }; for (const fn of this.listeners[ev] || []) fn(event); return event; },\n"
            "};\n"
            "globalThis.window = {};\n"
            "ChartWorkspace.refresh({ id: 'j1', attempt_id: 'att1', total_lines: 10, status: 'DONE' });\n"
            "await flushAsync();\n"
            "const canvas = node('chartCanvas');\n"
            "canvas.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 200, clientY: 34 });\n"
            "canvas.dispatch('pointerup', { button: 0, pointerId: 1, clientX: 200, clientY: 34 });\n"
            "canvas.dispatch('pointerdown', { button: 1, pointerId: 2, clientX: 600, clientY: 34 });\n"
            "canvas.dispatch('pointerup', { button: 1, pointerId: 2, clientX: 600, clientY: 34 });\n"
            "assert.equal(node('chartCursorAInput').value, '25');\n"
            "assert.equal(node('chartCursorBInput').value, '75');\n"
            "assert.match(node('chartCursorSummary').textContent, /Δt: 50/);\n"
            "node('chartAxis').value = 'seq';\n"
            "node('chartAxis').dispatch('change');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartCursorAInput').value, '15', 'Cursor A remaps via flow_seq/log_seq to seq=15');\n"
            "assert.equal(node('chartCursorBInput').value, '25', 'Cursor B remaps via flow_seq/log_seq to seq=25');\n"
            "assert.match(node('chartCursorSummary').textContent, /Δt: 10/);\n"
            "})().catch(err => { console.error(err); process.exitCode = 1; });\n"
        ).encode(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_verdi_r2_scope_filter_duplicate_resize_clipboard_units_and_no_pan_flicker():
    result = subprocess.run(
        [NODE, "-"],
        input=(
            "const assert=require('node:assert/strict');\n"
            + CHARTS.read_text(encoding="utf-8")
            + "\n(async () => {\n"
            "let baseDraws = 0;\n"
            "function makeCtx(kind) {\n"
            "  const bump = () => { if (kind === 'base') baseDraws++; };\n"
            "  return {\n"
            "    setTransform(){}, save(){}, restore(){}, beginPath(){}, rect(){}, clip(){},\n"
            "    moveTo(){}, lineTo(){}, setLineDash(){}, arc(){}, closePath(){}, clearRect(){},\n"
            "    measureText(s){ return { width: String(s).length * 6 }; },\n"
            "    fillRect(){ bump(); }, strokeRect(){ bump(); }, stroke(){ bump(); }, fill(){ bump(); }, fillText(){ bump(); },\n"
            "  };\n"
            "}\n"
            "function makeEl(tag, id = '') {\n"
            "  const classes = new Set();\n"
            "  return {\n"
            "    id, tagName: tag.toUpperCase(), hidden: false, disabled: false, checked: false, value: '', textContent: '',\n"
            "    title: '', scrollTop: 0, clientWidth: 800, clientHeight: 300, dataset: {}, attributes: {},\n"
            "    children: [], listeners: {}, style: { setProperty(){}, getPropertyValue(){ return ''; } },\n"
            "    get options(){ return this.children; }, selectedOptions: [],\n"
            "    classList: { toggle(c, f){ if (f === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); } else if (f) classes.add(c); else classes.delete(c); }, contains(c){ return classes.has(c); }, add(...cs){ cs.forEach(c => classes.add(c)); }, remove(...cs){ cs.forEach(c => classes.delete(c)); } },\n"
            "    setAttribute(k, v){ this[k] = v; this.attributes[k] = v; },\n"
            "    removeAttribute(k){ delete this[k]; delete this.attributes[k]; },\n"
            "    addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "    dispatch(ev, e = {}){\n"
            "      const event = { preventDefault(){}, stopPropagation(){}, currentTarget: this, ...e, target: e.target || this };\n"
            "      for (const fn of this.listeners[ev] || []) fn(event);\n"
            "      return event;\n"
            "    },\n"
            "    replaceChildren(...kids){ this.children = []; this.append(...kids); },\n"
            "    append(...kids){ for (const k of kids) { if (k && k._isFrag) this.children.push(...k.children); else this.children.push(k); } },\n"
            "    getBoundingClientRect(){ return { left: 0, top: 0, width: 800, height: 300 }; },\n"
            "    focus(){}, contains(){ return false; }, querySelector(){ return null; }, querySelectorAll(){ return []; }, closest(){ return null; },\n"
            "    setPointerCapture(){}, releasePointerCapture(){}, checkValidity(){ return true; },\n"
            "    getContext(){ return makeCtx(id === 'chartOverlayCanvas' ? 'overlay' : 'base'); },\n"
            "  };\n"
            "}\n"
            "const elements = new Map();\n"
            "function node(id){ return elements.get(id) ?? (elements.set(id, makeEl('div', id)), elements.get(id)); }\n"
            "node('chartPlot').clientWidth = 800; node('chartPlot').clientHeight = 300;\n"
            "node('chartFindOp').replaceChildren(Object.assign(makeEl('option'), { value: 'any', textContent: 'Any edge' }));\n"
            "node('chartFindOp').value = 'any';\n"
            "const timers = []; let nextTimerId = 1;\n"
            "globalThis.setTimeout = (fn, ms) => { const id = nextTimerId++; timers.push({ id, fn, ms, active: true }); return id; };\n"
            "globalThis.clearTimeout = id => { const t = timers.find(item => item.id === id); if (t) t.active = false; };\n"
            "let windowPromiseResolve = null;\n"
            "const flushAsync = async () => {\n"
            "  for (const t of timers.splice(0)) { if (t.active) t.fn(); }\n"
            "  for (let i = 0; i < 12; i++) await Promise.resolve();\n"
            "};\n"
            "const allSeries = [\n"
            "  { id: 'top.lock', label: 'lock', path: ['top', 'lock'], type: 'bit' },\n"
            "  { id: 'top.phy.aagc_level', label: 'aagc_level', path: ['top', 'phy', 'aagc_level'], type: 'bus', bits: 12, radix: 'hex' },\n"
            "  { id: 'top.phy.LOCK_REG', label: 'LOCK_REG', path: ['top', 'phy', 'LOCK_REG'], type: 'bus', bits: 8, radix: 'hex' },\n"
            "];\n"
            "const windowRequests = [];\n"
            "let deferWindow = false;\n"
            "globalThis.WORKSPACE_LAYOUT = 'charts';\n"
            "globalThis.STATE = { workspace_id: 'w1', archive: { enabled: true } };\n"
            "globalThis.AnalysisWorkspace = { openContext(){}, context(){} };\n"
            "globalThis.RunHistory = {\n"
            "  selection: null,\n"
            "  async query(opts) {\n"
            "    if (opts.view === 'chart_meta') {\n"
            "      return {\n"
            "        as_of_seq: 10, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },\n"
            "        views: [{\n"
            "          id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 3,\n"
            "          axes: ['time'], default_axis: 'time', domains: { time: [0, 100] },\n"
            "          axis: { label: 'Time', unit: 'ns' }, series: { mode: 'long', defaults: ['top.lock'] },\n"
            "        }],\n"
            "      };\n"
            "    }\n"
            "    if (opts.view === 'chart_series') {\n"
            "      const pref = opts.prefix ? JSON.parse(opts.prefix) : [];\n"
            "      const q = (opts.query || '').toLowerCase();\n"
            "      const list = allSeries.filter(s => pref.every((p, i) => s.path[i] === p) && (!q || s.id.toLowerCase().includes(q)));\n"
            "      return { series: list, complete: true, next_cursor: null };\n"
            "    }\n"
            "    if (opts.view === 'chart_window') {\n"
            "      windowRequests.push(opts);\n"
            "      if (deferWindow) await new Promise(r => { windowPromiseResolve = r; });\n"
            "      return {\n"
            "        complete: true, scanned: 2, next_cursor: null, health: { closed_cleanly: true },\n"
            "        left: { 'top.lock': { x: 0, flow_seq: 1, log_seq: 11, value: '0' } },\n"
            "        points: [{ series_id: 'top.lock', x: 25, flow_seq: 2, log_seq: 22, value: '1' }],\n"
            "      };\n"
            "    }\n"
            "    return {};\n"
            "  },\n"
            "};\n"
            "globalThis.document = {\n"
            "  listeners: {}, getElementById: node, createElement: tag => makeEl(tag),\n"
            "  createDocumentFragment(){ return { _isFrag: true, children: [], append(...k){ this.children.push(...k); } }; },\n"
            "  addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "  dispatch(ev, e = {}){ const event = { preventDefault(){}, stopPropagation(){}, ...e, target: e.target || this }; for (const fn of this.listeners[ev] || []) fn(event); return event; },\n"
            "};\n"
            "globalThis.window = {};\n"
            "ChartWorkspace.refresh({ id: 'j1', attempt_id: 'att1', total_lines: 10, status: 'DONE' });\n"
            "await flushAsync();\n"
            "\n"
            "// 1. Single-view keeps View selector visible; pan right does not paint stale flicker before response resolves\n"
            "assert.equal(node('chartViewSelectWrap').hidden, false);\n"
            "document.dispatch('keydown', { key: 'Z' });\n"
            "await flushAsync();\n"
            "const drawsBeforePan = baseDraws;\n"
            "deferWindow = true;\n"
            "node('chartPanRight').dispatch('click');\n"
            "await Promise.resolve();\n"
            "assert.equal(baseDraws, drawsBeforePan, 'Pan must not repaint stale canvas before new window arrives');\n"
            "deferWindow = false;\n"
            "windowPromiseResolve();\n"
            "await flushAsync();\n"
            "assert.ok(baseDraws > drawsBeforePan, 'Pan repaints once new window arrives');\n"
            "\n"
            "// 2. Scope Sub-tree vs Level Only + Log-style Aa / ab / .* filter + duplicate signal add\n"
            "const findInTree = (root, pred) => {\n"
            "  if (!root || typeof root !== 'object') return null;\n"
            "  if (pred(root)) return root;\n"
            "  for (const ch of root.children || []) { const r = findInTree(ch, pred); if (r) return r; }\n"
            "  return null;\n"
            "};\n"
            "const topScopeBtn = findInTree(node('chartHierarchyTree'), el => el.tagName === 'BUTTON' && el.dataset?.scopeName === 'top');\n"
            "assert.ok(topScopeBtn);\n"
            "topScopeBtn.dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartSignalTree').children.length, 3, 'Sub-tree includes all descendant signals under top');\n"
            "assert.ok(node('chartSignalTree').children[0].className.includes('in-wave'), 'Added signal uses in-wave highlight class');\n"
            "assert.equal(node('chartSignalTree').children[0].textContent.includes('Wave'), false, 'No Wave badge suffix');\n"
            "node('chartScopeRecursive').dispatch('click');\n"
            "assert.equal(node('chartSignalTree').children.length, 1, 'Level Only hides phy sub-level signals');\n"
            "node('chartScopeRecursive').dispatch('click');\n"
            "node('chartSignalSearch').value = 'lock';\n"
            "node('chartSignalCase').checked = true;\n"
            "node('chartSignalCase').dispatch('change');\n"
            "assert.equal(node('chartSignalTree').children.length, 1, 'Aa excludes LOCK_REG');\n"
            "node('chartSignalCase').checked = false;\n"
            "node('chartSignalWord').checked = true;\n"
            "node('chartSignalRegex').checked = true;\n"
            "node('chartSignalSearch').value = 'aagc_.*';\n"
            "node('chartSignalRegex').dispatch('change');\n"
            "assert.equal(node('chartSignalTree').children.length, 1, '.* + ab regex matches aagc_level');\n"
            "node('chartSignalWord').checked = false;\n"
            "node('chartSignalRegex').checked = false;\n"
            "node('chartSignalSearch').value = '';\n"
            "node('chartSignalSearch').dispatch('input');\n"
            "await flushAsync();\n"
            "node('chartSignalTree').children[0].dispatch('dblclick');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartSignalRows').children.length, 2, 'Double-clicking already-added signal adds duplicate row');\n"
            "assert.deepEqual(JSON.parse(windowRequests.at(-1).series), ['top.lock'], 'Backend series query deduplicates IDs');\n"
            "\n"
            "// 3. Direct row height drag resize & double-click auto-fit\n"
            "const firstRow = node('chartSignalRows').children[0];\n"
            "const rowResizer = firstRow.children.find(el => el.className === 'wf-row-resizer');\n"
            "assert.ok(rowResizer);\n"
            "rowResizer.dispatch('pointerdown', { button: 0, pointerId: 21, clientY: 23 });\n"
            "rowResizer.dispatch('pointermove', { pointerId: 21, clientY: 50 });\n"
            "rowResizer.dispatch('pointerup', { pointerId: 21, clientY: 50 });\n"
            "assert.equal(node('chartSignalRows').children[0].style.height, '50px', 'Drag bottom edge sets custom row height');\n"
            "node('chartSignalRows').children[0].children.find(el => el.className === 'wf-row-resizer').dispatch('dblclick');\n"
            "assert.equal(node('chartSignalRows').children[0].style.height, '23px', 'Double-clicking row border auto-fits height');\n"
            "\n"
            "// 4. Cut / Copy / Paste + Time Unit selector (ns -> ps)\n"
            "node('chartSignalRows').children[0].dispatch('click');\n"
            "assert.equal(node('chartPasteSignal').disabled, true);\n"
            "node('chartCopySignal').dispatch('click');\n"
            "assert.equal(node('chartPasteSignal').disabled, false);\n"
            "node('chartPasteSignal').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartSignalRows').children.length, 3, 'Paste inserts duplicate signal below selection');\n"
            "node('chartCutSignal').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartSignalRows').children.length, 2, 'Cut removes selected signal');\n"
            "node('chartCanvas').dispatch('pointerdown', { button: 0, pointerId: 30, clientX: 200, clientY: 34 });\n"
            "node('chartCanvas').dispatch('pointerup', { button: 0, pointerId: 30, clientX: 200, clientY: 34 });\n"
            "node('chartTimeUnit').value = '1ps';\n"
            "node('chartTimeUnit').dispatch('change');\n"
            "const aValNum = Number(node('chartCursorAInput').value);\n"
            "assert.ok(aValNum >= 20000, 'Switching time unit ns -> 1ps scales Cursor A by 1000x');\n"
            "})().catch(err => { console.error(err); process.exitCode = 1; });\n"
        ).encode(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_verdi_r3_modals_rc_vcd_scale_autofit_and_val_dir():
    result = subprocess.run(
        [NODE, "-"],
        input=(
            "const assert=require('node:assert/strict');\n"
            + CHARTS.read_text(encoding="utf-8")
            + "\n(async () => {\n"
            "function makeCtx() { return { setTransform(){}, save(){}, restore(){}, beginPath(){}, rect(){}, clip(){}, moveTo(){}, lineTo(){}, setLineDash(){}, arc(){}, closePath(){}, clearRect(){}, measureText(s){ return { width: String(s).length * 6 }; }, fillRect(){}, strokeRect(){}, stroke(){}, fill(){}, fillText(){} }; }\n"
            "function queryMatch(root, sel) {\n"
            "  for (const ch of root.children || []) {\n"
            "    if (!ch || typeof ch !== 'object') continue;\n"
            "    if (sel.startsWith('.')) {\n"
            "      const cls = sel.slice(1);\n"
            "      if ((ch.className && ch.className.split(/\\s+/).includes(cls)) || ch.classList?.contains?.(cls)) return ch;\n"
            "    } else if (sel.startsWith('#')) {\n"
            "      if (ch.id === sel.slice(1)) return ch;\n"
            "    } else if (ch.tagName === sel.toUpperCase()) {\n"
            "      return ch;\n"
            "    }\n"
            "    const nested = queryMatch(ch, sel);\n"
            "    if (nested) return nested;\n"
            "  }\n"
            "  return null;\n"
            "}\n"
            "function makeEl(tag, id = '') {\n"
            "  const classes = new Set();\n"
            "  const styleVars = new Map();\n"
            "  return {\n"
            "    id, tagName: tag.toUpperCase(), hidden: false, disabled: false, checked: false, value: '', textContent: '',\n"
            "    title: '', scrollTop: 0, clientWidth: 800, clientHeight: 300, dataset: {}, attributes: {},\n"
            "    children: [], listeners: {},\n"
            "    style: {\n"
            "      setProperty(k, v){ styleVars.set(k, v); this[k] = v; },\n"
            "      getPropertyValue(k){ return styleVars.get(k) || ''; },\n"
            "    },\n"
            "    get options(){ return this.children; }, selectedOptions: [],\n"
            "    classList: { toggle(c, f){ if (f === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); } else if (f) classes.add(c); else classes.delete(c); }, contains(c){ return classes.has(c); }, add(...cs){ cs.forEach(c => classes.add(c)); }, remove(...cs){ cs.forEach(c => classes.delete(c)); } },\n"
            "    setAttribute(k, v){ this[k] = v; this.attributes[k] = v; },\n"
            "    removeAttribute(k){ delete this[k]; delete this.attributes[k]; },\n"
            "    addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "    dispatch(ev, e = {}){ const event = { preventDefault(){}, stopPropagation(){}, currentTarget: this, ...e, target: e.target || this }; for (const fn of this.listeners[ev] || []) fn(event); return event; },\n"
            "    replaceChildren(...kids){ this.children = []; this.append(...kids); },\n"
            "    append(...kids){ for (const k of kids) { if (k && k._isFrag) this.children.push(...k.children); else this.children.push(k); } },\n"
            "    insertBefore(newNode, refNode){ const i = this.children.indexOf(refNode); if (i >= 0) this.children.splice(i, 0, newNode); else this.children.push(newNode); },\n"
            "    getBoundingClientRect(){ return { left: 0, top: 0, width: id === 'chartSignalPane' ? 274 : 800, height: 300 }; },\n"
            "    focus(){}, contains(){ return false; }, querySelector(sel){ return queryMatch(this, sel); }, querySelectorAll(){ return []; }, closest(){ return null; },\n"
            "    setPointerCapture(){}, releasePointerCapture(){}, checkValidity(){ return true; },\n"
            "    getContext(){ return makeCtx(); },\n"
            "  };\n"
            "}\n"
            "const elements = new Map();\n"
            "function node(id){ return elements.get(id) ?? (elements.set(id, makeEl('div', id)), elements.get(id)); }\n"
            "node('chartPlot').clientWidth = 800; node('chartPlot').clientHeight = 300;\n"
            "node('chartTimeUnit').replaceChildren(\n"
            "  Object.assign(makeEl('option'), { value: '1ps', textContent: 'x 1ps' }),\n"
            "  Object.assign(makeEl('option'), { value: '1ns', textContent: 'x 1ns' }),\n"
            "  Object.assign(makeEl('option'), { value: 'custom', textContent: 'Custom…' })\n"
            ");\n"
            "node('chartTimeUnit').value = '1ns';\n"
            "node('chartFindOp').replaceChildren(Object.assign(makeEl('option'), { value: 'any', textContent: 'Any change' }));\n"
            "node('chartFindOp').value = 'any';\n"
            "const timers = []; let nextTimerId = 1;\n"
            "globalThis.setTimeout = (fn, ms) => { const id = nextTimerId++; timers.push({ id, fn, ms, active: true }); return id; };\n"
            "globalThis.clearTimeout = id => { const t = timers.find(item => item.id === id); if (t) t.active = false; };\n"
            "const flushAsync = async () => { for (const t of timers.splice(0)) { if (t.active) t.fn(); } for (let i = 0; i < 12; i++) await Promise.resolve(); };\n"
            "const fileCalls = []; let savedRcBlob = '';\n"
            "globalThis.fetchJson = async (url, opts = {}) => {\n"
            "  if (url.startsWith('/api/files')) {\n"
            "    const reqPath = decodeURIComponent((url.split('path=')[1] || '').split('&')[0] || '');\n"
            "    if (reqPath === 'D:/work/sub') {\n"
            "      return { path: 'D:/work/sub', parent: 'D:/work', shortcuts: [{ label: 'Workspace', path: 'D:/work', exists: true }], entries: [] };\n"
            "    }\n"
            "    return { path: 'D:/work', parent: 'D:/', shortcuts: [{ label: 'Workspace', path: 'D:/work', exists: true }], entries: [{ name: 'sub', path: 'D:/work/sub', directory: true }, { name: 'rtl.wave.rc', path: 'D:/work/rtl.wave.rc', directory: false }] };\n"
            "  }\n"
            "  if (url === '/api/chart/file') {\n"
            "    const body = JSON.parse(opts.body);\n"
            "    fileCalls.push(body);\n"
            "    if (body.action === 'save_rc') { savedRcBlob = body.content; return { path: body.path, bytes: body.content.length }; }\n"
            "    if (body.action === 'read_rc') return { path: body.path, content: savedRcBlob };\n"
            "    if (body.action === 'export_vcd') return { path: body.path, bytes: 128 };\n"
            "  }\n"
            "  return {};\n"
            "};\n"
            "globalThis.WORKSPACE_LAYOUT = 'charts';\n"
            "globalThis.STATE = { workspace_id: 'w1', archive: { enabled: true } };\n"
            "globalThis.AnalysisWorkspace = { openContext(){}, context(){} };\n"
            "globalThis.RunHistory = {\n"
            "  selection: null,\n"
            "  async query(opts) {\n"
            "    if (opts.view === 'chart_meta') {\n"
            "      return {\n"
            "        as_of_seq: 10, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },\n"
            "        views: [{\n"
            "          id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 2,\n"
            "          axes: ['time'], default_axis: 'time', domains: { time: [0, 100] },\n"
            "          axis: { label: 'Time', unit: 'ns' }, series: { mode: 'long', defaults: ['top.bus8'] },\n"
            "        }],\n"
            "      };\n"
            "    }\n"
            "    if (opts.view === 'chart_series') {\n"
            "      return { series: [\n"
            "        { id: 'top.bus8', label: 'bus8', path: ['top', 'bus8'], type: 'bus', bits: 8, radix: 'hex' },\n"
            "        { id: 'top.lock', label: 'lock', path: ['top', 'lock'], type: 'bit' },\n"
            "      ], complete: true, next_cursor: null };\n"
            "    }\n"
            "    if (opts.view === 'chart_window') {\n"
            "      return {\n"
            "        complete: true, scanned: 3, next_cursor: null, health: { closed_cleanly: true },\n"
            "        left: { 'top.bus8': { x: 0, flow_seq: 1, log_seq: 11, value: '16' } },\n"
            "        points: [\n"
            "          { series_id: 'top.bus8', x: 25, flow_seq: 2, log_seq: 22, value: '32' },\n"
            "          { series_id: 'top.bus8', x: 75, flow_seq: 3, log_seq: 33, value: '8' },\n"
            "        ],\n"
            "      };\n"
            "    }\n"
            "    return {};\n"
            "  },\n"
            "};\n"
            "globalThis.document = {\n"
            "  listeners: {}, getElementById: node, createElement: tag => makeEl(tag),\n"
            "  createDocumentFragment(){ return { _isFrag: true, children: [], append(...k){ this.children.push(...k); } }; },\n"
            "  addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },\n"
            "  dispatch(ev, e = {}){ const event = { preventDefault(){}, stopPropagation(){}, ...e, target: e.target || this }; for (const fn of this.listeners[ev] || []) fn(event); return event; },\n"
            "};\n"
            "globalThis.window = {};\n"
            "ChartWorkspace.refresh({ id: 'j1', attempt_id: 'att1', total_lines: 10, status: 'DONE' });\n"
            "await flushAsync();\n"
            "\n"
            "// 1. Value column shows edge transition and direction indicator\n"
            "const findInTree = (root, pred) => {\n"
            "  if (!root || typeof root !== 'object') return null;\n"
            "  if (pred(root)) return root;\n"
            "  for (const ch of root.children || []) { const r = findInTree(ch, pred); if (r) return r; }\n"
            "  return null;\n"
            "};\n"
            "const valCell = node('chartSignalRows').children[0].children[1];\n"
            "node('chartCursorAInput').value = '25';\n"
            "node('chartCursorAInput').dispatch('keydown', { key: 'Enter' });\n"
            "assert.equal(valCell.children[0].textContent, '0x10 → 0x20', 'Edge cursor shows spaced prev → next transition');\n"
            "node('chartCursorAInput').value = '50';\n"
            "node('chartCursorAInput').dispatch('keydown', { key: 'Enter' });\n"
            "assert.equal(valCell.children[0].textContent, '0x20');\n"
            "assert.equal(valCell.children[1].textContent, '↑', 'Held interval shows up arrow when raw value rose');\n"
            "node('chartCursorAInput').value = '90';\n"
            "node('chartCursorAInput').dispatch('keydown', { key: 'Enter' });\n"
            "assert.equal(valCell.children[0].textContent, '0x08');\n"
            "assert.equal(valCell.children[1].textContent, '↓', 'Held interval shows down arrow when raw value fell');\n"
            "\n"
            "// 2. Overview bucket duplicate (flow_seq, x) fix preserves edge transition\n"
            "const bucketSpans = ChartMath.waveSegments({\n"
            "  range: [0, 100], complete: true, bucket_count: 10,\n"
            "  left: { 'top.lock': { x: 0, flow_seq: 1, value: '0' } },\n"
            "  buckets: { 'top.lock': [{\n"
            "    transitions: 1, index: 1,\n"
            "    first: { x: 10, flow_seq: 2, value: '1' },\n"
            "    first_change: { x: 10, flow_seq: 2, value: '1' },\n"
            "    last: { x: 10, flow_seq: 2, value: '1' },\n"
            "  }] },\n"
            "}, { id: 'top.lock', type: 'bit' });\n"
            "const edgeInfo = ChartMath.valueAtCursor(bucketSpans, 10, { id: 'top.lock', type: 'bit' }, (_l, v) => String(v), 0.1);\n"
            "assert.equal(edgeInfo.transitionText, '0 → 1', 'Overview bucket deduplicates first === first_change so 0 → 1 is preserved');\n"
            "\n"
            "// 3. Custom time scale via #chartParamModal + Cancel restore + Escape handling\n"
            "node('chartTimeUnit').value = 'custom';\n"
            "node('chartTimeUnit').dispatch('change');\n"
            "assert.equal(node('chartParamModal').hidden, false);\n"
            "document.dispatch('keydown', { key: 'Escape' });\n"
            "assert.equal(node('chartParamModal').hidden, true, 'Escape closes Param Modal');\n"
            "assert.equal(node('chartTimeUnit').value, '1ns', 'Canceling Custom restores prior time unit');\n"
            "node('chartTimeUnit').value = 'custom';\n"
            "node('chartTimeUnit').dispatch('change');\n"
            "const scaleInp = findInTree(node('chartParamBody'), el => el.tagName === 'INPUT');\n"
            "scaleInp.value = '2.5ns';\n"
            "node('chartParamApply').dispatch('click');\n"
            "assert.equal(node('chartParamModal').hidden, true);\n"
            "assert.equal(node('chartTimeUnit').value, '2.5ns');\n"
            "node('chartCursorAInput').value = '25ns';\n"
            "node('chartCursorAInput').dispatch('keydown', { key: 'Enter' });\n"
            "assert.equal(node('chartCursorAInput').value, '10', '25ns / 2.5ns custom scale displays 10');\n"
            "\n"
            "// 4. Double-click auto-fit + ratio cycle + toolbar group button\n"
            "node('chartNameColResizer').dispatch('dblclick');\n"
            "assert.ok(node('chartsView').style.getPropertyValue('--wf-val-w').endsWith('px'));\n"
            "assert.ok(node('chartsView').style.getPropertyValue('--wf-pane-w').endsWith('px'));\n"
            "node('chartColRatioBtn').dispatch('click');\n"
            "assert.equal(node('chartsView').style.getPropertyValue('--wf-val-w'), '96px', '274 * 0.35 = 96px');\n"
            "node('chartAddDivider').dispatch('click');\n"
            "assert.equal(node('chartSignalRows').children[0].children[0].children[1].textContent, '= G1');\n"
            "\n"
            "// 5. Verdi .wave.rc Save (with Back/Forward nav) + Open .rc + Export .vcd\n"
            "node('chartSaveRcBtn').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileModal').hidden, false, '1st Save prompts with #chartFileModal');\n"
            "assert.equal(node('chartFileBack').disabled, true);\n"
            "assert.equal(node('chartFileForward').disabled, true);\n"
            "node('chartFileDir').value = 'D:/work/sub';\n"
            "node('chartFileGo').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileDir').value, 'D:/work/sub');\n"
            "assert.equal(node('chartFileBack').disabled, false, 'Navigating to sub folder enables Back');\n"
            "node('chartFileBack').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileDir').value, 'D:/work', 'Back returns to previous folder');\n"
            "assert.equal(node('chartFileForward').disabled, false, 'Going Back enables Forward');\n"
            "node('chartFileForward').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileDir').value, 'D:/work/sub', 'Forward returns to sub folder');\n"
            "node('chartFileBack').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileName').value, 'rtl.wave.rc');\n"
            "node('chartFileConfirm').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileModal').hidden, true);\n"
            "assert.equal(fileCalls.at(-1).action, 'save_rc');\n"
            "assert.match(savedRcBlob, /addGroup \"G1\"/);\n"
            "assert.match(savedRcBlob, /addSignal .* \\/top\\/bus8/);\n"
            "node('chartSaveRcBtn').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileModal').hidden, true, '2nd Save writes directly to currentRcPath without modal');\n"
            "assert.equal(fileCalls.length, 2);\n"
            "node('chartSaveRcBtn').dispatch('click', { shiftKey: true });\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileModal').hidden, false, 'Shift+Click Save As reopens modal');\n"
            "node('chartFileCancel').dispatch('click');\n"
            "assert.equal(node('chartFileModal').hidden, true);\n"
            "node('chartOpenRcBtn').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileModal').hidden, false);\n"
            "node('chartFileConfirm').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(fileCalls.at(-1).action, 'read_rc');\n"
            "node('chartExportVcdBtn').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(node('chartFileName').value, 'j1_rtl.vcd');\n"
            "node('chartFileConfirm').dispatch('click');\n"
            "await flushAsync();\n"
            "assert.equal(fileCalls.at(-1).action, 'export_vcd');\n"
            "})().catch(err => { console.error(err); process.exitCode = 1; });\n"
        ).encode(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
