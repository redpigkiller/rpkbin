"""Phase 4 regression checks for Wave frontend modules and UI contracts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


WAVE_DIR = Path(__file__).parents[2] / "rpkbin" / "wave"
STATIC_DIR = WAVE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_CSS = STATIC_DIR / "app.css"
APP_JS = STATIC_DIR / "app.js"
ICONS_JS = WAVE_DIR / "icons.js"
HISTORY_JS = WAVE_DIR / "history.js"
READING_JS = WAVE_DIR / "reading.js"
JOBS_JS = WAVE_DIR / "jobs.js"
ANALYSIS_JS = WAVE_DIR / "analysis.js"
FLOW_JS = WAVE_DIR / "flow.js"
CHARTS_JS = WAVE_DIR / "charts.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def _run_node(script: str, timeout: int = 12) -> None:
    result = subprocess.run(
        [NODE, "-"],
        input=script.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode(
        "utf-8", errors="replace"
    )


def _strip_app_bootstrap(source: str) -> str:
    stripped, removed = re.subn(
        r"\nsetupMonitorLayout\(\);\s*\npollState\(\);\s*$", "\n", source
    )
    assert removed == 1, "Failed to strip app.js auto-startup calls"
    return stripped


def _run_app_checks(checks: str) -> None:
    setup = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const nodes = new Map();
const docListeners = {};
const storage = new Map();
const feedback = [];
const calls = [];
function makeNode(id, tag = 'DIV') {
  const classes = new Set();
  const children = [];
  const elObj = {
    id, tagName: tag, hidden: false, disabled: false, open: false,
    checked: false, value: '', textContent: '', innerHTML: '', placeholder: '',
    title: '', dataset: {}, attrs: {}, listeners: {},
    style: { setProperty(){}, getPropertyValue(){ return ''; } },
    scrollTop: 0, scrollHeight: 400, clientHeight: 200,
    isConnected: true,
    classList: {
      add(...cs){ cs.forEach(c => classes.add(c)); },
      remove(...cs){ cs.forEach(c => classes.delete(c)); },
      contains(c){ return classes.has(c); },
      toggle(c, force){
        if (force === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); }
        else if (force) classes.add(c); else classes.delete(c);
      },
    },
    getClientRects(){ return [{}]; },
    getBoundingClientRect(){ return { left: 0, top: 0, right: 800, bottom: 300, width: 800, height: 300 }; },
    querySelectorAll(){ return []; },
    querySelector(){ return null; },
    contains(other){ return other === elObj || children.includes(other); },
    closest(sel){
      if (!sel) return null;
      for (const part of sel.split(',').map(s => s.trim())) {
        if (part.startsWith('#') && elObj.id === part.slice(1)) return elObj;
        if (part.startsWith('.') && classes.has(part.slice(1))) return elObj;
      }
      return elObj.parentElement?.closest?.(sel) || null;
    },
    replaceChildren(...kids){ children.length = 0; children.push(...kids); elObj.innerHTML = ''; },
    appendChild(ch){ ch.parentElement = elObj; children.push(ch); return ch; },
    setAttribute(k, v){ elObj[k] = String(v); elObj.attrs[k] = String(v); },
    removeAttribute(k){ delete elObj[k]; delete elObj.attrs[k]; },
    getAttribute(k){ return elObj.attrs[k]; },
    addEventListener(ev, fn){ (elObj.listeners[ev] ||= []).push(fn); },
    focus(){ elObj.focused = true; document.activeElement = elObj; },
    scrollIntoView(){ elObj.scrolledIntoView = true; },
    setSelectionRange(s, e){ elObj.selectionStart = s; elObj.selectionEnd = e; },
  };
  return elObj;
}
function el(id) {
  if (!nodes.has(id)) nodes.set(id, makeNode(id));
  return nodes.get(id);
}
const document = {
  body: makeNode('body', 'BODY'),
  documentElement: makeNode('html', 'HTML'),
  activeElement: null,
  getElementById: el,
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: tag => makeNode('dyn_' + tag, tag.toUpperCase()),
  addEventListener(ev, fn, cap) { (docListeners[ev] ||= []).push(fn); },
};
const window = {
  localStorage: {
    getItem: k => storage.get(k) ?? null,
    setItem: (k, v) => storage.set(k, String(v)),
  },
  innerWidth: 1280,
  innerHeight: 800,
  addEventListener(){},
};
const getComputedStyle = () => ({ visibility: 'visible' });
const ctx = vm.createContext({
  assert, document, window, navigator: {}, getComputedStyle,
  console, process, Map, Set, URL, URLSearchParams, AbortController,
  requestAnimationFrame: fn => fn(),
  performance: { now: () => 1000 },
  setTimeout, clearTimeout,
});
vm.runInContext(ICONS_SRC, ctx);
vm.runInContext(JOBS_SRC, ctx);
vm.runInContext(APP_SRC, ctx);
ctx.el = el;
ctx.makeNode = makeNode;
ctx.nodes = nodes;
ctx.docListeners = docListeners;
ctx.feedback = feedback;
ctx.calls = calls;
ctx.recordFeedback = (...args) => feedback.push(args);
ctx.fetchState = async () => {};
ctx.openModal = (id) => { el(id).hidden = false; };
ctx.closeModal = (id) => { el(id).hidden = true; };
ctx.ReadingLayout = {
  initialize(){}, register(){}, autoFitColumn(){}, autoFitAll(){}, autoFitInitial(){},
  openColumnMenu(){}, closeColumnMenu(){}, columns: c => c || [], order: c => c || [],
  isVisible: () => true, isResizing: () => false, isAnyDragging: () => false,
  context(){}, hidden: () => [], width: () => undefined,
};
vm.runInContext(CHECKS_SRC, ctx);
"""
    checks_wrapped = (
        "(async () => {\n"
        + checks
        + "\n})().catch(e => { console.error(e); process.exitCode = 1; });"
    )
    script = (
        setup.replace("ICONS_SRC", json.dumps(ICONS_JS.read_text(encoding="utf-8")))
        .replace("JOBS_SRC", json.dumps(JOBS_JS.read_text(encoding="utf-8")))
        .replace(
            "APP_SRC",
            json.dumps(_strip_app_bootstrap(APP_JS.read_text(encoding="utf-8"))),
        )
        .replace("CHECKS_SRC", json.dumps(checks_wrapped))
    )
    _run_node(script)


