"""Behavioral checks for the log-chunk merger embedded in Wave's page."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = Path(__file__).parents[2] / "rpkbin" / "wave" / "static" / "app.js"
NODE = shutil.which("node")


def _page_script():
    source = APP_JS.read_text(encoding="utf-8")
    script, removed = re.subn(
        r"\nsetupMonitorLayout\(\);\s*\npollState\(\);\s*$", "\n", source
    )
    assert removed == 1, "Wave page script was not found"
    return script


@pytest.mark.skipif(
    NODE is None, reason="Node.js is required for the browser-side check"
)
def test_log_chunk_merge_behaviors():
    script = _page_script()

    checks = r"""
const chunk = (overrides = {}) => Object.assign({
  job_id: 'job-a', attempt_id: 'attempt-1', first_line: 1, next_line: 0,
  retained_count: 10, capacity: 10, reset: true, records: []
}, overrides);
const lines = (...numbers) => numbers.map(line_no => ({ line_no, text: `line ${line_no}` }));
const merge = (cache, value, workspace = 'workspace-a') => mergeLogChunk(cache, value, workspace);
const same = (actual, expected) => assert.deepStrictEqual(JSON.parse(JSON.stringify(actual)), expected);

// Initial snapshot, then appended output and an empty no-change response.
let cache = merge(null, chunk({ next_line: 2, records: lines(1, 2) }));
same(cache.records, lines(1, 2));
cache = merge(cache, chunk({ reset: false, first_line: 1, next_line: 3, records: lines(3) }));
same(cache.records, lines(1, 2, 3));
cache = merge(cache, chunk({ reset: false, first_line: 1, next_line: 3, records: [] }));
same(cache.records, lines(1, 2, 3));

// Replaying the same response cannot duplicate already merged lines.
const replay = chunk({ reset: false, first_line: 1, next_line: 3, records: lines(3) });
cache = merge(cache, replay);
cache = merge(cache, replay);
same(cache.records, lines(1, 2, 3));

// Server retention bounds evict old lines even when no new records arrive;
// growing retention later cannot reconstruct lines already discarded.
cache = merge(cache, chunk({ reset: false, first_line: 3, next_line: 3, records: [] }));
same(cache.records, lines(3));
cache = merge(cache, chunk({ reset: false, first_line: 1, next_line: 3, retained_count: 10, records: [] }));
same(cache.records, lines(3));

// An eviction/reset snapshot replaces the old window. A new attempt on the
// same job starts with a fresh line sequence at 1.
cache = merge(cache, chunk({ reset: true, first_line: 8, next_line: 9, records: lines(8, 9) }));
same(cache.records, lines(8, 9));
cache = merge(cache, chunk({ attempt_id: 'attempt-2', first_line: 1, next_line: 1, records: lines(1) }));
same(cache.records, lines(1));
assert.strictEqual(cache.attempt_id, 'attempt-2');

// Reset snapshots isolate executions even when attempt labels are reused.
cache = merge(cache, chunk({ job_id: 'job-b', attempt_id: 'attempt-2', next_line: 1, records: lines(1) }));
assert.strictEqual(cache.job_id, 'job-b');
cache = merge(cache, chunk({ job_id: 'job-a', attempt_id: 'attempt-2', next_line: 1, records: lines(1) }), 'workspace-b');
assert.strictEqual(cache.workspace_id, 'workspace-b');

// A delta cannot be applied to a cache from another workspace, job, or attempt.
for (const [workspace, delta] of [
  ['workspace-b', chunk({ reset: false, job_id: 'job-z', attempt_id: 'attempt-2' })],
  ['workspace-c', chunk({ reset: false, job_id: 'job-a', attempt_id: 'attempt-2' })],
  ['workspace-b', chunk({ reset: false, job_id: 'job-a', attempt_id: 'other-attempt' })]
]) {
  assert.throws(() => merge(cache, delta, workspace), /different execution/);
}

// A terminal status alone does not imply worker cleanup has released the job.
assert.strictEqual(allowed({status: 'DONE', can_rerun: false}, 'rerun'), false);
assert.strictEqual(allowed({status: 'DONE', can_rerun: true}, 'rerun'), true);
assert.strictEqual(allowed({status: 'PENDING', can_rerun: true}, 'rerun'), false);
"""

    # Execute the real page script with only its DOM event-registration boundary stubbed.
    harness = (
        "const vm = require('node:vm');\n"
        "const assert = require('node:assert/strict');\n"
        "vm.runInNewContext("
        + repr(script + "\n" + checks)
        + ", { assert, document: { addEventListener() {} }, window: {} });\n"
    )
    result = subprocess.run(
        [NODE, "-"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode(
        "utf-8", errors="replace"
    )


@pytest.mark.skipif(
    NODE is None, reason="Node.js is required for the browser-side check"
)
def test_fetch_state_discards_stale_response_and_recovers_from_zero_cursor():
    checks = r"""
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const requests = [];
const urls = [];
fetchJson = url => { urls.push(url); return requests.shift().promise; };
setConnection = yes => { CONNECTED = yes; };
renderTopBar = () => {};
renderMonitor = () => {};
closePanels = () => {};
updateControls = () => {};
document.getElementById = () => ({ hidden: true });
const stateFor = (jobId, attemptId, workspace = 'workspace-a') => ({
  workspace_id: workspace,
  jobs: [{ id: 'job-a', attempt_id: 'attempt-a' }, { id: 'job-b', attempt_id: 'attempt-b' }],
  available_columns: [],
  detail: {
    id: jobId,
    attempt_id: attemptId,
    log_chunk: {
      job_id: jobId, attempt_id: attemptId, first_line: 1, next_line: 1,
      retained_count: 10, capacity: 10, reset: true,
      records: [{ line_no: 1, text: jobId }]
    }
  }
});
const query = index => new URL(urls[index], 'http://wave.test').searchParams;

