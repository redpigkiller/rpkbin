"""Phase 4 regression tests for rpkbin/wave/charts.js (unit4b_wave_charts)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
WAVE_DIR = Path(__file__).parents[2] / "rpkbin" / "wave"
ICONS_JS = WAVE_DIR / "icons.js"
CHARTS_JS = WAVE_DIR / "charts.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def _run_charts(body: str, *, include_icons: bool = True, timeout: int = 12) -> None:
    prelude = r"""
const assert = require('node:assert/strict');
function makeCtx(kind, counters, segments) {
  const bump = () => { if (kind === 'base') counters.base++; else counters.overlay++; };
  let curX = 0, curY = 0;
  return {
    setTransform(){}, save(){}, restore(){}, beginPath(){}, rect(){}, clip(){},
    setLineDash(){}, arc(){}, closePath(){}, clearRect(){},
    measureText(s){ return { width: String(s).length * 6 }; },
    moveTo(x, y){ curX = x; curY = y; },
    lineTo(x, y){
      if (kind === 'base' && segments) segments.push([curX, curY, x, y]);
      curX = x; curY = y;
    },
    fillRect(){ bump(); }, strokeRect(){ bump(); }, stroke(){ bump(); }, fill(){ bump(); }, fillText(){ bump(); },
  };
}
const counters = { base: 0, overlay: 0 };
const baseSegments = [];
const elements = new Map();
function matchSingle(el, sel) {
  sel = sel.trim();
  if (!sel) return false;
  if (sel === '[role="treeitem"]') return el.attributes?.role === 'treeitem';
  if (sel === '[role="menu"]') return el.attributes?.role === 'menu';
  if (sel === '[role="menuitem"]') return el.attributes?.role === 'menuitem';
  if (sel === '[role="separator"]') return el.attributes?.role === 'separator';
  if (sel === '[role="scrollbar"]') return el.attributes?.role === 'scrollbar';
  if (sel === '[data-wave-icon]') return el.attributes?.['data-wave-icon'] !== undefined;
  if (sel === '[data-gs-resize]') return el.attributes?.['data-gs-resize'] !== undefined || el.dataset?.gsResize !== undefined;
  if (sel === '.wf-ctx-item:not(:disabled)') return el.classList?.contains('wf-ctx-item') && !el.disabled;
  if (sel === '[tabindex]') return el.tabIndex !== undefined && el.tabIndex !== -1 && el.attributes?.tabindex !== undefined;
  if (sel === 'a[href]') return el.tagName === 'A' && el.attributes?.href !== undefined;
  if (sel === 'svg') return el.tagName === 'SVG' || String(el.innerHTML || '').includes('<svg');
  if (sel.startsWith('#')) return el.id === sel.slice(1);
  if (sel.startsWith('.')) return el.classList?.contains(sel.slice(1));
  return el.tagName === sel.toUpperCase();
}
function matchesAny(el, selector) {
  return selector.split(',').some(part => matchSingle(el, part));
}
function isVisibleTree(el) {
  let cur = el;
  while (cur) {
    if (cur.hidden) return false;
    cur = cur.parentElement;
  }
  return true;
}
function makeEl(tag, id = '') {
  const classes = new Set();
  const styleVars = new Map();
  let _innerHTML = '';
  const obj = {
    id, tagName: tag.toUpperCase(), hidden: false, disabled: false, checked: false, value: '', textContent: '',
    title: '', scrollTop: 0, clientWidth: 800, clientHeight: 300, dataset: {}, attributes: {},
    children: [], listeners: {}, isConnected: true, parentElement: null,
    tabIndex: ['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY'].includes(tag.toUpperCase()) ? 0 : -1,
    get innerHTML(){ return _innerHTML; },
    set innerHTML(v){ _innerHTML = String(v ?? ''); },
    style: {
      setProperty(k, v){ styleVars.set(k, v); this[k] = v; },
      getPropertyValue(k){ return styleVars.get(k) || ''; },
    },
    get options(){ return this.children; }, selectedOptions: [],
    classList: {
      toggle(c, force){
        if (force === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); }
        else if (force) classes.add(c); else classes.delete(c);
        obj.className = [...classes].join(' ');
      },
      contains(c){ return classes.has(c); },
      add(...cs){ cs.forEach(c => classes.add(c)); obj.className = [...classes].join(' '); },
      remove(...cs){ cs.forEach(c => classes.delete(c)); obj.className = [...classes].join(' '); },
    },
    _className: '',
    get className(){ return this._className; },
    set className(v){
      this._className = String(v || '');
      classes.clear();
      for (const c of this._className.split(/\s+/).filter(Boolean)) classes.add(c);
    },
    setAttribute(k, v){
      const sv = String(v);
      this.attributes[k] = sv;
      if (k === 'tabindex') this.tabIndex = Number(sv);
      else this[k] = sv;
    },
    getAttribute(k){ return this.attributes[k] ?? null; },
    hasAttribute(k){ return Object.prototype.hasOwnProperty.call(this.attributes, k); },
    removeAttribute(k){ delete this.attributes[k]; delete this[k]; },
    addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },
    dispatch(ev, e = {}){
      const evt = {
        defaultPrevented: false,
        preventDefault(){ this.defaultPrevented = true; },
        stopPropagation(){},
        currentTarget: this,
        ...e,
        target: e.target || this,
      };
      for (const fn of this.listeners[ev] || []) fn(evt);
      return evt;
    },
    click(){ return this.dispatch('click'); },
    replaceChildren(...kids){
      this._replaceCount = (this._replaceCount || 0) + 1;
      for (const old of this.children) old.parentElement = null;
      this.children = [];
      this.append(...kids);
    },
    append(...kids){
      for (const k of kids) {
        if (k && k._isFrag) {
          for (const fk of k.children) { fk.parentElement = this; this.children.push(fk); }
        } else if (k && typeof k === 'object') {
          k.parentElement = this; this.children.push(k);
        }
      }
    },
    insertBefore(newNode, refNode){
      newNode.parentElement = this;
      const i = this.children.indexOf(refNode);
      if (i >= 0) this.children.splice(i, 0, newNode);
      else this.children.push(newNode);
    },
    closest(sel){
      if (!sel) return null;
      if (matchesAny(this, sel)) return this;
      return this.parentElement?.closest?.(sel) || null;
    },
    querySelectorAll(sel){
      const out = [];
      const walk = n => {
        for (const ch of n.children || []) {
          if (matchesAny(ch, sel)) out.push(ch);
          walk(ch);
        }
      };
      walk(this);
      return out;
    },
    querySelector(sel){
      if (sel === 'svg' && _innerHTML.includes('<svg')) return { tagName: 'SVG' };
      return this.querySelectorAll(sel)[0] || null;
    },
    getClientRects(){ return isVisibleTree(this) ? [{}] : []; },
    getBoundingClientRect(){
      if (this.id === 'chartSignalPane') return { left: 0, top: 0, width: 274, height: 300, right: 274, bottom: 300 };
      if (this.id === 'chartHierarchyPane') return { left: 0, top: 0, width: 250, height: 300, right: 250, bottom: 300 };
      if (this.classList?.contains('wf-col-val')) return { left: 170, top: 0, width: 104, height: 24, right: 274, bottom: 24 };
      return { left: 0, top: 0, right: 800, bottom: 300, width: 800, height: 300 };
    },
    focus(){ document.activeElement = this; },
    blur(){ if (document.activeElement === this) document.activeElement = document.body; },
    contains(other){
      if (!other) return false;
      if (other === this) return true;
      for (const c of this.children) if (c.contains?.(other)) return true;
      return false;
    },
    setPointerCapture(){}, releasePointerCapture(){}, scrollIntoView(){}, checkValidity(){ return true; },
    getContext(){ return makeCtx(id === 'chartOverlayCanvas' ? 'overlay' : 'base', counters, baseSegments); },
  };
  return obj;
}
function node(id){ return elements.get(id) ?? (elements.set(id, makeEl('div', id)), elements.get(id)); }
// Ensure key semantic tags match index.html
for (const bid of [
  'chartZoomIn', 'chartZoomOut', 'chartPanLeft', 'chartPanRight', 'chartResetWindow',
  'chartLastView', 'chartSignalsToggle', 'chartCloseSignals', 'chartSelectAllSignals',
  'chartAddSignals', 'chartResetSignals', 'chartScopeAll', 'chartScopeRecursive',
  'chartOpenRcBtn', 'chartSaveRcBtn', 'chartExportVcdBtn', 'chartNewData',
  'chartColRatioBtn', 'chartHScrollLeft', 'chartHScrollRight', 'chartParamClose',
  'chartParamCancel', 'chartParamApply', 'chartFileClose', 'chartFileCancel', 'chartFileConfirm',
  'chartFileBack', 'chartFileForward', 'chartFileUp', 'chartFileRefresh', 'chartFileGo',
  'chartFileLocalBtn', 'chartAddDivider', 'chartCutSignal', 'chartCopySignal', 'chartPasteSignal',
]) {
  elements.set(bid, makeEl('button', bid));
}
for (const iid of ['chartSignalSearch', 'chartSignalCase', 'chartSignalWord', 'chartSignalRegex', 'chartCursorAInput', 'chartCursorBInput', 'chartFileDir', 'chartFileName', 'chartRcFileInput', 'chartFindValue']) {
  elements.set(iid, makeEl('input', iid));
}
for (const sid of ['chartViewSelect', 'chartAxis', 'chartTimeUnit', 'chartFindOp']) {
  elements.set(sid, makeEl('select', sid));
}
node('chartSignalPane').append(makeEl('div', 'wfHead'));
node('wfHead').append(Object.assign(makeEl('div'), { className: 'wf-col-val' }));
node('chartSignals').append(node('chartSignalsDialog'));
node('chartSignalsDialog').append(
  node('chartResetSignals'), node('chartCloseSignals'), node('chartSignalSearch'),
  node('chartSignalCase'), node('chartSignalWord'), node('chartSignalRegex'),
  node('chartScopeAll'), node('chartHierarchyTree'), node('chartBrowserSplitter'),
  node('chartScopeRecursive'), node('chartSignalTree'), node('chartMoreSignals'),
  node('chartSelectAllSignals'), node('chartAddSignals'),
);
node('chartParamModal').append(node('chartParamDialog'));
node('chartParamDialog').append(
  node('chartParamClose'), node('chartParamBody'), node('chartParamCancel'), node('chartParamApply'),
);
node('chartFileModal').append(node('chartFileDialog'));
node('chartFileDialog').append(
  node('chartFileClose'), node('chartFileBack'), node('chartFileForward'), node('chartFileUp'),
  node('chartFileRefresh'), node('chartFileDir'), node('chartFileGo'), node('chartFileShortcuts'),
  node('chartFileList'), node('chartFileName'), node('chartFileLocalBtn'),
  node('chartFileCancel'), node('chartFileConfirm'),
);
node('chartTimeUnit').replaceChildren(
  Object.assign(makeEl('option'), { value: '1ps', textContent: 'x 1ps' }),
  Object.assign(makeEl('option'), { value: '1ns', textContent: 'x 1ns' }),
  Object.assign(makeEl('option'), { value: 'custom', textContent: 'Custom…' }),
);
node('chartTimeUnit').value = '1ns';
node('chartFindOp').replaceChildren(
  Object.assign(makeEl('option'), { value: 'any', textContent: 'Any' }),
  Object.assign(makeEl('option'), { value: 'eq', textContent: 'Eq' }),
);
node('chartFindOp').value = 'any';
node('chartPlot').clientWidth = 800; node('chartPlot').clientHeight = 300;
const timers = []; let nextTimerId = 1;
globalThis.setTimeout = (fn, ms) => { const id = nextTimerId++; timers.push({ id, fn, ms, active: true }); return id; };
globalThis.clearTimeout = id => { const t = timers.find(item => item.id === id); if (t) t.active = false; };
const flushAsync = async () => {
  for (let pass = 0; pass < 4; pass++) {
    for (const t of timers.splice(0)) { if (t.active) t.fn(); }
    for (let i = 0; i < 8; i++) await Promise.resolve();
  }
};
globalThis.requestAnimationFrame = fn => { fn(); return 1; };
globalThis.getComputedStyle = () => ({ visibility: 'visible' });
const storage = new Map();
globalThis.localStorage = { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, String(v)) };
globalThis.window = { innerWidth: 1200, innerHeight: 800, localStorage: globalThis.localStorage, addEventListener(){} };
globalThis.document = {
  body: makeEl('body', 'body'),
  getElementById: node, activeElement: null, hidden: false,
  listeners: {},
  addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },
  dispatch(ev, e = {}){
    const evt = { defaultPrevented: false, preventDefault(){ this.defaultPrevented = true; }, stopPropagation(){}, ...e, target: e.target || this };
    for (const fn of this.listeners[ev] || []) fn(evt);
    return evt;
  },
  createElement: tag => makeEl(tag),
  createDocumentFragment: () => ({ _isFrag: true, children: [], append(...k){ this.children.push(...k); } }),
  querySelector: () => null,
};
document.activeElement = document.body;
globalThis.WORKSPACE_LAYOUT = 'charts';
globalThis.STATE = { workspace_id: 'w1', archive: { enabled: true } };
globalThis.AnalysisWorkspace = { openContext(){}, context(){} };
"""
    icons_src = ICONS_JS.read_text(encoding="utf-8") if include_icons else ""
    charts_src = CHARTS_JS.read_text(encoding="utf-8")
    script = (
        prelude
        + "\n"
        + icons_src
        + "\n"
        + charts_src
        + f"\n(async () => {{\n{body}\n}})().catch(e => {{ console.error(e); process.exitCode = 1; }});\n"
    )
    res = subprocess.run(
        [NODE, "-"],
        input=script.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    assert res.returncode == 0, (res.stdout + res.stderr).decode("utf-8", errors="replace")


def test_wcf01_wcf04_wcf06_wcf10_wcf11_wcf12_core_charts() -> None:
    _run_charts(r"""