class _TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.help_tips: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if "id" in attr_map and attr_map["id"]:
            self.by_id[attr_map["id"]] = (tag, attr_map)
        cls = attr_map.get("class") or ""
        if "help-tip" in cls.split():
            self.help_tips.append((tag, attr_map))


# ---------------------------------------------------------------------------
# WF-01 .. WF-08 + WF-G01 .. WF-G05 (S1 regressions)
# ---------------------------------------------------------------------------


def test_wf01_resources_panel_errors_stay_inline_and_open() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    parser = _TagCollector()
    parser.feed(html)
    _, apply_attrs = parser.by_id["btnApplyResourcesPanel"]
    assert apply_attrs.get("data-keep-menu-open") == "true"

    _run_app_checks(r"""
CONNECTED = true;
STATE = { workspace_id: 'ws', scheduling_mode: 'backfill', resources: { gpu: 2 },
  resources_in_use: {}, active_workers: 0, max_workers: 2, jobs: [] };
el('resourcesMenu').open = true;
const qGpu = makeNode('resQuota0', 'INPUT');
qGpu.dataset = { resource: 'gpu', resQuota: 'gpu' };
qGpu.value = '1';
el('resourcesTableBody').querySelectorAll = sel =>
  sel.includes('res-quota') ? [qGpu] : [];
el('resMaxWorkers').value = '2';
el('resSchedulingMode').value = 'backfill';

// 1) showFormError with SETTINGS_FIELDS and resources.gpu must not throw on null resourceFields
showFormError({ field: 'resources.gpu', message: 'Bad quota' }, SETTINGS_FIELDS, 'settingsFeedback');
assert.equal(document.activeElement, qGpu);

// 2) saveResourcesPanel handles 400 _FieldError via onError into #resourcesFeedback cleanly
fetchJson = async () => {
  throw Object.assign(new Error('GPU quota invalid'), { field: 'resources.gpu', status: 400 });
};
await saveResourcesPanel();
assert.match(el('resourcesFeedback').textContent, /GPU quota invalid/);
assert.equal(el('resourcesMenu').open, true);
assert.equal(document.activeElement, qGpu);
""")


def test_wf02_broadcast_send_skips_non_sendable_checked_jobs() -> None:
    _run_app_checks(r"""
CONNECTED = true;
const j1 = { id: 'j1', name: 'j1', attempt_id: 'a1', submission_id: 's1', status: 'RUNNING' };
const j2 = { id: 'j2', name: 'j2', attempt_id: 'a2', submission_id: 's2', status: 'DONE' };
STATE = { workspace_id: 'ws', jobs: [j1, j2], detail: j1 };
CHECKED_JOBS.add('j1'); CHECKED_JOBS.add('j2');
el('broadcastText').value = 'hello';
fetchJson = async (url, opts) => {
  calls.push({ url, body: JSON.parse(opts.body) });
  return { results: [{ job_id: 'j1', attempt_id: 'a1', ok: true }], affected: 1 };
};
await broadcastSend();
assert.equal(calls.length, 1);
assert.deepEqual(calls[0].body.targets.map(t => t.job_id), ['j1']);
assert.equal(el('broadcastText').value, '');
""")


def test_wf03_retained_log_toolbar_sync_navigation_and_regex_error() -> None:
    _run_app_checks(r"""
CONNECTED = true;
DETAIL_VIEW = 'log';
WORKSPACE_LAYOUT = 'split';
STATE = {
  workspace_id: 'ws', archive: null,
  jobs: [{ id: 'j1', name: 'j1', attempt_id: 'a1', status: 'RUNNING' }],
  detail: {
    id: 'j1', name: 'j1', attempt_id: 'a1', status: 'RUNNING', total_lines: 3,
    max_log_lines: 100, log_count: 3,
    log_records: [
      { line_no: 1, text: 'alpha' },
      { line_no: 2, text: 'beta' },
      { line_no: 3, text: 'gamma' },
    ],
  },
};
const box = el('logBox');
setupMonitorLayout();

// Unchecking follow when scrolled up must uncheck analysisFollow and vice-versa
el('analysisFollow').checked = true;
el('chkAutoScroll').checked = true;
box.scrollTop = 10; box.scrollHeight = 1000; box.clientHeight = 200;
box.onscroll();
assert.equal(el('chkAutoScroll').checked, false);
assert.equal(el('analysisFollow').checked, false);

el('analysisFollow').checked = true;
el('analysisFollow').onchange({ target: el('analysisFollow') });
assert.equal(el('chkAutoScroll').checked, true);
assert.equal(box.scrollTop, box.scrollHeight);

// Ctrl+Home / Jump to beginning moves logBox to top and disables follow
box.onkeydown({ key: 'Home', ctrlKey: true, preventDefault(){} });
assert.equal(box.scrollTop, 0);
assert.equal(el('chkAutoScroll').checked, false);
assert.equal(el('analysisFollow').checked, false);

box.onkeydown({ key: 'End', ctrlKey: true, preventDefault(){} });
assert.equal(box.scrollTop, box.scrollHeight);

// Invalid regex displays regex error in #analysisMatchCount, not "No results"
el('analysisQuery').value = '[unclosed';
el('analysisRegex').checked = true;
renderLogs(true);
assert.notEqual(el('analysisMatchCount').textContent, 'No results');
assert.match(el('analysisMatchCount').textContent, /regex|invalid|pattern|unterminated|\(/i);

// WF-03 rereview: live mode ignores Elapsed inputs/badge and stepRetainedMatch unchecks analysisFollow
el('analysisRegex').checked = false;
el('analysisQuery').value = 'hit';
el('analysisStart').value = '5';
el('analysisEnd').value = '15';
el('analysisFollow').checked = true;
el('chkAutoScroll').checked = true;
STATE.detail = {
  id: 'j1', name: 'j1', attempt_id: 'a1', status: 'RUNNING', total_lines: 2,
  max_log_lines: 100, log_count: 2,
  log_records: [
    { line_no: 1, source: 'stdout', text: 'hit one', time: '09:00:01' },
    { line_no: 2, source: 'stdout', text: 'hit two', time: '09:00:20' },
  ],
};
renderLogs(true);
assert.equal(el('analysisFilterCount').textContent, '');
assert.ok(el('logBox').innerHTML.includes('log-line-1') && el('logBox').innerHTML.includes('one'));
assert.ok(el('logBox').innerHTML.includes('log-line-2') && el('logBox').innerHTML.includes('two'));
stepRetainedMatch(1);
assert.equal(el('chkAutoScroll').checked, false);
assert.equal(el('analysisFollow').checked, false);

// Archive mode applies Elapsed filter BEFORE building RETAINED_MATCHES (keeping non-numeric elapsed)
RETAINED_MATCH_INDEX = -1;
STATE.archive = { run_id: 'r1', enabled: true };
STATE.detail = {
  id: 'j1', name: 'j1', attempt_id: 'a1', status: 'DONE', total_lines: 4,
  max_log_lines: 100, log_count: 4,
  log_records: [
    { line_no: 1, source: 'stdout', text: 'hit early', time: '09:00:01', elapsed: 1 },
    { line_no: 2, source: 'stdout', text: 'hit middle', time: '09:00:10', elapsed: 10 },
    { line_no: 3, source: 'stdout', text: 'hit late', time: '09:00:20', elapsed: 20 },
    { line_no: 4, source: 'stdout', text: 'hit bare', time: '09:00:25' },
  ],
};
el('analysisFilterMode').checked = true;
renderLogs(true);
assert.equal(el('analysisFilterCount').textContent, '(1)');
assert.equal(el('analysisMatchCount').textContent, '1 of 2');
assert.ok(!el('logBox').innerHTML.includes('early'));
assert.ok(el('logBox').innerHTML.includes('middle'));
assert.ok(!el('logBox').innerHTML.includes('late'));
assert.ok(el('logBox').innerHTML.includes('bare'));
""")


