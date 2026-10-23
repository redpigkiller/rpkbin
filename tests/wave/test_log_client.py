"""Behavioral checks for the log-chunk merger embedded in Wave's page."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


WEB_PY = Path(__file__).parents[2] / "rpkbin" / "wave" / "web.py"
NODE = shutil.which("node")


def _page_script():
    html = WEB_PY.read_text(encoding="utf-8")
    match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert match, "Wave page script was not found"
    return re.sub(
        r"\nsetupMonitorLayout\(\);\s*\npollState\(\);\s*$", "\n", match.group(1)
    )


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