const windowRequests = [];
let deferWindow = false, resolveDeferred = null;
const allSignals = [
  { id: 'top.bus8', label: 'bus8', path: ['top', 'bus8'], type: 'bus', bits: 8, signed: false },
  { id: 'top.sig2', label: 'sig2', path: ['top', 'sig2'], type: 'bit' },
];
let metaSeq = 10;
let domainMax = 1000;
globalThis.RunHistory = {
  selection: null,
  async query(opts) {
    if (opts.view === 'chart_meta') {
      return {
        as_of_seq: metaSeq, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },
        views: [{
          id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 2,
          axes: ['time'], default_axis: 'time', domains: { time: [0, domainMax] },
          axis: { label: 'Time', unit: 'ns' }, series: { mode: 'long', defaults: ['top.bus8'] }, options: { radix: 'hex' },
        }],
      };
    }
    if (opts.view === 'chart_series') {
      return { series: allSignals, complete: true, next_cursor: null };
    }
    if (opts.view === 'chart_window') {
      windowRequests.push(opts);
      if (deferWindow) await new Promise(r => { resolveDeferred = r; });
      return {
        view: 'rtl', axis: 'time', range: [opts.lo, opts.hi], resolution: 'raw',
        left: { 'top.bus8': { x: 0, flow_seq: 1, value: '255' } },
        points: [
          { x: 100, flow_seq: 2, value: '1', series_id: 'top.bus8' },
          { x: 400, flow_seq: 3, value: '2', series_id: 'top.bus8' },
        ],
        complete: true, coverage: [domainMax, domainMax, 0],
      };
    }
    return {};
  },
};