def test_wf04_reading_reorder_and_alt_arrow_visible_columns() -> None:
    harness = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const stored = new Map(), nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, dataset: {}, style: {}, listeners: {}, attrs: {}, hidden: false, innerHTML: '',
    addEventListener(k, v){ (this.listeners[k] ||= []).push(v); },
    setAttribute(k, v){ this.attrs[k] = v; }, getAttribute(k){ return this.attrs[k]; },
    querySelector(){ return null; }, querySelectorAll(){ return []; }, contains(){ return false; },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500 }),
  });
  return nodes.get(id);
}
const root = node('flowWorkspaceView');
const pane = node('analysisContext');
pane.parentElement = node('readingArea');
const sandbox = vm.createContext({
  document: { getElementById: node, addEventListener(){}, activeElement: null },
  window: { innerWidth: 1280, innerHeight: 800, localStorage: { getItem: k => stored.get(k) || null, setItem: (k, v) => stored.set(k, v) } },
  Map, Set, JSON, Number, setTimeout, clearTimeout,
  getComputedStyle: () => ({ flexDirection: 'row' }),
});
vm.runInContext(ICONS_SRC, sandbox);
vm.runInContext(READING_SRC, sandbox);
const api = vm.runInContext('ReadingLayout', sandbox);
let changes = 0;
api.initialize(root, () => changes++);

// 1) Adjacent reorder dragging col 2 onto 'right' of col 1 must keep col 2 right of col 1 (or symmetric move)
const k = 'jobs-split';
api.register(['name', 'status', 'result', 'warning_count'], k, ['name', 'status', 'result']);
api.reorder(k, 'warning_count', 'result', 'right');
assert.deepEqual(Array.from(api.order(['name', 'status', 'result', 'warning_count'], k)), ['name', 'status', 'result', 'warning_count']);
// Dragging status onto 'right' of result swaps them cleanly
api.reorder(k, 'status', 'result', 'right');
assert.deepEqual(Array.from(api.order(['name', 'status', 'result', 'warning_count'], k)), ['name', 'result', 'status', 'warning_count']);

// 2) Alt+ArrowRight on visible column 'status' skips hidden 'warning_count' and swaps with visible next
api.reset(k);
api.register(['status', 'warning_count', 'result', 'elapsed'], k, ['status', 'result', 'elapsed']);
assert.equal(api.isVisible(k, 'warning_count'), false);
const th = { dataset: { readingField: 'status' }, closest: s => s === 'th[data-reading-field]' ? th : (s === 'table' ? tbl : null) };
const tbl = { dataset: { readingScope: k } };
let preventCount = 0;
for (const fn of root.listeners.keydown || []) {
  fn({ key: 'ArrowRight', altKey: true, target: th, preventDefault(){ preventCount++; } });
}
const reorderedVis = Array.from(api.order(['status', 'warning_count', 'result', 'elapsed'], k)).filter(c => api.isVisible(k, c));
assert.deepEqual(reorderedVis, ['result', 'status', 'elapsed']);
assert.equal(preventCount, 1);

// 3) Bare ArrowRight on sort button must NOT call preventDefault
const sortBtn = { closest: s => s === 'th[data-reading-field]' ? th : (s === '[data-column-resize]' ? null : null) };
let barePrevented = false;
for (const fn of root.listeners.keydown || []) {
  fn({ key: 'ArrowRight', altKey: false, target: sortBtn, preventDefault(){ barePrevented = true; } });
}
assert.equal(barePrevented, false);
""".replace("ICONS_SRC", json.dumps(ICONS_JS.read_text(encoding="utf-8"))).replace(
        "READING_SRC", json.dumps(READING_JS.read_text(encoding="utf-8"))
    )
    _run_node(harness)


def test_wf05_file_browser_keyboard_focus_and_error_target() -> None:
    _run_app_checks(r"""