(async () => {
  STATE = { workspace_id: 'workspace-a', jobs: [
    { id: 'job-a', attempt_id: 'attempt-a' }, { id: 'job-b', attempt_id: 'attempt-b' }
  ], detail: null, columns: [] };
  SELECTED_JOB_ID = 'job-a';
  SELECTED_ATTEMPT_ID = 'attempt-a';
  const oldA = deferred();
  requests.push(oldA);
  const pendingA = fetchState();

  // The user switches jobs while A is in flight. B completes first.
  SELECTED_JOB_ID = 'job-b';
  SELECTED_ATTEMPT_ID = 'attempt-b';
  STATE.detail = null;
  LOG_CACHE = null;
  const newB = deferred();
  requests.push(newB);
  const pendingB = fetchState();
  newB.resolve(stateFor('job-b', 'attempt-b'));
  await pendingB;
  assert.strictEqual(STATE.detail.id, 'job-b');
  assert.strictEqual(LOG_CACHE.job_id, 'job-b');
  assert.strictEqual(query(1).get('log_after'), '0');

  // A's late response must not replace B's state or log cache.
  oldA.resolve(stateFor('job-a', 'attempt-a'));
  await pendingA;
  assert.strictEqual(STATE.detail.id, 'job-b');
  assert.strictEqual(LOG_CACHE.job_id, 'job-b');

  // A current request failure clears the cursor; recovery starts at zero.
  const failed = deferred();
  requests.push(failed);
  const pendingFailure = fetchState();
  failed.reject(new Error('offline'));
  await pendingFailure;
  assert.strictEqual(CONNECTED, false);
  assert.strictEqual(LOG_CACHE, null);

  const reconnect = deferred();
  requests.push(reconnect);
  const pendingReconnect = fetchState();
  assert.strictEqual(query(3).get('log_after'), '0');
  assert.strictEqual(query(3).has('log_attempt'), false);
  reconnect.resolve(stateFor('job-b', 'attempt-b'));
  await pendingReconnect;
  assert.strictEqual(CONNECTED, true);
  assert.strictEqual(LOG_CACHE.job_id, 'job-b');

  // A replacement workspace can reuse identity strings. Discard the response
  // made with the old cursor, fetch its summary, then reset the new selection.
  const replacement = deferred(), summary = deferred(), selected = deferred();
  requests.push(replacement, summary, selected);
  const pendingWorkspace = fetchState();
  const replacementState = stateFor('job-b', 'attempt-b', 'workspace-new');
  replacementState.detail.log_chunk.reset = false;
  replacement.resolve(replacementState);
  const newSummary = stateFor(null, null, 'workspace-new');
  newSummary.detail = null;
  summary.resolve(newSummary);
  selected.resolve(stateFor('job-a', 'attempt-a', 'workspace-new'));
  await pendingWorkspace;
  assert.strictEqual(STATE.workspace_id, 'workspace-new');
  assert.strictEqual(LOG_CACHE.workspace_id, 'workspace-new');
  assert.strictEqual(LOG_CACHE.job_id, 'job-a');
  assert.strictEqual(LOG_CACHE.records[0].text, 'job-a');
  assert.strictEqual(query(5).get('log_after'), '0');
  assert.strictEqual(query(5).has('log_attempt'), false);
  assert.strictEqual(query(6).get('job_id'), 'job-a');
})().then(() => console.log('fetch-state-checks-complete'))
  .catch(error => { console.error(error); process.exitCode = 1; });
"""
    harness = (
        "const vm = require('node:vm');\n"
        "const assert = require('node:assert/strict');\n"
        "const document = { addEventListener() {}, getElementById() { return { hidden: true }; } };\n"
        "const window = {};\n"
        "const context = vm.createContext({ assert, document, window, URL, URLSearchParams, console, process });\n"
        "vm.runInContext(" + repr(_page_script() + "\n" + checks) + ", context);\n"
    )
    result = subprocess.run(
        [NODE, "-"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode(
        "utf-8", errors="replace"
    )
    assert b"fetch-state-checks-complete" in result.stdout


@pytest.mark.skipif(
    NODE is None, reason="Node.js is required for the browser-side check"
)
def test_analysis_log_ansi_find_and_tail_behaviors():
    analysis_source = (Path(__file__).parents[2] / "rpkbin" / "wave" / "analysis.js").read_text(encoding="utf-8")
    checks = r"""