ChartWorkspace.refresh({ id: 'j1', attempt_id: 'a1' });
await flushAsync();

// WCF-06: Signed radix override updates lastDir in valueAtCursor & signal pane value cell
const busRow = node('chartSignalRows').children[0];
busRow.dispatch('contextmenu', { clientX: 20, clientY: 20 });
const signedBtn = node('chartContextMenu').querySelectorAll('button').find(b => b.textContent === 'Signed Decimal');
assert.ok(signedBtn);
signedBtn.click();
node('chartCursorAInput').value = '200';
node('chartCursorAInput').dispatch('keydown', { key: 'Enter' });
const valCell = node('chartSignalRows').children[0].children[1];
assert.equal(valCell.children[0].textContent, '1');
assert.equal(valCell.children[1].textContent, '↑', 'Unsigned 255 (-1) -> 1 must show upward arrow under Signed radix');

// WCF-11: Repeated pointermove over same interval must not mutate valEl via replaceChildren
const beforeMoves = valCell._replaceCount || 0;
node('chartCanvas').dispatch('pointermove', { clientX: 200, clientY: 40 });
node('chartCanvas').dispatch('pointermove', { clientX: 205, clientY: 40 });
assert.equal(valCell._replaceCount || 0, beforeMoves, 'Unchanged cursor/hover value must skip redundant replaceChildren');

