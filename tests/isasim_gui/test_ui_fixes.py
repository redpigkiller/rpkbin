"""Static HTML/CSS/ESR102 checks and Node VM UI regression tests for isasim_gui."""

from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
INDEX_HTML = Path(__file__).resolve().parents[2] / "rpkbin" / "app" / "isasim_gui" / "static" / "index.html"


def _extract_script(html: str) -> str:
    match = re.search(r"<script>([\s\S]*?)</script>", html)
    assert match is not None
    return match.group(1)


def _run_node(checks: str) -> None:
    if NODE is None:
        pytest.skip("Node.js is required")
    script = _extract_script(INDEX_HTML.read_text(encoding="utf-8"))
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

const nodes = new Map();
const docListeners = {};
const winListeners = {};
const storageMap = new Map();

function makeClassList(el) {
  const set = new Set();
  const sync = () => { el._className = Array.from(set).join(' '); };
  return {
    add(...names) { names.forEach(n => { if (n) set.add(n); }); sync(); },
    remove(...names) { names.forEach(n => set.delete(n)); sync(); },
    toggle(name, force) {
      if (force === undefined) {
        if (set.has(name)) set.delete(name); else set.add(name);
      } else if (force) set.add(name);
      else set.delete(name);
      sync();
      return set.has(name);
    },
    contains(name) { return set.has(name); },
    _resetFromString(str) {
      set.clear();
      String(str || '').split(/\s+/).filter(Boolean).forEach(n => set.add(n));
      sync();
    }
  };
}

function makeElement(id, tagName = 'DIV') {
  const el = {
    id,
    tagName,
    type: tagName === 'INPUT' ? 'text' : '',
    _className: '',
    get className() { return this._className; },
    set className(v) { this.classList._resetFromString(v); },
    dataset: {},
    style: {
      props: {},
      setProperty(k, v) { this.props[k] = v; },
      getPropertyValue(k) { return this.props[k] || ''; },
    },
    value: '',
    checked: id === 'hideUninit',
    hidden: id === 'memAliasModal',
    disabled: false,
    placeholder: '',
    title: '',
    innerHTML: '',
    innerText: '',
    textContent: '',
    children: [],
    attributes: {},
    listeners: {},
    offsetWidth: 100,
    isConnected: true,
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return Object.hasOwn(this.attributes, k) ? this.attributes[k] : null; },
    removeAttribute(k) { delete this.attributes[k]; },
    hasAttribute(k) { return Object.hasOwn(this.attributes, k); },
    addEventListener(ev, fn) { (this.listeners[ev] ??= []).push(fn); },
    removeEventListener(ev, fn) {
      if (this.listeners[ev]) this.listeners[ev] = this.listeners[ev].filter(f => f !== fn);
    },
    focus() { document.activeElement = this; },
    select() {},
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    },
    remove() {
      if (this.parentElement && Array.isArray(this.parentElement.children)) {
        this.parentElement.children = this.parentElement.children.filter(c => c !== this);
      }
    },
    replaceChildren(...items) { this.children = items; },
    querySelector(sel) {
      if (sel === 'input') return this.children.find(c => c.tagName === 'INPUT') || null;
      return null;
    },
    querySelectorAll() { return []; },
    closest() { return null; },
    getBoundingClientRect() { return { left: 10, top: 10, right: 80, bottom: 30, width: 70, height: 20 }; },
    scrollIntoView() {},
  };
  el.classList = makeClassList(el);
  return el;
}

const tagHints = {
  spaceSelect: 'SELECT',
  sourceSelect: 'SELECT',
  hideUninit: 'INPUT',
  memFilter: 'INPUT',
  regFilterInput: 'INPUT',
  constFilterInput: 'INPUT',
  regAliasFilterInput: 'INPUT',
  condPopoverInput: 'INPUT',
  watchTargetInput: 'INPUT',
  watchCondInput: 'INPUT',
  memAliasModalSearch: 'INPUT',
};