CONNECTED = true;
el('workOverlay').hidden = false;
const list = el('fileList');
const rowBtns = [0, 1, 2].map(i => {
  const b = makeNode('fbBtn' + i, 'BUTTON');
  b.dataset = { entry: String(i) };
  return b;
});
const rowWraps = rowBtns.map((b, i) => {
  const w = makeNode('fbWrap' + i, 'DIV');
  w.classList.add('fb-row-wrap');
  b.parentElement = w;
  return w;
});
list.querySelectorAll = sel => {
  if (sel === '[data-entry]') return rowBtns;
  if (sel === '.fb-row-wrap') return rowWraps;
  return [];
};
let innerWrites = 0;
let listHtml = '<div>rows</div>';
Object.defineProperty(list, 'innerHTML', {
  get(){ return listHtml; },
  set(v){ innerWrites++; listHtml = v; },
});
BROWSE_KIND = 'work';
BROWSE_VISIBLE = [
  { name: 'a.py', path: '/tmp/a.py', directory: false },
  { name: 'b.py', path: '/tmp/b.py', directory: false },
  { name: 'c.py', path: '/tmp/c.py', directory: false },
];
BROWSE_SELECTED_INDEX = 0;
rowBtns[2].focus();
handleFileListKey({ key: 'Enter', target: rowBtns[2], preventDefault(){} });
assert.equal(el('workPath').value, '/tmp/c.py');

// ArrowDown must update selection and focus in place without rebuilding #fileList.innerHTML
innerWrites = 0;
BROWSE_SELECTED_INDEX = 0;
rowBtns[0].focus();
handleFileListKey({ key: 'ArrowDown', target: rowBtns[0], preventDefault(){} });
assert.equal(innerWrites, 0);
assert.equal(BROWSE_SELECTED_INDEX, 1);
assert.equal(document.activeElement, rowBtns[1]);

// browseFiles error must populate #browsePathError instead of behind-modal #loadError
fetchJson = async () => { throw new Error('Permission denied'); };
await browseFiles('/root/secret', 'work');
assert.match(el('browsePathError').textContent, /Permission denied/);
assert.equal(el('loadError').textContent, '');
""")


def test_wf06_history_retry_and_post_query_poll_timestamp() -> None:
    harness = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const nodes = new Map();
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, dataset: {}, value: '', checked: true, hidden: false, disabled: false, textContent: '',
    selectedOptions: [], innerHTML: '', setAttribute(){}, contains(){ return false; },
    querySelector(){ return element(id + '_q'); }, querySelectorAll(){ return []; },
    replaceChildren(){}, scrollIntoView(){},
  });
  return nodes.get(id);
}
let failFirst = true;
let nowClock = 100;
class FakeDate { static now(){ return nowClock * 1000; } }
const queries = [];
async function fetchJson(url) {
  const q = Object.fromEntries(new URL(url, 'http://localhost').searchParams);
  queries.push(q);
  if (q.view === 'attempts' && failFirst) {
    failFirst = false;
    throw new Error('transient');
  }
  // Advance clock across awaited attempt poll!
  if (q.view === 'attempt' && failFirst === false && nowClock === 110) nowClock = 125;
  if (q.view === 'attempt') {
    return {
      attempt: { id: 'a1', attempt_id: 'a1', source_kind: 'tail', total_lines: 10, status: 'RUNNING', name: 'a1', attempt_number: 1 },
      health: { enabled: true, status: 'recording' },
    };
  }
  return {
    rows: [{ id: 'a1', attempt_id: 'a1', source_kind: 'tail', total_lines: 10, status: 'RUNNING', name: 'a1', attempt_number: 1 }],
    before: null, has_more: false,
    health: { enabled: true, status: 'recording' },
  };
}
const ctx = vm.createContext({
  document: { getElementById: element, activeElement: null },
  STATE: { workspace_id: 'w1', archive: { run_id: 'r1', enabled: true } },
  DETAIL_VIEW: 'details', fetchJson, escHtml: s => String(s), URLSearchParams, Date: FakeDate, console,
  recordFeedback(){}, openModal(){}, closeModal(){}, setWorkspaceLayout(){},
  setDetailView(){}, renderDetail(){}, updateControls(){},
});
vm.runInContext(HISTORY_SRC, ctx);
const api = vm.runInContext('RunHistory', ctx);
await api.openInitial('r1');
assert.equal(api.selection, null);
await api.openInitial('r1');
assert.ok(api.selection, 'openInitial must retry after transient failure');

// pollFollow with no explicit timestamp samples clock AFTER await query (>= 20s -> interrupted)
const saved = api.selection;
saved.follow = 'following';
nowClock = 110;
await api.pollFollow();
assert.equal(saved.follow, 'interrupted');
""".replace("HISTORY_SRC", json.dumps(HISTORY_JS.read_text(encoding="utf-8")))
    _run_node("(async()=>{\n" + harness + "\n})().catch(e=>{console.error(e);process.exitCode=1});")


def test_wf07_queue_only_toast_and_retention_coordination() -> None:
    _run_app_checks(r"""
CONNECTED = true;
const p1 = { id: 'p1', name: 'p1', attempt_id: 'a1', submission_id: 's1', status: 'PENDING', held: false };
STATE = { workspace_id: 'ws', jobs: [p1], detail: p1 };
el('settingsOverlay').hidden = false;

// 1) runCheckedQueueOnly must NOT show success toast when hold POST fails (null)
fetchJson = async () => { throw new Error('400 failed'); };
await runCheckedQueueOnly();
assert.ok(!feedback.some(([msg]) => /Updated queue/.test(String(msg))));

// 2) applyRetentionSettings aborts before saveHistoryLimit if applyLogRetention fails validation/scope
calls.length = 0;
el('logRetentionScope').value = 'selected';
STATE.detail = null;
el('logRetentionInput').value = '50';
el('maxHistoryInput').value = '5';
await applyRetentionSettings();
assert.equal(calls.length, 0);

// 3) saveHistoryLimit sets BUSY during API request and combine retains log retention note
let busyDuringCall = false;
el('logRetentionScope').value = 'all';
fetchJson = async (url, opts) => {
  if (url === '/api/settings/save') busyDuringCall = BUSY;
  calls.push(url);
  return { ok: true, results: [{ ok: true, job_id: 'p1' }], affected: 1, evicted_lines: 12, evicted_history: 0 };
};
await applyRetentionSettings();
assert.equal(busyDuringCall, true);
const lastMsg = feedback.at(-1)?.[0] || '';
assert.match(lastMsg, /12 older line/);
""")


