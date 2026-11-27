"""Client-side unit tests for Stage U3 Topbar, Resources table, Queue Editor, Reload, and C2 layout."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


WAVE = Path(__file__).parents[2] / "rpkbin" / "wave"
APP_JS = WAVE / "static" / "app.js"
JOBS_JS = WAVE / "jobs.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def run_u3(checks: str) -> None:
    app_src = APP_JS.read_text(encoding="utf-8")
    script, removed = re.subn(
        r"\nsetupMonitorLayout\(\);\s*\npollState\(\);\s*$", "\n", app_src
    )
    assert removed == 1
    combined = JOBS_JS.read_text(encoding="utf-8") + "\n" + script + r"""
const nodes = new Map(), calls = [], feedback = [], alerts = [];
const storage = new Map();
window.localStorage = { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v) };
function makeNode(id) {
  const n = {
    id, value: '', hidden: false, disabled: false, checked: false, open: false,
    textContent: '', innerHTML: '', placeholder: '', title: '', dataset: {}, attrs: {},
    classes: new Set(), style: { setProperty() {} }, isConnected: true,
    children: [], firstElementChild: null,
    getBoundingClientRect: () => ({ width: 800, height: 600 }),
    getClientRects: () => [{}],
    querySelectorAll: () => [],
    querySelector: () => null,
    closest: () => null,
    appendChild(child) { child.parent = id; n.children.push(child); n.firstElementChild = n.children[0] || null; return child; },
    insertBefore(child, ref) { const idx = ref ? n.children.indexOf(ref) : -1; const old = n.children.indexOf(child); if (old >= 0) n.children.splice(old, 1); if (idx >= 0) n.children.splice(idx, 0, child); else n.children.push(child); n.firstElementChild = n.children[0] || null; return child; },
    remove() {},
    addEventListener() {},
    contains: () => false,
    replaceChildren() { this.innerHTML = ''; this.children = []; this.firstElementChild = null; },
    setAttribute(k, v) { this[k] = v; this.attrs[k] = v; },
    removeAttribute(k) { delete this[k]; delete this.attrs[k]; },
    getAttribute(k) { return this.attrs[k]; },
    focus() { this.focused = true; document.activeElement = this; },
    classList: {
      toggle(cls, force) { const on = force === undefined ? !n.classes.has(cls) : !!force; on ? n.classes.add(cls) : n.classes.delete(cls); },
      add(cls) { n.classes.add(cls); },
      remove(cls) { n.classes.delete(cls); },
      contains(cls) { return n.classes.has(cls); },
    },
  };
  return n;
}
document.getElementById = id => { if (!nodes.has(id)) nodes.set(id, makeNode(id)); return nodes.get(id); };
document.createElement = tag => {
  const node = makeNode(tag);
  if (tag === 'template') {
    const inner = makeNode('tr');
    inner.firstElementChild = makeNode('td');
    node.content = { firstElementChild: inner };
  }
  return node;
};
document.querySelectorAll = () => [];
document.querySelector = () => null;
document.body = makeNode('body');
recordFeedback = (...args) => feedback.push(args);
addWarning = (...args) => alerts.push(args);
syncDetailReaders = () => {};
renderDetail = () => {};
fetchState = async () => {};
fetchJson = async (path, options) => {
  calls.push({ path, body: JSON.parse(options.body) });
  return { ok: true, affected: 1, results: [{ job_id: 'a', ok: true, status: 'accepted', reason: 'ok' }], message: 'done' };
};
const mkJob = (id, overrides={}) => ({
  id, name: id, attempt_id: id + '-a1', submission_id: id + '-s1', attempt_number: 1,
  status: 'PENDING', priority: 10, held: false, can_rerun: false, resources: {},
  capabilities: { has_process: true, can_stop: true, can_kill: true, can_rerun: true },
  ...overrides
});
CONNECTED = true;
STATE = {
  workspace_id: 'ws-1', started: false, paused: true, ready: true,
  max_workers: 4, active_workers: 0, scheduling_mode: 'backfill',
  resources: { matlab_license: 1, grid: 2 }, resources_in_use: { matlab_license: 0, grid: 0 },
  source_path: 'D:/work/sim.py', launch_cwd: 'D:/work',
  jobs: [
    mkJob('a', { resources: { matlab_license: 1, grid: 1 }, priority: 40 }),
    mkJob('b', { resources: { matlab_license: 1, grid: 1 }, priority: 30 }),
    mkJob('c', { resources: { grid: 1 }, priority: 20, held: true }),
  ]
};
(async () => {
""" + checks + r"""
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    harness = (
        "const vm=require('node:vm'), assert=require('node:assert/strict');\n"
        "const document={addEventListener(){}}, getComputedStyle=()=>({visibility:'visible'});\n"
        "vm.runInNewContext("
        + json.dumps(combined)
        + ",{assert,document,getComputedStyle,window:{},URLSearchParams,Headers,console,process});\n"
    )
    result = subprocess.run(
        [NODE, "-"], input=harness.encode("utf-8"), capture_output=True, timeout=10
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode(
        "utf-8", errors="replace"
    )


def test_default_jobs_layout_and_edit_queue_auto_exits_for_inspect():
    run_u3(r"""
assert.equal(WORKSPACE_LAYOUT, 'jobs');
toggleEditQueue(true);
assert.equal(EDIT_QUEUE_MODE, true);
assert.equal(el('jobsTableWrap').hidden, true);
assert.equal(el('queueEditorView').hidden, false);
setWorkspaceLayout('split');
assert.equal(WORKSPACE_LAYOUT, 'split');
assert.equal(EDIT_QUEUE_MODE, false);
assert.equal(el('jobsTableWrap').hidden, false);
assert.equal(el('queueEditorView').hidden, true);
assert.equal(JobsColumns.scope(), 'jobs-split');
let colHead = el('jobTableHead').innerHTML;
assert.ok(!colHead.includes('data-reading-field="priority"'));
setWorkspaceLayout('jobs');
assert.equal(JobsColumns.scope(), 'jobs-overview');
assert.ok(el('jobTableHead').innerHTML.includes('data-reading-field="priority"'));
setWorkspaceLayout('charts');
setWorkspaceLayout('split');
assert.equal(JobsColumns.scope(), 'jobs-split');
assert.ok(!el('jobTableHead').innerHTML.includes('data-reading-field="priority"'));
""")


def test_resources_panel_summary_live_holders_and_nonclobber_save():
    run_u3(r"""
STATE.started = true; STATE.paused = false; STATE.active_workers = 1;
STATE.jobs[0].status = 'RUNNING';
STATE.resources_in_use = { matlab_license: 1, grid: 1 };
renderTopBar();
assert.equal(el('workspaceInfo').textContent, 'sim.py');
assert.ok(el('workspaceInfo').title.includes('cwd: D:/work'));
assert.equal(el('resourcesButtonText').textContent, '1/4 W · 1 Full ▾');
assert.ok(el('resourcesMenu').open === false);
assert.ok(el('resourcesTableBody').innerHTML.includes('matlab_license'));
assert.ok(el('resourcesTableBody').innerHTML.includes('>a<') || el('resourcesTableBody').innerHTML.includes('>a</td>'));

// While resourcesMenu is closed and keys are unchanged, polling must NOT rebuild resourcesTableBody
let bodyWrites = 0;
const tbody = el('resourcesTableBody');
const savedHtml = tbody.innerHTML;
Object.defineProperty(tbody, 'innerHTML', {
  get() { return savedHtml; },
  set() { bodyWrites++; }
});
renderResourcesPanel(false);
assert.equal(bodyWrites, 0);

// While resourcesMenu is open, polling renderResourcesPanel(false) must not overwrite user input
el('resourcesMenu').open = true;
el('resourcesTableBody').dataset.keys = 'max_workers\0matlab_license\0grid';
el('resSchedulingMode').value = 'strict';
el('resMaxWorkers').value = '2';
const q0 = makeNode('resQuota1'); q0.dataset = { resource: 'matlab_license', resQuota: 'matlab_license' }; q0.value = '2';
el('resourcesTableBody').querySelectorAll = sel => sel === 'input[data-res-quota]' ? [q0] : [];
renderResourcesPanel(false);
assert.equal(el('resSchedulingMode').value, 'strict');
assert.equal(el('resMaxWorkers').value, '2');

await saveResourcesPanel();
assert.equal(calls.length, 1);
assert.equal(calls[0].path, '/api/settings/save');
assert.deepEqual(calls[0].body, {
  workspace_id: 'ws-1', max_workers: 2,
  resources: { matlab_license: 2 }, scheduling_mode: 'strict'
});
""")


def test_queue_editor_buckets_held_badges_and_api_actions():
    run_u3(r"""
const heldCell = JobsColumns.cell(STATE.jobs[2], 'status');
assert.match(heldCell, /st-HELD"[^>]*>[\s\S]*HELD/);
el('heldFilter').checked = true;
assert.deepEqual(filteredJobs().map(j => j.id), ['c']);
el('heldFilter').checked = false;

// Pending click filter strictly excludes Held job 'c'
quickFilterState('PENDING');
assert.deepEqual(filteredJobs().map(j => j.id), ['a', 'b']);
quickFilterState('PENDING');

// Summary pills include Running and Pending (excluding Held)
STATE.jobs[0].status = 'RUNNING';
let summaryHtml = '';
setMarkup = (id, html) => { if (id === 'summaryPills') summaryHtml = html; };
renderTopBar();
assert.ok(summaryHtml.includes('Running: 1'));
assert.ok(summaryHtml.includes('Pending: 1'));
assert.ok(summaryHtml.includes('Held: 1'));
STATE.jobs[0].status = 'PENDING';

const b = queueEditorBuckets();
assert.deepEqual(b.pendingJobs.map(j => j.id), ['a', 'b']);
assert.deepEqual(b.heldJobs.map(j => j.id), ['c']);
assert.equal(b.isPreview, true);

// 2-row reorder: move 'b' above 'a' -> payload is ['b', 'a', 'c']
await reorderPendingIds(['b', 'a']);
assert.equal(calls[0].path, '/api/jobs/reorder');
assert.deepEqual(calls[0].body.targets.map(t => t.job_id), ['b', 'a', 'c']);

await holdQueueJobs([STATE.jobs[0]], true);
assert.equal(calls[1].path, '/api/jobs/hold');
assert.equal(calls[1].body.held, true);

await removeQueueJobs([STATE.jobs[1]]);
assert.equal(calls[2].path, '/api/jobs/remove');

// Reload does not crash rememberLoadedPaths
await reloadWorkFile();
assert.equal(calls[3].path, '/api/work/reload');
assert.equal(alerts.length, 0);

// Rendering Queue Editor across mixed RUNNING + WAITING_INPUT + PENDING + HELD + DONE jobs
// uses uniform leading rounded pills and terse neutral controls
STATE.started = true; STATE.paused = false; STATE.active_workers = 1;
STATE.jobs = [
  mkJob('a', { status: 'RUNNING', priority: 40, elapsed: 1.5 }),
  mkJob('p', { status: 'WAITING_INPUT', priority: 35 }),
  mkJob('b', { status: 'PENDING', priority: 30 }),
  mkJob('k', { status: 'PENDING', priority: 5, held: true }),
  mkJob('done1', { status: 'DONE', result: 'PASS', can_rerun: true, priority: 1 }),
];
toggleEditQueue(true);
const qeHtml = el('queueEditorView').innerHTML;
const qeHeadHtml = el('qeHeaderInline').innerHTML;
assert.ok(qeHtml.includes('done1'));
assert.ok(qeHtml.includes('Re-queue'));
assert.ok(qeHeadHtml.includes('Queue Editor'));
assert.ok(!qeHtml.includes('Stage Safely'));
assert.ok(qeHtml.includes('Scheduling Mode:'));
assert.ok(qeHtml.includes('qe-status-pill st-WAITING_INPUT">PROMPT'));
assert.ok(qeHtml.includes('qe-status-pill st-HELD">'));
assert.ok(qeHtml.includes('qe-row qe-held'));
assert.ok(qeHtml.includes('qe-grip-spacer'));
assert.equal(el('paneLeft').classList.contains('queue-editing'), true);
assert.equal(el('btnEditQueue').hidden, false);
assert.equal(el('btnEditQueue').textContent, 'Close Queue Editor');
assert.equal(el('btnEditQueue').attrs['aria-pressed'], 'true');
""")


def test_phase21_queue_editor_no_busy_flash_soft_delete_restore_and_inline_scheduling_mode():
    run_u3(r"""
STATE.removed_jobs = [
  mkJob('gone1', { status: 'CANCELLED', priority: 15, resources: { grid: 1 } }),
];
toggleEditQueue(true);

// #1: Busy flag during another poll/edit does not disable Queue Editor buttons
BUSY = true;
renderQueueEditor(true);
BUSY = false;
const qeHtml = el('queueEditorView').innerHTML;
assert.ok(!qeHtml.includes('data-qe-action="hold_one" disabled'));

// runQueueEditAction clears BUSY before awaiting fetchState
let fetchStateBusy = null;
fetchState = async () => { fetchStateBusy = BUSY; };
await reorderPendingIds(['b', 'a']);
assert.equal(fetchStateBusy, false);

// #3: Collapsible Removed section + Restore API call
assert.ok(qeHtml.includes('qe-section qe-removed-section'));
assert.ok(qeHtml.includes('<strong>Removed</strong> (1)'));
assert.ok(qeHtml.includes('data-qe-action="restore_all"'));
assert.ok(qeHtml.includes('data-qe-action="restore_one"'));
assert.ok(qeHtml.includes('gone1'));

await restoreQueueJobs(STATE.removed_jobs);
const restoreCall = calls.find(c => c.path === '/api/jobs/restore');
assert.ok(restoreCall);
assert.deepEqual(restoreCall.body.targets.map(t => t.job_id), ['gone1']);

// #4: Inline Scheduling Mode select calls /api/settings/save
assert.ok(qeHtml.includes('id="qeSchedulingMode" data-qe-scheduling'));
assert.ok(qeHtml.includes('Scheduling Mode:'));
await setQueueSchedulingMode('strict');
const schedCall = calls.find(c => c.path === '/api/settings/save');
assert.deepEqual(schedCall.body.scheduling_mode, 'strict');

// Phase 2.2 #2: suppress generic "Ready to start" dummy note while keeping real blockers; apply qe-just-moved class
STATE.jobs = [
  mkJob('a', { priority: 40, pending_reason: 'Ready to start' }),
  mkJob('b', { priority: 30, pending_reason: 'Waiting for matlab_license (1/1)' }),
];
markQueueRowMoved('b');
renderQueueEditor(true);
const movedHtml = el('queueEditorView').innerHTML;
assert.ok(!movedHtml.includes('Ready to start'));
assert.ok(movedHtml.includes('Waiting for matlab_license (1/1)'));
assert.ok(movedHtml.includes('qe-row qe-just-moved'));
""")