function getEl(id) {
  if (!nodes.has(id)) nodes.set(id, makeElement(id, tagHints[id] || 'DIV'));
  return nodes.get(id);
}

const bodyEl = makeElement('__body__', 'BODY');
const docEl = makeElement('__html__', 'HTML');

const document = {
  body: bodyEl,
  documentElement: docEl,
  activeElement: bodyEl,
  getElementById: getEl,
  querySelector(sel) {
    if (sel === 'meta[name="isasim-token"]') return { content: 'test-token' };
    return null;
  },
  querySelectorAll() { return []; },
  createElement(tag) { return makeElement('', String(tag).toUpperCase()); },
  addEventListener(ev, fn) { (docListeners[ev] ??= []).push(fn); },
};

const localStorage = {
  getItem(k) { return storageMap.has(k) ? storageMap.get(k) : null; },
  setItem(k, v) { storageMap.set(k, String(v)); },
  removeItem(k) { storageMap.delete(k); },
};

function makeState(overrides = {}) {
  const status = {
    stop_reason: 'ready',
    error: null,
    detail: '',
    cycle: 0,
    steps: 0,
    instructions: 0,
    pc: 0,
    pc_hex: '0x0000',
    can_step_back: false,
    step_back_reason: null,
    can_step_depth: true,
    step_depth_reason: null,
    active_file: 'main.asm',
    active_file_path: '/tmp/main.asm',
    program_path: '/tmp/main.asm',
    active_line: 1,
    ...(overrides.status || {}),
  };
  return {
    status,
    files: overrides.files || {
      'main.asm': [{ line: 1, text: 'LDI r0, 1', addr: 0, addr_hex: '0x0000', cells_hex: '1001', is_instr: true, addresses: [0], bp_cond: null, is_pc: true }],
      'sub.asm': [{ line: 1, text: 'HLT', addr: 1, addr_hex: '0x0001', cells_hex: 'ffff', is_instr: true, addresses: [1], bp_cond: null, is_pc: false }],
    },
    registers: overrides.registers || [
      { name: 'pc', bits: 8, is_flag: false, value: status.pc, hex: status.pc_hex, unsigned: status.pc, unsigned_str: String(status.pc), signed: status.pc, signed_str: String(status.pc), changed: false, old_hex: null, fields: [] },
      { name: 'r0', bits: 16, is_flag: false, value: 0, hex: '0x0000', unsigned: 0, unsigned_str: '0', signed: 0, signed_str: '0', changed: false, old_hex: null, fields: [] },
      { name: 'flags', bits: 3, is_flag: true, value: 0, hex: '0x0', unsigned: 0, unsigned_str: '0', signed: 0, signed_str: '0', changed: false, old_hex: null, fields: [{ name: 'Z', target: 'flags.Z', bits: 1, value: 0, value_str: '0', hex: '0x0', changed: false }] },
    ],
    aliases: overrides.aliases || [],
    mem_aliases: overrides.mem_aliases || [],
    constants: overrides.constants || {},
    constants_display: overrides.constants_display || {},
    watchpoints: overrides.watchpoints || [],
    memory: overrides.memory || {
      spaces: ['data'],
      current_space: 'data',
      cells: [{ addr: 0, addr_hex: '0x00', initialized: false, value: 0, hex: '--', unsigned: null, unsigned_str: null, signed: null, signed_str: null, changed: false }],
      filter_errors: [],
    },
    trace: overrides.trace || [],
  };
}

let fetchHandler = async () => ({
  ok: true,
  status: 200,
  async json() { return makeState(); },
  async text() { return JSON.stringify(makeState()); },
});