// WCF-01: Alt+Drag pan & hThumb drag (immediate: false) repaint base canvas smoothly;
// immediate button pan still waits for response
document.dispatch('keydown', { key: 'Z' });
await flushAsync();
deferWindow = true;
const baseBeforeBtnPan = counters.base;
node('chartPanRight').click();
await Promise.resolve();
assert.equal(counters.base, baseBeforeBtnPan, 'Immediate button pan must still wait for new window');
deferWindow = false;
resolveDeferred();
await flushAsync();

const baseBeforeCanvasPan = counters.base;
node('chartCanvas').dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 400, clientY: 60, altKey: true });
node('chartCanvas').dispatch('pointermove', { pointerId: 1, clientX: 350, clientY: 60 });
assert.ok(counters.base > baseBeforeCanvasPan, 'Alt+Drag pan must repaint base waveform smoothly');
node('chartCanvas').dispatch('pointerup', { pointerId: 1, clientX: 350, clientY: 60 });
await flushAsync();

node('chartHScroll').getBoundingClientRect = () => ({ left: 0, top: 0, width: 500, height: 14 });
const baseBeforeThumb = counters.base;
node('chartHScrollThumb').dispatch('pointerdown', { button: 0, pointerId: 2, clientX: 50 });
node('chartHScrollThumb').dispatch('pointermove', { pointerId: 2, clientX: 80 });
assert.ok(counters.base > baseBeforeThumb, 'Scrollbar thumb drag must repaint base waveform');
node('chartHScrollThumb').dispatch('pointerup', { pointerId: 2, clientX: 80 });
await flushAsync();

// WCF-10: 5 rapid Ctrl+Wheel zoom ticks debounce to 1 window request and collapse viewHistory
const winBeforeWheel = windowRequests.length;
let fakeNow = 10000;
Date.now = () => fakeNow;
for (let i = 0; i < 5; i++) {
  fakeNow += 20;
  node('chartCanvas').dispatch('wheel', { cancelable: true, ctrlKey: true, deltaY: -120, deltaMode: 0, clientX: 300, clientY: 60 });
}
assert.equal(windowRequests.length, winBeforeWheel, 'Rapid wheel ticks must debounce instead of firing immediately');
await flushAsync();
assert.equal(windowRequests.length, winBeforeWheel + 1, '5 rapid wheel ticks must coalesce into 1 window query');
const postWheelWin = windowRequests.at(-1);
// 1st l restores pre-wheel range directly (history collapsed across wheel burst)
document.dispatch('keydown', { key: 'l' });
await flushAsync();
assert.ok(windowRequests.at(-1).hi - windowRequests.at(-1).lo > postWheelWin.hi - postWheelWin.lo);

// WCF-04: Shift+L & #chartNewData preserve cursors, zoom range, displayTimeUnit, viewHistory, and currentRcPath
node('chartCursorAInput').value = '150';
node('chartCursorAInput').dispatch('keydown', { key: 'Enter' });
node('chartCursorBInput').value = '300';
node('chartCursorBInput').dispatch('keydown', { key: 'Enter' });
node('chartTimeUnit').value = '1ps';
node('chartTimeUnit').dispatch('change');
document.dispatch('keydown', { key: 'Z' });
await flushAsync();
const zoomedReq = windowRequests.at(-1);
metaSeq = 20; domainMax = 1200;
document.dispatch('keydown', { key: 'L' });
await flushAsync();
assert.equal(node('chartCursorAInput').value, '150000', 'Shift+L must preserve Cursor A and displayTimeUnit=1ps');
assert.equal(node('chartCursorBInput').value, '300000', 'Shift+L must preserve Cursor B');
assert.equal(node('chartLastView').disabled, false, 'Shift+L must preserve viewHistory');
assert.ok(Math.abs(windowRequests.at(-1).lo - zoomedReq.lo) < 1e-6 && Math.abs(windowRequests.at(-1).hi - zoomedReq.hi) < 1e-6,
  'Shift+L must preserve zoomed range');