const nodes = new Map();
function element(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, dataset: {}, value: '', checked: false, hidden: false, disabled: false,
    attributes: {}, listeners: {}, textContent: '', innerHTML: '',
    scrollTop: 0, scrollHeight: 10000, clientHeight: 300, children: [],
    classList: { toggle() {} },
    addEventListener(event, fn) { (this.listeners[event] ??= []).push(fn); },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    focus() { document.activeElement = this; },
    select() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { top: 0, bottom: 300 }; },
    appendChild(child) { child.parentElement = this; },
    replaceChildren() { this.innerHTML = ''; }
  });
  return nodes.get(id);
}
const document = { getElementById: element, activeElement: null };
const STATE = { workspace_id: 'ws', archive: { run_id: 'run1', enabled: true } };
const RunHistory = { selection: null };
let WORKSPACE_LAYOUT = 'split';
const queries = [];
let responder = async () => ({ rows: [], first: null, last: null, next: null, has_more: false, scanned: 0, scanned_seq: 0, end_seq: 0 });
function fetchJson(url) {
  const q = Object.fromEntries(new URL(url, 'http://wave.test').searchParams);
  queries.push(q);
  return responder(q);
}
function escHtml(v) {
  return String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}
""" + analysis_source + r"""
(async () => {
  // 1. ANSI stripping and SGR rendering
  assert.strictEqual(AnalysisWorkspace.stripAnsi('\x1b[1;32mPASS\x1b[0m \x1b[2Kdone'), 'PASS done');
  const parsed = AnalysisWorkspace.parseAnsi('\x1b[33mWARN\x1b[0m: check');
  assert.strictEqual(parsed.plain, 'WARN: check');
  assert.strictEqual(parsed.runs[0].classes, 'ansi-fg-yellow');

  // 2. Hidden layout guard stops background polling in jobs/charts layout
  WORKSPACE_LAYOUT = 'jobs';
  await AnalysisWorkspace.refresh({ id: 'j1', attempt_id: 'a1', total_lines: 5 });
  assert.strictEqual(queries.length, 0);
  WORKSPACE_LAYOUT = 'charts';
  await AnalysisWorkspace.refresh({ id: 'j1', attempt_id: 'a1', total_lines: 5 });
  assert.strictEqual(queries.length, 0);

  // 3. Incremental forward tailing and Find-in-Context vs Filter Mode
  WORKSPACE_LAYOUT = 'split';
  element('analysisFollow').checked = true;
  responder = async () => ({
    rows: [
      { seq: 1, text: '\x1b[31mFailure\x1b[0m on step_1', elapsed: 0.1, source: 'stdout' },
      { seq: 2, text: 'step_2 failure retry', elapsed: 0.2, source: 'stdout' }
    ],
    first: 'c1', last: 'c2', next: 'c2', has_more: false, scanned: 2, scanned_seq: 2, end_seq: 2
  });
  await AnalysisWorkspace.refresh({ id: 'j1', attempt_id: 'a1', total_lines: 2 });
  assert.strictEqual(queries.length, 1);
  assert.strictEqual(queries[0].direction, 'backward');

  responder = async () => ({
    rows: [{ seq: 3, text: 'step_3 FAILURE final', elapsed: 0.3, source: 'stdout' }],
    first: 'c3', last: 'c3', next: 'c3', has_more: false, scanned: 1, scanned_seq: 3, end_seq: 3
  });
  await AnalysisWorkspace.refresh({ id: 'j1', attempt_id: 'a1', total_lines: 3 });
  assert.strictEqual(queries.length, 2);
  assert.strictEqual(queries[1].direction, 'forward');
  assert.strictEqual(queries[1].cursor, 'c2');

  // Find-in-Context (Mode B) highlights all 3 matches and steps through them
  element('analysisQuery').value = 'failure';
  await AnalysisWorkspace.apply();
  assert.strictEqual(element('analysisMatchCount').textContent, '1 of 3');
  await AnalysisWorkspace.nextMatch();
  assert.strictEqual(element('analysisMatchCount').textContent, '2 of 3');
  await AnalysisWorkspace.prevMatch();
  assert.strictEqual(element('analysisMatchCount').textContent, '1 of 3');

  // Clearing query immediately clears highlights and counter
  element('analysisQuery').value = '';
  element('analysisQuery').listeners.input[0]();
  assert.strictEqual(element('analysisMatchCount').textContent, '');
  assert.ok(!element('analysisRows').innerHTML.includes('<mark class="log-hit'));
})().then(() => console.log('analysis-log-checks-complete'))
  .catch(error => { console.error(error); process.exitCode = 1; });
"""
    harness = (
        "const vm = require('node:vm');\n"
        "const assert = require('node:assert/strict');\n"
        "const context = vm.createContext({ assert, URL, URLSearchParams, AbortController, Date, setTimeout, clearTimeout, console, process });\n"
        "vm.runInContext(" + repr(checks) + ", context);\n"
    )
    result = subprocess.run(
        [NODE, "-"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode(
        "utf-8", errors="replace"
    )
    assert b"analysis-log-checks-complete" in result.stdout