const sandbox = vm.createContext({
  document,
  localStorage,
  location: { host: '127.0.0.1:8000' },
  innerWidth: 1280,
  innerHeight: 800,
  navigator: {},
  assert,
  URLSearchParams,
  AbortController,
  Option: function(text, value) { return { textContent: text, text, value, title: '' }; },
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  matchMedia: () => ({ matches: false }),
  requestAnimationFrame: fn => fn(),
  setTimeout,
  clearTimeout,
  console,
  addEventListener(ev, fn) { (winListeners[ev] ??= []).push(fn); },
  removeEventListener() {},
  fetch: (url, init) => fetchHandler(url, init),
  makeState,
  makeElement,
  getEl,
  docListeners,
  winListeners,
  storageMap,
  setFetchHandler(fn) { fetchHandler = fn; },
});
sandbox.window = sandbox;
vm.runInContext(SCRIPT, sandbox);
""".replace("SCRIPT", json.dumps(script))

    full = (
        harness
        + "\nconst __p = vm.runInContext("
        + json.dumps("(async () => {\n" + checks + "\n})()")
        + ", sandbox);\n"
        + "__p.catch(e => { console.error(e); process.exitCode = 1; });"
    )
    proc = subprocess.run([NODE, "-"], input=full.encode("utf-8"), capture_output=True, timeout=15)
    assert proc.returncode == 0, (proc.stdout + proc.stderr).decode("utf-8", errors="replace")


def test_static_html_css_a11y_responsive_and_esr102():
    html = INDEX_HTML.read_text(encoding="utf-8")

    # RHEL 8.8 Firefox ESR 102+ incompatible features forbidden
    assert ":has(" not in html
    assert "@container" not in html
    assert "@scope" not in html
    assert "color-mix(" not in html
    assert ".toSorted(" not in html

    # U7G-03 responsive layout rules
    assert "@media (max-width: 760px)" in html
    # U7G-06 reduced motion
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert "prefers-reduced-motion: reduce" in _extract_script(html)
    # U7G-07 focus-visible & no clipping on .seg-ctrl
    assert ":focus-visible" in html
    # U7G-13 dead .debug-controls removed, .muted & tabular-nums present
    assert ".debug-controls" not in html
    assert ".muted" in html
    assert ".reg-name" in html
    assert "tabular-nums" in html

    class _Audit(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.buttons: list[dict[str, str | None]] = []
            self.div_tabs: list[dict[str, str | None]] = []
            self.labels: list[dict[str, str | None]] = []
            self.inputs: list[dict[str, str | None]] = []
            self.selects: list[dict[str, str | None]] = []
            self.svgs: list[dict[str, str | None]] = []
            self.popover_attrs: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            amap = dict(attrs)
            if "popover" in amap:
                self.popover_attrs.append(tag)
            if tag == "button":
                self.buttons.append(amap)
            elif tag == "div" and "tab" in (amap.get("class") or "").split():
                self.div_tabs.append(amap)
            elif tag == "label":
                self.labels.append(amap)
            elif tag == "input":
                self.inputs.append(amap)
            elif tag == "select":
                self.selects.append(amap)
            elif tag == "svg":
                self.svgs.append(amap)

    audit = _Audit()
    audit.feed(html)

    # RHEL 8.8 Firefox ESR 102+ forbids native HTML popover attribute
    assert audit.popover_attrs == []
    # U7G-01: no <div class="tab">
    assert audit.div_tabs == []
    # U7G-02: all non-submit buttons have type="button"
    for btn in audit.buttons:
        assert btn.get("type") in ("button", "submit"), f"button missing type: {btn}"
    # U7G-02: decorative svgs carry aria-hidden="true"
    for svg in audit.svgs:
        assert svg.get("aria-hidden") == "true", f"svg missing aria-hidden: {svg}"
    # U7G-02: all static inputs/selects have accessible name (aria-label or matching <label for>)
    label_fors = {lbl.get("for") for lbl in audit.labels if lbl.get("for")}
    for ctrl in [*audit.inputs, *audit.selects]:
        cid = ctrl.get("id")
        if ctrl.get("type") == "checkbox":
            continue  # wrapped in <label>
        assert ctrl.get("aria-label") or (cid and cid in label_fors), f"control missing label: {ctrl}"

    # U7G-01: dynamic flag pills use <button type="button">, pc-marker.set-btn not visibility:hidden
    assert 'class="flag-pill' not in html.split("<script>")[0]
    assert "<div class=\"${cls}\"" not in html
    assert "visibility: hidden" not in html


def test_u7_04_u7_10_fetch_race_error_banner_and_400_refresh():
    _run_node(r"""