def test_wf08_ctrl_f_scoped_to_log_view_and_no_primary_select() -> None:
    _run_app_checks(r"""
WORKSPACE_LAYOUT = 'split';
DETAIL_VIEW = 'log';
STATE = { workspace_id: 'ws', archive: null, jobs: [], detail: { id: 'j1', attempt_id: 'a1', log_records: [] } };
el('vscodeFindWidget').hidden = true;
let selectCalls = 0;
el('analysisQuery').select = () => { selectCalls++; };

// 1) Ctrl+F on #jobSearch or body must NOT open #vscodeFindWidget or preventDefault
for (const outside of [el('jobSearch'), document.body]) {
  let prevented = false;
  handleWorkbenchKey({ key: 'f', ctrlKey: true, target: outside, preventDefault(){ prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(el('vscodeFindWidget').hidden, true);
}

// 2) Ctrl+F inside #logView opens #vscodeFindWidget WITHOUT calling .select()
let preventedInside = false;
handleWorkbenchKey({ key: 'f', ctrlKey: true, target: el('logView'), preventDefault(){ preventedInside = true; } });
assert.equal(preventedInside, true);
assert.equal(el('vscodeFindWidget').hidden, false);
assert.equal(selectCalls, 0);

// 3) Escape from find button inside #vscodeFindWidget closes widget and restores focus to #btnToggleFind
const nextBtn = makeNode('analysisFindNext', 'BUTTON');
nextBtn.parentElement = el('vscodeFindWidget');
nextBtn.focus();
handleWorkbenchKey({ key: 'Escape', target: nextBtn, preventDefault(){}, stopImmediatePropagation(){} });
assert.equal(el('vscodeFindWidget').hidden, true);
assert.equal(document.activeElement, el('btnToggleFind'));
""")


def test_wf_g01_to_g05_focus_menu_responsive_and_archive_confirm() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    # WF-G01: checkboxes/radios keep focus ring; open-mode-pills not clipped; sort-button inset focus
    assert re.search(
        r"input:not\(\[type=['\"]checkbox['\"]\]\):not\(\[type=['\"]radio['\"]\]\):focus",
        css,
    ), "input:focus outline:none must exclude checkboxes and radios"
    assert ".fb-path-edit:focus-within" in css
    assert ".wf-rows:focus-visible" in css
    assert "outline-offset: -2px" in css

    # WF-G02: help-tip is semantic button, not non-interactive span
    parser = _TagCollector()
    parser.feed(html)
    assert parser.help_tips and all(tag == "button" for tag, _ in parser.help_tips)

    # WF-G03 & WF-G04: qe-table-wrap scroll + popup/toolbar responsive rules
    assert ".qe-table-wrap" in css and ".topbar-right" in css and "flex-wrap: wrap" in css

    # WF-G05: clicking .archive-delete-btn triggers two-step inline confirmation naming archive
    _run_app_checks(r"""
CONNECTED = true;
OPEN_MODE = 'archive';
STATE = { workspace_id: 'ws', jobs: [] };
el('workOverlay').hidden = false;
const list = el('archiveRecentList');
const rowObj = { run_id: 'run1', filename: 'keep_me.sqlite3', path: '/tmp/keep_me.sqlite3', closed_cleanly: true };
const delBtn = makeNode('delBtn0', 'BUTTON');
delBtn.dataset = { archiveDelete: '0' };
const rowDom = makeNode('row0', 'DIV');
rowDom.classList.add('archive-recent-row');
delBtn.parentElement = rowDom;
list.querySelectorAll = sel => sel.includes('archive-delete') ? [delBtn] : [];
fetchJson = async url => {
  if (url.startsWith('/api/archive?')) return { rows: [rowObj], total_files: 1 };
  calls.push(url);
  return { ok: true };
};
await loadRecentArchives();
delBtn.onclick();
assert.equal(calls.filter(u => u.includes('/delete')).length, 0, 'First click must NOT delete immediately');
assert.match(rowDom.innerHTML + list.innerHTML, /keep_me\.sqlite3/);
assert.match(list.innerHTML, /Confirm Delete/);
""")


# ---------------------------------------------------------------------------
# WCF-01 .. WCF-09 + WCF-G01 .. WCF-G06 (Charts / Analysis / Flow S1s)
# ---------------------------------------------------------------------------