// WCF-12: waveSegments per-page cache invalidates on more=true push, and firstSpanIndex binary searches
const pageData = {
  range: [0, 100], complete: true, left: {},
  points: [
    { series_id: 'top.bus8', x: 10, flow_seq: 1, value: '1' },
    { series_id: 'top.bus8', x: 20, flow_seq: 2, value: '2' },
    { series_id: 'top.sig2', x: 30, flow_seq: 3, value: 1 },
  ],
};
assert.equal(ChartMath.waveSegments(pageData, { id: 'top.bus8', type: 'bus', bits: 8 }).length, 3);
pageData.points.push({ series_id: 'top.bus8', x: 50, flow_seq: 4, value: '3' });
assert.equal(ChartMath.waveSegments(pageData, { id: 'top.bus8', type: 'bus', bits: 8 }).length, 4);
const spansTest = [
  { lo: 0, hi: 10, value: '0' },
  { lo: 10, hi: 25, value: '1' },
  { lo: 25, hi: 50, value: '2' },
];
assert.equal(ChartMath.firstSpanIndex(spansTest, 15), 1);
assert.equal(ChartMath.firstSpanIndex(spansTest, 26), 2);
""")


def test_wcf02_wcf03_wcf05_directory_clipboard_and_rc() -> None:
    _run_charts(r"""
const allSignals = Array.from({ length: 150 }, (_, i) => ({
  id: i < 50 ? `top.a.s${i}` : `top.b.s${i}`,
  label: `s${i}`,
  path: i < 50 ? ['top', 'a', `s${i}`] : ['top', 'b', `s${i}`],
  type: 'bus', bits: 8, signed: false,
}));
allSignals[0] = { id: 'top.a.def0', label: 'def0', path: ['top', 'a', 'def0'], type: 'bus', bits: 8 };
const seriesCalls = [];
let mockRcContent = 'invalid_not_json_or_rc';
globalThis.fetchJson = async (url, opts = {}) => {
  if (url.startsWith('/api/files')) {
    return { path: '/tmp', parent: '/', shortcuts: [], entries: [] };
  }
  const body = JSON.parse(opts.body);
  if (body.action === 'read_rc') return { path: body.path, content: mockRcContent };
  return { path: body.path || '/tmp/a.wave.rc', bytes: 10 };
};
globalThis.RunHistory = {
  selection: null,
  async query(opts) {
    if (opts.view === 'chart_meta') {
      return {
        as_of_seq: 1, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },
        views: [{
          id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 150,
          axes: ['time'], default_axis: 'time', domains: { time: [0, 100] },
          axis: { label: 'Time', unit: 'ns' }, series: { mode: 'long', defaults: ['top.a.def0'] },
        }],
      };
    }
    if (opts.view === 'chart_series') {
      seriesCalls.push(opts);
      if (opts.ids) {
        const want = new Set(JSON.parse(opts.ids));
        return { series: allSignals.filter(s => want.has(s.id)), complete: true, next_cursor: null };
      }
      const pref = opts.prefix ? JSON.parse(opts.prefix) : [];
      const q = (opts.query || '').toLowerCase();
      const filtered = allSignals.filter(s => pref.every((p, i) => s.path[i] === p) && (!q || s.id.toLowerCase().includes(q)));
      const start = Number(opts.cursor || 0);
      const end = start + 100;
      return { series: filtered.slice(start, end), complete: end >= filtered.length, next_cursor: end < filtered.length ? String(end) : null };
    }
    return { view: 'rtl', axis: 'time', range: [0, 100], resolution: 'raw', left: {}, points: [], complete: true, coverage: [100, 100, 0] };
  },
};

ChartWorkspace.refresh({ id: 'j1', attempt_id: 'a1' });
await flushAsync();

// WCF-02: With directoryCursor != null, Regex search auto-pages without wiping directory with query=""
node('chartSignalsToggle').click();
node('chartSignalRegex').checked = true;
node('chartSignalSearch').value = '^top\\.b\\.s149$';
node('chartSignalRegex').dispatch('change');
await flushAsync();
assert.ok(node('chartSignalTree').children.some(c => c.querySelector('.wf-sig-name')?.textContent.includes('s149')),
  'Regex search must auto-page to find s149 beyond page 1');

// Invalid regex sets error-text + role=alert on #chartSignalStatus (WCF-G10)
node('chartSignalSearch').value = '[unclosed';
node('chartSignalSearch').dispatch('input');
await flushAsync();
assert.ok(node('chartSignalStatus').classList.contains('error-text'));
assert.equal(node('chartSignalStatus').getAttribute('role'), 'alert');
node('chartSignalRegex').checked = false;
node('chartSignalSearch').value = '';
node('chartSignalRegex').dispatch('change');
await flushAsync();
node('chartMoreSignals').click();
await flushAsync();