let posted = 0;
let stateCalls = 0;
let holdSlowFetch;
setFetchHandler((url, init) => {
  if (url.startsWith('/api/state')) {
    stateCalls++;
    if (stateCalls === 1) {
      return new Promise(resolve => { holdSlowFetch = resolve; });
    }
    return Promise.resolve({
      ok: true, status: 200,
      async json() { return makeState({ status: { steps: 1, pc: 1, pc_hex: '0x0001' } }); },
      async text() { return ''; },
    });
  }
  posted++;
  if (posted === 1) {
    return Promise.resolve({
      ok: true, status: 200,
      async json() { return makeState({ status: { steps: 1, pc: 1, pc_hex: '0x0001' } }); },
      async text() { return ''; },
    });
  }
  return Promise.resolve({
    ok: false, status: 400,
    async json() { return { detail: 'DebugError: division by zero' }; },
    async text() { return JSON.stringify({ detail: 'DebugError: division by zero' }); },
  });
});

// Start a slow fetchState() while act('step_in') runs and resolves first
const slow = fetchState();
const stepped = await act('step_in');
assert.equal(stepped, true);
assert.equal(state.status.steps, 1);

// Resolving stale fetchState() with steps=0 must NOT overwrite steps=1
holdSlowFetch({
  ok: true, status: 200,
  async json() { return makeState({ status: { steps: 0, pc: 0, pc_hex: '0x0000' } }); },
  async text() { return ''; },
});
await slow;
assert.equal(state.status.steps, 1);

// HTTP 400 in act() refreshes /api/state while keeping #errorBanner visible
const failed = await act('continue');
assert.equal(failed, false);
assert.ok(stateCalls >= 2);
assert.equal(getEl('errorBanner').style.display, 'block');
assert.ok(getEl('errorBanner').innerText.includes('division by zero'));
""")


def test_u7_05_u7_06_u7_07_u7_08_u7g_04_u7g_11_u7g_12_js_behavior():
    _run_node(r"""
const postedActions = [];
setFetchHandler(async (url, init) => {
  if (url.startsWith('/api/action')) {
    const body = JSON.parse(init.body);
    postedActions.push(body);
    return {
      ok: true, status: 200,
      async json() { return makeState(); },
      async text() { return ''; },
    };
  }
  return {
    ok: true, status: 200,
    async json() { return makeState(); },
    async text() { return ''; },
  };
});

await fetchState();

// U7-05: uninitialized cell (data-uninit="1") commits 0x0 on Enter
const td = makeElement('memCell', 'TD');
td.dataset.uninit = '1';
startInlinePoke(td, 'data[0x00]', '0x0');
const inp = td.children[0];
assert.ok(inp);
inp.listeners.keydown[0]({ key: 'Enter', stopPropagation() {}, preventDefault() {} });
await new Promise(r => setTimeout(r, 0));
assert.equal(postedActions.length, 1);
assert.equal(postedActions[0].action, 'poke');
assert.equal(postedActions[0].value, '0x0');

// U7-06: breakpoint/poke preserves currentFile='sub.asm'
currentFile = 'sub.asm';
await act('breakpoint', { file: 'sub.asm', line: 1 });
assert.equal(currentFile, 'sub.asm');
await act('poke', { target: 'r0', value: '5' });
assert.equal(currentFile, 'sub.asm');

// U7-07: Hide All keeps flag registers out of hiddenRegs
setAllRegs(false);
assert.ok(hiddenRegs.has('r0'));
assert.ok(!hiddenRegs.has('flags'));