def _run_charts_test(checks: str) -> None:
    harness = r"""
const assert = require('node:assert/strict');
function makeCtx(kind, counters) {
  const bump = () => { if (kind === 'base') counters.base++; else counters.overlay++; };
  return {
    setTransform(){}, save(){}, restore(){}, beginPath(){}, rect(){}, clip(){},
    moveTo(){}, lineTo(){}, setLineDash(){}, arc(){}, closePath(){}, clearRect(){},
    measureText(s){ return { width: String(s).length * 6 }; },
    fillRect(){ bump(); }, strokeRect(){ bump(); }, stroke(){ bump(); }, fill(){ bump(); }, fillText(){ bump(); },
  };
}
const counters = { base: 0, overlay: 0 };
const elements = new Map();
function makeEl(tag, id = '') {
  const classes = new Set();
  const obj = {
    id, tagName: tag.toUpperCase(), hidden: false, disabled: false, value: '', textContent: '',
    title: '', scrollTop: 0, clientWidth: 800, clientHeight: 300, dataset: {}, attributes: {},
    children: [], listeners: {}, style: { setProperty(){}, getPropertyValue(){ return ''; } },
    get options(){ return this.children; }, selectedOptions: [], checked: false,
    classList: {
      toggle(c, force){
        if (force === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); }
        else if (force) classes.add(c); else classes.delete(c);
      },
      contains(c){ return classes.has(c); },
      add(...cs){ cs.forEach(c => classes.add(c)); },
      remove(...cs){ cs.forEach(c => classes.delete(c)); },
    },
    setAttribute(k, v){ this[k] = String(v); this.attributes[k] = String(v); },
    getAttribute(k){ return this.attributes[k]; },
    addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },
    dispatch(ev, e = {}){
      const evt = { preventDefault(){}, stopPropagation(){}, ...e, target: e.target || this };
      for (const fn of this.listeners[ev] || []) fn(evt);
      return evt;
    },
    replaceChildren(...kids){ this.children = []; this.append(...kids); },
    append(...kids){
      for (const k of kids) {
        if (k && k._isFrag) {
          for (const fk of k.children) { fk.parentElement = this; this.children.push(fk); }
        } else if (k && typeof k === 'object') {
          k.parentElement = this; this.children.push(k);
        }
      }
    },
    closest(sel){
      if (!sel) return null;
      for (const part of sel.split(',').map(s => s.trim())) {
        if (part.startsWith('#') && this.id === part.slice(1)) return this;
        if (part.startsWith('.') && classes.has(part.slice(1))) return this;
      }
      return this.parentElement?.closest?.(sel) || null;
    },
    querySelectorAll(sel){
      const out = [];
      const walk = n => {
        for (const ch of n.children || []) {
          if (sel === '[role="treeitem"]' && ch.attributes?.role === 'treeitem') out.push(ch);
          else if (sel === '.wf-ctx-item:not(:disabled)' && ch.classList?.contains('wf-ctx-item') && !ch.disabled) out.push(ch);
          else if (sel === 'input' && ch.tagName === 'INPUT') out.push(ch);
          else if (sel.startsWith('button,') && ch.tagName === 'BUTTON') out.push(ch);
          walk(ch);
        }
      };
      walk(this);
      return out;
    },
    querySelector(sel){ return this.querySelectorAll(sel)[0] || null; },
    getBoundingClientRect(){ return { left: 0, top: 0, right: 800, bottom: 300, width: 800, height: 300 }; },
    focus(){ document.activeElement = this; },
    contains(other){
      if (other === this) return true;
      for (const c of this.children) if (c.contains?.(other)) return true;
      return false;
    },
    setPointerCapture(){}, releasePointerCapture(){}, scrollIntoView(){},
    getContext(){ return makeCtx(id === 'chartOverlayCanvas' ? 'overlay' : 'base', counters); },
  };
  return obj;
}
function node(id){ return elements.get(id) ?? (elements.set(id, makeEl('div', id)), elements.get(id)); }
globalThis.window = { innerWidth: 1200, innerHeight: 800, localStorage: { getItem(){ return null; }, setItem(){} }, addEventListener(){} };
globalThis.document = {
  getElementById: node, activeElement: null, hidden: false,
  listeners: {},
  addEventListener(ev, fn){ (this.listeners[ev] ||= []).push(fn); },
  dispatch(ev, e = {}){ const evt = { preventDefault(){}, stopPropagation(){}, ...e, target: e.target || node('chartCanvas') }; for (const fn of this.listeners[ev] || []) fn(evt); return evt; },
  createElement: tag => makeEl(tag),
  createDocumentFragment: () => ({ _isFrag: true, children: [], append(...k){ this.children.push(...k); } }),
  querySelector: () => null,
};
globalThis.requestAnimationFrame = fn => fn();
globalThis.WORKSPACE_LAYOUT = 'charts';
globalThis.STATE = { workspace_id: 'w1', archive: { enabled: true } };
"""
    script = (
        harness
        + "\n"
        + ICONS_JS.read_text(encoding="utf-8")
        + "\n"
        + CHARTS_JS.read_text(encoding="utf-8")
        + f"\n(async () => {{\n{checks}\n}})().catch(e => {{ console.error(e); process.exitCode = 1; }});\n"
    )
    _run_node(script)


def test_wcf07_09_analysis_and_flow_s1_regressions() -> None:
    # WCF-07 & WCF-08: AnalysisReader Follow catch-up & Filter boundary seq
    analysis_js = ANALYSIS_JS.read_text(encoding="utf-8")
    analysis_harness = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const nodes = new Map();
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, dataset: {}, value: '', checked: false, hidden: false, disabled: false, attributes: {}, listeners: {},
    textContent: '', innerHTML: '', scrollTop: 0, scrollHeight: 10000, clientHeight: 300, children: [],
    classList: { toggle(){} }, addEventListener(e, fn){ (this.listeners[e] ||= []).push(fn); },
    setAttribute(k, v){ this.attributes[k] = v; }, focus(){ document.activeElement = this; },
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    getBoundingClientRect(){ return { top: 0, bottom: 300 }; },
    appendChild(ch){ ch.parentElement = this; }, replaceChildren(){ this.innerHTML = ''; },
  });
  return nodes.get(id);
}
const document = { getElementById: element, activeElement: null };
const STATE = { workspace_id: 'ws', archive: { run_id: 'r1', enabled: true } };
let mode = 'burst';
const queries = [];
async function fetchJson(url) {
  const q = Object.fromEntries(new URL(url, 'http://localhost').searchParams);
  queries.push(q);
  if (mode === 'burst') {
    if (q.direction === 'backward' && !q.cursor && q.after === undefined && !queries.some(x => x.direction === 'forward')) {
      return { rows: [{ seq: 1, text: 'l1' }, { seq: 2, text: 'l2' }], first: 'c1', last: 'c2', has_more: true, total_lines: 100, end_seq: 100 };
    }
    if (q.direction === 'backward' && !q.cursor) {
      return { rows: [{ seq: 349, text: 'l349' }, { seq: 350, text: 'l350' }], first: 'c349', last: 'c350', has_more: false, total_lines: 350, end_seq: 350 };
    }
    return { rows: [{ seq: 3, text: 'l3' }, { seq: 4, text: 'l4' }], first: 'c3', last: 'c4', has_more: true, total_lines: 350, end_seq: 350 };
  }
  // Filter 600-row full-buffer boundary step across 100-row pages
  const offset = q.cursor ? Number(q.cursor.slice(1)) : 0;
  const rows = Array.from({ length: 100 }, (_, i) => ({ seq: offset + 1 + i, text: 'hit ' + (offset + 1 + i) }));
  const end = offset + 100;
  return { rows, first: 'c' + (offset + 1), last: 'c' + end, next: 'c' + end, has_more: end < 700 };
}
const sandbox = vm.createContext({
  document, STATE,
  RunHistory: { selection: null, query: (o, d) => fetchJson('/api/archive?' + new URLSearchParams({ ...o, job_id: d.id })) },
  isHistory: () => false, fetchJson, escHtml: s => String(s), URLSearchParams, AbortController,
  DETAIL_VIEW: 'log', openModal(){}, closeModal(){}, Date, console, setTimeout, clearTimeout,
  requestAnimationFrame: fn => fn(), setDetailView(){},
});
vm.runInContext(ANALYSIS_SRC, sandbox);
const api = vm.runInContext('AnalysisWorkspace', sandbox);
const d = { id: 'j1', attempt_id: 'a1', total_lines: 100, status: 'RUNNING', data: {} };
element('analysisFollow').checked = true;
await api.refresh(d);
queries.length = 0;
d.total_lines = 350;
await api.refresh(d);
assert.ok(queries.some(q => q.direction === 'forward'), 'Follow burst should probe forward first');
assert.ok(queries.some(q => q.direction === 'backward' && !q.cursor) && element('analysisRows').innerHTML.includes('l350'), 'Follow burst must catch up to latest rows up to 350');