// WCF-03: Add non-default signal s140, cut it, browse another scope, paste -> restored!
node('chartSignalTree').children.find(c => c.querySelector('.wf-sig-name')?.textContent.includes('s140')).dispatch('dblclick');
await flushAsync();
assert.equal(node('chartSignalRows').children.length, 2);
node('chartSignalRows').children[1].click();
document.dispatch('keydown', { key: 'x', ctrlKey: true });
await flushAsync();
assert.equal(node('chartSignalRows').children.length, 1);
const scopeA = node('chartHierarchyTree').querySelectorAll('[role="treeitem"]').find(b => b.dataset.scopeName === 'a');
assert.ok(scopeA);
scopeA.click();
await flushAsync();
document.dispatch('keydown', { key: 'v', ctrlKey: true });
await flushAsync();
assert.equal(node('chartSignalRows').children.length, 2, 'Paste after scope browse must restore cut signal');

// WCF-05 (1): Invalid .rc open must NOT overwrite error status with Loaded
node('chartOpenRcBtn').click();
await flushAsync();
node('chartFileDir').value = '/tmp';
node('chartFileName').value = 'bad.rc';
await node('chartFileConfirm').click();
await flushAsync();
assert.doesNotMatch(node('chartStatus').textContent, /^Loaded /);

// WCF-05 (2): Save a.wave.rc -> local import -> Save must open modal instead of overwriting a.wave.rc
node('chartSaveRcBtn').click();
await flushAsync();
node('chartFileDir').value = '/tmp';
node('chartFileName').value = 'a.wave.rc';
await node('chartFileConfirm').click();
await flushAsync();
assert.equal(node('chartFileModal').hidden, true);
node('chartRcFileInput').files = [{ text: async () => 'addGroup "foo \\"bar\\" baz"\naddSignal /top/a/def0\n' }];
await node('chartRcFileInput').dispatch('change');
await flushAsync();
// WCF-05 (4): Escaped quotes in addGroup parsed intact
assert.equal(node('chartSignalRows').children[0].children[0].children[1].textContent, '= foo "bar" baz');
// Clicking Save now must reopen Save modal (currentRcPath cleared)
await node('chartSaveRcBtn').click();
await flushAsync();
assert.equal(node('chartFileModal').hidden, false, 'Save after local import must prompt for path');
node('chartFileCancel').click();

// WCF-05 (3): JSON .rc with 70 signals is capped at MAX_LANES (64)
mockRcContent = JSON.stringify({
  selected: Array.from({ length: 70 }, (_, i) => i < 50 ? `top.a.s${i}` : `top.b.s${i}`),
});
node('chartOpenRcBtn').click();
await flushAsync();
node('chartFileDir').value = '/tmp';
node('chartFileName').value = 'many.rc';
await node('chartFileConfirm').click();
await flushAsync();
assert.ok(node('chartSignalRows').children.length <= 64, 'JSON .rc must enforce 64-lane cap');
""")


def test_wcf_g01_g02_g03_g05_g07_g09_g10_g11_accessibility_and_icons() -> None:
    _run_charts(r"""
let viewKind = 'waveform';
globalThis.RunHistory = {
  selection: null,
  async query(opts) {
    if (opts.view === 'chart_meta') {
      return {
        as_of_seq: 1, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },
        views: [
          {
            id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 2,
            axes: ['time'], default_axis: 'time', domains: { time: [0, 100] },
            axis: { label: 'Time', unit: 'ns' },
            series: { mode: 'long', defaults: [{ type: 'group', id: 'g1', label: 'Grp', collapsed: false, signals: ['top.sub.b'] }] },
          },
          {
            id: 'hist', kind: 'histogram', title: 'Hist', series_count: 2,
            axes: ['time'], default_axis: 'time', domains: { time: [0, 100] },
            axis: { label: 'Time', unit: 'ns' },
            series: { mode: 'long', defaults: ['top.a'] },
          },
        ],
      };
    }
    if (opts.view === 'chart_series') {
      return { series: [
        { id: 'top.a', label: 'a', path: ['top', 'a'], type: 'bus', bits: 8 },
        { id: 'top.sub.b', label: 'b', path: ['top', 'sub', 'b'], type: 'bit' },
      ], complete: true, next_cursor: null };
    }
    if (opts.view === 'chart_window') {
      return { view: viewKind, axis: 'time', range: [0, 100], resolution: 'raw', left: {}, points: [], complete: true, coverage: [100, 100, 0] };
    }
    return {};
  },
};
globalThis.fetchJson = async (url) => {
  if (url.startsWith('/api/files')) {
    return {
      path: '/work', parent: '/', shortcuts: [],
      entries: [
        { name: 'sub', path: '/work/sub', directory: true },
        { name: 'rtl.wave.rc', path: '/work/rtl.wave.rc', directory: false },
      ],
    };
  }
  return {};
};

ChartWorkspace.refresh({ id: 'j1', attempt_id: 'a1' });
await flushAsync();