// U7-08: tracked mem aliases localStorage key uses stable program_path
memAliasLoaded = false;
initTrackedMemAliases(makeState({
  status: { active_file: 'sub.asm', active_file_path: '/tmp/sub.asm', program_path: '/tmp/main.asm' }
}));
assert.equal(memAliasStorageKey, 'isasim_tracked_mem_aliases:/tmp/main.asm');

// U7G-04: undefined_opcode and stuck set errorBanner and non-green dot
render(makeState({ status: { stop_reason: 'undefined_opcode', detail: 'pc=0x0000' } }));
assert.equal(getEl('errorBanner').style.display, 'block');
assert.ok(getEl('statusDot').className.includes('err'));
assert.notEqual(getEl('badgeStatus').innerText, 'UNDEFINED_OPCODE');

// U7G-11: column drag + row drag cannot cross-swap
onColDragStart({ dataTransfer: {} }, 'reg', 0);
onRegRowDragStart({ dataTransfer: {} }, 'r0');
onColDrop({ preventDefault() {}, currentTarget: makeElement('th', 'TH') }, 'reg', 2);
const beforeCols = regCols.map(c => c.key);
onColDragEnd();
onRegRowDragEnd();
onRegRowDragStart({ dataTransfer: {} }, 'r0');
const fakeTh = makeElement('th', 'TH');
let prevented = false;
onColDragOver({ preventDefault() { prevented = true; }, currentTarget: fakeTh }, 'reg');
assert.equal(prevented, false);
onColDrop({ preventDefault() {}, currentTarget: fakeTh }, 'reg', 1);
assert.equal(JSON.stringify(regCols.map(c => c.key)), JSON.stringify(beforeCols));

// U7G-12: #sourceSelect renders disambiguated basenames
render(makeState({
  files: {
    'D:/work/dir1/main.asm': [{ line: 1, text: 'HLT', addr: 0, addr_hex: '0x0000', cells_hex: 'ffff', is_instr: true, addresses: [0], bp_cond: null, is_pc: true }],
    'D:/work/sub/inc.asm': [],
  },
  status: { active_file: 'D:/work/dir1/main.asm' },
}));
const opts = getEl('sourceSelect').children;
assert.equal(JSON.stringify(opts.map(o => o.textContent)), JSON.stringify(['main.asm', 'inc.asm']));
assert.equal(opts[0].value, 'D:/work/dir1/main.asm');

// U7-14: invalid memory filter shows inline hint and invalid class
render(makeState({
  memory: { spaces: ['data'], current_space: 'data', cells: [], filter_errors: ['0xZZ'] }
}));
assert.equal(getEl('memFilterError').hidden, false);
assert.ok(getEl('memFilterError').textContent.includes('0xZZ'));
assert.ok(getEl('memFilter').className.includes('filter-invalid'));
assert.ok(getEl('memBody').innerHTML.includes('0xZZ'));

// U7G-09: changed memory cell renders non-color change-badge marker
render(makeState({
  memory: {
    spaces: ['data'],
    current_space: 'data',
    cells: [{ addr: 0, addr_hex: '0x00', initialized: true, value: 5, hex: '0x05', unsigned: 5, unsigned_str: '5', signed: 5, signed_str: '5', changed: true }],
    filter_errors: [],
  }
}));
assert.ok(getEl('memBody').innerHTML.includes('change-badge'));

// U7G-10: Escape closes #settingsPopover and restores focus to #btnSettings
toggleSettings({ stopPropagation() {} });
assert.ok(getEl('settingsPopover').classList.contains('open'));
for (const fn of (winListeners.keydown || [])) {
  fn({ key: 'Escape', preventDefault() {} });
}
assert.equal(getEl('settingsPopover').classList.contains('open'), false);
assert.equal(document.activeElement, getEl('btnSettings'));
""")