// WCF-08: stepMatch forward across full 600-row buffer advances to seq 601 instead of jumping to index 0
mode = 'filter';
element('analysisFollow').checked = false;
element('analysisQuery').value = 'hit';
element('analysisFilterMode').checked = true;
await api.apply();
for (let p = 0; p < 5; p++) await api.edge('forward');
for (let i = 0; i < 599; i++) api.stepMatch(1);
assert.equal(element('analysisMatchCount').textContent, '600 of 600+');
await api.stepMatch(1);
assert.ok(element('analysisMatchCount').textContent.includes('501 of'), 'Boundary step must select first newly loaded match (seq 601), got ' + element('analysisMatchCount').textContent);
""".replace("ANALYSIS_SRC", json.dumps(analysis_js))
    _run_node("(async()=>{\n" + analysis_harness + "\n})().catch(e=>{console.error(e);process.exitCode=1});")

    # WCF-09: FlowReader policy switch sends 1 query per click (not 2)
    flow_js = FLOW_JS.read_text(encoding="utf-8")
    flow_harness = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const nodes = new Map(), handlers = {};
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, dataset: {}, hidden: false, value: '', checked: false, attributes: {}, listeners: {},
    innerHTML: '', textContent: '', scrollTop: 0, scrollHeight: 1000, clientHeight: 300,
    classList: { toggle(){}, contains(){ return false; } }, style: {},
    setAttribute(k, v){ this.attributes[k] = v; }, getAttribute(k){ return this.attributes[k]; },
    addEventListener(k, fn){ (this.listeners[k] ||= []).push(fn); },
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    replaceChildren(){}, appendChild(){}, focus(){},
  });
  return nodes.get(id);
}
const document = {
  getElementById: element,
  createElement: tag => element('c_' + tag),
  body: { appendChild(){} },
  addEventListener(k, fn){ (handlers[k] ||= []).push(fn); },
};
element('analysisContext').parentElement = element('flowReadingArea');
let policyQueries = 0;
const sandbox = vm.createContext({
  document,
  window: { innerWidth: 1280, innerHeight: 800, localStorage: { getItem: () => null, setItem(){} } },
  navigator: {}, STATE: { workspace_id: 'ws', archive: { run_id: 'r1', enabled: true } },
  RunHistory: { selection: null, healthText: () => 'Saved', query: async () => { policyQueries++; return { rows: [], has_more: false }; } },
  AnalysisWorkspace: { openContext: async () => {} },
  escHtml: s => String(s), Map, Set, JSON, Number, DETAIL_VIEW: 'parsed', WORKSPACE_LAYOUT: 'split',
  badge: s => s, displayState: s => s, AbortController, setTimeout, clearTimeout, console,
  getComputedStyle: () => ({ flexDirection: 'row' }), openModal(){}, closeModal(){}, setDetailView(){},
});
vm.runInContext(ICONS_SRC, sandbox);
vm.runInContext(READING_SRC, sandbox);
vm.runInContext(JOBS_SRC, sandbox);
vm.runInContext(FLOW_SRC, sandbox);
const api = vm.runInContext('FlowWorkspace', sandbox);
api.refresh({ id: 'j1', name: 'j1', attempt_id: 'a1', status: 'RUNNING', flow: { name: 'f', parsers: [{ name: 'p', outputs: { x: {} }, count: 1 }], analyzers: [], automations: [], diagnostics: [] } });
await new Promise(r => setImmediate(r));
policyQueries = 0;
const btn = element('flowPolicyFields');
btn.dataset = { flowPolicy: 'fields' };
btn.closest = sel => sel === '[data-flow-policy]' ? btn : null;
for (const fn of btn.listeners.click || []) fn({ target: btn });
for (const fn of handlers.click || []) fn({ target: btn });
await new Promise(r => setImmediate(r));
assert.equal(policyQueries, 1, 'Clicking Merged must trigger only 1 query');
""".replace("ICONS_SRC", json.dumps(ICONS_JS.read_text(encoding="utf-8"))) \
        .replace("READING_SRC", json.dumps(READING_JS.read_text(encoding="utf-8"))) \
        .replace("JOBS_SRC", json.dumps(JOBS_JS.read_text(encoding="utf-8"))) \
        .replace("FLOW_SRC", json.dumps(flow_js))
    _run_node("(async()=>{\n" + flow_harness + "\n})().catch(e=>{console.error(e);process.exitCode=1});")