// WCF-G01: semantic button caret, Enter/Space collapse, Shift+F10 context menu, Alt+Arrow row height
const grpRow = node('chartSignalRows').children[0];
const grpCaret = grpRow.children[0].children[0];
assert.equal(grpCaret.tagName, 'BUTTON');
assert.equal(grpCaret.getAttribute('aria-expanded'), 'true');
grpRow.click();
node('chartSignalRows').dispatch('keydown', { key: ' ' });
assert.equal(node('chartSignalRows').children.length, 1, 'Space must collapse group');
node('chartSignalRows').dispatch('keydown', { key: 'Enter' });
assert.equal(node('chartSignalRows').children.length, 2, 'Enter must expand group');

const sigRow = node('chartSignalRows').children[1];
sigRow.click();
node('chartSignalRows').focus();
node('chartSignalRows').dispatch('keydown', { key: 'ArrowDown', altKey: true });
assert.equal(node('chartSignalRows').children[1].style.height, '28px', 'Alt+ArrowDown increases row height');
node('chartSignalRows').dispatch('keydown', { key: 'Home', altKey: true });
assert.equal(node('chartSignalRows').children[1].style.height, '23px', 'Alt+Home resets row height');

// WCF-G05: Context menu focus, aria-haspopup/expanded, ArrowUp/Down/Right/Left, vertical clamp, keydown guard
node('chartSignalRows').dispatch('keydown', { key: 'F10', shiftKey: true });
const menu = node('chartContextMenu');
assert.equal(menu.hidden, false);
assert.ok(menu.contains(document.activeElement), 'Opening context menu focuses first enabled item');
const subWrap = menu.querySelectorAll('.wf-ctx-subwrap')[0];
const subTrigger = subWrap.children[0];
assert.equal(subTrigger.getAttribute('aria-haspopup'), 'menu');
assert.equal(subTrigger.getAttribute('aria-expanded'), 'false');
// Move focus to subTrigger and press ArrowRight to open submenu
subTrigger.focus();
subTrigger.getBoundingClientRect = () => ({ left: 10, top: 760, width: 180, height: 24, right: 190, bottom: 784 });
subWrap.children[1].getBoundingClientRect = () => ({ left: 190, top: 756, width: 185, height: 150, right: 375, bottom: 906 });
menu.dispatch('keydown', { key: 'ArrowRight', target: subTrigger });
assert.equal(subWrap.classList.contains('open'), true);
assert.equal(subTrigger.getAttribute('aria-expanded'), 'true');
assert.ok(menu.querySelector('.wf-ctx-submenu').contains(document.activeElement), 'ArrowRight focuses first submenu item');
assert.ok(parseFloat(subWrap.children[1].style.top) < -4, 'Submenu near bottom must clamp vertically against window.innerHeight');
menu.dispatch('keydown', { key: 'ArrowLeft', target: document.activeElement });
assert.equal(document.activeElement, subTrigger, 'ArrowLeft returns focus to trigger');
// HandleWaveKeydown ignores Delete while menu is open
document.dispatch('keydown', { key: 'Delete', target: node('chartSignalRows') });
assert.equal(node('chartSignalRows').children.length, 2, 'Delete ignored while context menu is open');
document.dispatch('keydown', { key: 'Escape' });
assert.equal(menu.hidden, true);
assert.equal(document.activeElement, node('chartSignalRows'), 'Escape restores focus to context menu opener');

// WCF-G10: Delete/Arrows on toolbar button must NOT remove signal or step edge
node('chartZoomIn').focus();
document.dispatch('keydown', { key: 'Delete', target: node('chartZoomIn') });
assert.equal(node('chartSignalRows').children.length, 2, 'Delete on toolbar button must not remove signal');
// Delete on #chartSignalRows removes selected signal
node('chartSignalRows').focus();
document.dispatch('keydown', { key: 'Delete', target: node('chartSignalRows') });
assert.equal(node('chartSignalRows').children.length, 1);

// WCF-G02 & WCF-G03: #chartHierarchyTree role=treeitem navigation + #chartSignals Tab trap & focus restore
node('chartSignalsToggle').focus();
node('chartSignalsToggle').click();
assert.equal(node('chartSignals').hidden, false);
const topNode = node('chartHierarchyTree').querySelectorAll('[role="treeitem"]')[0];
assert.equal(topNode.getAttribute('aria-expanded'), 'true');
assert.ok(topNode.getAttribute('aria-selected') !== null);
assert.equal(topNode.querySelector('.wf-scope-caret').listeners?.click, undefined, 'Caret span must not carry interactive click listener');
topNode.focus();
node('chartHierarchyTree').dispatch('keydown', { key: 'ArrowLeft', target: topNode });
const collapsedTop = node('chartHierarchyTree').querySelectorAll('[role="treeitem"]')[0];
assert.equal(collapsedTop.getAttribute('aria-expanded'), 'false');
assert.equal(document.activeElement, collapsedTop, 'Focus remains on collapsed treeitem');
node('chartHierarchyTree').dispatch('keydown', { key: 'ArrowRight', target: collapsedTop });
assert.equal(node('chartHierarchyTree').querySelectorAll('[role="treeitem"]')[0].getAttribute('aria-expanded'), 'true');
node('chartHierarchyTree').dispatch('keydown', { key: 'ArrowDown', target: document.activeElement });
assert.equal(document.activeElement.dataset.scopeName, 'sub');

