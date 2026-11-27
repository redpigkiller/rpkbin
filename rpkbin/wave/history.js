/* Saved-run and attempt selection. Output readers own their own bounded requests. */
const RunHistory = (() => {
  let detail = null, selection = null;
  let attemptsBefore = null, runsCursor = null, openedWorkspace = null, selectionEpoch = 0;
  let catalogEpoch = 0, runsNotice = '';
  const node = id => document.getElementById(id);
  const escape = text => escHtml(text);
  function reset() { detail = null; }
  function refresh(d) { detail = selection?.detail || d; }
  async function query(options = {}, d = detail, requestOptions = {}) {
    const params = new URLSearchParams({
      workspace_id: STATE.workspace_id,
      run_id: selection?.run_id || STATE.archive?.run_id || '',
      ...(d ? {job_id: d.id, attempt_id: d.attempt_id} : {}), ...options
    });
    return fetchJson('/api/archive?' + params, {cache: 'no-store', ...requestOptions});
  }

  function healthText(health) {
    if (health?.error) return 'History save failed — available records may be incomplete';
    if (health?.status === 'unclosed') return 'Unverified archive — committed records only';
    if (health?.status === 'closed') return 'Saved on disk';
    return health?.enabled ? 'Recording to disk' : 'History unavailable';
  }

  async function openRuns() {
    const loading = listRuns();
    openModal('archiveOverlay');
    await loading;
  }

  function isCurrentCatalog(workspace, epoch) {
    return workspace === STATE.workspace_id && epoch === catalogEpoch && !node('archiveOverlay').hidden;
  }

  function clearAttempts() {
    node('archiveAttempt').replaceChildren();
    node('archiveAttempt').disabled = true;
    node('archiveEarlierAttempts').disabled = true;
    node('archiveViewAttempt').disabled = true;
  }

  async function listRuns(earlier = false) {
    const workspace = STATE.workspace_id, epoch = ++catalogEpoch;
    const cursor = earlier ? runsCursor : null;
    runsCursor = cursor;
    attemptsBefore = null;
    runsNotice = '';
    const runSelect = node('archiveRun');
    runSelect.replaceChildren();
    runSelect.disabled = true;
    node('archiveEarlierRuns').disabled = true;
    node('archiveError').textContent = '';
    clearAttempts();
    try {
      const data = await query({view: 'runs', ...(cursor ? {cursor} : {})}, null);
      if (!isCurrentCatalog(workspace, epoch)) return;
      runsCursor = data.cursor;
      runSelect.innerHTML = data.rows.map(run => `<option value="${escape(run.run_id)}">${escape(new Date(run.created * 1000).toLocaleString())} · ${run.closed_cleanly ? 'saved' : 'not closed'} · ${run.run_id.slice(0, 8)}</option>`).join('');
      runSelect.disabled = !data.rows.length;
      node('archiveEarlierRuns').disabled = !data.has_more;
      runsNotice = data.unreadable_files ? `${data.unreadable_files} unreadable archive file(s); they were not treated as empty runs.` : '';
      if (!data.rows.length && !runsNotice) runsNotice = 'No saved runs for this workspace.';
      await listAttempts();
    } catch (error) {
      if (isCurrentCatalog(workspace, epoch)) {
        node('archiveEarlierRuns').disabled = !cursor;
        node('archiveError').textContent = error.message;
      }
    }
  }

  async function listAttempts(earlier = false) {
    const run = node('archiveRun').value, workspace = STATE.workspace_id, epoch = ++catalogEpoch;
    const before = earlier ? attemptsBefore : null;
    attemptsBefore = before;
    clearAttempts();
    node('archiveError').textContent = runsNotice;
    if (!run) return;
    const current = () => isCurrentCatalog(workspace, epoch) && run === node('archiveRun').value;
    try {
      const data = await query({view: 'attempts', run_id: run, ...(before ? {before} : {})}, null);
      if (!current()) return;
      attemptsBefore = data.before;
      const attemptSelect = node('archiveAttempt');
      attemptSelect.innerHTML = data.rows.map(row => `<option value="${escape(row.attempt_id)}" data-job="${escape(row.id)}">${escape(row.name)} · #${row.attempt_number} · ${escape(row.result || row.status)}${row.verified === false ? ' / Unverified' : ''}</option>`).join('');
      attemptSelect.disabled = !data.rows.length;
      node('archiveEarlierAttempts').disabled = !data.has_more;
      node('archiveViewAttempt').disabled = !data.rows.length;
    } catch (error) {
      if (current()) {
        node('archiveEarlierAttempts').disabled = !before;
        node('archiveError').textContent = error.message;
      }
    }
  }

  function updateFollow(saved, now) {
    if (saved.detail.finalized || saved.health?.closed_cleanly) {
      if (saved.follow && saved.follow !== 'finished' && !saved.paused) saved.catchUp = true;
      saved.follow = 'finished';
    } else {
      const heartbeat = saved.health?.writer_heartbeat;
      const timeout = Math.max(20, 4 * (saved.health?.writer_commit_interval || 1));
      const lastHeartbeat = Number.isFinite(heartbeat) ? heartbeat : saved.openedAt;
      if (saved.health?.error || now - lastHeartbeat >= timeout) saved.follow = 'interrupted';
      else saved.follow = saved.paused ? 'paused' : 'following';
    }
  }

  function selectData(run, data) {
    selection = {run_id: run, detail: {...data.attempt, log_records: []}, health: data.health};
    if (data.attempt.source_kind === 'tail' && !data.health?.closed_cleanly) {
      selection.openedAt = Date.now() / 1000;
      updateFollow(selection, selection.openedAt);
    }
    reset();
    if (typeof AnalysisWorkspace !== 'undefined') AnalysisWorkspace.reset();
    setDetailView('log');
    renderDetail();
    updateControls();
  }

  async function pollFollow(now = null) {
    const saved = selection, workspace = STATE.workspace_id, token = selectionEpoch;
    if (!saved?.follow || saved.health?.closed_cleanly || saved.pending) return;
    saved.pending = true;
    try {
      const {attempt, health} = await query({view: 'attempt'}, saved.detail);
      if (saved !== selection || workspace !== STATE.workspace_id || token !== selectionEpoch) return;
      saved.detail = {...attempt, log_records: []};
      saved.health = health;
    } catch {
      // Keep the last committed view when a poll cannot reach the archive.
    } finally {
      saved.pending = false;
    }
    if (saved !== selection || workspace !== STATE.workspace_id || token !== selectionEpoch) return;
    updateFollow(saved, now == null ? Date.now() / 1000 : now);
    renderDetail();
  }

  function toggleFollow(now = Date.now() / 1000) {
    if (!['following', 'paused'].includes(selection?.follow)) return;
    selection.paused = !selection.paused;
    updateFollow(selection, now);
    renderDetail();
  }

  async function chooseAttempt(run, job, attempt, valid = () => true) {
    const workspace = STATE.workspace_id, token = ++selectionEpoch;
    const data = await query({view:'attempt', run_id:run, job_id:job, attempt_id:attempt}, null);
    if (workspace !== STATE.workspace_id || token !== selectionEpoch || !valid()) return;
    selectData(run, data);
    return true;
  }

  async function viewAttempt() {
    const option = node('archiveAttempt').selectedOptions[0];
    if (!option || node('archiveViewAttempt').disabled) return;
    const run = node('archiveRun').value, attempt = option.value;
    const workspace = STATE.workspace_id, epoch = catalogEpoch;
    const current = () => isCurrentCatalog(workspace, epoch) &&
      node('archiveRun').value === run && node('archiveAttempt').value === attempt;
    const request = chooseAttempt(run, option.dataset.job, attempt, current);
    const token = selectionEpoch;
    try {
      const applied = await request;
      if (!applied || token !== selectionEpoch || !current()) return;
      setWorkspaceLayout('split');
      closeModal('archiveOverlay');
    } catch (error) {
      if (token === selectionEpoch && current()) node('archiveError').textContent = error.message;
    }
  }

  async function openInitial(run) {
    if (!run || openedWorkspace === STATE.workspace_id) return;
    const workspace = STATE.workspace_id, token = ++selectionEpoch;
    openedWorkspace = workspace;
    try {
      const attempts = await query({view:'attempts', run_id:run, limit:1}, null);
      const attempt = attempts.rows[0];
      if (workspace !== STATE.workspace_id || token !== selectionEpoch) return;
      if (!attempt) { openedWorkspace = null; return; }
      const data = await query({view:'attempt', run_id:run, job_id:attempt.id, attempt_id:attempt.attempt_id}, null);
      if (workspace !== STATE.workspace_id || token !== selectionEpoch) return;
      selectData(run, data);
    } catch (error) {
      if (openedWorkspace === workspace) openedWorkspace = null;
      if (workspace === STATE.workspace_id && token === selectionEpoch) recordFeedback('Could not open saved attempt: ' + error.message, 'error');
    }
  }

  return {refresh, query, healthText, pollFollow, toggleFollow, openRuns, listRuns, listAttempts, viewAttempt, chooseAttempt, openInitial,
    clearSelection() { ++selectionEpoch; selection = null; reset(); },
    get selection() { return selection; }};
})();