def test_wcf_g01_to_g06_charts_flow_analysis_accessibility_and_layout() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    flow_src = FLOW_JS.read_text(encoding="utf-8")
    analysis_src = ANALYSIS_JS.read_text(encoding="utf-8")

    # WCF-G04: [data-flow-full] aria-label, #chartParamClose/#chartFileClose title, analysis button types
    parser = _TagCollector()
    parser.feed(html)
    assert "title" in parser.by_id["chartParamClose"][1]
    assert "title" in parser.by_id["chartFileClose"][1]
    assert 'aria-label="Inspect full output"' in flow_src
    assert 'class="analysis-truncated"' in analysis_src
    for m in re.finditer(r"<button\b[^>]*>", analysis_src):
        assert 'type="button"' in m.group(0), f"Missing type='button' in analysis.js: {m.group(0)}"

    # WCF-G06: chart-toolbar wrapping, clamped chart-signals-dialog, splitter hidden <=720px
    assert "min(420px" in css
    assert re.search(r"@media\s*\(\s*max-width:\s*720px\s*\)[^{]*\{[\s\S]*#chartBrowserSplitter", css)

    # WCF-G01, WCF-G02, WCF-G05
    _run_charts_test(r"""
node('chartPlot').clientWidth = 800; node('chartPlot').clientHeight = 300;
node('chartFindOp').replaceChildren(makeEl('option')); node('chartFindOp').value = 'any';
globalThis.RunHistory = {
  selection: null,
  async query(opts) {
    if (opts.view === 'chart_meta') {
      return {
        as_of_seq: 1, spec_version: 1, digest: 'd1', diagnostics: [], health: { closed_cleanly: true },
        views: [{
          id: 'rtl', kind: 'waveform', title: 'RTL', series_count: 2,
          axes: ['time'], default_axis: 'time', domains: { time: [0, 100] },
          axis: { label: 'Time', unit: 'ns' },
          series: { mode: 'long', defaults: [{ type: 'group', id: 'g1', label: 'Grp', collapsed: false, signals: ['top.sub.b'] }] },
          options: { radix: 'hex' },
        }],
      };
    }
    if (opts.view === 'chart_series') {
      return { series: [
        { id: 'top.a', label: 'a', path: ['top', 'a'], type: 'bit' },
        { id: 'top.sub.b', label: 'b', path: ['top', 'sub', 'b'], type: 'bit' },
      ], complete: true, next_cursor: null };
    }
    return { view: 'rtl', axis: 'time', range: [0, 100], resolution: 'raw', left: {}, points: [], complete: true, coverage: [100, 100, 0] };
  },
};
ChartWorkspace.refresh({ id: 'j1', attempt_id: 'a1' });
await new Promise(r => setTimeout(r, 20));

// WCF-G02: Hierarchy tree items have role="treeitem", aria-expanded, and keyboard arrow navigation
node('chartSignalsToggle').dispatch('click');
const treeItems = node('chartHierarchyTree').querySelectorAll('[role="treeitem"]');
assert.ok(treeItems.length >= 1, '#chartHierarchyTree must render role="treeitem" nodes');

// WCF-G05: Context menu focuses menu item and Shift+F10 opens it for selected row; handleWaveKeydown skips 'Z' while open
const grpRow = node('chartSignalRows').children[0];
grpRow.dispatch('click');
node('chartSignalRows').dispatch('keydown', { key: 'Enter' });
assert.equal(node('chartSignalRows').children.length, 1, 'Enter must collapse group in #chartSignalRows');
node('chartSignalRows').dispatch('keydown', { key: 'Enter' });
assert.equal(node('chartSignalRows').children.length, 2, 'Enter again expands group');
node('chartSignalRows').dispatch('keydown', { key: 'F10', shiftKey: true });
assert.equal(node('chartContextMenu').hidden, false);
assert.ok(node('chartContextMenu').contains(document.activeElement), 'Opening context menu must focus first menu item');
const rangeBeforeZ = node('chartWindowStatus').textContent;
document.dispatch('keydown', { key: 'Z' });
assert.equal(node('chartWindowStatus').textContent, rangeBeforeZ, 'handleWaveKeydown must ignore shortcuts while context menu is open');
""")


def test_wf09_to_12_and_wf_g06_to_11_regressions() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    app_src = APP_JS.read_text(encoding="utf-8")

    # WF-10 / WF-G10: zero missing button types, updated copy, preserved #archiveOverlay
    assert not [m.group(0) for m in re.finditer(r"<button\b[^>]*>", html) if "type=" not in m.group(0)]
    assert not [m.group(0) for m in re.finditer(r"<button\b[^>]*>", app_src) if "type=" not in m.group(0)]
    assert 'Static (Read once)' in html
    assert 'Static (--once)' not in html
    assert 'id="archiveOverlay"' in html
    assert 'id="btnAnalysisElapsedApply"' in html

    # WF-11, WF-12, WF-G06..G11, WCF-G05..G11 static CSS guarantees
    assert ".control-popup .danger { color: #fca5a5;" in css
    assert ".ansi-fg-black { color: #8193ab; }" in css
    assert "scroll-padding-top: var(--jobs-row-height)" in css
    assert "scroll-padding-top: 34px" in css
    assert ".wf-param-err" in css and ".wf-ctx-submenu" in css
    assert ".resources-table { width: 100%; border-collapse: separate; border-spacing: 0;" in css
    assert "#logFilterInput" not in css

    _run_app_checks(r"""
CONNECTED = true;
STATE = { workspace_id: 'ws', jobs: [{ id: 'j1', name: 'alpha', status: 'DONE', result: 'PASS', capabilities: {} }], detail: null };

// WF-09: row.oncontextmenu switches from 'jobs' layout to 'split' before opening details
const row = makeNode('jobRow1', 'TR');
row.dataset = { jobId: 'j1' };
bindJobRowHandlers(row);
WORKSPACE_LAYOUT = 'jobs';
renderMonitor = () => {};
renderDetail = () => {};
fetchJson = async () => ({ jobs: STATE.jobs, detail: STATE.jobs[0], columns: [], available_columns: [] });
await row.oncontextmenu({ preventDefault(){} });
assert.equal(WORKSPACE_LAYOUT, 'split');

// WF-10: per-mode recentByMode retains work paths even after 6 logs paths
rememberLoadedPaths('work', ['D:/keep.py']);
for (let i = 0; i < 6; i++) rememberLoadedPaths('logs', [`D:/${i}.log`]);
assert.equal(getOpenDraft().recentByMode.work[0], 'D:/keep.py');
assert.equal(getOpenDraft().recentByMode.logs.length, 5);

// WF-10: copyArchiveOpenCmd avoids .select() and warns when clipboard fails
let selectCalls = 0;
const origCreate = document.createElement;
document.createElement = tag => {
  const n = origCreate(tag);
  n.select = () => { selectCalls++; };
  return n;
};
document.body.appendChild = () => {};
document.body.removeChild = () => {};
document.execCommand = () => false;
el('archiveOpenCliCmd').textContent = 'wave --archive a.sqlite3';
await copyArchiveOpenCmd();
assert.equal(selectCalls, 0);
assert.match(feedback.at(-1)[0], /Copy failed/);
""")