// Tab trap inside #chartSignals
node('chartAddSignals').disabled = false;
node('chartAddSignals').focus();
const tabEvt = document.dispatch('keydown', { key: 'Tab', target: node('chartAddSignals') });
assert.equal(tabEvt.defaultPrevented, true);
assert.ok(node('chartSignals').contains(document.activeElement), 'Tab stays trapped inside #chartSignals');
document.dispatch('keydown', { key: 'Escape' });
assert.equal(node('chartSignals').hidden, true);
assert.equal(document.activeElement, node('chartSignalsToggle'), 'Closing #chartSignals restores focus to #chartSignalsToggle');

// WCF-G03 & WCF-G10: #chartParamModal Tab trap, focus restore to #chartTimeUnit, error-text + role=alert
node('chartTimeUnit').focus();
node('chartTimeUnit').value = 'custom';
node('chartTimeUnit').dispatch('change');
assert.equal(node('chartParamModal').hidden, false);
const scaleInp = node('chartParamBody').querySelector('input');
scaleInp.value = 'bogus';
node('chartParamApply').click();
const errEl = node('chartParamBody').querySelector('.wf-param-err');
assert.ok(errEl.classList.contains('error-text'));
assert.equal(errEl.getAttribute('role'), 'alert');
assert.equal(scaleInp.getAttribute('aria-invalid'), 'true');
node('chartParamCancel').focus();
document.dispatch('keydown', { key: 'Tab', target: node('chartParamCancel') });
assert.ok(node('chartParamModal').contains(document.activeElement), 'Tab stays trapped inside #chartParamModal');
document.dispatch('keydown', { key: 'Escape' });
assert.equal(node('chartParamModal').hidden, true);
assert.equal(document.activeElement, node('chartTimeUnit'), 'Closing #chartParamModal restores focus to #chartTimeUnit');

// WCF-G03 & WCF-G09 part 1: #chartFileModal Tab trap, focus restore, SVG icons (no emoji), ArrowUp/Down/Enter
node('chartOpenRcBtn').focus();
await node('chartOpenRcBtn').click();
await flushAsync();
assert.equal(node('chartFileModal').hidden, false);
const fileRows = node('chartFileList').children;
assert.equal(fileRows.length, 2);
assert.equal(fileRows[0].querySelector('.wf-file-name')?.textContent, 'sub');
assert.equal(fileRows[1].querySelector('.wf-file-name')?.textContent, 'rtl.wave.rc');
assert.ok(fileRows[0].querySelector('svg'), 'Folder row must render SVG icon');
node('chartFileList').focus();
node('chartFileList').dispatch('keydown', { key: 'ArrowDown', target: node('chartFileList') });
assert.equal(fileRows[0].classList.contains('selected'), true);
node('chartFileList').dispatch('keydown', { key: 'ArrowDown', target: fileRows[0] });
assert.equal(fileRows[1].classList.contains('selected'), true);
assert.equal(node('chartFileName').value, 'rtl.wave.rc');
node('chartFileClose').click();
assert.equal(document.activeElement, node('chartOpenRcBtn'), 'Closing #chartFileModal restores focus to opener');

// WCF-G07: Splitter keyboard ArrowLeft/ArrowRight/Home handlers + Shift+ArrowRight timeline pan
for (const sid of ['chartBrowserSplitter', 'chartSignalPaneResizer', 'chartNameColResizer']) {
  const s = node(sid);
  assert.equal(s.getAttribute('role'), 'separator');
  assert.equal(s.tabIndex, 0);
}
node('chartSignalPaneResizer').dispatch('keydown', { key: 'ArrowRight' });
assert.equal(node('chartsView').style.getPropertyValue('--wf-pane-w'), '290px');
node('chartSignalPaneResizer').dispatch('keydown', { key: 'Home' });
assert.equal(node('chartsView').style.getPropertyValue('--wf-pane-w'), '274px');
node('chartNameColResizer').dispatch('keydown', { key: 'ArrowRight' });
assert.equal(node('chartsView').style.getPropertyValue('--wf-val-w'), '88px');
node('chartBrowserSplitter').dispatch('keydown', { key: 'ArrowLeft' });
assert.equal(node('chartBrowserSplit').style.getPropertyValue('--wf-hier-w'), '234px');

// WCF-G11: Histogram mode hides and disables #chartSelectAllSignals
viewKind = 'histogram';
node('chartViewSelect').value = 'hist';
node('chartViewSelect').dispatch('change');
await flushAsync();
assert.equal(node('chartSelectAllSignals').hidden, true);
assert.equal(node('chartSelectAllSignals').disabled, true);
""")


def test_charts_js_avoids_emoji_and_unicode_carets() -> None:
    src = CHARTS_JS.read_text(encoding="utf-8")
    for ch in ("📁", "📄", "▸", "▾", "⇔", "◀", "▶"):
        assert ch not in src, f"Unexpected emoji/unicode glyph {ch!r} in charts.js"
