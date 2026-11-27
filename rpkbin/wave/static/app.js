const PREFS_STORAGE_KEY = 'wave_display_prefs_v1';
const DEFAULT_DISPLAY_PREFS = {
  logFontSize: 13,
  logWrap: true,
  ansiColor: true,
  stackedMode: false,
  splitWidth: 37,
  stackHeight: 38
};
let DISPLAY_PREFS = JSON.parse(JSON.stringify(DEFAULT_DISPLAY_PREFS));
let STATE = {
    jobs: [],
    detail: null,
    columns: [],
    resources: {},
    resources_in_use: {},
    scheduling_mode: 'backfill',
    max_history: 20
  },
  SELECTED_JOB_ID = null,
  SELECTED_ATTEMPT_ID = null,
  CHECKED_JOBS = new Set(),
  EDITOR_CHECKED = new Set(),
  EDIT_QUEUE_MODE = false,
  EDITOR_DRAG_ID = null,
  LAST_MOVED_JOB_ID = null,
  LAST_MOVED_TOKEN = 0,
  QE_REMOVED_OPEN = false,
  IS_DRAGGING_SPLIT = false,
  PENDING_POST_DRAG_RENDER = false,
  SPLIT_DRAG_RAF = 0,
  PENDING_SPLIT_PERCENT = null,
  STDIN_HISTORY = [],
  STDIN_HISTORY_INDEX = -1,
  STDIN_DRAFT = '';
let CONNECTED = false,
  REQUEST_ID = 0,
  HIGHLIGHT_LINE_NO = null,
  LAST_LOG_KEY = '',
  SORT_KEY = '',
  SORT_ASC = true,
  BROWSE_PARENT = null,
  BROWSE_DIRECTORY = null,
  BROWSE_KIND = null,
  BROWSE_REQUEST = 0,
  BROWSE_ENTRIES = [],
  BROWSE_VISIBLE = [],
  BROWSE_SHORTCUTS = [],
  BROWSE_SORT = 'name',
  BROWSE_SORT_ASC = true,
  BROWSE_FILTER = '',
  BROWSE_SELECTED_INDEX = -1,
  BROWSE_HISTORY = [],
  BROWSE_HISTORY_INDEX = -1,
  OPEN_MODE = 'work',
  OPEN_DIALOG_EPOCH = 0,
  ARCHIVE_CATALOG = [],
  ARCHIVE_CATALOG_EPOCH = 0,
  ARCHIVE_CATALOG_CONTROLLER = null,
  ARCHIVE_DELETE_CONFIRM_ID = null,
  FB_WINDOW_RECT = null,
  BUSY = false,
  STOPPING_SOURCES = false,
  STACKED_MODE = false,
  PROGRAMMATIC_SCROLL_UNTIL = 0,
  SETTINGS_SECTION = 'retention',
  SETTINGS_DIALOG_EPOCH = 0;
let PRIORITY_REQUEST = null,
  DETAIL_KEY = '',
  DETAIL_VIEW = 'log',
  LAST_LOG_RENDER_KEY = '',
  LAST_LOG_DOM_KEY = '',
  LOG_CACHE = null,
  SCROLL_TO_EVIDENCE = false,
  STDIN_STATUS_BY_ATTEMPT = {},
  STDIN_HISTORY_BY_ATTEMPT = {},
  RETAINED_MATCHES = [],
  RETAINED_MATCH_INDEX = -1;
const JOB_SEARCH_CACHE = new WeakMap();
const ACTIVE = new Set(['RUNNING', 'WAITING_INPUT', 'STOPPING']),
  FINISHED = new Set(['DONE', 'FAILED', 'CANCELLED']);
const LABELS = {
  stop: 'Stop',
  force_stop: 'Force Stop',
  rerun: 'Rerun',
  send: 'Send',
  interrupt: 'Ctrl+C',
  max_log_lines: 'Log Retention',
  priority: 'Priority',
  hold: 'Hold',
  include: 'Include',
  reorder: 'Reorder Queue',
  remove: 'Remove',
  restore: 'Restore',
  requeue: 'Re-queue',
  signal: 'Signal'
};
const STOP_HINT = 'Stop automatically escalates to Force Stop if the process does not exit in time.';
const INTERRUPT_HINT = 'Send one interrupt signal; no stop timeout. The program may handle or ignore it. Ctrl+C will not trigger a retry. Use Stop to request termination.';
const el = id => document.getElementById(id),
  currentJob = () => STATE.jobs.find(j => j.id === SELECTED_JOB_ID),
  isHistory = () => !!SELECTED_ATTEMPT_ID || (typeof RunHistory !== 'undefined' && !!RunHistory.selection);
const currentDetailMatches = () => !!STATE.detail && STATE.detail.id === SELECTED_JOB_ID && STATE.detail
  .attempt_id === currentJob()?.attempt_id;

function escHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function setMarkup(id, markup) {
  const box = el(id);
  if (box.dataset.markup !== markup) {
    box.innerHTML = markup;
    box.dataset.markup = markup;
  }
}

const MODAL_STACK = [], MODAL_FOCUS = new Map();
const MODAL_FALLBACK = {
  workOverlay: 'btnOpenWorkspace', fileBrowserOverlay: 'btnOpenWorkspace', archiveOverlay: 'btnOpenWorkspace',
  settingsOverlay: 'btnSettings', statusWarningOverlay: 'btnOpenWorkspace',
  reportOverlay: 'btnOpenWorkspace',
  priorityOverlay: 'jobSearch', jobPickerOverlay: 'detailTitle',
  flowSpecOverlay: 'detailTitle', flowReportOverlay: 'detailTitle'
};
function canFocus(node) {
  return node?.isConnected && !node.disabled && node.getClientRects().length &&
    getComputedStyle(node).visibility === 'visible';
}
function restoreFocus(prior, fallback) {
  const menu = prior?.closest?.('details');
  const trigger = menu && !menu.open ? menu.querySelector('summary') : prior;
  const stable = fallback?.matches('details') ? fallback.querySelector('summary') : fallback;
  const target = [trigger, stable, el('btnOpenWorkspace')].find(canFocus);
  target?.focus({preventScroll: true});
}
function modalControls(id) {
  return [...el(id).querySelectorAll('button,input,select,textarea,summary,a[href],[tabindex]')]
    .filter(x => x.tabIndex !== -1 && canFocus(x));
}
function modalDialog(id) {
  return el(id).querySelector('[role="dialog"]');
}
function openModal(id, focusId) {
  if (id === 'flowSpecOverlay') {
    clearTimeout(window._specCopyTimer);
    if (el('flowSpecCopyStatus')) el('flowSpecCopyStatus').textContent = '';
  }
  if (el(id).hidden) {
    MODAL_FOCUS.set(id, document.activeElement);
    MODAL_STACK.push(id);
  }
  el(id).hidden = false;
  const preferred = focusId && el(focusId);
  const controls = modalControls(id);
  const firstBody = controls.find(x => !x.closest?.('.card-title'));
  (canFocus(preferred) ? preferred : firstBody || controls[0] || modalDialog(id)).focus();
}
function closeModal(id) {
  if (el(id).hidden) return;
  if (id === 'flowSpecOverlay') {
    clearTimeout(window._specCopyTimer);
    if (el('flowSpecCopyStatus')) el('flowSpecCopyStatus').textContent = '';
    if (typeof FlowWorkspace !== 'undefined') FlowWorkspace.closeInspection();
  }
  el(id).hidden = true;
  const index = MODAL_STACK.indexOf(id);
  if (index >= 0) MODAL_STACK.splice(index, 1);
  const prior = MODAL_FOCUS.get(id);
  MODAL_FOCUS.delete(id);
  if (index !== MODAL_STACK.length) return;
  const parent = MODAL_STACK.at(-1);
  if (parent) {
    const controls = modalControls(parent);
    (controls.includes(prior) ? prior : controls[0] || modalDialog(parent)).focus({preventScroll: true});
  } else restoreFocus(prior, el(MODAL_FALLBACK[id]));
}
async function copyFlowSpecCode() {
  const codeEl = el('flowSpecCode'), statusEl = el('flowSpecCopyStatus');
  const text = codeEl?.textContent || '';
  if (!text) return;
  let copied = false;
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch (_) {}
  if (!copied && typeof document !== 'undefined' && typeof document.createElement === 'function') {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute?.('readonly', '');
      if (ta.style) { ta.style.position = 'fixed'; ta.style.left = '-9999px'; ta.style.opacity = '0'; }
      document.body?.appendChild?.(ta);
      ta.select?.();
      copied = !!document.execCommand?.('copy');
      ta.remove?.();
    } catch (_) {}
  }
  if (statusEl) {
    clearTimeout(window._specCopyTimer);
    statusEl.textContent = copied ? 'Copied' : 'Copy unavailable';
    window._specCopyTimer = setTimeout(() => {
      if (el('flowSpecCopyStatus')) el('flowSpecCopyStatus').textContent = '';
    }, 1800);
  }
}

function dismissControlMenusOutside(target) {
  document.querySelectorAll('.control-menu[open]').forEach(menu => {
    if (!menu.contains(target)) menu.open = false;
  });
}
document.addEventListener('pointerdown', event => dismissControlMenusOutside(event.target));
document.addEventListener('focusin', event => dismissControlMenusOutside(event.target));
document.addEventListener('click', event => {
  if (event.target.closest?.('.control-popup button') &&
      !event.target.closest('#analysisFilters') &&
      !event.target.closest('#logOptionsMenu') &&
      !event.target.closest('[data-keep-menu-open]')) {
    const menu = event.target.closest('details');
    menu?.removeAttribute('open');
    if (menu?.contains(document.activeElement) || document.activeElement === document.body) {
      menu?.querySelector('summary')?.focus();
    }
  }
});

function closeStopAllMenu() {
  const menu = el('stopAllMenu');
  if (menu) menu.open = false;
  if (el('forceAllConfirm')) el('forceAllConfirm').hidden = true;
}
function promptForceStopAll(event) {
  event?.stopPropagation?.();
  if (el('btnForceAll')?.disabled) return;
  if (el('forceAllConfirm')) el('forceAllConfirm').hidden = false;
  el('btnForceAllCancel')?.focus?.();
}
function cancelForceStopAll(event) {
  event?.stopPropagation?.();
  if (el('forceAllConfirm')) el('forceAllConfirm').hidden = true;
  el('btnForceAll')?.focus?.();
}
function confirmForceStopAll() {
  closeStopAllMenu();
  allAction('force_stop');
}

function toggleHelpTip(btn, force) {
  if (!btn) return;
  const targetId = btn.getAttribute?.('aria-controls');
  const panel = targetId ? el(targetId) : null;
  const next = typeof force === 'boolean' ? force : btn.getAttribute?.('aria-expanded') !== 'true';
  btn.setAttribute?.('aria-expanded', String(next));
  if (panel) panel.hidden = !next;
}
document.addEventListener('click', event => {
  const tipBtn = event.target?.closest?.('button.help-tip');
  if (tipBtn) {
    event.preventDefault();
    toggleHelpTip(tipBtn);
  }
});

function handleWorkbenchKey(event) {
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
  const id = MODAL_STACK.at(-1);
  if (event.key === 'Escape') {
    const activeTip = event.target?.closest?.('button.help-tip[aria-expanded="true"]');
    const menu = event.target.closest?.('details[open]') ||
      (id ? el(id) : document).querySelector('.control-menu[open]');
    if (menu) {
      menu.open = false;
      menu.querySelector('summary')?.focus();
    } else if (activeTip) {
      toggleHelpTip(activeTip, false);
    } else if (!id && el('vscodeFindWidget') && !el('vscodeFindWidget').hidden &&
        event.target?.closest?.('#vscodeFindWidget')) {
      closeLogFind();
    } else if (id) {
      if (id === 'fileBrowserOverlay') {
        if (el('browsePathWrap') && !el('browsePathWrap').hidden) toggleBrowsePathEdit(false);
        else hideFileBrowser();
      } else if (id === 'workOverlay') closeWorkFile();
      else closeModal(id);
    } else if (!el('broadcastPanel').hidden) toggleBroadcast(false);
    else if (WORKSPACE_LAYOUT === 'charts' && typeof ChartWorkspace !== 'undefined' && ChartWorkspace?.hasEscapeTarget?.()) {
      return;
    } else if (!el('actionToast').hidden) dismissFeedback();
    else return;
    event.preventDefault();
    event.stopImmediatePropagation();
  } else if (id && event.key === 'Tab') {
    const controls = modalControls(id), index = controls.indexOf(document.activeElement);
    const next = event.shiftKey ? (index <= 0 ? controls.length - 1 : index - 1) :
      (index + 1) % controls.length;
    (controls[next] || modalDialog(id)).focus();
    event.preventDefault();
    event.stopImmediatePropagation();
  } else if (!id && (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey &&
      event.key?.toLowerCase?.() === 'f' &&
      (WORKSPACE_LAYOUT === 'split' || WORKSPACE_LAYOUT === 'analysis') && DETAIL_VIEW === 'log' &&
      event.target?.closest?.('#logView, #readerToolbar, #vscodeFindWidget')) {
    event.preventDefault();
    openLogFind();
  } else if (!id && event.key === '[' && !event.ctrlKey && !event.altKey && !event.metaKey &&
      !document.querySelector('details[open]') && !event.target.isContentEditable &&
      !event.target.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])') &&
      (WORKSPACE_LAYOUT === 'split' || WORKSPACE_LAYOUT === 'analysis')) {
    event.preventDefault();
    toggleAnalysisFocus();
  } else if (event.key === '/' && !id && !event.ctrlKey && !event.altKey && !event.metaKey &&
      !event.shiftKey && !document.querySelector('details[open]') &&
      !event.target.isContentEditable &&
      !event.target.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) {
    let search = null;
    if (event.target.closest?.('#logView,#readerToolbar')) {
      if (DETAIL_VIEW === 'log') {
        if (el('vscodeFindWidget')?.hidden) openLogFind();
        search = el('analysisQuery');
      }
    } else if (event.target.closest?.('#paneLeft') || event.target === document.body) {
      if (WORKSPACE_LAYOUT === 'analysis' && DETAIL_VIEW === 'log') {
        if (el('vscodeFindWidget')?.hidden) openLogFind();
        search = el('analysisQuery');
      } else if (WORKSPACE_LAYOUT === 'jobs' || WORKSPACE_LAYOUT === 'split') {
        search = el('jobSearch');
      }
    }
    if (canFocus(search)) {
      event.preventDefault();
      search.focus();
    }
  }
}
document.addEventListener('keydown', handleWorkbenchKey, true);
document.addEventListener('focusin', event => {
  if (event.target.matches?.('.reader-toolbar [role="tab"]')) revealReaderTab(event.target);
});

const OPEN_FIELDS = {path:'workPath', targets:'logTargets', globs:'logGlobs', flow_path:'logFlowPath', browse_path:'browsePath'};
function activeOpenFields() {
  return { ...OPEN_FIELDS, path: OPEN_MODE === 'archive' ? 'archivePath' : 'workPath' };
}
const SETTINGS_FIELDS = {max_workers:'resMaxWorkers', max_history:'maxHistoryInput', max_log_lines:'logRetentionInput', scheduling_mode:'resSchedulingMode'};

function fieldError(input, message = '') {
  const error = el(input.id + 'Error');
  if (!error) return;
  error.textContent = message;
  error.hidden = !message;
  if (message) input.setAttribute('aria-invalid', 'true');
  else input.removeAttribute('aria-invalid');
}
function clearFormErrors(overlay, feedback) {
  el(feedback).textContent = '';
  el(overlay).querySelectorAll('[aria-invalid="true"]').forEach(input => fieldError(input));
}
function showFieldError(input, message) {
  fieldError(input, message);
  input.focus();
}
function showFormError(error, fields, feedback) {
  const fieldId = Object.hasOwn(fields, error.field) ? fields[error.field] : null;
  const resource = fields === SETTINGS_FIELDS && error.field?.startsWith('resources.') &&
    [...(el('resourcesTableBody')?.querySelectorAll?.('input[data-res-quota]') || [])].find(input =>
      (input.dataset?.resQuota ?? input.dataset?.resource) === error.field.slice('resources.'.length));
  const input = fieldId ? el(fieldId) : resource;
  if (canFocus(input)) {
    el(feedback).textContent = '';
    showFieldError(input, error.message);
  } else el(feedback).textContent = error.message;
}

const SOURCE_LABELS = {
  tail: {running:'WATCHING', action:'Watch', unit:'Log', target:'logs', status:'Watching logs…'},
  file: {running:'ANALYZING', action:'Analyze', unit:'Log', target:'logs', status:'Analyzing logs…'},
  reanalysis: {running:'REPLAYING', action:'Reanalyze', unit:'Run', target:'saved runs', status:'Replaying saved runs…'}
};

function displayState(s, sourceKind) {
  if (s === 'RUNNING') return SOURCE_LABELS[sourceKind]?.running || s;
  return s === 'DONE' ? 'FINISHED' : s === 'WAITING_INPUT' ? 'PROMPT DETECTED' : s;
}

function nowStamp() {
  const d = new Date();
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
}

function markProgrammaticScroll(ms = 90) {
  PROGRAMMATIC_SCROLL_UNTIL = performance.now() + ms;
}

function isLogAtBottom(box) {
  if (!box) return true;
  return box.scrollHeight - box.scrollTop - box.clientHeight <= 8;
}

function clampSplitWidth(percent) {
  const split = el('monitorSplit');
  const width = split?.getBoundingClientRect?.().width || 0;
  let low = 20, high = 80;
  if (width > 0) {
    low = Math.min(175 / width * 100, 24);
    const minRightPx = Math.min(420, width * 0.65);
    high = Math.max(low, 100 - (minRightPx / width * 100));
  }
  return Math.round(Math.max(low, Math.min(high, percent)) * 10) / 10;
}

function isAnyLayoutDragging() {
  return IS_DRAGGING_SPLIT || !!EDITOR_DRAG_ID ||
    (typeof ReadingLayout !== 'undefined' && typeof ReadingLayout.isAnyDragging === 'function' && ReadingLayout.isAnyDragging());
}

function flushDeferredRender() {
  if (!PENDING_POST_DRAG_RENDER || isAnyLayoutDragging()) return;
  PENDING_POST_DRAG_RENDER = false;
  renderTopBar();
  renderMonitor();
}

function loadDisplayPreferences() {
  try {
    const raw = window.localStorage.getItem(PREFS_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      const fs = Number(parsed.logFontSize);
      if (Number.isFinite(fs) && fs >= 9 && fs <= 20) DISPLAY_PREFS.logFontSize = Math.round(fs);
      if (typeof parsed.logWrap === 'boolean') DISPLAY_PREFS.logWrap = parsed.logWrap;
      if (typeof parsed.ansiColor === 'boolean') DISPLAY_PREFS.ansiColor = parsed.ansiColor;
      if (typeof parsed.stackedMode === 'boolean') DISPLAY_PREFS.stackedMode = parsed.stackedMode;
      const sw = Number(parsed.splitWidth);
      if (Number.isFinite(sw) && sw >= 20 && sw <= 80) DISPLAY_PREFS.splitWidth = clampSplitWidth(sw);
      const sh = Number(parsed.stackHeight);
      if (Number.isFinite(sh) && sh >= 20 && sh <= 75) DISPLAY_PREFS.stackHeight = Math.round(sh);
    }
  } catch (_) {}
  STACKED_MODE = DISPLAY_PREFS.stackedMode;
}

function saveDisplayPreferences() {
  DISPLAY_PREFS.stackedMode = !!STACKED_MODE;
  try {
    window.localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(DISPLAY_PREFS));
  } catch (_) {}
}

function showDisplaySaved(text = 'Saved') {
  const statusEl = el('displaySavedStatus');
  if (!statusEl) return;
  statusEl.textContent = text;
  clearTimeout(window._displaySavedTimer);
  window._displaySavedTimer = setTimeout(() => {
    if (el('displaySavedStatus')) el('displaySavedStatus').textContent = '';
  }, 2000);
}

function applyDisplayPreferences() {
  document.documentElement.style.setProperty('--log-font-size', DISPLAY_PREFS.logFontSize + 'px');
  document.documentElement.style.setProperty('--log-white-space', DISPLAY_PREFS.logWrap ? 'pre-wrap' : 'pre');
  document.documentElement.style.setProperty('--log-word-break', DISPLAY_PREFS.logWrap ? 'break-all' : 'normal');
  document.documentElement.style.setProperty('--log-row-width', DISPLAY_PREFS.logWrap ? '100%' : 'max-content');
  if (el('logFontSize')) el('logFontSize').value = String(DISPLAY_PREFS.logFontSize);
  if (el('logWrapToggle')) el('logWrapToggle').checked = !!DISPLAY_PREFS.logWrap;
  if (el('logAnsiToggle')) el('logAnsiToggle').checked = DISPLAY_PREFS.ansiColor !== false;
  if (typeof AnalysisWorkspace !== 'undefined' && 'ansiColor' in AnalysisWorkspace) {
    AnalysisWorkspace.ansiColor = DISPLAY_PREFS.ansiColor !== false;
  }
  const split = el('monitorSplit'),
    divider = el('paneDivider');
  STACKED_MODE = !!DISPLAY_PREFS.stackedMode;
  if (!STACKED_MODE) DISPLAY_PREFS.splitWidth = clampSplitWidth(DISPLAY_PREFS.splitWidth);
  if (split) {
    split.classList.toggle('stacked', STACKED_MODE);
    split.style.setProperty('--list-width', DISPLAY_PREFS.splitWidth + '%');
    split.style.setProperty('--list-height', DISPLAY_PREFS.stackHeight + '%');
  }
  if (divider) {
    divider.setAttribute('aria-orientation', STACKED_MODE ? 'horizontal' : 'vertical');
    divider.setAttribute('aria-valuenow', String(STACKED_MODE ? DISPLAY_PREFS.stackHeight : DISPLAY_PREFS.splitWidth));
  }
  syncFocusButtonState();
  syncNarrowTableClass();
  const box = el('logBox');
  if (box && el('chkAutoScroll')?.checked && DETAIL_VIEW === 'log') {
    markProgrammaticScroll();
    box.scrollTop = box.scrollHeight;
  }
}

function setLogWrap(wrap) {
  DISPLAY_PREFS.logWrap = !!wrap;
  applyDisplayPreferences();
  saveDisplayPreferences();
  syncDisplayPrefsControls();
  if (!isDiskArchive() && DETAIL_VIEW === 'log') { LAST_LOG_RENDER_KEY = ''; renderLogs(); }
}

function setLogAnsiColor(enabled) {
  DISPLAY_PREFS.ansiColor = !!enabled;
  applyDisplayPreferences();
  saveDisplayPreferences();
  if (!isDiskArchive() && DETAIL_VIEW === 'log') { LAST_LOG_RENDER_KEY = ''; renderLogs(); }
}

function setLogFontSize(value) {
  const size = Math.round(Number(value));
  if (!Number.isFinite(size) || size < 9 || size > 20) return;
  const anchors = ['analysisRows','analysisContextRows'].map(id => {
    const box = el(id), top = box.getBoundingClientRect().top;
    const row = [...box.querySelectorAll('.analysis-row')].find(row => row.getBoundingClientRect().bottom > top);
    return {box, row, offset:row ? row.getBoundingClientRect().top - top : 0};
  });
  DISPLAY_PREFS.logFontSize = size;
  applyDisplayPreferences();
  for (const {box,row,offset} of anchors) if (row) box.scrollTop += row.getBoundingClientRect().top - box.getBoundingClientRect().top - offset;
  saveDisplayPreferences();
  syncDisplayPrefsControls();
}

function syncDisplayPrefsControls() {
  if (!el('prefLogFontSize')) return;
  el('prefLogFontSize').value = String(DISPLAY_PREFS.logFontSize);
  el('prefLogWrap').checked = !!DISPLAY_PREFS.logWrap;
  el('prefLayoutMode').value = STACKED_MODE ? 'stack' : 'split';
  el('prefSplitWidth').value = String(DISPLAY_PREFS.splitWidth);
  el('prefStackHeight').value = String(DISPLAY_PREFS.stackHeight);
}

function updateDisplayPrefsFromUI() {
  const fs = Math.round(Number(el('prefLogFontSize').value));
  if (Number.isFinite(fs) && fs >= 9 && fs <= 20) DISPLAY_PREFS.logFontSize = fs;
  DISPLAY_PREFS.logWrap = !!el('prefLogWrap').checked;
  DISPLAY_PREFS.stackedMode = el('prefLayoutMode').value === 'stack';
  const sw = Number(el('prefSplitWidth').value);
  if (Number.isFinite(sw)) DISPLAY_PREFS.splitWidth = clampSplitWidth(sw);
  const sh = Number(el('prefStackHeight').value);
  if (Number.isFinite(sh)) DISPLAY_PREFS.stackHeight = Math.max(20, Math.min(75, Math.round(sh)));
  applyDisplayPreferences();
  saveDisplayPreferences();
  syncDisplayPrefsControls();
  showDisplaySaved('Saved');
}

function resetDisplayPreferences() {
  DISPLAY_PREFS = JSON.parse(JSON.stringify(DEFAULT_DISPLAY_PREFS));
  try {
    window.localStorage.removeItem(PREFS_STORAGE_KEY);
  } catch (_) {}
  applyDisplayPreferences();
  syncDisplayPrefsControls();
  if (!el('settingsOverlay').hidden) refreshSettingsLiveStatus();
  renderMonitor();
  showDisplaySaved('Reset to defaults');
}

const LOCAL_WARNINGS = new Map();
let pendingReports = 0;

function recordFeedback(message, tone = 'info', reportMarkup = '') {
  if (!reportMarkup && !el('settingsOverlay').hidden) {
    const feedback = el('settingsFeedback');
    feedback.textContent = message;
    feedback.classList.toggle('error-text', tone !== 'info');
  }
  if (reportMarkup) {
    const reports = el('actionReport');
    reports.insertAdjacentHTML('beforeend', '<section class="action-receipt"><h3>' +
      escHtml(message) + '</h3>' + reportMarkup + '</section>');
    const report = el('btnLastReport');
    report.hidden = false;
    report.textContent = 'Report (' + ++pendingReports + ')';
    announceStatus(message);
    return;
  }
  if (tone === 'error') {
    addWarning('Action error', message);
    return;
  }
  clearTimeout(window.feedbackTimer);
  const toast = el('actionToast');
  toast.hidden = false;
  toast.classList.toggle('warn', tone === 'warn');
  el('toastText').textContent = message;
  el('toastText').title = message;
  announceStatus(message);
  window.feedbackTimer = setTimeout(dismissFeedback, 4000);
}

function announceStatus(message) {
  const announcement = el('statusAnnouncement');
  if (announcement.textContent !== message) announcement.textContent = message;
}

function addWarning(label, message) {
  LOCAL_WARNINGS.set(message, label);
  renderStatusBanner();
}

function acknowledgeWarnings() {
  LOCAL_WARNINGS.clear();
  renderStatusBanner();
  closeModal('statusWarningOverlay');
}

function acknowledgeReports() {
  pendingReports = 0;
  el('actionReport').replaceChildren();
  el('btnLastReport').hidden = true;
  closeModal('reportOverlay');
}

function dismissFeedback() {
  clearTimeout(window.feedbackTimer);
  const toast = el('actionToast'), focused = toast.contains(document.activeElement);
  toast.hidden = true;
  if (focused) restoreFocus(null, el('btnOpenWorkspace'));
}

function openJobFinding(kind) {
  const d = RunHistory.selection?.detail || STATE.detail;
  if (d?.flow) {
    if (WORKSPACE_LAYOUT === 'jobs') setWorkspaceLayout('split');
    setDetailView('analysis');
    const selected = d.flow.analyzers.find(a => kind === 'warnings' ? a.issues?.some(i => i.level === 'warning') : a.error);
    if (selected) FlowWorkspace.selectAnalyzer(selected.name);
    if (kind === 'warnings' || selected) FlowWorkspace.findings(kind);
    else if (kind === 'errors') FlowWorkspace.diagnostics();
    return;
  }
  setDetailView('details');
  FlowWorkspace.diagnostics();
}

const DETAIL_TABS = [
  ['log', 'analysisLogTab', 'logView'],
  ['parsed', 'analysisParsedTab', 'flowWorkspaceView'],
  ['analysis', 'analysisResultsTab', 'flowWorkspaceView'],
  ['automation', 'automationTab', 'flowWorkspaceView'],
  ['details', 'btnDetailsTab', 'flowWorkspaceView']
];

function isDiskArchive() {
  const saved = typeof RunHistory !== 'undefined' ? RunHistory.selection : null;
  return !!(saved?.health?.enabled || STATE.archive?.enabled);
}

function syncFocusButtonState() {
  const button = el('btnFocusAnalysis');
  if (!button) return;
  const focused = WORKSPACE_LAYOUT === 'analysis';
  const label = focused ? 'Show jobs' : 'Hide jobs';
  button.hidden = WORKSPACE_LAYOUT === 'jobs' || WORKSPACE_LAYOUT === 'charts';
  button.setAttribute('aria-pressed', String(focused));
  button.setAttribute('aria-label', label);
  button.title = label + ' · [';
  if (typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function') {
    const iconName = STACKED_MODE ?
      (focused ? 'panel-top-open' : 'panel-top-close') :
      (focused ? 'panel-left-open' : 'panel-left-close');
    const svg = WaveIcons.svg(iconName);
    if (svg && button.dataset?.icon !== iconName) {
      button.innerHTML = svg;
      if (button.dataset) button.dataset.icon = iconName;
    }
  }
}

function syncDetailReaders(d) {
  const view = WORKSPACE_LAYOUT === 'jobs' ? 'hidden' : WORKSPACE_LAYOUT === 'charts' ? 'charts' : DETAIL_VIEW;
  if (typeof AnalysisWorkspace !== 'undefined') AnalysisWorkspace.setContextView(view, d);
  if (typeof ChartWorkspace !== 'undefined') ChartWorkspace.refresh(WORKSPACE_LAYOUT === 'charts' ? d : null);
}

function setDetailView(view, openSectionId, render = true) {
  const tab = DETAIL_TABS.find(([name]) => name === view);
  if (!tab) return;
  if (typeof FlowWorkspace !== 'undefined') FlowWorkspace.rememberLayout();
  DETAIL_VIEW = view;
  markProgrammaticScroll();
  for (const [name, id, panel] of DETAIL_TABS) {
    const button = el(id), active = view === name;
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
    el(panel).hidden = panel !== tab[2];
  }
  el(tab[2]).setAttribute('aria-labelledby', tab[1]);
  el('analysisFilterControls').hidden = view !== 'log';
  el('analysisFilterChips').hidden = view !== 'log' || !el('analysisFilterChips').innerHTML;
  revealReaderTab(el(tab[1]));
  const d = typeof RunHistory !== 'undefined' && RunHistory.selection?.detail || STATE.detail;
  syncDetailReaders(d);
  if (typeof FlowWorkspace !== 'undefined') FlowWorkspace.display(view);
  if (render && view === 'log' && WORKSPACE_LAYOUT !== 'jobs' && WORKSPACE_LAYOUT !== 'charts') {
    LAST_LOG_RENDER_KEY = '';
    renderLogs();
  }
  if (render && tab[2] === 'flowWorkspaceView' && typeof FlowWorkspace !== 'undefined' &&
      WORKSPACE_LAYOUT !== 'jobs' && WORKSPACE_LAYOUT !== 'charts') {
    FlowWorkspace.refresh(d);
  }
}

function toggleAnalysisFocus() {
  if (WORKSPACE_LAYOUT === 'jobs' || WORKSPACE_LAYOUT === 'charts') return;
  setWorkspaceLayout(el('monitorSplit').classList.contains('analysis-focused') ? 'split' : 'analysis');
}

let WORKSPACE_LAYOUT = 'jobs';
let WORKSPACE_POSITIONS = {};
function setWorkspaceLayout(layout) {
  if (!['jobs','split','analysis','charts'].includes(layout)) return;
  const prevLayout = WORKSPACE_LAYOUT;
  if (prevLayout !== 'jobs' && prevLayout !== 'charts') {
    for (const id of ['analysisRows','analysisContextRows','detailsViewport']) if (el(id)?.clientHeight) WORKSPACE_POSITIONS[id] = el(id).scrollTop;
    if (typeof FlowWorkspace !== 'undefined') FlowWorkspace.rememberLayout();
  }
  WORKSPACE_LAYOUT = layout;
  if (layout !== 'jobs' && EDIT_QUEUE_MODE) {
    EDIT_QUEUE_MODE = false;
    EDITOR_DRAG_ID = null;
    if (typeof syncQueueEditorView === 'function') syncQueueEditorView();
  }
  const focused = layout === 'analysis';
  const chartsMode = layout === 'charts';
  el('monitorSplit').classList.toggle('analysis-focused', focused);
  el('monitorSplit').classList.toggle('jobs-overview', layout === 'jobs');
  el('monitorSplit').classList.toggle('charts-mode', chartsMode);
  document.body.classList.toggle('analysis-focus', focused);
  if (el('chartsView')) el('chartsView').hidden = !chartsMode;
  syncFocusButtonState();
  el('layoutJobs').setAttribute('aria-pressed', String(layout === 'jobs'));
  el('layoutInspect').setAttribute('aria-pressed', String(layout === 'split' || layout === 'analysis'));
  el('layoutCharts')?.setAttribute?.('aria-pressed', String(chartsMode));
  syncNarrowTableClass();
  const d = (typeof RunHistory !== 'undefined' && RunHistory.selection?.detail) || STATE.detail;
  syncDetailReaders(d);
  if (prevLayout !== layout && (prevLayout === 'jobs' || layout === 'jobs' || (prevLayout === 'charts' && !chartsMode)) && el('jobTableBody')?.querySelectorAll) {
    renderMonitor();
  } else if (chartsMode && typeof renderChartsHostStrip === 'function' && el('chartJobSelect')) {
    renderChartsHostStrip(currentJob(), d, typeof RunHistory !== 'undefined' ? RunHistory.selection : null);
  } else if ((layout === 'split' || layout === 'analysis') && (prevLayout === 'jobs' || prevLayout === 'charts')) {
    if (typeof renderDetail === 'function' && el('detailTitle')?.parentElement) renderDetail();
  }
  if (typeof FlowWorkspace !== 'undefined') FlowWorkspace.layoutChanged();
  if (layout !== 'jobs' && layout !== 'charts') {
    for (const [id,top] of Object.entries(WORKSPACE_POSITIONS)) if(el(id)) el(id).scrollTop = top;
  }
}

function selectChartJob(id) {
  if (!id || id === SELECTED_JOB_ID) return;
  selectJob(id);
}
function selectChartAttempt(id) {
  selectAttempt(id);
}

function openLogFind() {
  if (isDiskArchive() && typeof AnalysisWorkspace !== 'undefined' && typeof AnalysisWorkspace.openFind === 'function') {
    AnalysisWorkspace.openFind();
    return;
  }
  const widget = el('vscodeFindWidget');
  if (widget) widget.hidden = false;
  el('btnToggleFind')?.setAttribute?.('aria-pressed', 'true');
  el('btnToggleFind')?.setAttribute?.('aria-expanded', 'true');
  const queryInput = el('analysisQuery');
  if (queryInput) {
    queryInput.focus?.();
    const end = String(queryInput.value || '').length;
    queryInput.setSelectionRange?.(end, end);
  }
}

function closeLogFind() {
  if (isDiskArchive() && typeof AnalysisWorkspace !== 'undefined' && typeof AnalysisWorkspace.closeFind === 'function') {
    AnalysisWorkspace.closeFind();
    return;
  }
  const widget = el('vscodeFindWidget');
  const hadFocus = typeof document !== 'undefined' &&
    (widget?.contains?.(document.activeElement) || document.activeElement?.closest?.('#vscodeFindWidget'));
  if (widget) widget.hidden = true;
  el('btnToggleFind')?.setAttribute?.('aria-pressed', 'false');
  el('btnToggleFind')?.setAttribute?.('aria-expanded', 'false');
  if (hadFocus) el('btnToggleFind')?.focus?.({ preventScroll: true });
}

function toggleLogFind() {
  if (isDiskArchive() && typeof AnalysisWorkspace !== 'undefined' && typeof AnalysisWorkspace.toggleFind === 'function') {
    AnalysisWorkspace.toggleFind();
    return;
  }
  const widget = el('vscodeFindWidget');
  if (widget?.hidden) openLogFind();
  else closeLogFind();
}

function revealReaderTab(tab) {
  tab?.scrollIntoView?.({block:'nearest', inline:'nearest'});
}
function readerTabKey(event, id) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const count = DETAIL_TABS.length;
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? count - 1 :
    (DETAIL_TABS.findIndex(([, tab]) => tab === id) + (event.key === 'ArrowLeft' ? count - 1 : 1)) % count;
  const [view, tab] = DETAIL_TABS[next];
  setDetailView(view);
  el(tab).focus();
}

function jumpLogBeginning(button) {
  const disclosure = button?.closest?.('details');
  if (disclosure) {
    disclosure.open = false;
    disclosure.querySelector?.('summary')?.focus?.();
  }
  if (isDiskArchive() && typeof AnalysisWorkspace !== 'undefined') {
    AnalysisWorkspace.edge('start');
    return;
  }
  if (el('chkAutoScroll')) el('chkAutoScroll').checked = false;
  if (el('analysisFollow')) el('analysisFollow').checked = false;
  SCROLL_TO_EVIDENCE = false;
  const box = el('logBox');
  if (box) {
    markProgrammaticScroll();
    box.scrollTop = 0;
  }
}

function applyAnalysisFilter(button) {
  const disclosure = button?.closest?.('details');
  if (disclosure) {
    disclosure.open = false;
    disclosure.querySelector?.('summary')?.focus?.();
  }
  if (!isDiskArchive()) {
    LAST_LOG_RENDER_KEY = '';
    renderLogs(true);
    return;
  }
  AnalysisWorkspace.apply();
}

function hideOriginalContext() {
  AnalysisWorkspace.context('hidden');
}
function toggleOriginalContextPosition() {
  const next = AnalysisWorkspace.contextPosition === 'below' ? 'right' : 'below';
  AnalysisWorkspace.context(next);
}

let JOB_PICKER_ROWS = [], JOB_PICKER_EPOCH = 0;
async function openJobPicker() {
  const token = ++JOB_PICKER_EPOCH;
  el('jobPickerSearch').value = '';
  JOB_PICKER_ROWS = [];
  openModal('jobPickerOverlay', 'jobPickerSearch');
  renderJobPicker();
  const saved = RunHistory.selection;
  if (!saved) {
    JOB_PICKER_ROWS = STATE.jobs.map(job => ({...job, current: job.id === SELECTED_JOB_ID}));
  } else {
    el('jobPickerStatus').textContent = 'Reading saved jobs…';
    try {
      const workspace = STATE.workspace_id;
      const result = await fetchJson('/api/archive?' + new URLSearchParams({view:'attempts',
        workspace_id:workspace, run_id:saved.run_id, limit:100}));
      if (token !== JOB_PICKER_EPOCH || workspace !== STATE.workspace_id || el('jobPickerOverlay').hidden) return;
      JOB_PICKER_ROWS = result.rows.map(job => ({...job, run_id:saved.run_id,
        current:job.attempt_id === saved.detail.attempt_id}));
      if (result.has_more) el('jobPickerStatus').textContent = 'Latest 100 attempts. Use Run history for older attempts.';
    } catch (error) { el('jobPickerStatus').textContent = error.message; return; }
  }
  renderJobPicker();
}

function renderJobPicker() {
  const query = el('jobPickerSearch').value.toLocaleLowerCase();
  const matches = JOB_PICKER_ROWS.filter(job => job.name.toLocaleLowerCase().includes(query));
  el('jobPickerList').innerHTML = matches.map(job =>
    `<button type="button" data-picker-job="${escHtml(job.id)}" data-picker-attempt="${escHtml(job.attempt_id || '')}" aria-current="${!!job.current}">${escHtml(job.name)} <span class="help-text">· #${job.attempt_number} · ${escHtml(job.result || displayState(job.status))}</span></button>`).join('');
  el('jobPickerList').querySelectorAll('button').forEach((button,index) => button.onclick = async () => {
    const job = matches[index];
    closeModal('jobPickerOverlay');
    if (job.current) return;
    try {
      if (job.run_id) await RunHistory.chooseAttempt(job.run_id, job.id, job.attempt_id);
      else await selectJob(job.id);
    } catch (error) { recordFeedback('Could not open job: ' + error.message, 'error'); }
  });
  el('jobPickerStatus').textContent = matches.length ? `${matches.length} matching jobs / attempts` : 'No matching jobs.';
}

function jobPickerKey(event) {
  if (event.key === 'Enter' || event.key === 'ArrowDown') {
    event.preventDefault();
    const first = el('jobPickerList').querySelector('button');
    if (event.key === 'Enter') first?.click();
    else first?.focus();
  }
}

function clearHighlight() {
  HIGHLIGHT_LINE_NO = null;
  SCROLL_TO_EVIDENCE = false;
  el('btnClearHighlight').hidden = true;
  renderLogs();
}

function syncNarrowTableClass() {
  const pane = el('paneLeft');
  if (!pane) return;
  const w = pane.getBoundingClientRect().width;
  pane.classList.toggle('narrow-table', w > 0 && w < 430);
  pane.style.setProperty('--jobs-pane-width', w + 'px');
}

function setupMonitorLayout() {
  if (typeof WaveIcons !== 'undefined') WaveIcons.mount(document);
  loadDisplayPreferences();
  applyDisplayPreferences();
  const split = el('monitorSplit'),
    divider = el('paneDivider'),
    box = el('logBox');
  function resize(percent, persist = true) {
    markProgrammaticScroll();
    if (STACKED_MODE) {
      const height = split.getBoundingClientRect().height;
      const low = height ? Math.min(110 / height * 100, 25) : 20,
        high = height ? 100 - Math.min(140 / height * 100, 35) : 75;
      const value = Math.round(Math.max(low, Math.min(high, percent)) * 10) / 10;
      DISPLAY_PREFS.stackHeight = value;
      split.style.setProperty('--list-height', value + '%');
      divider.setAttribute('aria-valuenow', String(Math.round(value)));
      divider.setAttribute('aria-valuemin', String(Math.round(low)));
      divider.setAttribute('aria-valuemax', String(Math.round(high)));
    } else {
      const width = split.getBoundingClientRect().width;
      const low = width ? Math.min(175 / width * 100, 24) : 20,
        minRightPx = width ? Math.min(420, width * 0.65) : 0,
        high = width ? Math.max(low, 100 - (minRightPx / width * 100)) : 80;
      const value = Math.round(Math.max(low, Math.min(high, percent)) * 10) / 10;
      DISPLAY_PREFS.splitWidth = value;
      split.style.setProperty('--list-width', value + '%');
      divider.setAttribute('aria-valuenow', String(Math.round(value)));
      divider.setAttribute('aria-valuemin', String(Math.round(low)));
      divider.setAttribute('aria-valuemax', String(Math.round(high)));
    }
    if (persist) {
      saveDisplayPreferences();
      syncDisplayPrefsControls();
      syncNarrowTableClass();
    }
    if (el('chkAutoScroll')?.checked && DETAIL_VIEW === 'log') {
      box.scrollTop = box.scrollHeight;
    }
  }
  divider.onpointerdown = e => {
    if (e.button !== 0) return;
    IS_DRAGGING_SPLIT = true;
    divider.setPointerCapture(e.pointerId);
    document.body.classList.add(STACKED_MODE ? 'resizing-row' : 'resizing');
    e.preventDefault();
  };
  divider.onpointermove = e => {
    if (!divider.hasPointerCapture(e.pointerId)) return;
    const bounds = split.getBoundingClientRect();
    PENDING_SPLIT_PERCENT = STACKED_MODE
      ? (e.clientY - bounds.top) / bounds.height * 100
      : (e.clientX - bounds.left) / bounds.width * 100;
    if (typeof requestAnimationFrame !== 'function') {
      resize(PENDING_SPLIT_PERCENT, false);
      PENDING_SPLIT_PERCENT = null;
      return;
    }
    if (SPLIT_DRAG_RAF) return;
    SPLIT_DRAG_RAF = requestAnimationFrame(() => {
      SPLIT_DRAG_RAF = 0;
      if (PENDING_SPLIT_PERCENT !== null) {
        const next = PENDING_SPLIT_PERCENT;
        PENDING_SPLIT_PERCENT = null;
        resize(next, false);
      }
    });
  };
  divider.onpointerup = divider.onpointercancel = e => {
    if (SPLIT_DRAG_RAF && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(SPLIT_DRAG_RAF);
      SPLIT_DRAG_RAF = 0;
    }
    if (PENDING_SPLIT_PERCENT !== null) {
      resize(PENDING_SPLIT_PERCENT, false);
      PENDING_SPLIT_PERCENT = null;
    }
    IS_DRAGGING_SPLIT = false;
    if (divider.hasPointerCapture(e.pointerId)) divider.releasePointerCapture(e.pointerId);
    document.body.classList.remove('resizing', 'resizing-row');
    saveDisplayPreferences();
    syncDisplayPrefsControls();
    syncNarrowTableClass();
    flushDeferredRender();
  };
  divider.ondblclick = () => resize(STACKED_MODE ? DEFAULT_DISPLAY_PREFS.stackHeight : DEFAULT_DISPLAY_PREFS.splitWidth);
  divider.onkeydown = e => {
    const current = Number(divider.getAttribute('aria-valuenow'));
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const delta = ['ArrowLeft', 'ArrowUp'].includes(e.key) ? -3 : 3;
    resize(e.key === 'Home' ? 0 : e.key === 'End' ? 100 : current + delta);
  };
  let leftObsRaf = 0;
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => {
      if (typeof requestAnimationFrame !== 'function') {
        markProgrammaticScroll(60);
        syncNarrowTableClass();
        return;
      }
      if (leftObsRaf) return;
      leftObsRaf = requestAnimationFrame(() => {
        leftObsRaf = 0;
        markProgrammaticScroll(60);
        syncNarrowTableClass();
      });
    }).observe(el('paneLeft'));
  }
  document.addEventListener?.('pointerup', flushDeferredRender);
  document.addEventListener?.('pointercancel', flushDeferredRender);
  window.addEventListener('resize', () => {
    markProgrammaticScroll(80);
    if (!STACKED_MODE) {
      const clamped = clampSplitWidth(DISPLAY_PREFS.splitWidth);
      if (clamped !== DISPLAY_PREFS.splitWidth) {
        DISPLAY_PREFS.splitWidth = clamped;
        split.style.setProperty('--list-width', clamped + '%');
        divider.setAttribute('aria-valuenow', String(clamped));
        syncDisplayPrefsControls();
      }
    }
    syncNarrowTableClass();
    if (FB_WINDOW_RECT && el('fileBrowserOverlay') && !el('fileBrowserOverlay').hidden) {
      applyFileBrowserRect(FB_WINDOW_RECT);
    }
    if (el('chkAutoScroll').checked && DETAIL_VIEW === 'log') {
      box.scrollTop = box.scrollHeight;
    }
  });
  const syncFollowTargets = on => {
    el('chkAutoScroll').checked = !!on;
    if (!isDiskArchive() && el('analysisFollow')) el('analysisFollow').checked = !!on;
  };
  const jumpToFollowBottom = () => {
    HIGHLIGHT_LINE_NO = null;
    SCROLL_TO_EVIDENCE = false;
    el('btnClearHighlight').hidden = true;
    markProgrammaticScroll();
    box.scrollTop = box.scrollHeight;
  };
  const pauseScroll = () => {
    syncFollowTargets(false);
    SCROLL_TO_EVIDENCE = false;
  };
  box.onwheel = e => {
    if (e.deltaY < 0) {
      pauseScroll();
    } else if (e.deltaY > 0) {
      requestAnimationFrame(() => {
        if (isLogAtBottom(box) && HIGHLIGHT_LINE_NO == null) syncFollowTargets(true);
      });
    }
  };
  box.onscroll = () => {
    if (performance.now() < PROGRAMMATIC_SCROLL_UNTIL || DETAIL_VIEW !== 'log' || !STATE.detail) return;
    if (isLogAtBottom(box)) {
      if (!el('chkAutoScroll').checked && HIGHLIGHT_LINE_NO == null) {
        syncFollowTargets(true);
      }
    } else if (el('chkAutoScroll').checked) {
      pauseScroll();
    }
  };
  box.tabIndex = 0;
  box.onkeydown = e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Home') {
      e.preventDefault?.();
      pauseScroll();
      markProgrammaticScroll();
      box.scrollTop = 0;
    } else if ((e.ctrlKey || e.metaKey) && e.key === 'End') {
      e.preventDefault?.();
      jumpToFollowBottom();
      syncFollowTargets(true);
    } else if (['PageUp', 'Home', 'ArrowUp'].includes(e.key)) {
      pauseScroll();
    } else if (['End', 'PageDown', 'ArrowDown'].includes(e.key)) {
      requestAnimationFrame(() => {
        if (isLogAtBottom(box) && HIGHLIGHT_LINE_NO == null) syncFollowTargets(true);
      });
    }
  };
  el('chkAutoScroll').onchange = e => {
    syncFollowTargets(!!e.target.checked);
    if (e.target.checked) jumpToFollowBottom();
  };
  if (el('analysisFollow')) {
    el('analysisFollow').onchange = e => {
      if (isDiskArchive()) return;
      const checked = !!(e?.target ? e.target.checked : el('analysisFollow').checked);
      syncFollowTargets(checked);
      if (checked) jumpToFollowBottom();
    };
  }
  for (const [view, id] of DETAIL_TABS) {
    el(id).onclick = () => setDetailView(view);
    el(id).onkeydown = e => readerTabKey(e, id);
  }
  JobsColumns.initialize(null, refreshJobsColumns);
  ReadingLayout.initialize(el('jobsTableWrap'), refreshJobsColumns);
  el('resourcesMenu')?.addEventListener?.('toggle', () => {
    if (el('resourcesMenu').open) {
      if (el('resourcesFeedback')) el('resourcesFeedback').textContent = '';
      renderResourcesPanel(true);
    }
  });
  el('stopAllMenu')?.addEventListener?.('toggle', () => {
    if (!el('stopAllMenu').open && el('forceAllConfirm')) el('forceAllConfirm').hidden = true;
  });
  setupRetainedFindControls();
  setupFileBrowserWindow();
}

function setConnection(yes) {
  CONNECTED = yes;
  updateControls();
}

function archiveProblem(health) {
  return !!health?.error || health?.status === 'failed';
}

function workspaceIsWritable() {
  return CONNECTED && !STATE.opened_run_id &&
    !archiveProblem(STATE.archive) && STATE.archive?.status !== 'unclosed';
}

function canControl() {
  return !BUSY && workspaceIsWritable();
}

function renderStatusBanner() {
  const saved = typeof RunHistory !== 'undefined' ? RunHistory.selection : null;
  const warnings = [];
  if (!CONNECTED) warnings.push(['Disconnected', 'Connection lost — last snapshot shown. Controls disabled until reconnection.']);
  const liveSaveFailed = archiveProblem(STATE.archive);
  if (liveSaveFailed || archiveProblem(saved?.health)) {
    const context = 'History save failed — available records may be incomplete.' +
      (liveSaveFailed && !STATE.opened_run_id ? ' Controls are disabled for this work file.' : '');
    const reasons = [...new Set([STATE.archive?.error?.message, saved?.health?.error?.message].filter(Boolean))];
    warnings.push(['Archive error', [context, ...reasons].join(' ')]);
  }
  if (STATE.archive?.status === 'unclosed' || saved?.health?.status === 'unclosed' ||
      saved?.detail?.verified === false) {
    warnings.push(['Unverified archive', 'Unverified archive — committed records only.']);
  }
  if (saved?.follow === 'interrupted') {
    warnings.push(['Writer may have stopped', 'Writer may have stopped — showing the last saved data.']);
  }
  if (STATE.watcher_error) warnings.push(['watcher_error', 'Folder watch failed: ' + STATE.watcher_error]);
  for (const [message, label] of LOCAL_WARNINGS) warnings.push([label, message]);
  const banner = el('connectionNotice');
  const summary = [...new Set(warnings.map(([label]) => label))].join(' · ');
  const fullText = warnings.map(([label, text]) => label + '\n' + text).join('\n\n');
  if (banner.textContent !== summary) {
    banner.textContent = summary;
    announceStatus(summary);
  }
  banner.hidden = !summary;
  banner.title = summary ? summary + ' — View details' : '';
  const detail = el('statusWarningText');
  if (detail.textContent !== fullText) detail.textContent = fullText;
  el('btnAcknowledgeWarnings').hidden = !LOCAL_WARNINGS.size;
  if (!summary && !el('statusWarningOverlay').hidden) closeModal('statusWarningOverlay');
  const activity = el('statusActivity');
  const text = BUSY && STOPPING_SOURCES ? 'Stopping / Saving history…' :
    CONNECTED ? (el('workspaceInfo').dataset.activity || 'Connected') : 'Offline';
  if (activity.textContent !== text) {
    activity.textContent = text;
    activity.title = text;
  }
}
async function fetchJson(path, options) {
  const c = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    c.abort();
  }, 15000);
  const external = options?.signal, abort = () => c.abort();
  if (external?.aborted) c.abort();
  else external?.addEventListener('abort', abort, {once:true});
  try {
    const headers = new Headers(options?.headers);
    headers.set('X-Wave-Token', new URLSearchParams(window.location.search).get('token') || '');
    const r = await fetch(path, Object.assign({}, options, {
        signal: c.signal,
        headers
      })),
      d = await r.json();
    if (!r.ok || d.error) {
      const error = new Error(d.error || 'HTTP ' + r.status);
      if (typeof d.field === 'string') error.field = d.field;
      error.status = r.status;
      throw error;
    }
    return d;
  } catch (err) {
    if (timedOut && !external?.aborted) throw new Error('Request timed out (15s)');
    if (external?.aborted || err?.name === 'AbortError' || /aborted without reason/i.test(String(err?.message || ''))) {
      throw Object.assign(new Error('Request cancelled'), {name: 'AbortError', isAborted: true});
    }
    throw err;
  } finally {
    clearTimeout(timer);
    external?.removeEventListener('abort', abort);
  }
}
async function apiPost(path, payload, {allowArchiveProblems = false, onError} = {}) {
  // BUSY is already set by callers that serialize their requests.
  if (!(allowArchiveProblems ? CONNECTED && !STATE.opened_run_id : workspaceIsWritable())) return null;
  try {
    return await fetchJson(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(Object.assign({
        workspace_id: STATE.workspace_id
      }, payload))
    });
  } catch (e) {
    const rejected = e.status >= 400 && e.status < 500;
    if (rejected && onError?.(e)) return null;
    const message = rejected ? e.message : 'Could not confirm action: ' + e.message + '. Check state before retrying.';
    recordFeedback(rejected ? 'Action rejected' : 'Action unconfirmed', 'error',
      '<p class="error-text">' + escHtml(message) + '</p>');
    if (!rejected) onError?.(new Error(message));
    return null;
  }
}
function mergeLogChunk(cache, chunk, workspaceId) {
  const same = cache && cache.workspace_id === workspaceId &&
    cache.job_id === chunk.job_id && cache.attempt_id === chunk.attempt_id;
  if (!chunk.reset && !same) throw new Error('Log cursor belongs to a different execution');
  const previous = same && !chunk.reset ? cache.records : [];
  const retained = previous.filter(r => r.line_no >= chunk.first_line && r.line_no <= chunk.next_line);
  const last = retained.length ? retained[retained.length - 1].line_no : chunk.first_line - 1;
  const records = retained.concat(chunk.records.filter(r => r.line_no > last));
  return {
    workspace_id: workspaceId,
    job_id: chunk.job_id,
    attempt_id: chunk.attempt_id,
    first_line: chunk.first_line,
    next_line: chunk.next_line,
    records
  };
}

function resetWorkspaceSelection() {
  if (typeof RunHistory !== 'undefined') RunHistory.clearSelection();
  if (typeof AnalysisWorkspace !== 'undefined') AnalysisWorkspace.reset();
  SELECTED_JOB_ID = null;
  SELECTED_ATTEMPT_ID = null;
  CHECKED_JOBS.clear();
  EDITOR_CHECKED.clear();
  EDIT_QUEUE_MODE = false;
  EDITOR_DRAG_ID = null;
  LAST_MOVED_JOB_ID = null;
  ++LAST_MOVED_TOKEN;
  QE_REMOVED_OPEN = false;
  if (typeof syncQueueEditorView === 'function') syncQueueEditorView();
  STDIN_STATUS_BY_ATTEMPT = {};
  STDIN_HISTORY_BY_ATTEMPT = {};
  renderStdinHistory();
  LOG_CACHE = null;
}

async function fetchState() {
  const request = ++REQUEST_ID;
  try {
    const q = new URLSearchParams();
    if (SELECTED_JOB_ID) q.set('job_id', SELECTED_JOB_ID);
    if (SELECTED_ATTEMPT_ID) q.set('attempt_id', SELECTED_ATTEMPT_ID);
    q.set('log_after', String(LOG_CACHE?.next_line ?? 0));
    if (LOG_CACHE) q.set('log_attempt', LOG_CACHE.attempt_id);
    const s = await fetchJson('/api/state?' + q, {
      cache: 'no-store'
    });
    if (request !== REQUEST_ID) return;
    if (STATE.workspace_id && STATE.workspace_id !== s.workspace_id) {
      resetWorkspaceSelection();
      closePanels();
      STATE = s;
      STATE.detail = null;
      updateControls();
      return fetchState();
    }
    if (s.detail) {
      const chunk = s.detail.log_chunk;
      if (!chunk || chunk.job_id !== s.detail.id || chunk.attempt_id !== s.detail.attempt_id) {
        throw new Error('Log response does not match the selected execution');
      }
      LOG_CACHE = mergeLogChunk(LOG_CACHE, chunk, s.workspace_id);
      s.detail.log_records = LOG_CACHE.records;
      delete s.detail.log_chunk;
    } else {
      LOG_CACHE = null;
    }
    STATE = s;
    document.body?.classList?.toggle('archive-only', !!s.opened_run_id);
    if (s.opened_run_id && WORKSPACE_LAYOUT === 'jobs') setWorkspaceLayout('split');
    if (!currentJob()) {
      SELECTED_JOB_ID = s.jobs[0]?.id || null;
      SELECTED_ATTEMPT_ID = null;
      if (SELECTED_JOB_ID) return fetchState();
    }
    setConnection(true);
    if (isAnyLayoutDragging()) {
      PENDING_POST_DRAG_RENDER = true;
    } else {
      renderTopBar();
      renderMonitor();
    }
    if (typeof RunHistory !== 'undefined') {
      RunHistory.openInitial(s.opened_run_id);
      RunHistory.pollFollow();
    }
    if (!el('settingsOverlay').hidden) refreshSettingsLiveStatus();
  } catch (e) {
    if (request === REQUEST_ID) {
      LOG_CACHE = null; // Reconnect from an authoritative retained interval.
      setConnection(false);
    }
  }
}
async function pollState() {
  await fetchState();
  setTimeout(pollState, 650);
}

function closePanels() {
  ['settingsOverlay', 'archiveOverlay'].forEach(closeModal);
  if (!el('broadcastPanel').hidden) toggleBroadcast(false, false);
}

function syncJobFilterControls() {
  const heldChecked = !!el('heldFilter')?.checked;
  const count = [
    el('stateFilter').value,
    el('resultFilter').value,
    el('warningFilter').checked ? 'Warn' : '',
    heldChecked ? 'Held' : ''
  ].filter(Boolean).length;
  el('jobFilterCount').textContent = count || '';
  el('jobFilters').setAttribute('data-active', String(count > 0));
  el('btnClearJobFilters').hidden = !count && !el('jobSearch').value.trim();
}

function removeJobFilter(kind) {
  if (kind === 'warning') el('warningFilter').checked = false;
  else if (kind === 'held') { if (el('heldFilter')) el('heldFilter').checked = false; }
  else if (kind === 'state' || kind === 'result') el(kind + 'Filter').value = '';
  else return;
  renderMonitor();
  el('jobFilters').querySelector('summary').focus();
}

function syncQuickFilters() {
  const state = el('stateFilter').value, result = el('resultFilter').value,
    warning = el('warningFilter').checked, held = !!el('heldFilter')?.checked, search = el('jobSearch').value.trim();
  syncJobFilterControls();
  document.querySelectorAll?.('[data-quick-kind]').forEach(button => {
    const kind = button.dataset.quickKind;
    const active = kind === 'all' ? !state && !result && !warning && !held && !search :
      kind === 'state' ? state === button.dataset.quickValue && (state !== 'PENDING' || !held) :
      kind === 'result' ? result === button.dataset.quickValue :
      kind === 'held' ? held : warning;
    button.setAttribute('aria-pressed', String(active));
  });
}

function quickFilterHeld() {
  if (el('heldFilter')) {
    const next = !el('heldFilter').checked;
    el('heldFilter').checked = next;
    if (next && el('stateFilter').value === 'PENDING') el('stateFilter').value = '';
  }
  renderMonitor();
}

function quickFilterState(st) {
  const next = el('stateFilter').value === st ? '' : st;
  el('stateFilter').value = next;
  if (next === 'PENDING' && el('heldFilter')) el('heldFilter').checked = false;
  renderMonitor();
}

function quickFilterResult(res) {
  el('resultFilter').value = el('resultFilter').value === res ? '' : res;
  renderMonitor();
}

function quickFilterWarnings() {
  el('warningFilter').checked = !el('warningFilter').checked;
  renderMonitor();
}

function hasActiveWork(state = STATE) {
  const jobs = state.jobs || [];
  const sources = state.sources || {};
  return jobs.some(j => ACTIVE.has(j.status) || j.reserved) ||
    (sources.active_readers ?? 0) > 0 || (sources.active_watchers ?? 0) > 0;
}
function hasUnfinishedBatch(state = STATE) {
  if (state.opened_run_id) return false;
  if (hasActiveWork(state)) return true;
  if (!state.started) return false;
  const jobs = state.jobs || [];
  return jobs.some(j => (j.status !== 'DONE' && j.status !== 'CANCELLED') || !!j.held);
}

function workBasename(path) {
  if (!path) return '';
  const cleaned = String(path).replace(/[\\/]+$/, '');
  const parts = (cleaned || String(path)).split(/[\\/]/);
  return parts[parts.length - 1] || String(path);
}

function renderResourcesPanel(forceInputs = false) {
  const menu = el('resourcesMenu'), tbody = el('resourcesTableBody'), modeSelect = el('resSchedulingMode');
  const resources = STATE.resources || {}, inUse = STATE.resources_in_use || {};
  const entries = Object.entries(resources);
  const workersUsed = STATE.active_workers ?? 0, workersCap = STATE.max_workers ?? 1;
  const fullResources = entries.filter(([k, cap]) => cap <= (inUse[k] || 0));
  const summaryEl = el('resourcesButtonText'), dotEl = el('resourcesDot');
  if (summaryEl) {
    const quotaText = entries.length ?
      (fullResources.length ? `${fullResources.length} Full` : `${entries.length} Quota${entries.length === 1 ? '' : 's'}`) :
      'Quotas';
    summaryEl.textContent = `${workersUsed}/${workersCap} W · ${quotaText} ▾`;
  }
  if (dotEl) {
    dotEl.classList.toggle('warn', fullResources.length > 0 || workersUsed >= workersCap);
    dotEl.classList.toggle('full', fullResources.some(([, cap]) => cap === 0));
  }
  if (!tbody) return;
  const metaEl = el('resourcesPanelMeta');
  if (metaEl) {
    const runningN = (STATE.jobs || []).filter(j => ACTIVE.has(j.status)).length;
    const pendingN = (STATE.jobs || []).filter(j => j.status === 'PENDING' && !j.held).length;
    metaEl.textContent = `${runningN} running · ${pendingN} queued`;
  }
  const activeJobs = (STATE.jobs || []).filter(j => ACTIVE.has(j.status));
  const modeVal = STATE.scheduling_mode || 'backfill';
  if (modeSelect && (forceInputs || !menu?.open)) modeSelect.value = modeVal;
  const rows = [['max_workers', workersUsed, workersCap, activeJobs.map(j => j.name)]].concat(
    entries.map(([k, cap]) => [
      k,
      inUse[k] || 0,
      cap,
      activeJobs.filter(j => (j.resources?.[k] || 0) > 0).map(j =>
        (j.resources[k] > 1 ? `${j.name} (${j.resources[k]})` : j.name))
    ])
  );
  const keysSig = rows.map(([k]) => k).join('\0');
  const shouldRebuild = forceInputs || tbody.dataset?.keys !== keysSig;
  if (!menu?.open && !shouldRebuild) return;
  if (shouldRebuild) {
    tbody.dataset.keys = keysSig;
    tbody.innerHTML = rows.map(([key, used, cap, holders], i) => {
      const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : (used > 0 ? 100 : 0);
      const isFull = cap === 0 || used >= cap;
      const pctText = cap === 0 ? 'PAUSED' : (isFull ? 'FULL' : pct + '%');
      const inputHtml = key === 'max_workers' ?
        `<input id="resMaxWorkers" type="number" min="1" step="1" value="${cap}" aria-label="Capacity max_workers">` :
        `<input id="resQuota${i}" type="number" min="0" step="1" value="${cap}" data-res-quota="${escHtml(key)}" aria-label="Capacity ${escHtml(key)}">`;
      const heldText = holders.length ? holders.join(', ') : '—';
      return `<tr data-res-row="${escHtml(key)}"><td><strong>${escHtml(key)}</strong></td>` +
        `<td class="num" data-res-used>${used}</td>` +
        `<td class="res-held" data-res-held title="${escHtml(heldText)}">${escHtml(heldText)}</td>` +
        `<td class="num">${inputHtml}</td>` +
        `<td><div class="res-usage-cell"><div class="res-bar"><div class="res-bar-fill ${isFull ? 'full' : ''}" data-res-bar style="width:${pct}%"></div></div><span class="res-usage-pct ${isFull ? 'full' : ''}" data-res-pct>${pctText}</span></div></td></tr>`;
    }).join('');
    return;
  }
  rows.forEach(([key, used, cap, holders]) => {
    const tr = [...(tbody.querySelectorAll?.('tr') || [])].find(r => r.dataset?.resRow === key);
    if (!tr) return;
    const heldText = holders.length ? holders.join(', ') : '—';
    const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : (used > 0 ? 100 : 0);
    const isFull = cap === 0 || used >= cap;
    const pctText = cap === 0 ? 'PAUSED' : (isFull ? 'FULL' : pct + '%');
    const usedCell = tr.querySelector('[data-res-used]');
    if (usedCell) usedCell.textContent = String(used);
    const heldCell = tr.querySelector('[data-res-held]');
    if (heldCell) { heldCell.textContent = heldText; heldCell.title = heldText; }
    const bar = tr.querySelector('[data-res-bar]');
    if (bar) { bar.style.width = pct + '%'; bar.classList.toggle('full', isFull); }
    const pctCell = tr.querySelector('[data-res-pct]');
    if (pctCell) { pctCell.textContent = pctText; pctCell.classList.toggle('full', isFull); }
  });
}

async function saveResourcesPanel() {
  if (!canControl()) return;
  const feedback = el('resourcesFeedback');
  if (feedback) feedback.textContent = '';
  const menu = el('resourcesMenu');
  const maxInput = el('resMaxWorkers');
  const maxWorkers = Number(maxInput?.value);
  if (!Number.isSafeInteger(maxWorkers) || maxWorkers < 1) {
    if (feedback) feedback.textContent = 'Worker capacity must be a positive integer (>= 1).';
    if (menu) menu.open = true;
    maxInput?.focus();
    return;
  }
  const resources = {};
  for (const input of el('resourcesTableBody')?.querySelectorAll?.('input[data-res-quota]') || []) {
    const val = Number(input.value);
    if (!input.value.trim() || !Number.isSafeInteger(val) || val < 0) {
      if (feedback) feedback.textContent = `Capacity for ${input.dataset.resQuota} must be a nonnegative integer (0 pauses resource).`;
      if (menu) menu.open = true;
      input.focus();
      return;
    }
    Object.defineProperty(resources, input.dataset.resQuota, { value: val, enumerable: true });
  }
  const sched = el('resSchedulingMode')?.value || STATE.scheduling_mode || 'backfill';
  BUSY = true; updateControls();
  try {
    const res = await apiPost('/api/settings/save', { max_workers: maxWorkers, resources, scheduling_mode: sched }, {
      onError: error => {
        if (menu) menu.open = true;
        showFormError(error, SETTINGS_FIELDS, 'resourcesFeedback');
        if (feedback && !feedback.textContent && error?.message) feedback.textContent = error.message;
        return true;
      }
    });
    if (res) {
      if (menu) menu.open = false;
      await fetchState();
      recordFeedback(`Updated quotas & scheduling mode (${sched}): ${maxWorkers} worker${maxWorkers === 1 ? '' : 's'}.`);
    }
  } finally {
    BUSY = false; updateControls();
  }
}

async function reloadWorkFile() {
  if (!CONNECTED || BUSY || STATE.opened_run_id || !STATE.source_path) return;
  EDIT_QUEUE_MODE = false;
  EDITOR_DRAG_ID = null;
  LAST_MOVED_JOB_ID = null;
  ++LAST_MOVED_TOKEN;
  EDITOR_CHECKED.clear();
  QE_REMOVED_OPEN = false;
  await loadWorkspacePreview('/api/work/reload', {});
}

function renderTopBar() {
  const count = STATE.jobs.length, sources = STATE.sources || {};
  const kinds = new Set(STATE.jobs.map(job => job.source_kind || 'process'));
  const sourceOnly = !kinds.has('process') && (count > 0 || STATE.ready || sources.configured_watchers > 0);
  const mode = kinds.size === 1 ? SOURCE_LABELS[kinds.values().next().value] : null;
  el('statusDot').classList.toggle('paused', STATE.paused || !STATE.started);
  el('btnPauseResume').textContent = STATE.paused ? 'Resume Queue' : 'Pause Queue';
  el('btnStart').hidden = !!STATE.opened_run_id || STATE.started || (!count && !STATE.ready);
  el('btnStart').textContent = !count ? 'Start Watching' :
    mode && mode !== SOURCE_LABELS.process && kinds.values().next().value !== 'process' ?
      `${mode.action} ${count} ${mode.unit}${count === 1 ? '' : 's'}` :
      `Start (${count})`;
  const pendingCount = STATE.jobs.filter(j => j.status === 'PENDING' && !j.held).length,
    heldCount = STATE.jobs.filter(j => j.status === 'PENDING' && !!j.held).length,
    runningCount = STATE.jobs.filter(j => j.status === 'RUNNING').length,
    promptCount = STATE.jobs.filter(j => j.status === 'WAITING_INPUT').length;
  const readers = sources.active_readers ?? 0;
  const folders = sources.active_watchers ?? 0;
  const waitingForLogs = folders > 0 && readers === 0;
  const folderActivity = folders > 1 ? `${folders} folder watches active` : 'Folder watch active';
  el('queueTargetLabel').textContent = 'Queue · all ' + STATE.jobs.length + ' jobs';
  let h = `<button type="button" class="pill interactive" data-quick-kind="all" onclick="clearFilters()" title="Clear job-list filters; keep Log filters and selection">All: ${STATE.jobs.length}</button>` +
    (pendingCount ? `<button type="button" class="pill interactive pending" data-quick-kind="state" data-quick-value="PENDING" onclick="quickFilterState('PENDING')" title="Click to filter Pending jobs (excluding Held)">Pending: ${pendingCount}</button>` : '') +
    (heldCount ? `<button type="button" class="pill interactive held" data-quick-kind="held" onclick="quickFilterHeld()" title="Click to filter Held / excluded jobs">Held: ${heldCount}</button>` : '') +
    (runningCount ? `<button type="button" class="pill interactive run" data-quick-kind="state" data-quick-value="RUNNING" onclick="quickFilterState('RUNNING')" title="Click to filter Running jobs">Running: ${runningCount}</button>` : '');
  if (promptCount) {
    h += `<button type="button" class="pill wait interactive" data-quick-kind="state" data-quick-value="WAITING_INPUT" onclick="quickFilterState('WAITING_INPUT')" title="Click to filter jobs with detected prompt">Prompt: ${promptCount}</button>`;
  }
  ['PASS', 'FAIL', 'ERROR', 'INCOMPLETE'].forEach(r => {
    const n = STATE.jobs.filter(j => j.result === r).length;
    if (n) h += `<button type="button" class="pill interactive ${r==='PASS'?'done':'fail'}" data-quick-kind="result" data-quick-value="${r}" onclick="quickFilterResult('${r}')" title="Click to filter ${r} result">${r}: ${n}</button>`;
  });
  el('resourceSummary').textContent = Object.entries(STATE.resources || {}).map(([k,v]) =>
    k + ': ' + ((STATE.resources_in_use || {})[k] || 0) + '/' + v).join(' · ');
  const n = STATE.jobs.reduce((acc, j) => acc + j.warning_count, 0);
  if (n) h += `<button type="button" class="pill interactive alert" data-quick-kind="warning" onclick="quickFilterWarnings()" title="Click to toggle warning filter">Warnings: ${n}</button>`;
  setMarkup('summaryPills', h);
  syncQuickFilters();
  let status;
  if (STATE.opened_run_id) status = 'Read-only saved run.';
  else if (!STATE.started) {
    if (mode) status = `Preview — review ${mode.target}, then ${mode.action}.`;
    else if (count) status = sourceOnly || kinds.size > 1 ? 'Preview — review sources, then start.' : 'Preview — review, then Start.';
    else status = STATE.ready ? 'Ready to watch for new logs.' : 'Open a work file or logs to begin.';
  } else if (sourceOnly) {
    if (waitingForLogs) status = 'Folder watch active — waiting for logs.';
    else if (readers) status = mode?.status || 'Reading sources…';
    else status = kinds.has('tail') || sources.configured_watchers ? 'Watching stopped.' : 'Source readers are idle.';
  } else status = STATE.paused ? 'Dispatch paused; running jobs continue.' : 'Dispatch active.';
  const active = hasActiveWork();
  const schedLabel = (STATE.scheduling_mode || 'backfill').toUpperCase();
  const stageBadge = STATE.opened_run_id ? 'READ-ONLY' :
    !STATE.started ? (schedLabel !== 'BACKFILL' ? `PREVIEW · ${schedLabel}` : 'PREVIEW') :
    sourceOnly ? (waitingForLogs || readers ? 'WATCHING' : 'STOPPED') :
    STATE.paused ? (active ? 'PAUSED · DRAINING' : 'PAUSED · IDLE') :
    (schedLabel !== 'BACKFILL' ? `ACTIVE · ${schedLabel}` : 'ACTIVE');
  const badgeEl = el('workspaceBadge');
  if (badgeEl) {
    badgeEl.textContent = stageBadge;
    badgeEl.classList.toggle('paused', !STATE.opened_run_id && STATE.started && !!STATE.paused);
    badgeEl.classList.toggle('active', !STATE.opened_run_id && STATE.started && !STATE.paused);
    badgeEl.classList.toggle('readonly', !!STATE.opened_run_id);
  }
  const baseName = workBasename(STATE.source_path);
  const fullStatus = [...(sourceOnly ? [status, STATE.source_path] : [STATE.source_path, status]), STATE.launch_cwd ? 'cwd: ' + STATE.launch_cwd : ''].filter(Boolean).join(' · ');
  el('workspaceInfo').dataset.activity = status;
  el('workspaceInfo').textContent = sourceOnly ? (baseName ? status + ' — ' + baseName : status) : (baseName || status);
  el('workspaceInfo').title = fullStatus;
  if (el('workspaceMeta')) el('workspaceMeta').title = fullStatus;
  renderResourcesPanel(false);
}

function jobSearchText(j) {
  let text = JOB_SEARCH_CACHE.get(j);
  if (text !== undefined) return text;
  const parts = [
    j.name,
    Array.isArray(j.cmd) ? j.cmd.join(' ') : j.cmd,
    j.pending_reason,
    j.flow_summary?.reason,
    j.flow_summary?.producer,
    j.resources ? Object.entries(j.resources).map(([k, v]) => k + ':' + v).join(' ') : '',
    j.errors?.length ? JSON.stringify(j.errors) : '',
    j.termination?.length ? JSON.stringify(j.termination) : '',
    j.data && Object.keys(j.data).length ? JSON.stringify(j.data) : ''
  ];
  text = parts.filter(Boolean).join(' ').toLowerCase();
  JOB_SEARCH_CACHE.set(j, text);
  return text;
}

function filteredJobs() {
  const s = el('stateFilter').value,
    r = el('resultFilter').value,
    heldOnly = !!el('heldFilter')?.checked,
    warnOnly = !!el('warningFilter')?.checked,
    q = el('jobSearch').value.toLowerCase().trim();
  let jobs = STATE.jobs.filter(j => {
    if (s) {
      if (j.status !== s) return false;
      if (s === 'PENDING' && !heldOnly && j.held) return false;
    }
    if (r && (j.result || '-') !== r) return false;
    if (heldOnly && !(j.status === 'PENDING' && !!j.held)) return false;
    if (warnOnly && !(j.warning_count > 0)) return false;
    if (q && !jobSearchText(j).includes(q)) return false;
    return true;
  });
  if (SORT_KEY) jobs = jobs.slice().sort((a, b) => {
    const value = job => SORT_KEY === 'pid' ? job.pid ?? job.origin_label :
      SORT_KEY.startsWith('data:') ? job.data[SORT_KEY.slice(5)] : job[SORT_KEY];
    const n = compareJobValues(value(a), value(b));
    return SORT_ASC ? n : -n;
  });
  return jobs;
}

function compareJobValues(a, b) {
  const numeric = value => typeof value === 'number' && Number.isFinite(value) ? value :
    (typeof value === 'string' && /^[+-]?\d+$/.test(value) ? BigInt(value) : null);
  const av = numeric(a), bv = numeric(b);
  // Relational comparison keeps integer strings exact, including against floats.
  if (av !== null && bv !== null) return av < bv ? -1 : (av > bv ? 1 : 0);
  return String(a ?? '').localeCompare(String(b ?? ''), undefined, { numeric: true });
}

function clearFilters() {
  el('stateFilter').value = '';
  el('resultFilter').value = '';
  el('warningFilter').checked = false;
  if (el('heldFilter')) el('heldFilter').checked = false;
  el('jobSearch').value = '';
  renderMonitor();
}

function sortJobs(key) {
  SORT_ASC = SORT_KEY === key ? !SORT_ASC : true;
  SORT_KEY = key;
  renderMonitor();
}

function badge(r) {
  const cls = r ? r.replace(/[^A-Z]/g, '') : '-';
  return `<span class="status-badge result-${escHtml(cls)}">${escHtml(r||'—')}</span>`;
}

function tableFocus() {
  const node = document.activeElement;
  if (!node) return null;
  if (el('jobTableHead').contains(node)) {
    const th = node.closest?.('th');
    return {node, sort: node.dataset.sort, header: true,
      field: th?.dataset.readingField ?? th?.dataset.readingFixed,
      resize: !!node.closest?.('[data-column-resize]')};
  }
  const row = node.closest?.('.job-row');
  if (!row || !el('jobTableBody').contains(row)) return null;
  const control = node.tagName === 'INPUT' ? 'input' :
    (node.classList.contains('warning-count') ? '.warning-count' :
      (node.classList.contains('error-badge-btn') ? '.error-badge-btn' :
        (node.classList.contains('log-save-error') ? '.log-save-error' :
          (node.classList.contains('job-priority') ? '.job-priority' : null))));
  return { node, jobId: row.dataset.jobId, control };
}

function restoreTableFocus(focus) {
  if (!focus || document.activeElement !== document.body) return;
  let node = focus.node.isConnected ? focus.node : null;
  if (!node && focus.header) {
    const th = [...el('jobTableHead').querySelectorAll('th')].find(h => (h.dataset.readingField ?? h.dataset.readingFixed) === focus.field);
    node = focus.field ? (focus.resize ? th?.querySelector('[data-column-resize]') : th?.querySelector('[data-sort]') || th) :
      el('jobTableHead').querySelector('input');
  }
  if (!node && focus.jobId) {
    const row = [...el('jobTableBody').querySelectorAll('.job-row')].find(r => r.dataset.jobId === focus.jobId);
    node = row && (focus.control ? row.querySelector(focus.control) || row : row);
  }
  node?.focus({ preventScroll: true });
}

function updateJobCells(row, fresh) {
  if (row.children.length !== fresh.children.length) {
    row.innerHTML = fresh.innerHTML;
    return;
  }
  // The first cell owns the checkbox; update its property without replacing it.
  for (let index = 1; index < row.children.length; index++) {
    const cell = row.children[index], next = fresh.children[index];
    if (typeof next.getAttributeNames === 'function') {
      for (const name of cell.getAttributeNames()) if (!next.hasAttribute(name)) cell.removeAttribute(name);
      for (const name of next.getAttributeNames()) if (cell.getAttribute(name) !== next.getAttribute(name))
        cell.setAttribute(name, next.getAttribute(name));
    }
    if (cell.innerHTML !== next.innerHTML) cell.innerHTML = next.innerHTML;
  }
}

function jobRowMarkup(j, cols) {
  return `<tr class="job-row ${j.id===SELECTED_JOB_ID?'selected':''}" data-job-id="${escHtml(j.id)}" tabindex="0" aria-selected="${j.id===SELECTED_JOB_ID}">
 <td><input type="checkbox" aria-label="Check ${escHtml(j.name)}" ${CHECKED_JOBS.has(j.id)?'checked':''}></td>
 ${cols.map(field => JobsColumns.cell(j, field)).join('')}<td class="col-filler"></td></tr>`;
}

function emptyJobsMarkup() {
  if (STATE.jobs.length) return `No jobs match these filters. <button type="button" onclick="clearFilters(); el('jobSearch').focus()">Clear filters</button>`;
  if (STATE.opened_run_id) return 'No jobs in this saved run.';
  const sources = STATE.sources;
  if (sources?.active_watchers) return 'Watching folders — waiting for new logs.';
  if (sources?.configured_watchers) return STATE.started ? 'Folder watching stopped. No matching logs found.' :
    'No matching logs yet. Start watching to wait for new logs.';
  return `No jobs loaded.
    <button type="button" onclick="openWorkFile('work')">Open Work File (.py)</button>
    <button type="button" onclick="openWorkFile('logs')">Open Logs or Folder</button>
    <button type="button" onclick="openWorkFile('archive')">Open Archive (.sqlite3)</button>`;
}

function bindJobRowHandlers(row) {
  if (!row._waveRowBound) {
    row._waveRowBound = true;
    row.onclick = () => selectJob(row.dataset.jobId);
    row.ondblclick = e => { if (!e.target.closest?.('button,input,select')) inspectJob(row.dataset.jobId); };
    row.oncontextmenu = async e => {
      e.preventDefault();
      if (WORKSPACE_LAYOUT === 'jobs') setWorkspaceLayout('split');
      await selectJob(row.dataset.jobId, 'details');
    };
    row.onkeydown = e => {
      if (e.target !== row) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        if (WORKSPACE_LAYOUT === 'jobs' && e.key === 'Enter') inspectJob(row.dataset.jobId);
        else selectJob(row.dataset.jobId);
      } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        const body = el('jobTableBody');
        const rows = [...body.querySelectorAll('.job-row')], at = rows.indexOf(row),
          next = rows[at + (e.key === 'ArrowUp' ? -1 : 1)];
        if (next) {
          next.focus({ preventScroll: true });
          next.scrollIntoView({ block: 'nearest' });
          selectJob(next.dataset.jobId);
        }
      }
    };
  }
  const jid = row.dataset.jobId;
  const c = row.querySelector('input');
  if (c) {
    c.checked = CHECKED_JOBS.has(jid);
    if (c.parentElement) c.parentElement.onclick = e => e.stopPropagation();
    c.onchange = () => {
      c.checked ? CHECKED_JOBS.add(jid) : CHECKED_JOBS.delete(jid);
      renderMonitor();
    };
  }
  const priority = row.querySelector('.job-priority');
  if (priority) {
    priority.onclick = e => { e.stopPropagation(); openPriorityEditor(jid); };
  }
  const w = row.querySelector('.warning-count');
  if (w) w.onclick = async e => {
    e.stopPropagation();
    await selectJob(jid, 'analysis');
    openJobFinding('warnings');
  };
  const reason = row.querySelector('.job-reason');
  if (reason) reason.onclick = async e => {
    e.stopPropagation();
    const job = STATE.jobs.find(item => item.id === jid);
    if (WORKSPACE_LAYOUT === 'jobs') setWorkspaceLayout('split');
    await selectJob(jid, 'analysis');
    if (job?.flow_summary?.producer && typeof FlowWorkspace !== 'undefined') FlowWorkspace.selectAnalyzer(job.flow_summary.producer);
  };
  const errBtn = row.querySelector('.error-badge-btn');
  if (errBtn) errBtn.onclick = async e => {
    e.stopPropagation();
    await selectJob(jid, 'analysis');
    openJobFinding('errors');
  };
  const saveBtn = row.querySelector('.log-save-error');
  if (saveBtn) saveBtn.onclick = async e => {
    e.stopPropagation();
    await selectJob(jid, 'details');
    FlowWorkspace.diagnostics();
  };
}

function renderMonitor() {
  syncQuickFilters();
  const focus = tableFocus(), jobs = filteredJobs(), cols = JobsColumns.columns(),
    head = el('jobTableHead'), body = el('jobTableBody');
  pruneCheckedJobs();
  const table = el('jobsTable'), layout = JobsColumns.layout(cols);
  const resizing = typeof ReadingLayout !== 'undefined' && ReadingLayout.isResizing(table);
  if (!resizing) {
    setMarkup('jobTableColumns', layout.html.replace(/^<colgroup>|<\/colgroup>$/g, ''));
    table.style.minWidth = layout.size + 'px';
    table.style.width = '100%';
  }
  const header = '<tr><th style="width:28px"><input type="checkbox" aria-label="Check visible jobs"></th>' +
    cols.map(field => JobsColumns.header(field)).join('') +
    '<th class="col-filler" aria-hidden="true"></th></tr>';
  if (head._waveMarkup !== header && !resizing) {
    head.innerHTML = header;
    head._waveMarkup = header;
    const master = head.querySelector('input');
    if (master) {
      master.onchange = () => {
        const visible = filteredJobs();
        visible.forEach(j => master.checked ? CHECKED_JOBS.add(j.id) : CHECKED_JOBS.delete(j.id));
        renderMonitor();
      };
    }
    head.querySelectorAll('[data-sort]').forEach(b => b.onclick = () => sortJobs(b.dataset.sort));
  }
  const master = head.querySelector('input'),
    n = jobs.filter(j => CHECKED_JOBS.has(j.id)).length;
  if (master) {
    master.checked = jobs.length > 0 && n === jobs.length;
    master.indeterminate = n > 0 && n < jobs.length;
    master.disabled = !jobs.length;
  }
  const previous = new Map([...body.querySelectorAll('.job-row')].map(row => [row.dataset.jobId, row]));
  jobs.forEach((j, index) => {
    let row = previous.get(j.id);
    const markup = jobRowMarkup(j, cols);
    const changed = !row || row._waveMarkup !== markup;
    if (changed) {
      const template = document.createElement('template');
      template.innerHTML = markup;
      const fresh = template.content.firstElementChild;
      if (row) {
        updateJobCells(row, fresh);
        row.className = fresh.className;
        row.setAttribute('aria-selected', fresh.getAttribute('aria-selected'));
      } else row = fresh;
      row._waveMarkup = markup;
      bindJobRowHandlers(row);
    } else {
      const c = row.querySelector('input');
      if (c) c.checked = CHECKED_JOBS.has(j.id);
    }
    if (body.children[index] !== row) body.insertBefore(row, body.children[index] || null);
    previous.delete(j.id);
  });
  previous.forEach(row => row.remove());
  const empty = body.querySelector('.jobs-empty');
  if (jobs.length) empty?.remove();
  else {
    const markup = emptyJobsMarkup();
    if (empty?.dataset.emptyMarkup !== markup) {
      body.innerHTML = '<tr class="jobs-empty"><td><div class="jobs-empty-content">' + markup + '</div></td></tr>';
      body.firstElementChild.dataset.emptyMarkup = markup;
    }
    body.firstElementChild.firstElementChild.colSpan = cols.length + 2;
  }
  restoreTableFocus(focus);
  if (jobs.length && !resizing && typeof ReadingLayout !== 'undefined' && typeof ReadingLayout.autoFitInitial === 'function') {
    ReadingLayout.autoFitInitial(table, JobsColumns.scope(), {
      maxWidth: 240,
      maxByField: { name: 280, pending_reason: 300, resources: 240 }
    });
  }
  el('tableInfo').textContent = jobs.length + '/' + STATE.jobs.length + ' shown';
  el('jobCount').textContent = jobs.length + '/' + STATE.jobs.length;
  el('tableInfo').title = el('tableInfo').textContent +
    ' · Click: select · Right-click: Details · Sorting does not change dispatch order';
  syncNarrowTableClass();
  syncQueueEditorView();
  if (EDIT_QUEUE_MODE) renderQueueEditor();
  renderDetail();
  updateControls(jobs);
}

function syncQueueEditorView() {
  const wrap = el('jobsTableWrap'), editor = el('queueEditorView'),
    btn = el('btnEditQueue'), pane = el('paneLeft'), inlineHead = el('qeHeaderInline');
  if (pane?.classList?.toggle) pane.classList.toggle('queue-editing', !!EDIT_QUEUE_MODE);
  if (wrap) wrap.hidden = EDIT_QUEUE_MODE;
  if (editor) editor.hidden = !EDIT_QUEUE_MODE;
  if (inlineHead) inlineHead.hidden = !EDIT_QUEUE_MODE;
  if (btn) {
    btn.setAttribute?.('aria-pressed', String(EDIT_QUEUE_MODE));
    if (EDIT_QUEUE_MODE) btn.textContent = 'Close Queue Editor';
    else {
      const icon = waveIconSvg('arrows-up-down');
      btn.innerHTML = (icon ? `<span aria-hidden="true">${icon}</span>` : '') + '<span class="btn-label">Edit Queue</span>';
    }
    btn.title = EDIT_QUEUE_MODE
      ? 'Close Queue Editor and return to Jobs table'
      : 'Edit queue dispatch priority order, hold/include jobs, or remove queued jobs';
  }
}

function toggleEditQueue(force) {
  const next = typeof force === 'boolean' ? force : !EDIT_QUEUE_MODE;
  if (next && STATE.opened_run_id) return;
  if (next && WORKSPACE_LAYOUT !== 'jobs') setWorkspaceLayout('jobs');
  EDIT_QUEUE_MODE = next;
  EDITOR_DRAG_ID = null;
  syncQueueEditorView();
  if (EDIT_QUEUE_MODE) {
    setupQueueEditorEvents();
    renderQueueEditor(true);
  }
  updateControls();
}

function queueEditorBuckets() {
  const processJobs = (STATE.jobs || []).filter(j => supportsAction(j, 'priority'));
  const indexMap = new Map(processJobs.map((j, i) => [j.id, i]));
  const activeJobs = processJobs.filter(j => ACTIVE.has(j.status));
  const pendingJobs = processJobs
    .filter(j => j.status === 'PENDING' && !j.held)
    .slice()
    .sort((a, b) => (b.priority - a.priority) || (indexMap.get(a.id) - indexMap.get(b.id)));
  const heldJobs = processJobs.filter(j => j.status === 'PENDING' && !!j.held);
  const finishedJobs = processJobs.filter(j => FINISHED.has(j.status));
  const isPreview = !STATE.started;
  const active = hasActiveWork();
  const isStoppedIdle = !!STATE.started && !!STATE.paused && !active;
  return { processJobs, activeJobs, pendingJobs, heldJobs, finishedJobs, isPreview, active, isStoppedIdle };
}

function formatCmdSummary(cmd) {
  if (Array.isArray(cmd)) return cmd.join(' ');
  return String(cmd || '');
}

function formatResTags(resources) {
  if (!resources || typeof resources !== 'object') return '';
  const entries = Object.entries(resources);
  if (!entries.length) return '';
  return entries.map(([k, v]) => `<span class="qe-res-tag">${escHtml(k)}: ${escHtml(v)}</span>`).join('');
}

function editorRowEditableRemoval(job, buckets) {
  if (buckets.isPreview) return job.status === 'PENDING' && !job.reserved;
  return buckets.isStoppedIdle && !job.reserved && !ACTIVE.has(job.status);
}

function queuePillLabel(status, held = false) {
  if (held) {
    const icon = typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function' ? WaveIcons.svg('pause') : '';
    return { cls: 'st-HELD', html: `${icon ? icon + ' ' : ''}HELD` };
  }
  const map = {
    PENDING: ['st-PENDING', 'PENDING'],
    RUNNING: ['st-RUNNING', 'RUNNING'],
    WAITING_INPUT: ['st-WAITING_INPUT', 'PROMPT'],
    STOPPING: ['st-STOPPING', 'STOPPING'],
    DONE: ['st-DONE', 'DONE'],
    FAILED: ['st-FAILED', 'FAILED'],
    CANCELLED: ['st-CANCELLED', 'CANCELLED'],
    REMOVED: ['st-REMOVED', 'REMOVED']
  };
  const [cls, text] = map[status] || ['st-' + escHtml(status), escHtml(status)];
  return { cls, html: text };
}

function queueLeadCell(pillCls, pillHtml, draggable) {
  const gripSvg = typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function' ? WaveIcons.svg('grip-vertical') : '::';
  const lead = draggable ?
    `<span class="qe-grip" aria-hidden="true">${gripSvg}</span>` :
    '<span class="qe-grip-spacer" aria-hidden="true"></span>';
  return `<div class="qe-lead-cell">${lead}<span class="qe-status-pill ${pillCls}">${pillHtml}</span></div>`;
}

const QE_COLGROUP = '<colgroup><col style="width:28px"><col style="width:118px"><col><col style="width:290px"><col style="width:175px"></colgroup>';

function renderQueueEditor(force = false) {
  const root = el('queueEditorView');
  if (!root || !EDIT_QUEUE_MODE || (!force && (EDITOR_DRAG_ID || document.activeElement?.id === 'qeSchedulingMode'))) return;
  const buckets = queueEditorBuckets();
  const validIds = new Set(buckets.processJobs.map(j => j.id));
  for (const id of EDITOR_CHECKED) if (!validIds.has(id)) EDITOR_CHECKED.delete(id);

  const enabled = workspaceIsWritable();
  const canDelete = enabled && (buckets.isPreview || buckets.isStoppedIdle);
  const canRequeue = enabled && !!STATE.started && !buckets.active;
  const canReorder = enabled && buckets.pendingJobs.length > 1;
  const checkedList = buckets.processJobs.filter(j => EDITOR_CHECKED.has(j.id));
  const checkedPending = checkedList.filter(j => j.status === 'PENDING' && !j.held);
  const checkedHeld = checkedList.filter(j => j.status === 'PENDING' && !!j.held);
  const checkedFinished = checkedList.filter(j => FINISHED.has(j.status));
  const checkedRemovable = canDelete ? checkedList.filter(j => editorRowEditableRemoval(j, buckets)) : [];
  const canRunCheckedOnly = enabled && checkedList.some(j => j.status === 'PENDING') && (buckets.pendingJobs.length + buckets.heldJobs.length) > 0;
  const schedMode = STATE.scheduling_mode || 'backfill';

  const zoneTitle = buckets.isPreview ? 'PREVIEW · Full edit' :
    buckets.isStoppedIdle ? 'PAUSED · Full edit' :
    STATE.paused ? 'PAUSED · Active locked' : 'LIVE · Active locked';
  const zoneClass = buckets.isStoppedIdle || buckets.isPreview ? '' : ' warn';
  const focusBtn = document.activeElement && root.contains(document.activeElement) ? {
    action: document.activeElement.dataset?.qeAction,
    move: document.activeElement.dataset?.qeMove,
    id: document.activeElement.closest?.('[data-job-id]')?.dataset?.jobId
  } : null;

  const headerHtml = `<div class="qe-header-title">
      <strong>Queue Editor</strong>
      <span class="qe-zone-tag${zoneClass}">${zoneTitle}</span>
    </div>
    <div class="qe-header-actions">
      ${STATE.started ? (!STATE.paused ? `<button type="button" data-qe-action="pause_stage" ${enabled ? '' : 'disabled'}>Pause Queue</button>` : `<button type="button" data-qe-action="resume_stage" ${enabled ? '' : 'disabled'}>Resume Queue</button>`) : ''}
      <button type="button" data-qe-action="hold_checked" ${enabled && checkedPending.length ? '' : 'disabled'}>Hold (${checkedPending.length})</button>
      <button type="button" data-qe-action="include_checked" ${enabled && checkedHeld.length ? '' : 'disabled'}>Include (${checkedHeld.length})</button>
      <button type="button" data-qe-action="run_checked_only" ${canRunCheckedOnly ? '' : 'disabled'} title="Hold unchecked pending jobs and include checked ones">Run Checked Only</button>
      <button type="button" data-qe-action="requeue_checked" ${canRequeue && checkedFinished.length ? '' : 'disabled'}>Re-queue (${checkedFinished.length})</button>
      <button type="button" class="danger" data-qe-action="delete_checked" ${canDelete && checkedRemovable.length ? '' : 'disabled'}>Delete (${checkedRemovable.length})</button>
    </div>`;
  const inlineHead = el('qeHeaderInline');
  if (inlineHead && inlineHead.dataset?.qeHeaderMarkup !== headerHtml) {
    inlineHead.innerHTML = headerHtml;
    if (inlineHead.dataset) inlineHead.dataset.qeHeaderMarkup = headerHtml;
  }

  let html = '';

  if (buckets.activeJobs.length) {
    html += `<section class="qe-section">
      <div class="qe-section-head"><span><strong>Active</strong> (${buckets.activeJobs.length})</span></div>
      <div class="qe-table-wrap"><table class="qe-table" aria-label="Active jobs">${QE_COLGROUP}<tbody>
      ${buckets.activeJobs.map(j => {
        const pill = queuePillLabel(j.status, false);
        const resTags = formatResTags(j.resources);
        const elapsed = escHtml(j.elapsed_str ?? (j.elapsed == null ? 'Active' : j.elapsed + 's'));
        return `<tr class="qe-row qe-locked" data-job-id="${escHtml(j.id)}">
          <td><input type="checkbox" disabled aria-label="Active ${escHtml(j.name)}"></td>
          <td>${queueLeadCell(pill.cls, pill.html, false)}</td>
          <td><div class="qe-job-cell"><span class="qe-job-name">${escHtml(j.name)}</span><span class="qe-job-cmd" title="${escHtml(formatCmdSummary(j.cmd))}">${escHtml(formatCmdSummary(j.cmd))}</span></div></td>
          <td><div class="qe-meta-cell">${resTags}<span class="help-text">${elapsed}</span><span class="qe-prio-tag">P:${escHtml(j.priority)}</span></div></td>
          <td><div class="qe-row-actions"><button type="button" data-qe-action="stop_one" ${enabled && allowed(j, 'stop') ? '' : 'disabled'}>Stop</button></div></td>
        </tr>`;
      }).join('')}
      </tbody></table></div>
    </section>`;
  }

  html += `<section class="qe-section">
    <div class="qe-section-head"><span><strong>Pending</strong> (${buckets.pendingJobs.length})</span>
      <label class="qe-sched-inline" for="qeSchedulingMode"><span>Scheduling Mode:</span>
        <select id="qeSchedulingMode" data-qe-scheduling aria-label="Scheduling mode" ${enabled ? '' : 'disabled'}>
          <option value="backfill"${schedMode === 'backfill' ? ' selected' : ''}>backfill</option>
          <option value="strict"${schedMode === 'strict' ? ' selected' : ''}>strict</option>
          <option value="tier_strict"${schedMode === 'tier_strict' ? ' selected' : ''}>tier_strict</option>
        </select>
      </label>
    </div>
    ${buckets.pendingJobs.length ? `<div class="qe-table-wrap"><table class="qe-table" aria-label="Pending dispatch queue">${QE_COLGROUP}<tbody>
    ${buckets.pendingJobs.map((j, idx) => {
      const checked = EDITOR_CHECKED.has(j.id);
      const pill = queuePillLabel('PENDING', false);
      const rawNote = j.pending_reason || '';
      const statusNote = /^(Ready to start|Not started)$/i.test(rawNote.trim()) ? '' : rawNote;
      const resTags = formatResTags(j.resources);
      const removable = canDelete && editorRowEditableRemoval(j, buckets);
      const movedCls = j.id === LAST_MOVED_JOB_ID ? ' qe-just-moved' : '';
      const movedAttr = j.id === LAST_MOVED_JOB_ID ? ` data-move-token="${LAST_MOVED_TOKEN}"` : '';
      const topIcon = waveIconSvg('corner-left-up') || '⤒';
      const upIcon = waveIconSvg('arrow-up') || '↑';
      const downIcon = waveIconSvg('arrow-down') || '↓';
      return `<tr class="qe-row${movedCls}" data-qe-pending="1" data-job-id="${escHtml(j.id)}"${movedAttr} draggable="${canReorder ? 'true' : 'false'}">
        <td><input type="checkbox" data-qe-check aria-label="Select ${escHtml(j.name)}" ${checked ? 'checked' : ''}></td>
        <td>${queueLeadCell(pill.cls, pill.html, canReorder)}</td>
        <td><div class="qe-job-cell"><span class="qe-job-name" title="${escHtml(j.name)}">${escHtml(j.name)}</span><span class="qe-job-cmd" title="${escHtml(formatCmdSummary(j.cmd))}">${escHtml(formatCmdSummary(j.cmd))}</span></div></td>
        <td><div class="qe-meta-cell"><span class="qe-rank">#${idx + 1}</span>${resTags}${statusNote ? `<span class="help-text qe-note" title="${escHtml(statusNote)}">${escHtml(statusNote)}</span>` : ''}<span class="qe-prio-tag">P:${escHtml(j.priority)}</span></div></td>
        <td><div class="qe-row-actions">
          <span class="qe-move-btns">
            <button type="button" data-qe-move="top" title="Move to top (#1)" aria-label="Move ${escHtml(j.name)} to top" ${!canReorder || idx === 0 ? 'disabled' : ''}>${topIcon}</button>
            <button type="button" data-qe-move="up" title="Move up" aria-label="Move ${escHtml(j.name)} up" ${!canReorder || idx === 0 ? 'disabled' : ''}>${upIcon}</button>
            <button type="button" data-qe-move="down" title="Move down" aria-label="Move ${escHtml(j.name)} down" ${!canReorder || idx === buckets.pendingJobs.length - 1 ? 'disabled' : ''}>${downIcon}</button>
          </span>
          <button type="button" class="warn" data-qe-action="hold_one" ${enabled ? '' : 'disabled'}>Hold</button>
          <button type="button" class="danger" data-qe-action="delete_one" ${removable ? '' : 'disabled'} title="${canDelete ? 'Remove job from queue' : 'Delete available in Preview or when Paused & Idle'}">Delete</button>
        </div></td>
      </tr>`;
    }).join('')}</tbody></table></div>` : '<div class="qe-empty-strip">No pending jobs.</div>'}
  </section>`;

  html += `<section class="qe-section">
    <div class="qe-section-head"><span><strong>Held</strong> (${buckets.heldJobs.length})</span>
    ${buckets.heldJobs.length ? `<div class="qe-section-actions"><button type="button" data-qe-action="include_all" ${enabled ? '' : 'disabled'}>Include All</button></div>` : ''}</div>
    ${buckets.heldJobs.length ? `<div class="qe-table-wrap"><table class="qe-table" aria-label="Held jobs">${QE_COLGROUP}<tbody>
    ${buckets.heldJobs.map(j => {
      const checked = EDITOR_CHECKED.has(j.id);
      const pill = queuePillLabel('PENDING', true);
      const resTags = formatResTags(j.resources);
      const removable = canDelete && editorRowEditableRemoval(j, buckets);
      return `<tr class="qe-row qe-held" data-job-id="${escHtml(j.id)}">
        <td><input type="checkbox" data-qe-check aria-label="Select held ${escHtml(j.name)}" ${checked ? 'checked' : ''}></td>
        <td>${queueLeadCell(pill.cls, pill.html, false)}</td>
        <td><div class="qe-job-cell"><span class="qe-job-name">${escHtml(j.name)}</span><span class="qe-job-cmd" title="${escHtml(formatCmdSummary(j.cmd))}">${escHtml(formatCmdSummary(j.cmd))}</span></div></td>
        <td><div class="qe-meta-cell">${resTags}<span class="qe-prio-tag">P:${escHtml(j.priority)}</span></div></td>
        <td><div class="qe-row-actions">
          <button type="button" data-qe-action="include_one" ${enabled ? '' : 'disabled'}>Include</button>
          <button type="button" class="danger" data-qe-action="delete_one" ${removable ? '' : 'disabled'}>Delete</button>
        </div></td>
      </tr>`;
    }).join('')}</tbody></table></div>` : '<div class="qe-empty-strip">No held jobs.</div>'}
  </section>`;

  if (buckets.finishedJobs.length) {
    html += `<section class="qe-section">
      <div class="qe-section-head"><span><strong>Finished</strong> (${buckets.finishedJobs.length})</span></div>
      <div class="qe-table-wrap"><table class="qe-table" aria-label="Finished jobs in queue editor">${QE_COLGROUP}<tbody>
      ${buckets.finishedJobs.map(j => {
        const checked = EDITOR_CHECKED.has(j.id);
        const pill = queuePillLabel(j.status, false);
        const resTags = formatResTags(j.resources);
        const removable = canDelete && editorRowEditableRemoval(j, buckets);
        return `<tr class="qe-row" data-job-id="${escHtml(j.id)}">
          <td><input type="checkbox" data-qe-check aria-label="Select finished ${escHtml(j.name)}" ${checked ? 'checked' : ''}></td>
          <td>${queueLeadCell(pill.cls, pill.html, false)}</td>
          <td><div class="qe-job-cell"><span class="qe-job-name">${escHtml(j.name)}</span><span class="qe-job-cmd" title="${escHtml(formatCmdSummary(j.cmd))}">${escHtml(formatCmdSummary(j.cmd))}</span></div></td>
          <td><div class="qe-meta-cell">${badge(j.result)}${resTags}${j.completion_reason ? `<span class="help-text qe-note">${escHtml(j.completion_reason)}</span>` : ''}<span class="qe-prio-tag">P:${escHtml(j.priority)}</span></div></td>
          <td><div class="qe-row-actions">
            <button type="button" data-qe-action="requeue_one" ${canRequeue && j.can_rerun ? '' : 'disabled'}>Re-queue</button>
            <button type="button" class="danger" data-qe-action="delete_one" ${removable ? '' : 'disabled'}>Delete</button>
          </div></td>
        </tr>`;
      }).join('')}</tbody></table></div>
    </section>`;
  }

  const removedJobs = (STATE.removed_jobs || []).filter(j => supportsAction(j, 'priority'));
  if (removedJobs.length) {
    const canRestore = enabled && (buckets.isPreview || buckets.isStoppedIdle);
    html += `<details class="qe-section qe-removed-section"${QE_REMOVED_OPEN ? ' open' : ''}>
      <summary class="qe-section-head">
        <span><span class="qe-removed-caret" aria-hidden="true">${waveIconSvg('chevron-right') || '▸'}</span> <strong>Removed</strong> (${removedJobs.length})</span>
        <div class="qe-section-actions"><button type="button" data-qe-action="restore_all" ${canRestore ? '' : 'disabled'} title="${canRestore ? 'Restore all removed jobs back to the queue' : 'Restore available in Preview or when Paused & Idle'}">Restore All</button></div>
      </summary>
      <div class="qe-table-wrap"><table class="qe-table" aria-label="Removed jobs in queue editor">${QE_COLGROUP}<tbody>
      ${removedJobs.map(j => {
        const pill = queuePillLabel('REMOVED', false);
        const resTags = formatResTags(j.resources);
        return `<tr class="qe-row qe-locked" data-job-id="${escHtml(j.id)}">
          <td></td>
          <td>${queueLeadCell(pill.cls, pill.html, false)}</td>
          <td><div class="qe-job-cell"><span class="qe-job-name">${escHtml(j.name)}</span><span class="qe-job-cmd" title="${escHtml(formatCmdSummary(j.cmd))}">${escHtml(formatCmdSummary(j.cmd))}</span></div></td>
          <td><div class="qe-meta-cell">${resTags}<span class="qe-prio-tag">P:${escHtml(j.priority)}</span></div></td>
          <td><div class="qe-row-actions"><button type="button" data-qe-action="restore_one" ${canRestore ? '' : 'disabled'} title="${canRestore ? 'Restore job to queue' : 'Restore available in Preview or when Paused & Idle'}">Restore</button></div></td>
        </tr>`;
      }).join('')}</tbody></table></div>
    </details>`;
  }

  if (root.dataset.qeMarkup !== html) {
    root.innerHTML = html;
    root.dataset.qeMarkup = html;
    if (focusBtn?.id) {
      const targetRow = [...root.querySelectorAll('[data-job-id]')].find(r => r.dataset.jobId === focusBtn.id);
      const restore = targetRow && (focusBtn.move ? targetRow.querySelector(`[data-qe-move="${focusBtn.move}"]`) :
        focusBtn.action ? targetRow.querySelector(`[data-qe-action="${focusBtn.action}"]`) : null);
      if (restore && !restore.disabled) restore.focus({ preventScroll: true });
    }
  }
}

async function handleQueueEditorAction(actBtn, event) {
  if (!actBtn || actBtn.disabled) return;
  const action = actBtn.dataset.qeAction;
  const rowId = actBtn.closest?.('[data-job-id]')?.dataset?.jobId;
  const buckets = queueEditorBuckets();
  const rowJob = rowId ? buckets.processJobs.find(j => j.id === rowId) : null;
  if (action === 'done') return toggleEditQueue(false);
  if (action === 'pause_stage') return managerAction('pause');
  if (action === 'resume_stage') return managerAction('resume');
  if (action === 'stop_one' && rowJob) return jobAction('stop', [rowJob]);
  if (action === 'hold_one' && rowJob) return holdQueueJobs([rowJob], true);
  if (action === 'include_one' && rowJob) return holdQueueJobs([rowJob], false);
  if (action === 'include_all') return holdQueueJobs(buckets.heldJobs, false);
  if (action === 'delete_one' && rowJob) return removeQueueJobs([rowJob]);
  if (action === 'requeue_one' && rowJob) return requeueStoppedJobs([rowJob]);
  if (action === 'restore_one' && rowId) {
    const removed = (STATE.removed_jobs || []).find(j => j.id === rowId);
    if (removed) return restoreQueueJobs([removed]);
    return;
  }
  if (action === 'restore_all') {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    return restoreQueueJobs((STATE.removed_jobs || []).filter(j => supportsAction(j, 'priority')));
  }
  const checked = buckets.processJobs.filter(j => EDITOR_CHECKED.has(j.id));
  if (action === 'hold_checked') return holdQueueJobs(checked.filter(j => j.status === 'PENDING' && !j.held), true);
  if (action === 'include_checked') return holdQueueJobs(checked.filter(j => j.status === 'PENDING' && !!j.held), false);
  if (action === 'run_checked_only') return runCheckedQueueOnly();
  if (action === 'requeue_checked') return requeueStoppedJobs(checked.filter(j => FINISHED.has(j.status)));
  if (action === 'delete_checked') return removeQueueJobs(checked.filter(j => editorRowEditableRemoval(j, buckets)));
}

function setupQueueEditorEvents() {
  const inlineHead = el('qeHeaderInline');
  if (inlineHead && !inlineHead._qeBound) {
    inlineHead._qeBound = true;
    inlineHead.addEventListener?.('click', async event => {
      const actBtn = event.target.closest?.('[data-qe-action]');
      if (actBtn) await handleQueueEditorAction(actBtn, event);
    });
  }
  const root = el('queueEditorView');
  if (!root || root._qeBound) return;
  root._qeBound = true;
  const clearDragMarks = () => {
    root.querySelectorAll('.drag-over-above,.drag-over-below,.dragging').forEach(node => {
      node.classList.remove('drag-over-above', 'drag-over-below', 'dragging');
    });
  };
  root.addEventListener('change', event => {
    const sched = event.target.closest?.('select[data-qe-scheduling]');
    if (sched) {
      setQueueSchedulingMode(sched.value);
      return;
    }
    const chk = event.target.closest?.('input[data-qe-check]');
    if (!chk) return;
    const jid = chk.closest?.('[data-job-id]')?.dataset?.jobId;
    if (!jid) return;
    if (chk.checked) EDITOR_CHECKED.add(jid); else EDITOR_CHECKED.delete(jid);
    renderQueueEditor(true);
  });
  root.addEventListener('toggle', event => {
    if (event.target?.classList?.contains?.('qe-removed-section')) {
      QE_REMOVED_OPEN = !!event.target.open;
    }
  }, true);
  root.addEventListener('dragstart', event => {
    const row = event.target.closest?.('tr.qe-row[data-qe-pending]');
    if (!row || !canControl()) return;
    if (event.target.closest?.('button,input,select')) {
      event.preventDefault();
      return;
    }
    EDITOR_DRAG_ID = row.dataset.jobId;
    row.classList.add('dragging');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', EDITOR_DRAG_ID);
    }
  });
  root.addEventListener('dragover', event => {
    const row = event.target.closest?.('tr.qe-row[data-qe-pending]');
    if (!row || !EDITOR_DRAG_ID) return;
    const targetId = row.dataset.jobId;
    const rows = [...root.querySelectorAll('tr.qe-row[data-qe-pending]')];
    const dragIdx = rows.findIndex(r => r.dataset.jobId === EDITOR_DRAG_ID);
    const hoverIdx = rows.findIndex(r => r.dataset.jobId === targetId);
    rows.forEach(r => r.classList.remove('drag-over-above', 'drag-over-below'));
    if (dragIdx === -1 || hoverIdx === -1 || dragIdx === hoverIdx) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    row.classList.add(dragIdx > hoverIdx ? 'drag-over-above' : 'drag-over-below');
  });
  root.addEventListener('drop', async event => {
    const row = event.target.closest?.('tr.qe-row[data-qe-pending]');
    const dragId = EDITOR_DRAG_ID;
    clearDragMarks();
    EDITOR_DRAG_ID = null;
    if (!row || !dragId || row.dataset.jobId === dragId) {
      flushDeferredRender();
      return;
    }
    event.preventDefault();
    const { pendingJobs } = queueEditorBuckets();
    const ids = pendingJobs.map(j => j.id);
    const from = ids.indexOf(dragId), to = ids.indexOf(row.dataset.jobId);
    if (from === -1 || to === -1 || from === to) {
      flushDeferredRender();
      return;
    }
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    markQueueRowMoved(dragId);
    await reorderPendingIds(ids);
  });
  root.addEventListener('dragend', () => {
    EDITOR_DRAG_ID = null;
    clearDragMarks();
    flushDeferredRender();
  });
  root.addEventListener('click', async event => {
    const moveBtn = event.target.closest?.('[data-qe-move]');
    if (moveBtn && !moveBtn.disabled) {
      const jid = moveBtn.closest?.('[data-job-id]')?.dataset?.jobId;
      const dir = moveBtn.dataset.qeMove;
      const { pendingJobs } = queueEditorBuckets();
      const ids = pendingJobs.map(j => j.id);
      const idx = ids.indexOf(jid);
      if (idx === -1) return;
      if (dir === 'top' && idx > 0) {
        ids.splice(idx, 1);
        ids.unshift(jid);
        markQueueRowMoved(jid);
        await reorderPendingIds(ids);
      } else if (dir === 'up' && idx > 0) {
        [ids[idx - 1], ids[idx]] = [ids[idx], ids[idx - 1]];
        markQueueRowMoved(jid);
        await reorderPendingIds(ids);
      } else if (dir === 'down' && idx < ids.length - 1) {
        [ids[idx], ids[idx + 1]] = [ids[idx + 1], ids[idx]];
        markQueueRowMoved(jid);
        await reorderPendingIds(ids);
      }
      return;
    }
    const actBtn = event.target.closest?.('[data-qe-action]');
    if (actBtn) await handleQueueEditorAction(actBtn, event);
  });
}

async function runQueueEditAction(task) {
  if (!canControl()) return null;
  BUSY = true;
  try {
    const res = await task();
    BUSY = false;
    await fetchState();
    return res;
  } finally {
    BUSY = false;
    updateControls();
    if (EDIT_QUEUE_MODE) renderQueueEditor(true);
  }
}

async function setQueueSchedulingMode(mode) {
  if (!['backfill', 'strict', 'tier_strict'].includes(mode) || mode === STATE.scheduling_mode) return null;
  return runQueueEditAction(async () => {
    const res = await apiPost('/api/settings/save', { scheduling_mode: mode });
    if (res) recordFeedback(`Scheduling mode set to ${mode}.`);
    return res;
  });
}

function markQueueRowMoved(jobId) {
  LAST_MOVED_JOB_ID = jobId;
  const tok = ++LAST_MOVED_TOKEN;
  if (typeof setTimeout === 'function') {
    setTimeout(() => {
      if (LAST_MOVED_TOKEN === tok) LAST_MOVED_JOB_ID = null;
    }, 850);
  }
}

async function reorderPendingIds(orderedPendingIds) {
  const buckets = queueEditorBuckets();
  const byId = new Map(buckets.pendingJobs.map(j => [j.id, j]));
  const orderedJobs = orderedPendingIds.map(id => byId.get(id)).filter(Boolean).concat(buckets.heldJobs);
  if (!orderedJobs.length) return null;
  return runQueueEditAction(async () => {
    const res = await apiPost('/api/jobs/reorder', { targets: targets(orderedJobs) });
    if (res) recordFeedback(`Updated dispatch queue order (${res.affected}/${orderedJobs.length}).`);
    return res;
  });
}

async function holdQueueJobs(jobsList, held) {
  if (!jobsList.length) return null;
  const reqTargets = captureTargets(jobsList);
  return runQueueEditAction(async () => {
    const res = await apiPost('/api/jobs/hold', { targets: targets(reqTargets), held });
    if (res) recordFeedback(`${held ? 'Held' : 'Included'} ${res.affected}/${jobsList.length} job(s).`);
    return res;
  });
}

async function removeQueueJobs(jobsList) {
  if (!jobsList.length) return null;
  const reqTargets = captureTargets(jobsList);
  return runQueueEditAction(async () => {
    const res = await apiPost('/api/jobs/remove', { targets: targets(reqTargets) });
    if (res) {
      if (res.affected !== jobsList.length) report(res);
      else recordFeedback(`Removed ${res.affected} job(s) from queue.`);
      for (const item of res.results || []) if (item.ok) EDITOR_CHECKED.delete(item.job_id);
    }
    return res;
  });
}

async function restoreQueueJobs(jobsList) {
  if (!jobsList.length) return null;
  const reqTargets = captureTargets(jobsList);
  return runQueueEditAction(async () => {
    const res = await apiPost('/api/jobs/restore', { targets: targets(reqTargets) });
    if (res) {
      if (res.affected !== jobsList.length) report(res);
      else recordFeedback(`Restored ${res.affected} job(s) to queue.`);
    }
    return res;
  });
}

async function requeueStoppedJobs(jobsList) {
  if (!jobsList.length) return null;
  const reqTargets = captureTargets(jobsList);
  return runQueueEditAction(async () => {
    const res = await apiPost('/api/jobs/requeue', { targets: targets(reqTargets) });
    if (res) {
      if (res.affected !== jobsList.length) report(res);
      else recordFeedback(`Re-queued ${res.affected} job(s) while paused.`);
    }
    return res;
  });
}

async function runCheckedQueueOnly() {
  const { pendingJobs, heldJobs } = queueEditorBuckets();
  const toHold = pendingJobs.filter(j => !EDITOR_CHECKED.has(j.id));
  const toInclude = heldJobs.filter(j => EDITOR_CHECKED.has(j.id));
  if (!toHold.length && !toInclude.length) return;
  await runQueueEditAction(async () => {
    if (toHold.length) {
      const holdRes = await apiPost('/api/jobs/hold', { targets: targets(captureTargets(toHold)), held: true });
      if (!holdRes) return null;
    }
    if (toInclude.length) {
      const incRes = await apiPost('/api/jobs/hold', { targets: targets(captureTargets(toInclude)), held: false });
      if (!incRes) return null;
    }
    recordFeedback('Updated queue to run checked pending jobs only.');
    return { ok: true };
  });
}
async function inspectJob(id) {
  if (WORKSPACE_LAYOUT === 'jobs') {
    await selectJob(id, 'details');
    setWorkspaceLayout('split');
  } else {
    await selectJob(id);
  }
}

async function selectJob(id, targetView = DETAIL_VIEW, sectionId = null) {
  if (id === SELECTED_JOB_ID && !isHistory() && !sectionId) {
    if (targetView !== DETAIL_VIEW) setDetailView(targetView);
    return;
  }
  if (typeof RunHistory !== 'undefined') RunHistory.clearSelection();
  if (typeof AnalysisWorkspace !== 'undefined') AnalysisWorkspace.reset();
  SELECTED_JOB_ID = id;
  SELECTED_ATTEMPT_ID = null;
  STATE.detail = null;
  STATE.history_error = null;
  LOG_CACHE = null;
  HIGHLIGHT_LINE_NO = null;
  LAST_LOG_KEY = '';
  LAST_LOG_RENDER_KEY = '';
  SCROLL_TO_EVIDENCE = false;
  el('btnClearHighlight').hidden = true;
  setDetailView(targetView, sectionId);
  renderDetail();
  updateControls();
  await fetchState();
}
async function selectAttempt(id) {
  if (typeof RunHistory !== 'undefined') RunHistory.clearSelection();
  if (typeof AnalysisWorkspace !== 'undefined') AnalysisWorkspace.reset();
  SELECTED_ATTEMPT_ID = id || null;
  STATE.detail = null;
  STATE.history_error = null;
  LOG_CACHE = null;
  HIGHLIGHT_LINE_NO = null;
  LAST_LOG_KEY = '';
  LAST_LOG_RENDER_KEY = '';
  SCROLL_TO_EVIDENCE = false;
  el('btnClearHighlight').hidden = true;
  renderDetail();
  updateControls();
  await fetchState();
}

function renderChartsHostStrip(j, d, archived) {
  const jobSel = el('chartJobSelect'), runSel = el('chartRunSelect');
  if (jobSel) {
    const jobOpts = archived && d ?
      `<option value="${escHtml(d.id)}">${escHtml(d.name)}</option>` :
      (STATE.jobs || []).map(job => `<option value="${escHtml(job.id)}">${escHtml(job.name)}</option>`).join('');
    if (jobSel.dataset.options !== jobOpts) {
      jobSel.innerHTML = jobOpts;
      jobSel.dataset.options = jobOpts;
    }
    jobSel.value = (archived ? d?.id : SELECTED_JOB_ID) || '';
    jobSel.disabled = !!archived || !(STATE.jobs || []).length;
  }
  if (runSel) {
    const history = j?.history || [];
    const runOpts = archived && d ?
      `<option value="">Run ${d.attempt_number}</option>` :
      `<option value="">Run ${j ? j.attempt_number : 1}${history.length ? ' · current' : ''}</option>` +
      history.map(a => `<option value="${escHtml(a.attempt_id)}">Run ${a.attempt_number} · ${escHtml(a.result || displayState(a.status))}</option>`).join('');
    if (runSel.dataset.options !== runOpts) {
      runSel.innerHTML = runOpts;
      runSel.dataset.options = runOpts;
    }
    runSel.value = archived ? '' : SELECTED_ATTEMPT_ID || '';
    runSel.disabled = !!archived || !d || !history.length;
  }
}

function renderDetail() {
  renderStatusBanner();
  const j = currentJob(),
    archived = typeof RunHistory !== 'undefined' ? RunHistory.selection : null,
    d = archived?.detail || STATE.detail,
    historyEvicted = isHistory() && !d && !!STATE.history_error;
  if (d && DETAIL_KEY !== d.id + ':' + d.attempt_id) {
    DETAIL_KEY = d.id + ':' + d.attempt_id;
    HIGHLIGHT_LINE_NO = null;
    SCROLL_TO_EVIDENCE = false;
    LAST_LOG_KEY = '';
    LAST_LOG_RENDER_KEY = '';
    el('btnClearHighlight').hidden = true;
  }
  if (WORKSPACE_LAYOUT === 'jobs') return;
  if (WORKSPACE_LAYOUT === 'charts') {
    renderChartsHostStrip(j, d, archived);
    syncDetailReaders(d);
    if (typeof RunHistory !== 'undefined') RunHistory.refresh(d);
    return;
  }
  el('detailTitle').textContent = d?.name || j?.name || 'Select a job';
  el('singleTargetLabel').textContent = d ? d.name + ' · Attempt ' + d.attempt_number : 'No job selected';
  if (d) {
    const origin = d.pid != null ? 'PID ' + d.pid : d.origin_label;
    const errChip = d.error_count ? ` <button type="button" class="error-badge-btn" onclick="openJobFinding('errors')" title="Inspect analysis or monitoring errors">${d.error_count} err</button>` : '';
    const warnChip = d.warning_count ? ` <button type="button" class="warning-count" onclick="openJobFinding('warnings')" title="Inspect warning sources">${d.warning_count} warn</button>` : '';
    setMarkup('detailMeta',
      `<span class="status-badge st-${escHtml(d.status)}">${escHtml(displayState(d.status, d.source_kind))}</span> ` +
      `${badge(d.result)}${errChip}${warnChip}${sourceBadges(d)}` +
      `<span class="help-text meta-pid">${origin ? ' · ' + escHtml(origin) : ''}${d.returncode != null ? ' · Exit ' + escHtml(d.returncode) : ''}</span>`);
  } else if (historyEvicted) {
    el('detailMeta').innerHTML =
      `<span class="status-badge result-INCOMPLETE">History Unavailable</span> ` +
      `<span class="help-text">${escHtml(STATE.history_error || 'Attempt history is no longer retained')}</span>`;
  } else {
    el('detailMeta').textContent = isHistory() ? 'Loading historical attempt…' : '';
  }
  el('detailTitle').parentElement.title = el('detailTitle').textContent + (d ? ' · Attempt ' + d.attempt_number + ' · ' + displayState(d.status, d.source_kind) + ' · ' + (d.result || '—') : '');
  const detailsBadges = [];
  if (d?.warning_count) detailsBadges.push(`${d.warning_count}w`);
  if (d?.error_count) detailsBadges.push(`⚠${d.error_count}e`);
  el('btnDetailsTab').textContent = 'Details';
  el('btnDetailsTab').title = detailsBadges.length ? detailsBadges.join(' · ') : 'Execution settings and diagnostics';
  const select = el('attemptSelect'),
    history = j?.history || [],
    missingSelectedHistory = SELECTED_ATTEMPT_ID && !history.some(a => a.attempt_id === SELECTED_ATTEMPT_ID),
    options = archived ? `<option value="">Run ${d.attempt_number}</option>` : `<option value="">Run ${j ? j.attempt_number : 1} · current</option>` + history.map(a =>
      `<option value="${escHtml(a.attempt_id)}">Run ${a.attempt_number} · ${escHtml(a.result||displayState(a.status))}</option>`
      ).join('') + (missingSelectedHistory ?
      '<option value="' + escHtml(SELECTED_ATTEMPT_ID) + '">History unavailable</option>' : '');
  if (select.dataset.options !== options) {
    select.innerHTML = options;
    select.dataset.options = options;
  }
  select.value = archived ? '' : SELECTED_ATTEMPT_ID || '';
  const singleRun = !archived && !history.length && !missingSelectedHistory;
  select.hidden = singleRun;
  select.disabled = !!archived || !d;
  const staticRun = el('attemptStatic');
  if (staticRun) {
    staticRun.hidden = !singleRun || !d;
    staticRun.textContent = d ? `Run ${d.attempt_number}` : '';
  }
  el('historyNotice').hidden = !isHistory();
  const follow = archived?.follow;
  const followLabel = {
    following: 'Following', paused: 'Follow paused', interrupted: 'Follow interrupted', finished: 'Saved',
  }[follow];
  el('archiveFollowControls').hidden = !followLabel;
  const followToggle = el('archiveFollowToggle');
  followToggle.hidden = follow !== 'following' && follow !== 'paused';
  followToggle.textContent = follow === 'paused' ? 'Resume follow' : 'Pause follow';
  const followStatus = el('archiveFollowStatus');
  if (followStatus.textContent !== (followLabel || '')) followStatus.textContent = followLabel || '';
  followStatus.title = followLabel || '';
  if (isHistory()) {
    const text = el('historyNoticeText');
    text.textContent = archived ?
      `Read-only · ${d.name} #${d.attempt_number} · Run ${archived.run_id.slice(0, 8)}` : historyEvicted ?
      'Historical attempt no longer retained' : 'Read-only historical attempt';
    text.title = archived ? `Read-only · ${d.name} #${d.attempt_number} · Run ${archived.run_id}` : text.textContent;
  }
  el('priorityEditor').hidden = !allowed(j, 'priority') || isHistory();
  el('stdinTarget').textContent = 'stdin ›';
  el('stdinTarget').title = j ? 'To: ' + j.name + ' (#' + (d?.attempt_number ?? (isHistory() ? 'history' : j.attempt_number)) + ')' : 'No target';
  renderStdinHistory();
  el('chkAutoScroll').disabled = !!archived;
  if (archived) el('btnClearHighlight').hidden = true;
  syncDetailReaders(d);
  renderLogs();
  if (typeof RunHistory !== 'undefined') RunHistory.refresh(d);
  if (typeof FlowWorkspace !== 'undefined') FlowWorkspace.refresh(d);
}

function jumpToLogEvidence(line) {
  if (!Number.isSafeInteger(line) || line <= 0) return;
  const d = RunHistory.selection?.detail || STATE.detail;
  HIGHLIGHT_LINE_NO = line;
  if (RunHistory.selection?.health?.enabled || STATE.archive?.enabled) {
    setDetailView('log');
    AnalysisWorkspace.jump(line);
    return;
  }
  if (!d?.log_records?.some(record => record.line_no === line)) {
    recordFeedback('Original Log record is no longer retained.', 'error');
    return;
  }
  SCROLL_TO_EVIDENCE = true;
  el('chkAutoScroll').checked = false;
  if (el('analysisFollow') && !isDiskArchive()) el('analysisFollow').checked = false;
  el('btnClearHighlight').hidden = false;
  setDetailView('log');
}

function compileRetainedMatcher() {
  const q = el('analysisQuery')?.value || '';
  if (!q) return { query: '', regex: null, error: null };
  const useRegex = !!el('analysisRegex')?.checked;
  const caseSensitive = !!el('analysisCase')?.checked;
  const wholeWord = !!el('analysisWord')?.checked;
  const core = useRegex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = wholeWord ? `\\b(?:${core})\\b` : core;
  try {
    return { query: q, regex: new RegExp(pattern, caseSensitive ? 'g' : 'gi'), error: null };
  } catch (err) {
    return { query: q, regex: null, error: err.message || 'Invalid regex' };
  }
}

function formatRetainedText(rawText, rowSeq, rowMatches, activeMatch) {
  const parsed = typeof AnalysisWorkspace !== 'undefined' && typeof AnalysisWorkspace.parseAnsi === 'function' ?
    AnalysisWorkspace.parseAnsi(rawText) :
    { plain: String(rawText ?? ''), runs: [{ start: 0, end: String(rawText ?? '').length, classes: '' }] };
  const { plain, runs } = parsed;
  const renderRuns = (sStart, sEnd) => {
    if (sStart >= sEnd) return '';
    if (!runs.length) return escHtml(plain.slice(sStart, sEnd));
    let out = '';
    for (const r of runs) {
      if (r.end <= sStart) continue;
      if (r.start >= sEnd) break;
      const s = Math.max(sStart, r.start), e = Math.min(sEnd, r.end);
      const text = escHtml(plain.slice(s, e));
      out += r.classes ? `<span class="${r.classes}">${text}</span>` : text;
    }
    return out;
  };
  if (!rowMatches || !rowMatches.length) return renderRuns(0, plain.length);
  let out = '', pos = 0;
  for (const m of rowMatches) {
    if (m.start > pos) out += renderRuns(pos, m.start);
    const activeCls = m.matchIndex === activeMatch ? ' active' : '';
    out += `<mark class="log-hit${activeCls}">${renderRuns(m.start, m.end)}</mark>`;
    pos = m.end;
  }
  if (pos < plain.length) out += renderRuns(pos, plain.length);
  return out;
}

function stepRetainedMatch(dir = 1) {
  if (isDiskArchive() || DETAIL_VIEW !== 'log') return;
  if (!RETAINED_MATCHES.length) {
    LAST_LOG_RENDER_KEY = '';
    renderLogs();
    if (!RETAINED_MATCHES.length) return;
  }
  const step = dir < 0 ? -1 : 1;
  RETAINED_MATCH_INDEX = RETAINED_MATCH_INDEX < 0 ?
    (step > 0 ? 0 : RETAINED_MATCHES.length - 1) :
    (RETAINED_MATCH_INDEX + step + RETAINED_MATCHES.length) % RETAINED_MATCHES.length;
  if (el('chkAutoScroll')) el('chkAutoScroll').checked = false;
  if (el('analysisFollow')) el('analysisFollow').checked = false;
  LAST_LOG_RENDER_KEY = '';
  renderLogs(true);
}

let _retainedFindTimer = null;
function setupRetainedFindControls() {
  if (setupRetainedFindControls._bound) return;
  setupRetainedFindControls._bound = true;
  el('btnToggleFind')?.addEventListener?.('click', () => { if (!isDiskArchive()) toggleLogFind(); });
  el('btnCloseFind')?.addEventListener?.('click', () => { if (!isDiskArchive()) closeLogFind(); });
  el('analysisFindNext')?.addEventListener?.('click', () => { if (!isDiskArchive()) stepRetainedMatch(1); });
  el('analysisFindPrev')?.addEventListener?.('click', () => { if (!isDiskArchive()) stepRetainedMatch(-1); });
  el('analysisQuery')?.addEventListener?.('input', () => {
    if (isDiskArchive()) return;
    clearTimeout(_retainedFindTimer);
    RETAINED_MATCH_INDEX = 0;
    const val = el('analysisQuery')?.value || '';
    if (!val) { LAST_LOG_RENDER_KEY = ''; renderLogs(); return; }
    _retainedFindTimer = setTimeout(() => { LAST_LOG_RENDER_KEY = ''; renderLogs(true); }, 100);
  });
  el('analysisQuery')?.addEventListener?.('keydown', e => {
    if (isDiskArchive()) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      stepRetainedMatch(e.shiftKey ? -1 : 1);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeLogFind();
    }
  });
  for (const id of ['analysisCase', 'analysisWord', 'analysisRegex', 'analysisFilterMode']) {
    el(id)?.addEventListener?.('change', () => {
      if (isDiskArchive()) return;
      RETAINED_MATCH_INDEX = 0;
      LAST_LOG_RENDER_KEY = '';
      renderLogs(true);
    });
  }
}

function renderLogs(scrollToMatch = false) {
  if (typeof AnalysisWorkspace !== 'undefined') {
    const saved = RunHistory.selection, d = saved?.detail || STATE.detail;
    const disk = !!(saved?.health?.enabled || STATE.archive?.enabled);
    el('analysisPanel').hidden = !disk;
    el('retainedLogView').hidden = disk;
    if (disk) {
      if (DETAIL_VIEW === 'log' && WORKSPACE_LAYOUT !== 'jobs' && WORKSPACE_LAYOUT !== 'charts') AnalysisWorkspace.refresh(d);
      return;
    }
  }
  const d = STATE.detail,
    box = el('logBox');
  if (!d) {
    if (isHistory() && STATE.history_error) {
      box.innerHTML = '<div class="log-empty">Historical attempt is no longer retained (evicted by the history retention limit). Click "Back to Current" above to view the active attempt.</div>';
      el('logCountInfo').textContent = 'History not retained';
    } else if (isHistory()) {
      box.innerHTML = '<div class="log-empty">Loading historical attempt…</div>';
      el('logCountInfo').textContent = 'Loading…';
    } else {
      box.innerHTML = '<div class="log-empty">Select a job to view its live or historical output log.</div>';
      el('logCountInfo').textContent = '0 lines';
    }
    RETAINED_MATCHES = [];
    RETAINED_MATCH_INDEX = -1;
    if (el('analysisMatchCount')) el('analysisMatchCount').textContent = '';
    LAST_LOG_RENDER_KEY = '';
    return;
  }
  const records = d.log_records || [],
    matcher = compileRetainedMatcher(),
    filterMode = !!el('analysisFilterMode')?.checked,
    q = matcher.query || '',
    archivedMode = isDiskArchive(),
    startRaw = archivedMode ? (el('analysisStart')?.value || '').trim() : '',
    endRaw = archivedMode ? (el('analysisEnd')?.value || '').trim() : '',
    startSec = startRaw !== '' && Number.isFinite(Number(startRaw)) ? Number(startRaw) : null,
    endSec = endRaw !== '' && Number.isFinite(Number(endRaw)) ? Number(endRaw) : null,
    cap = d.attempt_max_log_lines ?? d.max_log_lines ?? 10000;
  let info = records.length + ' retained / ' + d.total_lines + ' records (limit ' + cap + ')';
  if (d.log_count < d.total_lines) info += ' · earlier discarded';
  if (HIGHLIGHT_LINE_NO != null) {
    if (!records.some(r => r.line_no === HIGHLIGHT_LINE_NO)) {
      info += ' · #' + HIGHLIGHT_LINE_NO + ' not retained (see Details)';
    } else {
      info += ' · highlighting #' + HIGHLIGHT_LINE_NO;
    }
  }
  el('logCountInfo').textContent = info;
  el('btnClearHighlight').hidden = HIGHLIGHT_LINE_NO == null;
  const firstNo = records.length ? records[0].line_no : 0;
  const key = d.attempt_id + ':' + records.length + ':' + d.total_lines + ':' + firstNo + ':' + cap;
  const matchFlags = (el('analysisCase')?.checked ? 'c' : '') + (el('analysisWord')?.checked ? 'w' : '') +
    (el('analysisRegex')?.checked ? 'r' : '') + (filterMode ? 'f' : '') + (DISPLAY_PREFS.ansiColor !== false ? 'a' : '') +
    '|' + startRaw + '|' + endRaw;
  const renderKey = key + ':' + q + ':' + matchFlags + ':' + RETAINED_MATCH_INDEX + ':' + HIGHLIGHT_LINE_NO + ':' + d.status;
  if (DETAIL_VIEW !== 'log' || (renderKey === LAST_LOG_RENDER_KEY && !SCROLL_TO_EVIDENCE && !scrollToMatch)) return;
  const scroll = box.scrollTop;
  let removedHeight = 0;

  const elapsedRecords = (startSec != null || endSec != null)
    ? records.filter(r => {
        const rel = r.elapsed ?? r.rel_s;
        if (typeof rel !== 'number') return true;
        if (startSec != null && rel < startSec) return false;
        if (endSec != null && rel > endSec) return false;
        return true;
      })
    : records;

  RETAINED_MATCHES = [];
  const rowMatchMap = new Map();
  if (matcher.regex) {
    for (const r of elapsedRecords) {
      const plain = typeof AnalysisWorkspace !== 'undefined' && typeof AnalysisWorkspace.stripAnsi === 'function' ?
        AnalysisWorkspace.stripAnsi(r.text) : String(r.text ?? '');
      matcher.regex.lastIndex = 0;
      const list = [];
      let m;
      while ((m = matcher.regex.exec(plain)) !== null) {
        if (m[0].length === 0) { matcher.regex.lastIndex++; continue; }
        const entry = { line_no: r.line_no, matchIndex: RETAINED_MATCHES.length, start: m.index, end: m.index + m[0].length };
        RETAINED_MATCHES.push(entry);
        list.push(entry);
        if (RETAINED_MATCHES.length >= 5000) break;
      }
      if (list.length) rowMatchMap.set(r.line_no, list);
      if (RETAINED_MATCHES.length >= 5000) break;
    }
  }
  if (!RETAINED_MATCHES.length) RETAINED_MATCH_INDEX = -1;
  else if (RETAINED_MATCH_INDEX < 0 || RETAINED_MATCH_INDEX >= RETAINED_MATCHES.length) RETAINED_MATCH_INDEX = 0;

  const matchCountEl = el('analysisMatchCount');
  if (matchCountEl) {
    matchCountEl.classList?.toggle('error-text', !!matcher.error);
    matchCountEl.textContent = !matcher.query ? '' :
      matcher.error ? `Invalid regex: ${matcher.error}` :
      !RETAINED_MATCHES.length ? 'No results' :
      `${RETAINED_MATCH_INDEX + 1} of ${RETAINED_MATCHES.length}`;
  }
  if (el('analysisFilterCount')) {
    const activeElapsed = (startSec != null || endSec != null) ? 1 : 0;
    el('analysisFilterCount').textContent = activeElapsed ? `(${activeElapsed})` : '';
  }

  const matching = (filterMode && matcher.query)
    ? elapsedRecords.filter(r => rowMatchMap.has(r.line_no))
    : elapsedRecords;
  const domKey = d.id + ':' + d.attempt_id + ':' + q + ':' + matchFlags + ':' + RETAINED_MATCH_INDEX + ':' + HIGHLIGHT_LINE_NO;
  let anchor = null, anchorTop = 0;
  if (!el('chkAutoScroll').checked && LAST_LOG_DOM_KEY === domKey &&
      box.firstElementChild?.classList.contains('log-line')) {
    const top = box.getBoundingClientRect().top;
    let lo = 0, hi = box.children.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (box.children[mid].getBoundingClientRect().bottom <= top) lo = mid + 1;
      else hi = mid;
    }
    anchor = box.children[lo] || null;
    if (anchor) anchorTop = anchor.getBoundingClientRect().top;
  }
  if (matching.length && box.style?.setProperty) {
    let maxNo = 0;
    for (const r of matching) if (Number(r.line_no) > maxNo) maxNo = Number(r.line_no);
    const digits = Math.max(3, String(maxNo || 1).length);
    box.style.setProperty('--log-gutter-w', Math.max(30, digits * 7 + 8) + 'px');
  }
  const rowHtml = rows => rows.map(r =>
    `<div class="log-line ${r.source==='wave'?'wave':''} ${r.line_no===HIGHLIGHT_LINE_NO?'highlight':''}" data-line-no="${r.line_no}" id="log-line-${r.line_no}"><span class="log-lineno">${r.source==='wave'?'Wave':r.line_no??'—'}</span><span>${formatRetainedText(r.text, r.line_no, rowMatchMap.get(r.line_no), RETAINED_MATCH_INDEX)}</span></div>`
  ).join('');
  if (!matching.length) {
    box.innerHTML = '<div class="log-empty">' + (records.length ? 'No log lines match "' + escHtml(q) + '".' :
      (d.status === 'PENDING' ? 'Job is PENDING (' + escHtml(d.pending_reason || 'waiting to start') + '). No output yet.' : 'No output lines recorded for this attempt.')) + '</div>';
  } else if (LAST_LOG_DOM_KEY === domKey && box.firstElementChild?.classList.contains('log-line')) {
    const first = matching[0].line_no;
    let keep = box.firstElementChild;
    const oldTop = keep.getBoundingClientRect().top;
    while (keep && Number(keep.dataset.lineNo) < first) keep = keep.nextElementSibling;
    removedHeight = keep ? keep.getBoundingClientRect().top - oldTop : box.scrollHeight;
    if (box.firstElementChild === keep) removedHeight = 0;
    while (box.firstElementChild && box.firstElementChild !== keep) box.firstElementChild.remove();
    const last = Number(box.lastElementChild?.dataset.lineNo || 0);
    box.insertAdjacentHTML('beforeend', rowHtml(matching.filter(r => r.line_no > last)));
  } else {
    box.innerHTML = rowHtml(matching);
  }
  markProgrammaticScroll();
  const h = HIGHLIGHT_LINE_NO == null ? null : el('log-line-' + HIGHLIGHT_LINE_NO);
  const hitMark = scrollToMatch ? box.querySelector?.('.log-hit.active') : null;
  if (hitMark?.scrollIntoView) hitMark.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  else if (h && SCROLL_TO_EVIDENCE) box.scrollTop += h.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 2;
  else if (el('chkAutoScroll').checked && key !== LAST_LOG_KEY) box.scrollTop = box.scrollHeight;
  else if (anchor?.isConnected) box.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
  else box.scrollTop = Math.max(0, scroll - removedHeight);
  LAST_LOG_KEY = key;
  LAST_LOG_RENDER_KEY = renderKey;
  LAST_LOG_DOM_KEY = domKey;
  SCROLL_TO_EVIDENCE = false;
}

function sourceBadges(attempt) {
  const reason = attempt.completion_reason === 'user_stopped' ?
    JobsColumns.completionLabel(attempt.completion_reason) : attempt.completion_reason;
  const kindBadge = attempt.source_kind && attempt.source_kind !== 'process' ? attempt.source_kind : null;
  const chips = [kindBadge, reason,
    attempt.verified === false ? 'Unverified' : null];
  return chips.filter(Boolean).map(text =>
    ` <span class="status-badge">${escHtml(text)}</span>`).join('');
}

const ACTION_CAPABILITIES = {
  priority: 'has_process', stop: 'can_stop', force_stop: 'can_kill',
  send: 'can_send', interrupt: 'can_interrupt', rerun: 'can_rerun',
};

function supportsAction(job, action) {
  if (!job) return false;
  const key = ACTION_CAPABILITIES[action];
  if (job.capabilities && key) return job.capabilities[key] === true;
  return (job.source_kind || 'process') === 'process' && (action !== 'rerun' || job.can_rerun === true);
}

function allowed(j, a) {
  if (!supportsAction(j, a)) return false;
  if (a === 'priority') return j.status === 'PENDING';
  if (a === 'rerun') return FINISHED.has(j.status);
  if (a === 'send' || a === 'interrupt') return ['RUNNING', 'WAITING_INPUT'].includes(j.status);
  if (a === 'force_stop') return ACTIVE.has(j.status);
  return j.status === 'PENDING' || ACTIVE.has(j.status);
}

function captureTargets(jobs) {
  return jobs.map(j => ({
    job_id: j.id || j.job_id,
    attempt_id: j.attempt_id,
    submission_id: j.submission_id,
    name: j.name,
    attempt_number: j.attempt_number
  }));
}

function targets(jobs) {
  return jobs.map(j => ({
    job_id: j.id || j.job_id,
    attempt_id: j.attempt_id,
    submission_id: j.submission_id
  }));
}

function pruneCheckedJobs() {
  if (!CHECKED_JOBS.size) return;
  const ids = new Set((STATE.jobs || []).map(j => j.id));
  for (const id of CHECKED_JOBS) if (!ids.has(id)) CHECKED_JOBS.delete(id);
}

function checkedJobs() {
  return STATE.jobs.filter(j => CHECKED_JOBS.has(j.id));
}

function canStopSource(job) {
  return !!(job?.source_kind && job.source_kind !== 'process' && !isHistory() &&
    STATE.sources?.stoppable_jobs?.includes(job.id));
}

function updateControls() {
  renderStatusBanner();
  const j = currentJob(),
    detail = (typeof RunHistory !== 'undefined' && RunHistory.selection?.detail) || STATE.detail || j,
    enabled = canControl();
  const nonProcess = detail && !supportsAction(detail, 'priority');
  el('detailHeader').classList.toggle('source-detail-header', !!nonProcess && !isHistory());
  el('attemptControls').hidden = !!nonProcess;
  el('attemptMore').hidden = !!detail && !['force_stop', 'interrupt'].some(a => supportsAction(detail, a));
  el('attemptConsole').hidden = !detail || !supportsAction(detail, 'send') || isHistory() || !j || !ACTIVE.has(j.status);
  const isFinished = !!j && FINISHED.has(j.status);
  const isWaitingInput = j?.status === 'WAITING_INPUT';
  document.querySelectorAll('[data-single-action]').forEach(b => {
    const a = b.dataset.singleAction;
    if (a === 'stop') {
      b.hidden = (!!detail && !supportsAction(detail, 'stop')) || isFinished;
      b.textContent = j?.status === 'PENDING' ? 'Cancel Pending' :
        j?.status === 'STOPPING' ? 'Stopping…' : 'Stop';
    } else if (a === 'rerun') {
      b.hidden = !!nonProcess || !isFinished;
    } else if (b.id === 'btnQuickForceStop') {
      b.hidden = j?.status !== 'STOPPING' || isHistory() || !allowed(j, 'force_stop');
    } else if (b.id === 'btnQuickInterrupt') {
      b.hidden = !isWaitingInput || isHistory() || !allowed(j, 'interrupt');
    } else {
      b.hidden = !!detail && !supportsAction(detail, a);
    }
    b.disabled = !enabled || isHistory() || !currentDetailMatches() || !allowed(j, a) ||
      (a === 'stop' && j?.status === 'STOPPING');
    const target = isHistory() ? 'History is read-only; return to Current.' : (!j ? 'Select a job.' :
      (a === 'rerun' && !allowed(j, 'rerun') ? 'Rerun is available after this attempt finishes and cleanup completes.' : 'Target: ' + j.name + ' (Attempt #' + j.attempt_number + ')'));
    if (a !== 'rerun') {
      b.title = [a === 'interrupt' ? INTERRUPT_HINT : a === 'stop' ? STOP_HINT : '', target].filter(Boolean).join('\n');
    }
  });
  const priorityButtons = document.querySelectorAll('[data-job-priority]');
  if (priorityButtons.length) {
    const byId = new Map(STATE.jobs.map(job => [job.id, job]));
    priorityButtons.forEach(b => {
      b.disabled = !enabled || !allowed(byId.get(b.dataset.jobPriority), 'priority');
    });
  }
  if (CHECKED_JOBS.size) pruneCheckedJobs();
  const checked = checkedJobs();
  el('batchMenu').hidden = !checked.length;
  if (!checked.length) el('batchMore').open = false;
  el('checkedCount').textContent = checked.length + ' checked';
  document.querySelectorAll('[data-batch-action]').forEach(b => {
    const a = b.dataset.batchAction,
      n = checked.filter(j => allowed(j, a)).length;
    b.hidden = !checked.length || !checked.some(j =>
      supportsAction(j, a === 'rerun' ? 'priority' : a));
    b.textContent = (a === 'stop' ? 'Stop/Cancel' : a === 'priority' ? 'Set Priority…' : LABELS[a]) + ' ' + n + '/' + checked.length;
    b.disabled = !enabled || !n;
    const skipped = checked.filter(j => !allowed(j, a)).map(j =>
      j.name + ': skipped (' + (a === 'rerun' && FINISHED.has(j.status) && !j.can_rerun ?
        'cleanup in progress' : displayState(j.status)) + ')');
    const scope = a === 'stop' ?
      `Cancels ${checked.filter(j => j.status === 'PENDING').length} pending; stops ${checked.filter(j => ACTIVE.has(j.status)).length} active checked job(s).` :
      `Applies ${LABELS[a]} to ${n}/${checked.length} checked job(s).`;
    const hint = a === 'interrupt' ? INTERRUPT_HINT : a === 'stop' ? STOP_HINT : '';
    b.title = checked.length ? [hint, scope, ...skipped].filter(Boolean).join('\n') :
      'Check one or more jobs in the table to use batch controls.';
  });
  const sendable = checked.filter(j => allowed(j, 'send'));
  el('btnBroadcast').textContent = 'Send (' + sendable.length + '/' + checked.length + ')…';
  el('btnBroadcast').hidden = checked.length > 0 && !checked.some(j => supportsAction(j, 'send'));
  el('btnBroadcast').disabled = !enabled || !sendable.length;
  el('btnBroadcastSend').disabled = !enabled || !sendable.length;
  el('broadcastTargets').textContent = 'Send to ' + sendable.length + '/' + checked.length + ' checked: ' +
    sendable.map(j => j.name).join(', ') + (checked.length > sendable.length ?
      ' · other checked jobs skipped' : '');
  el('broadcastTargets').title = el('broadcastTargets').textContent;
  el('btnBroadcastSend').textContent = 'Send to ' + sendable.length + ' Job(s)';
  const canSend = enabled && !isHistory() && currentDetailMatches() && allowed(j, 'send');
  ['btnSend', 'stdinInput'].forEach(id => {
    el(id).hidden = !!detail && !supportsAction(detail, 'send');
    el(id).disabled = !canSend;
  });
  el('stdinInput').placeholder = isHistory() ? 'Historical attempt is read-only' :
    (canSend ? 'Send to stdin… (Enter · ↑↓ history)' : 'Process not running — stdin unavailable');
  const sigMenu = el('stdinSignalMenu');
  if (sigMenu) {
    if (sigMenu.dataset) sigMenu.dataset.disabled = String(!canSend);
    if (!canSend) sigMenu.open = false;
  }
  if (el('btnSendEmptyEnter')) el('btnSendEmptyEnter').disabled = !canSend;
  if (el('btnSendCtrlD')) {
    const canCtrlD = STATE.platform === 'posix' && !!detail?.pty;
    el('btnSendCtrlD').hidden = !canCtrlD;
    el('btnSendCtrlD').disabled = !canSend || !canCtrlD;
  }
  const sigSection = el('stdinPosixSection'), sigHost = el('stdinPosixSignals');
  const supportedSigs = Array.isArray(STATE.supported_signals) ? STATE.supported_signals : [];
  if (sigSection) sigSection.hidden = !supportedSigs.length;
  if (sigHost) {
    const sigKey = supportedSigs.join(',') + ':' + (canSend ? '1' : '0');
    if (sigHost.dataset?.signals !== sigKey) {
      const sigDesc = {
        SIGINT: 'Interrupt (Ctrl+C)', SIGTERM: 'Terminate', SIGTSTP: 'Suspend (Ctrl+Z)',
        SIGCONT: 'Resume', SIGUSR1: 'User signal 1', SIGUSR2: 'User signal 2',
        SIGHUP: 'Hangup', SIGQUIT: 'Quit'
      };
      sigHost.innerHTML = supportedSigs.map(name => {
        const desc = sigDesc[name] || '';
        return `<button type="button" class="stdin-sig-item" data-signal="${escHtml(name)}" onclick="sendJobSignal('${escHtml(name)}')"${canSend ? '' : ' disabled'}><span>${escHtml(name)}</span>${desc ? `<span class="stdin-sig-desc">${escHtml(desc)}</span>` : ''}</button>`;
      }).join('');
      if (sigHost.dataset) sigHost.dataset.signals = sigKey;
    }
  }
  el('btnStart').disabled = !enabled || !STATE.ready;
  const stopWatchingButton = el('btnStopWatching'), stopSourceButton = el('btnStopSource');
  if (stopWatchingButton) {
    stopWatchingButton.hidden = !!STATE.opened_run_id || !STATE.sources?.watching;
    stopWatchingButton.disabled = stopWatchingButton.hidden || !CONNECTED || BUSY;
    const ids = new Set(STATE.sources?.stoppable_jobs || []);
    const kinds = new Set(STATE.jobs.filter(job => ids.has(job.id)).map(job => job.source_kind));
    if (STATE.sources?.active_watchers) kinds.add('tail');
    const label = !kinds.has('tail') && kinds.size ? 'Stop Reading' :
      kinds.size > 1 ? 'Stop Sources' : 'Stop Watching';
    stopWatchingButton.textContent = label;
    stopWatchingButton.setAttribute('aria-label', label + ' all sources');
  }
  if (stopSourceButton) {
    stopSourceButton.hidden = !!STATE.opened_run_id || !canStopSource(j);
    stopSourceButton.disabled = stopSourceButton.hidden || !CONNECTED || BUSY;
    const label = j?.source_kind === 'tail' ? 'Stop Watching' : 'Stop Reading';
    stopSourceButton.textContent = label;
    stopSourceButton.setAttribute?.('aria-label', j ? label + ' ' + j.name : label);
  }
  const sourceOnly = (STATE.jobs.length > 0 || STATE.sources?.configured_watchers > 0) &&
    STATE.jobs.every(job => job.source_kind && job.source_kind !== 'process');
  const showActiveQueueControls = !!STATE.started && !sourceOnly && !STATE.opened_run_id;
  el('btnPauseResume').hidden = !showActiveQueueControls;
  el('btnPauseResume').disabled = !enabled || !STATE.started;
  const canStopAll = enabled && STATE.jobs.some(j => allowed(j, 'stop') && !(j.status === 'PENDING' && j.held));
  const canForceAll = enabled && STATE.jobs.some(j => allowed(j, 'force_stop'));
  if (el('stopAllGroup')) el('stopAllGroup').hidden = !showActiveQueueControls;
  el('btnStopAll').hidden = !showActiveQueueControls;
  el('btnForceAll').hidden = !showActiveQueueControls;
  el('btnStopAll').disabled = !canStopAll;
  if (el('btnMenuStopAll')) el('btnMenuStopAll').disabled = !canStopAll;
  el('btnForceAll').disabled = !canForceAll;
  if (el('btnForceAllConfirm')) el('btnForceAllConfirm').disabled = !canForceAll;
  if (!canForceAll && el('forceAllConfirm')) el('forceAllConfirm').hidden = true;
  if (el('btnReloadWorkspace')) {
    const hasPy = !!STATE.source_path && String(STATE.source_path).toLowerCase().endsWith('.py');
    const blockedByBatch = !STATE.opened_run_id && hasPy && hasUnfinishedBatch();
    el('btnReloadWorkspace').disabled = !CONNECTED || BUSY || !!STATE.opened_run_id || !hasPy || blockedByBatch;
    el('btnReloadWorkspace').title = STATE.opened_run_id
      ? 'Saved archives cannot be reloaded; use Open to load a work file.'
      : !hasPy
        ? 'Reload requires an active Python work file workspace.'
        : blockedByBatch
          ? 'Finish or cancel active/queued/held jobs before reloading this started batch.'
          : 'Reload work file and Python helper modules from disk';
  }
  if (!el('workOverlay').hidden && OPEN_MODE === 'archive') updateArchiveBusyUi();
  if (el('btnApplyResourcesPanel')) el('btnApplyResourcesPanel').disabled = !enabled;
  const hideEditQueue = !!STATE.opened_run_id || sourceOnly || WORKSPACE_LAYOUT !== 'jobs';
  if (el('btnEditQueue')) {
    el('btnEditQueue').hidden = hideEditQueue;
    el('btnEditQueue').disabled = !STATE.jobs.length && !(STATE.removed_jobs || []).length;
  }
  if (el('editQueueDivider')) el('editQueueDivider').hidden = hideEditQueue || EDIT_QUEUE_MODE;
  el('btnLoadFile').disabled = !CONNECTED || BUSY;
  const loadLogsButton = el('btnLoadLogs');
  if (loadLogsButton) loadLogsButton.disabled = !CONNECTED || BUSY;
  const openArchiveBtn = el('btnOpenArchive');
  if (openArchiveBtn) openArchiveBtn.disabled = !CONNECTED || BUSY || hasUnfinishedBatch();
  el('btnSettings').disabled = !CONNECTED;
  if (el('btnApplyRetention')) el('btnApplyRetention').disabled = !enabled;
  const selOpt = el('logRetentionScope')?.querySelector?.('option[value="selected"]');
  if (selOpt) {
    selOpt.disabled = !j || isHistory() || !currentDetailMatches();
    if (selOpt.disabled && el('logRetentionScope').value === 'selected') el('logRetentionScope').value = 'all';
  }
  el('btnApplyPriority').disabled = !enabled || isHistory() || !currentDetailMatches() || !allowed(j, 'priority');
  updatePriorityAvailability();
}

function receiptRows(items, describe) {
  return items.map(item =>
    `<div class="action-row"><strong>${escHtml(item.name || item.job_id)}</strong><span class="status-badge ${item.ok?'action-accepted':'result-INCOMPLETE'}">${escHtml(describe(item))}</span></div>`
  ).join('');
}

function report(res) {
  const items = res.results || [];
  const outcomes = {accepted: 'Succeeded', rejected: 'Skipped', failed: 'Failed'};
  const counts = {accepted: 0, rejected: 0, failed: 0};
  items.forEach(x => counts[x.status]++);
  const msg = (LABELS[res.action] || res.action) + ': ' +
    `${counts.accepted} succeeded · ${counts.rejected} skipped · ${counts.failed} failed.` +
    (['send', 'interrupt'].includes(res.action) ? ' Transport only; tool execution is not verified.' : '');
  const notes = counts.failed || counts.rejected ?
    receiptRows(items, item => outcomes[item.status] + ': ' + item.reason) : '';
  recordFeedback(msg, counts.failed ? 'error' : counts.rejected ? 'warn' : 'info', notes);
}
async function jobAction(action, jobs, extra = {}, options = {}) {
  if (!canControl() || !jobs.length) return null;
  const reqTargets = captureTargets(jobs);
  BUSY = true;
  updateControls();
  try {
    const r = await apiPost('/api/job/action', Object.assign({
      action,
      targets: targets(reqTargets)
    }, extra));
    if (r) {
      r.requested_targets = reqTargets;
      const allOk = Array.isArray(r.results) ? r.results.every(x => x.ok) : true;
      if (!(options.silentSuccess && allOk)) report(r);
    }
    BUSY = false;
    await fetchState();
    return r;
  } finally {
    BUSY = false;
    updateControls();
  }
}
async function singleJobAction(a, options = {}) {
  if (isHistory() || !currentDetailMatches()) return;
  const j = currentJob();
  if (allowed(j, a)) {
    const target = captureTargets([j])[0];
    const result = await jobAction(a, [target], {}, options);
    if (a === 'rerun' && result?.affected === 1) {
      const item = (result.results || []).find(x => x.job_id === target.job_id);
      const nextNo = item?.new_attempt_number ?? STATE.jobs.find(x => x.id === target.job_id)?.attempt_number ?? 'next';
      recordFeedback('Rerun accepted: ' + target.name + ' (from #' + target.attempt_number + ') → Attempt #' + nextNo);
    } else if (a === 'interrupt' && result?.affected === 1) {
      recordStdinHistory(target.attempt_id, { kind: 'signal', label: 'Ctrl+C (interrupt)', ok: true });
      renderDetail();
    } else if (a === 'interrupt' && result) {
      const item = (result.results || []).find(x => x.job_id === target.job_id);
      recordStdinHistory(target.attempt_id, { kind: 'signal', label: 'Ctrl+C (interrupt)', ok: false, reason: item?.reason || 'rejected' });
      renderDetail();
    }
  }
}
async function batchJobAction(a) {
  const reqTargets = captureTargets(checkedJobs());
  await jobAction(a, reqTargets);
}
async function allAction(a) {
  const reqTargets = captureTargets(
    STATE.jobs.filter(j => allowed(j, a) && !(a === 'stop' && j.status === 'PENDING' && j.held))
  );
  const extra = (a === 'stop' || a === 'force_stop') ? { pause_dispatch: true } : {};
  await jobAction(a, reqTargets, extra);
}
async function stopWatching(selected = false) {
  const job = selected ? currentJob() : null;
  if (!CONNECTED || BUSY || STATE.opened_run_id ||
      !(selected ? canStopSource(job) : STATE.sources?.watching)) return;
  BUSY = STOPPING_SOURCES = true;
  updateControls();
  try {
    const r = await apiPost('/api/sources/stop', {job_id: job?.id ?? null}, {allowArchiveProblems:true});
    if (r) {
      recordFeedback(r.message);
      await fetchState();
    }
  } finally {
    BUSY = STOPPING_SOURCES = false;
    updateControls();
  }
}
async function managerAction(a) {
  if (BUSY || !workspaceIsWritable()) return;
  BUSY = true;
  updateControls();
  try {
    const r = await apiPost('/api/manager/action', {
      action: a
    });
    if (r) {
      recordFeedback(r.message);
    }
    BUSY = false;
    await fetchState();
  } finally {
    BUSY = false;
    updateControls();
  }
}

function recordStdinHistory(attemptId, { kind = 'stdin', label = '', text = null, ok = true, reason = '' } = {}) {
  if (!attemptId) return;
  const list = (STDIN_HISTORY_BY_ATTEMPT[attemptId] ??= []);
  list.push({ time: nowStamp(), kind, label, text, ok, reason });
  if (list.length > 50) list.shift();
  const display = kind === 'stdin' ? `"${text === '' ? '↵' : text}"` : label;
  STDIN_STATUS_BY_ATTEMPT[attemptId] = ok ? `✓ ${kind === 'stdin' ? 'Sent ' + display : label} · ${nowStamp()}` : `⚠ ${display} rejected · ${nowStamp()}`;
  const histMenu = el('stdinHistoryMenu');
  if (histMenu?.classList?.toggle) {
    histMenu.classList.toggle('flash', true);
    if (typeof clearTimeout === 'function') clearTimeout(window._stdinHistFlashTimer);
    if (typeof setTimeout === 'function') {
      window._stdinHistFlashTimer = setTimeout(() => el('stdinHistoryMenu')?.classList?.toggle?.('flash', false), 650);
    }
  }
  renderStdinHistory();
}

function renderStdinHistory() {
  const menu = el('stdinHistoryMenu'), btn = el('btnStdinHistory'),
    list = el('stdinHistoryList'), title = el('stdinHistoryTitle'), clearBtn = el('btnClearStdinHistory');
  if (!menu) return;
  const archived = typeof RunHistory !== 'undefined' ? RunHistory.selection : null;
  const d = archived?.detail || STATE.detail;
  const attemptId = d?.attempt_id;
  const entries = attemptId ? (STDIN_HISTORY_BY_ATTEMPT[attemptId] || []) : [];
  const empty = !entries.length;
  menu.hidden = empty;
  if (empty) menu.open = false;
  if (btn) {
    btn.textContent = `Stdin history (${entries.length})`;
    btn.title = empty
      ? 'No stdin or signals sent for this job attempt'
      : `Show ${entries.length} sent stdin/signal record(s) for ${d?.name || 'job'} #${d?.attempt_number ?? ''}`;
  }
  if (title) title.textContent = d ? `${d.name} · Attempt #${d.attempt_number}` : 'Stdin history';
  if (clearBtn) clearBtn.disabled = empty;
  if (!list) return;
  const writableStdin = canControl() && !isHistory() && currentDetailMatches() && !el('btnSend')?.disabled;
  const markup = entries.slice().reverse().map((item, revIdx) => {
    const origIdx = entries.length - 1 - revIdx;
    const isStdin = item.kind === 'stdin' || item.kind === 'broadcast';
    const canFill = isStdin && typeof item.text === 'string' && writableStdin;
    const textLabel = item.text === '' ? '↵ (Enter)' : item.label;
    const cls = 'status-stdin-item' + (item.ok ? '' : ' warn');
    const stateMark = item.ok ? '✓' : '⚠';
    const body = `<span class="status-stdin-time">${escHtml(item.time)}</span>` +
      `<span class="status-stdin-kind">${escHtml(item.kind)}</span>` +
      `<span class="status-stdin-text" title="${escHtml((item.ok ? '' : item.reason + ' · ') + textLabel)}">${stateMark} ${escHtml(textLabel)}${!item.ok && item.reason ? ' · ' + escHtml(item.reason) : ''}</span>`;
    return canFill
      ? `<button type="button" class="${cls}" data-stdin-hist="${origIdx}" title="Click to fill into stdin box">${body}</button>`
      : `<div class="${cls}">${body}</div>`;
  }).join('');
  if (list.dataset?.markup !== markup) {
    list.innerHTML = markup;
    if (list.dataset) list.dataset.markup = markup;
  }
  if (!list._stdinHistBound && typeof list.addEventListener === 'function') {
    list._stdinHistBound = true;
    list.addEventListener('click', event => {
      const btnEl = event.target?.closest?.('[data-stdin-hist]');
      if (!btnEl) return;
      const dCur = (typeof RunHistory !== 'undefined' ? RunHistory.selection : null)?.detail || STATE.detail;
      const curList = dCur?.attempt_id ? STDIN_HISTORY_BY_ATTEMPT[dCur.attempt_id] : null;
      const entry = curList?.[Number(btnEl.dataset.stdinHist)];
      const inp = el('stdinInput');
      if (entry && typeof entry.text === 'string' && inp && !inp.disabled) {
        inp.value = entry.text;
        if (el('stdinHistoryMenu')) el('stdinHistoryMenu').open = false;
        inp.focus?.();
      }
    });
  }
}

function clearSelectedStdinHistory() {
  const menu = el('stdinHistoryMenu');
  const hadFocus = typeof document !== 'undefined' &&
    (menu?.contains?.(document.activeElement) || document.activeElement?.closest?.('#stdinHistoryMenu'));
  const d = (typeof RunHistory !== 'undefined' ? RunHistory.selection : null)?.detail || STATE.detail;
  if (d?.attempt_id) {
    delete STDIN_HISTORY_BY_ATTEMPT[d.attempt_id];
    delete STDIN_STATUS_BY_ATTEMPT[d.attempt_id];
  }
  if (menu) menu.open = false;
  renderStdinHistory();
  if (hadFocus) restoreFocus(el('stdinInput'), el('btnOpenWorkspace'));
}
function handleStdinKey(event) {
  if (!event || event.isComposing || event.keyCode === 229) return;
  const input = el('stdinInput');
  if (!input) return;
  if (event.key === 'Enter') {
    event.preventDefault?.();
    sendStdinCommand();
    return;
  }
  if (event.key === 'ArrowUp') {
    if (!STDIN_HISTORY.length) return;
    event.preventDefault?.();
    if (STDIN_HISTORY_INDEX === -1) {
      STDIN_DRAFT = input.value;
      STDIN_HISTORY_INDEX = STDIN_HISTORY.length - 1;
    } else if (STDIN_HISTORY_INDEX > 0) {
      STDIN_HISTORY_INDEX--;
    }
    input.value = STDIN_HISTORY[STDIN_HISTORY_INDEX];
  } else if (event.key === 'ArrowDown') {
    if (STDIN_HISTORY_INDEX === -1) return;
    event.preventDefault?.();
    if (STDIN_HISTORY_INDEX < STDIN_HISTORY.length - 1) {
      STDIN_HISTORY_INDEX++;
      input.value = STDIN_HISTORY[STDIN_HISTORY_INDEX];
    } else {
      STDIN_HISTORY_INDEX = -1;
      input.value = STDIN_DRAFT;
    }
  }
}

async function deliverStdin(text, { newline = true, receiptLabel = null, restoreInputFocus = false } = {}) {
  if (isHistory() || !currentDetailMatches() || el('btnSend').disabled) return null;
  const j = currentJob();
  if (!j) return null;
  const target = captureTargets([j])[0];
  const r = await jobAction('send', [target], { text, newline }, { silentSuccess: true });
  const label = receiptLabel ?? (text === '' ? '↵' : text);
  if (r?.affected === 1) {
    recordStdinHistory(target.attempt_id, { kind: 'stdin', label, text, ok: true });
    const sendBtn = el('btnSend');
    if (sendBtn?.classList?.toggle) {
      sendBtn.classList.toggle('just-sent', true);
      if (typeof clearTimeout === 'function') clearTimeout(window._stdinSendFlashTimer);
      if (typeof setTimeout === 'function') {
        window._stdinSendFlashTimer = setTimeout(() => el('btnSend')?.classList?.toggle?.('just-sent', false), 450);
      }
    }
    renderDetail();
  } else if (r) {
    const item = (r.results || []).find(x => x.job_id === target.job_id);
    recordStdinHistory(target.attempt_id, { kind: 'stdin', label, text, ok: false, reason: item?.reason || 'rejected' });
    renderDetail();
  }
  if (restoreInputFocus && !el('stdinInput')?.disabled) {
    el('stdinInput')?.focus?.({ preventScroll: true });
  }
  return r;
}

async function sendStdinCommand() {
  if (isHistory() || !currentDetailMatches() || el('btnSend').disabled) return;
  const input = el('stdinInput');
  if (!input) return;
  const wasFocused = typeof document !== 'undefined' && document.activeElement === input;
  const sentText = input.value;
  if (sentText !== '') {
    if (STDIN_HISTORY.at(-1) !== sentText) {
      STDIN_HISTORY.push(sentText);
      if (STDIN_HISTORY.length > 50) STDIN_HISTORY.shift();
    }
    STDIN_HISTORY_INDEX = -1;
    STDIN_DRAFT = '';
  }
  const r = await deliverStdin(sentText, { newline: true, restoreInputFocus: wasFocused });
  if (r?.affected === 1 && input.value === sentText) {
    input.value = '';
    if (wasFocused && !input.disabled) input.focus?.({ preventScroll: true });
  }
}

async function sendStdinEmptyEnter() {
  if (el('stdinSignalMenu')) el('stdinSignalMenu').open = false;
  return deliverStdin('', { newline: true, receiptLabel: '↵ (Enter)' });
}

async function sendStdinCtrlD() {
  if (el('stdinSignalMenu')) el('stdinSignalMenu').open = false;
  return deliverStdin('\x04', { newline: false, receiptLabel: 'Ctrl+D (EOF)' });
}

async function sendJobSignal(sigName) {
  if (el('stdinSignalMenu')) el('stdinSignalMenu').open = false;
  if (!sigName || isHistory() || !currentDetailMatches()) return;
  const j = currentJob();
  if (!j || !allowed(j, 'interrupt')) return;
  const target = captureTargets([j])[0];
  const r = await jobAction('signal', [target], { signal: sigName }, { silentSuccess: true });
  if (r?.affected === 1) {
    recordStdinHistory(target.attempt_id, { kind: 'signal', label: sigName, ok: true });
    renderDetail();
  } else if (r) {
    const item = (r.results || []).find(x => x.job_id === target.job_id);
    recordStdinHistory(target.attempt_id, { kind: 'signal', label: sigName, ok: false, reason: item?.reason || 'rejected' });
    renderDetail();
  }
}

function toggleBroadcast(open = el('broadcastPanel').hidden, returnFocus = true) {
  el('broadcastPanel').hidden = !open;
  if (open) el('broadcastText').focus();
  else if (returnFocus) restoreFocus(el('btnBroadcast'), el('jobSearch'));
}
async function broadcastSend() {
  if (!el('broadcastText').value) return;
  const sendable = checkedJobs().filter(j => allowed(j, 'send'));
  if (!sendable.length) return;
  const text = el('broadcastText').value,
    reqTargets = captureTargets(sendable),
    targetMap = new Map(reqTargets.map(t => [t.job_id, t]));
  const r = await jobAction('send', reqTargets, {
    text
  });
  if (r) {
    r.results.forEach(item => {
      const orig = targetMap.get(item.job_id);
      const boundAttemptId = item.attempt_id || orig?.attempt_id;
      if (boundAttemptId) {
        recordStdinHistory(boundAttemptId, {
          kind: 'broadcast', label: text, text, ok: !!item.ok, reason: item.reason || ''
        });
      }
    });
    if (r.results.every(x => x.ok) && el('broadcastText').value === text) el('broadcastText').value = '';
    renderDetail();
  }
}
function openSelectedPriority() {
  if (!isHistory() && currentDetailMatches()) openPriorityEditor(currentJob().id);
}

function openJobsPriority() {
  const job = currentJob();
  if (job) openPriorityEditor(job.id);
}

function openPriorityEditor(scope) {
  if (!canControl()) return;
  const jobs = scope === 'checked' ? checkedJobs() : STATE.jobs.filter(j => j.id === scope);
  if (!jobs.some(j => allowed(j, 'priority'))) return;
  PRIORITY_REQUEST = {workspace: STATE.workspace_id, targets: captureTargets(jobs)};
  el('priorityTargets').textContent = jobs.length === 1 ? jobs[0].name + ' · Attempt ' + jobs[0].attempt_number :
    jobs.length + ' checked jobs: ' + jobs.map(j => j.name).join(', ');
  const values = new Set(jobs.filter(j => allowed(j, 'priority')).map(j => j.priority));
  el('priorityValue').value = values.size === 1 ? String([...values][0]) : '';
  el('priorityValue').placeholder = values.size === 1 ? '' : 'Mixed priorities';
  el('priorityError').textContent = '';
  openModal('priorityOverlay', 'priorityValue');
  updatePriorityAvailability();
}

function updatePriorityAvailability() {
  if (!PRIORITY_REQUEST || el('priorityOverlay').hidden) return;
  const sameWorkspace = PRIORITY_REQUEST.workspace === STATE.workspace_id;
  const pending = sameWorkspace ? PRIORITY_REQUEST.targets.filter(t => STATE.jobs.some(j =>
    j.id === t.job_id && j.attempt_id === t.attempt_id && j.submission_id === t.submission_id && allowed(j, 'priority'))).length : 0;
  el('priorityAvailability').textContent = sameWorkspace ? pending + '/' + PRIORITY_REQUEST.targets.length +
    ' original targets still pending. Others will be reported as not changed.' : 'Work file changed. Close and reopen this action.';
  el('priorityApply').disabled = !canControl() || !pending;
}

async function applyPriority() {
  const request = PRIORITY_REQUEST, text = el('priorityValue').value.trim(), value = Number(text);
  if (!request || !canControl() || request.workspace !== STATE.workspace_id) return;
  if (!text || !Number.isSafeInteger(value)) {
    el('priorityError').textContent = 'Enter an integer between −9007199254740991 and 9007199254740991.';
    return;
  }
  BUSY = true; updateControls();
  try {
    const response = await apiPost('/api/job/priority', {targets: targets(request.targets), priority: value});
    if (response) {
      closeModal('priorityOverlay'); PRIORITY_REQUEST = null;
      report(response);
    } else el('priorityError').textContent = 'Could not confirm the priority update. Check current state before retrying.';
    await fetchState();
  } finally { BUSY = false; updateControls(); }
}

function refreshJobsColumns() {
  if (SORT_KEY && !JobsColumns.columns().includes(SORT_KEY)) SORT_KEY = '';
  renderMonitor();
}

const SETTINGS_TABS = [
  ['retention', 'btnSecRetention', 'settingsSectionRetention'],
  ['display', 'btnSecDisplay', 'settingsSectionDisplay']
];

function focusSettingsSection(section) {
  const next = SETTINGS_TABS.some(([k]) => k === section) ? section : 'retention';
  SETTINGS_SECTION = next;
  for (const [key, tabId, panelId] of SETTINGS_TABS) {
    const active = next === key;
    const tab = el(tabId);
    if (tab) {
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      tab.classList?.toggle('active', active);
    }
    const panel = el(panelId);
    if (panel) panel.hidden = !active;
  }
}

function settingsTabKey(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const idx = SETTINGS_TABS.findIndex(([k]) => k === SETTINGS_SECTION);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? SETTINGS_TABS.length - 1 :
    (idx + (event.key === 'ArrowLeft' ? SETTINGS_TABS.length - 1 : 1)) % SETTINGS_TABS.length;
  const [section, tabId] = SETTINGS_TABS[next];
  focusSettingsSection(section);
  el(tabId)?.focus?.();
}

function refreshSettingsLiveStatus() {
  const j = currentJob();
  if (el('retSelectedJobInfo')) {
    el('retSelectedJobInfo').innerHTML = j ?
      `Selected: <strong>${escHtml(j.name)}</strong> (#${j.attempt_number}) · limit <strong>${j.max_log_lines ?? 10000}</strong> · ${j.log_count}/${j.total_lines} lines` :
      'Selected job: None';
  }
  const distinctLimits = [...new Set(STATE.jobs.map(x => x.max_log_lines ?? 10000))].sort((a, b) => a - b);
  if (el('retBatchJobInfo')) {
    if (!STATE.jobs.length) {
      el('retBatchJobInfo').textContent = 'All jobs: No jobs loaded.';
    } else if (distinctLimits.length === 1) {
      el('retBatchJobInfo').innerHTML = `All ${STATE.jobs.length} job(s): <strong>${distinctLimits[0]}</strong> lines`;
    } else {
      el('retBatchJobInfo').innerHTML = `All ${STATE.jobs.length} job(s): <span class="status-badge result-INCOMPLETE">mixed</span> (${distinctLimits.join(', ')})`;
    }
  }
  const totalTruncated = STATE.jobs.reduce((acc, x) => acc + (x.history_truncated || 0), 0);
  if (el('retHistoryInfo')) {
    el('retHistoryInfo').innerHTML = `Keeping up to <strong>${STATE.max_history ?? 20}</strong> completed attempts per job` +
      (totalTruncated ? ` · <span style="color:#fde68a">${totalTruncated} evicted</span>` : '');
  }
}

function populateSettingsDialog() {
  const j = currentJob(),
    distinctLimits = [...new Set(STATE.jobs.map(x => x.max_log_lines ?? 10000))];
  if (distinctLimits.length > 1) {
    el('logRetentionInput').value = j ? String(j.max_log_lines ?? 10000) : '';
    el('logRetentionInput').placeholder = 'Mixed (' + distinctLimits.join(', ') + ')';
  } else {
    el('logRetentionInput').value = String(j?.max_log_lines ?? distinctLimits[0] ?? 10000);
    el('logRetentionInput').placeholder = '10000';
  }
  el('maxHistoryInput').value = String(STATE.max_history ?? 20);
  if (el('displaySavedStatus')) el('displaySavedStatus').textContent = '';
  refreshSettingsLiveStatus();
  syncDisplayPrefsControls();
  updateControls();
}

function openSettings(section = 'retention') {
  // Populate inputs only from a confirmed core snapshot, including on first load.
  if (!CONNECTED) return;
  ++SETTINGS_DIALOG_EPOCH;
  clearFormErrors('settingsOverlay', 'settingsFeedback');
  populateSettingsDialog();
  const target = SETTINGS_TABS.some(([k]) => k === section) ? section : 'retention';
  focusSettingsSection(target);
  openModal('settingsOverlay', target === 'display' ? 'btnSecDisplay' : 'btnSecRetention');
}

function closeSettings() {
  closeModal('settingsOverlay');
}

function settingsErrorHandler() {
  const epoch = SETTINGS_DIALOG_EPOCH;
  return error => {
    if (el('settingsOverlay').hidden || epoch !== SETTINGS_DIALOG_EPOCH) return false;
    showFormError(error, SETTINGS_FIELDS, 'settingsFeedback');
    return true;
  };
}
function clearSettingsErrors() {
  clearFormErrors('settingsOverlay', 'settingsFeedback');
}

async function applyRetentionSettings() {
  clearSettingsErrors();
  const logRaw = el('logRetentionInput').value.trim(),
    lines = Number(logRaw),
    histRaw = el('maxHistoryInput').value.trim(),
    limit = Number(histRaw),
    scope = el('logRetentionScope')?.value || 'all';
  if (logRaw && (!Number.isSafeInteger(lines) || lines < 1)) {
    showFieldError(el('logRetentionInput'), 'Log retention must be a positive integer (>= 1).');
    return false;
  }
  if (!histRaw || !Number.isSafeInteger(limit) || limit < 1) {
    showFieldError(el('maxHistoryInput'), 'History retention limit must be a positive integer (>= 1).');
    return false;
  }
  let logOutcome = null;
  if (logRaw && STATE.jobs.length) {
    logOutcome = await applyLogRetention(scope, false, { silent: true });
    if (!logOutcome) return false;
  }
  return saveHistoryLimit(false, { prefixFeedback: logOutcome });
}

async function applyLogRetention(scope, clearErr = true, { silent = false } = {}) {
  if (clearErr) clearSettingsErrors();
  const raw = el('logRetentionInput').value.trim(),
    lines = Number(raw);
  if (!raw || !Number.isSafeInteger(lines) || lines < 1) {
    showFieldError(el('logRetentionInput'), 'Log retention must be a positive integer (>= 1).');
    return false;
  }
  let targetJobs = [];
  if (scope === 'selected') {
    if (isHistory() || !currentDetailMatches() || !currentJob()) {
      recordFeedback('Return to Current attempt of a selected job before applying per-job retention.', 'warn');
      return false;
    }
    targetJobs = [currentJob()];
  } else {
    targetJobs = STATE.jobs.slice();
  }
  if (!targetJobs.length) {
    recordFeedback('No target jobs available.', 'warn');
    return false;
  }
  const reqTargets = captureTargets(targetJobs);
  BUSY = true;
  updateControls();
  try {
    const r = await apiPost('/api/job/retention', {
      max_log_lines: lines,
      targets: targets(reqTargets)
    }, {onError: settingsErrorHandler()});
    if (!r) return false;
    const items = r.results || [],
      skipped = items.filter(x => !x.ok),
      evicted = r.evicted_lines || 0;
    const scopeLabel = scope === 'selected' ? reqTargets[0].name : `all ${items.length} job(s)`;
    const evictNote = evicted ? ` (${evicted} older line(s) evicted)` : '';
    const details = skipped.length ? receiptRows(items, item => item.message || (item.ok ? 'Accepted' : 'Rejected')) : '';
    const msg = `Log retention set to ${lines} lines for ${scopeLabel}: ${r.affected}/${items.length} accepted${evictNote}.`;
    const tone = skipped.length ? 'warn' : 'info';
    if (!silent) recordFeedback(msg, tone, details);
    await fetchState();
    if (!el('settingsOverlay').hidden) refreshSettingsLiveStatus();
    return { ok: true, msg, tone, details };
  } finally {
    BUSY = false;
    updateControls();
  }
}

async function saveHistoryLimit(clearErr = true, { manageBusy = true, prefixFeedback = null } = {}) {
  if (clearErr) clearSettingsErrors();
  const raw = el('maxHistoryInput').value.trim(),
    limit = Number(raw);
  if (!raw || !Number.isSafeInteger(limit) || limit < 1) {
    showFieldError(el('maxHistoryInput'), 'History retention limit must be a positive integer (>= 1).');
    return false;
  }
  if (manageBusy) {
    BUSY = true;
    updateControls();
  }
  try {
    const r = await apiPost('/api/settings/save', {
      max_history: limit
    }, {onError: settingsErrorHandler()});
    if (!r) return false;
    const evicted = r.evicted_history || 0;
    await fetchState();
    if (!el('settingsOverlay').hidden) refreshSettingsLiveStatus();
    const histMsg = evicted ?
      `Keeping up to ${limit} completed attempts per job (${evicted} older attempt(s) removed from memory).` :
      `Keeping up to ${limit} completed attempts per job in memory.`;
    const histTone = evicted ? 'warn' : 'info';
    if (prefixFeedback?.ok) {
      const tone = prefixFeedback.tone === 'warn' || histTone === 'warn' ? 'warn' : 'info';
      recordFeedback(`${prefixFeedback.msg} · ${histMsg}`, tone, prefixFeedback.details || '');
    } else {
      recordFeedback(histMsg, histTone);
    }
    return true;
  } finally {
    if (manageBusy) {
      BUSY = false;
      updateControls();
    }
  }
}

const OPEN_DRAFT_KEY = 'wave_open_draft_v1';
const LOG_TARGET_TYPES = new Map();
const BROWSE_CHECKED = new Set();
let openDraft;

function waveIconSvg(name) {
  return typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function' ? WaveIcons.svg(name) : '';
}

function buildRecentByMode(recentEntries, rawByMode) {
  const out = { work: [], logs: [], archive: [] };
  for (const mode of ['work', 'logs', 'archive']) {
    const arr = Array.isArray(rawByMode?.[mode]) ? rawByMode[mode].filter(p => typeof p === 'string') : [];
    const seen = new Set();
    for (const p of arr) {
      if (!seen.has(p) && seen.size < 5) { seen.add(p); out[mode].push(p); }
    }
    for (const entry of recentEntries || []) {
      if (entry?.mode === mode && typeof entry.path === 'string' && !seen.has(entry.path) && out[mode].length < 5) {
        seen.add(entry.path);
        out[mode].push(entry.path);
      }
    }
  }
  return out;
}

function getOpenDraft() {
  if (openDraft) return openDraft;
  let data;
  try { data = JSON.parse(window.localStorage.getItem(OPEN_DRAFT_KEY)); }
  catch { /* Storage can be unavailable on local or restricted profiles. */ }
  const recent = Array.isArray(data?.recent) ? data.recent.filter(entry =>
    ['work', 'logs', 'archive'].includes(entry?.mode) && typeof entry.path === 'string').slice(0, 5) : [];
  openDraft = {
    work: typeof data?.work === 'string' ? data.work : '',
    logs: Array.isArray(data?.logs) ? data.logs.filter(path => typeof path === 'string') : [],
    archive: typeof data?.archive === 'string' ? data.archive : '',
    directory: typeof data?.directory === 'string' ? data.directory : '',
    recentDirs: Array.isArray(data?.recentDirs) ? data.recentDirs.filter(path => typeof path === 'string').slice(0, 6) : [],
    recent,
    recentByMode: buildRecentByMode(recent, data?.recentByMode)
  };
  return openDraft;
}

function saveOpenDraft() {
  try { window.localStorage.setItem(OPEN_DRAFT_KEY, JSON.stringify(getOpenDraft())); }
  catch { /* The current page can still reuse confirmed paths. */ }
}

function rememberBrowseDirectory(dirPath) {
  if (!dirPath) return;
  const draft = getOpenDraft();
  draft.recentDirs = [dirPath, ...(draft.recentDirs || []).filter(p => p !== dirPath)].slice(0, 6);
  saveOpenDraft();
}

function rememberLoadedPaths(mode, paths) {
  const draft = getOpenDraft();
  if (mode === 'work') draft.work = paths[0];
  else if (mode === 'archive') draft.archive = paths[0];
  else draft.logs = paths.slice();
  draft.recent = [
    ...paths.map(path => ({mode, path})),
    ...draft.recent.filter(entry => entry.mode !== mode || !paths.includes(entry.path))
  ].slice(0, 5);
  if (!draft.recentByMode) draft.recentByMode = buildRecentByMode(draft.recent);
  if (['work', 'logs', 'archive'].includes(mode)) {
    const prev = Array.isArray(draft.recentByMode[mode]) ? draft.recentByMode[mode] : [];
    draft.recentByMode[mode] = [...paths, ...prev.filter(p => !paths.includes(p))].slice(0, 5);
  }
  saveOpenDraft();
}

const recentPathId = mode =>
  mode === 'work' ? 'recentWorkPaths' : mode === 'archive' ? 'recentArchivePaths' : 'recentLogPaths';
const recentChipIds = mode =>
  mode === 'work' ? ['recentWorkChipsWrap', 'recentWorkChips']
    : mode === 'archive' ? ['recentArchiveChipsWrap', 'recentArchiveChips']
      : ['recentLogChipsWrap', 'recentLogChips'];

function applyPickedRecentPath(mode, path) {
  if (!path) return;
  if (mode === 'logs') addLogTarget(path);
  else {
    const input = el(mode === 'archive' ? 'archivePath' : 'workPath');
    input.value = path;
    fieldError(input);
    if (mode === 'archive') updateArchiveCliHint();
  }
  const focusId = mode === 'logs' ? (canFocus(el('logTargetAddInput')) ? 'logTargetAddInput' : 'logTargets') :
    mode === 'archive' ? 'archivePath' : 'workPath';
  el(focusId).focus();
}

function renderRecentPaths(mode) {
  const draft = getOpenDraft(), id = recentPathId(mode);
  const byMode = draft.recentByMode?.[mode];
  const paths = Array.isArray(byMode) && byMode.length
    ? byMode.slice()
    : draft.recent.filter(entry => entry.mode === mode).map(entry => entry.path);
  const [wrapId, boxId] = recentChipIds(mode);
  const wrap = el(wrapId), chipsBox = el(boxId);
  if (wrap && chipsBox) {
    wrap.hidden = !paths.length;
    chipsBox.innerHTML = paths.map((path, idx) => {
      const shortName = path.split(/[\\/]/).filter(Boolean).pop() || path;
      return `<button type="button" class="recent-chip" data-recent-chip="${idx}" title="${escHtml(path)}">` +
        `<span class="recent-chip-name">${escHtml(shortName)}</span></button>`;
    }).join('');
    chipsBox.querySelectorAll?.('button[data-recent-chip]').forEach(btn => {
      const picked = paths[Number(btn.dataset.recentChip)];
      if (picked) btn.onclick = () => applyPickedRecentPath(mode, picked);
    });
  }
  const label = el(id + 'Label'), select = el(id);
  if (label) label.hidden = !paths.length || !!(wrap && chipsBox);
  if (select) {
    select.innerHTML = '<option value="">Choose a path…</option>' + paths.map(path =>
      `<option value="${escHtml(path)}" title="${escHtml(path)}">${escHtml(path)}</option>`).join('');
  }
}

function useRecentPath(mode) {
  const select = el(recentPathId(mode)), path = select.value;
  if (!path) return;
  applyPickedRecentPath(mode, path);
  select.value = '';
}

function openWorkFile(mode = OPEN_MODE) {
  const normalized = ['work', 'logs', 'archive'].includes(mode) ? mode : 'work';
  if (!openDraft) {
    const draft = getOpenDraft();
    el('workPath').value = draft.work;
    el('logTargets').value = draft.logs.join('\n');
    if (el('archivePath')) el('archivePath').value = draft.archive;
  }
  ++OPEN_DIALOG_EPOCH;
  setOpenMode(normalized);
  const focusId = normalized === 'logs'
    ? (el('logTargetAddInput')?.id || 'logTargets')
    : normalized === 'archive' ? 'archivePath' : 'workPath';
  openModal('workOverlay', focusId);
}

function setOpenMode(mode) {
  OPEN_MODE = ['work', 'logs', 'archive'].includes(mode) ? mode : 'work';
  ++BROWSE_REQUEST;
  if (OPEN_MODE !== 'archive') {
    ++ARCHIVE_CATALOG_EPOCH;
    ARCHIVE_CATALOG_CONTROLLER?.abort?.();
    ARCHIVE_CATALOG_CONTROLLER = null;
  }
  BROWSE_KIND = BROWSE_PARENT = BROWSE_DIRECTORY = null;
  BROWSE_ENTRIES = [];
  BROWSE_VISIBLE = [];
  BROWSE_FILTER = '';
  BROWSE_SELECTED_INDEX = -1;
  BROWSE_CHECKED.clear();
  el('fileBrowser').hidden = true;
  el('btnAddFolder').hidden = true;
  if (el('btnAddSelectedLogs')) el('btnAddSelectedLogs').hidden = true;
  if (el('btnSelectBrowseFile')) el('btnSelectBrowseFile').hidden = true;
  if (el('browseFilterInput')) el('browseFilterInput').value = '';
  clearFormErrors('workOverlay', 'loadError');
  renderRecentPaths(OPEN_MODE);
  for (const [kind, tabId, panelId] of [
    ['work', 'openWorkTab', 'openWorkPanel'],
    ['logs', 'openLogsTab', 'openLogsPanel'],
    ['archive', 'openArchiveTab', 'openArchivePanel']
  ]) {
    const selected = OPEN_MODE === kind, tab = el(tabId);
    if (!tab) continue;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    el(panelId).hidden = !selected;
  }
  if (el('btnLoadFile')) el('btnLoadFile').hidden = OPEN_MODE !== 'work';
  if (el('btnLoadLogs')) el('btnLoadLogs').hidden = OPEN_MODE !== 'logs';
  if (el('btnOpenArchive')) el('btnOpenArchive').hidden = OPEN_MODE !== 'archive';
  if (OPEN_MODE === 'logs') {
    renderLogTargets();
    updateLogOptions();
  } else if (OPEN_MODE === 'archive') {
    updateArchiveBusyUi();
    loadRecentArchives();
  }
}

function switchOpenTab(event) {
  const order = ['work', 'logs', 'archive'];
  const tabs = {work: 'openWorkTab', logs: 'openLogsTab', archive: 'openArchiveTab'};
  let idx = order.indexOf(OPEN_MODE);
  if (idx < 0) idx = 0;
  let mode;
  if (event.key === 'Home') mode = 'work';
  else if (event.key === 'End') mode = 'archive';
  else if (event.key === 'ArrowLeft') mode = order[(idx + order.length - 1) % order.length];
  else if (event.key === 'ArrowRight') mode = order[(idx + 1) % order.length];
  else return;
  event.preventDefault();
  setOpenMode(mode);
  el(tabs[mode]).focus();
}

function readLogTargets() {
  return [...new Set(el('logTargets').value.split(/\r?\n/).map(path => path.trim()).filter(Boolean))];
}

function isFolderTargetPath(path) {
  const known = LOG_TARGET_TYPES.get(path);
  if (known === 'dir') return true;
  if (known === 'file') return false;
  const trimmed = String(path || '').trim();
  if (!trimmed) return false;
  if (/[\\/]$/.test(trimmed)) return true;
  const base = trimmed.split(/[\\/]/).pop() || '';
  return !/\.[^.]+$/.test(base);
}

function renderLogTargets() {
  const targets = readLogTargets();
  const list = el('logTargetList');
  if (el('logTargetCount')) el('logTargetCount').textContent = targets.length ? `(${targets.length})` : '';
  list.innerHTML = targets.map((path, index) => {
    const isDir = isFolderTargetPath(path);
    const kindLabel = isDir ? 'DIR' : 'FILE';
    return `<li><span class="open-target-kind${isDir ? ' dir' : ''}">${kindLabel}</span><span class="open-target-path" title="${escHtml(path)}">${escHtml(path)}</span><button type="button" onclick="removeLogTarget(${index})" aria-label="Remove ${escHtml(path)}">Remove</button></li>`;
  }).join('');
  list.hidden = !targets.length;
  if (el('logTargetEmpty')) el('logTargetEmpty').hidden = !!targets.length;
  el('btnClearLogTargets').disabled = !targets.length;
  const globs = (el('logGlobs').value || '').trim();
  const hasFolder = targets.some(isFolderTargetPath) || !!el('logRecursive').checked || (!!globs && globs !== '*.log');
  if (el('logFolderOptions')) el('logFolderOptions').hidden = !hasFolder;
  if (el('logGlobHint')) el('logGlobHint').hidden = !hasFolder;
}

function setLogTargets(paths) {
  el('logTargets').value = paths.join('\n');
  fieldError(el('logTargets'));
  renderLogTargets();
}

function addLogTarget(path, kind = null) {
  const cleaned = String(path || '').trim();
  if (!cleaned) return;
  if (kind === 'dir' || kind === 'file') LOG_TARGET_TYPES.set(cleaned, kind);
  setLogTargets([...new Set([...readLogTargets(), cleaned])]);
}

function addLogTargetFromInput() {
  const input = el('logTargetAddInput');
  const raw = (input?.value || '').trim();
  if (!raw) {
    fieldError(el('logTargets'), 'Enter a log file or folder path.');
    input?.focus?.();
    return;
  }
  input.value = '';
  addLogTarget(raw);
  input.focus?.();
}

function clearLogTargetsUi() {
  setLogTargets([]);
  (canFocus(el('logTargetAddInput')) ? el('logTargetAddInput') : el('logTargets')).focus?.();
}

function removeLogTarget(index) {
  setLogTargets(readLogTargets().filter((_, i) => i !== index));
  (canFocus(el('logTargetAddInput')) ? el('logTargetAddInput') : el('logTargets')).focus?.();
}

function addBrowsedFolder() {
  if (OPEN_MODE === 'logs' && BROWSE_KIND === 'logs' && BROWSE_DIRECTORY) {
    addLogTarget(BROWSE_DIRECTORY, 'dir');
    updateBrowseFooter();
  }
}

function setLogsMode(mode) {
  el('logsMode').value = mode === 'static' ? 'static' : 'live';
  updateLogOptions();
}

function updateLogOptions() {
  const isStatic = el('logsMode').value === 'static';
  el('logStart').disabled = isStatic;
  if (el('logStartLabel')) el('logStartLabel').hidden = isStatic;
  if (el('logsModeLiveBtn')) {
    el('logsModeLiveBtn').classList.toggle('active', !isStatic);
    el('logsModeLiveBtn').setAttribute('aria-pressed', String(!isStatic));
  }
  if (el('logsModeStaticBtn')) {
    el('logsModeStaticBtn').classList.toggle('active', isStatic);
    el('logsModeStaticBtn').setAttribute('aria-pressed', String(isStatic));
  }
}

function closeWorkFile() {
  ++OPEN_DIALOG_EPOCH;
  ++BROWSE_REQUEST;
  ++ARCHIVE_CATALOG_EPOCH;
  ARCHIVE_CATALOG_CONTROLLER?.abort?.();
  ARCHIVE_CATALOG_CONTROLLER = null;
  if (el('fileBrowserOverlay') && !el('fileBrowserOverlay').hidden) closeModal('fileBrowserOverlay');
  el('fileBrowser').hidden = true;
  closeModal('workOverlay');
}

function hideFileBrowser() {
  ++BROWSE_REQUEST;
  el('fileBrowser').hidden = true;
  closeModal('fileBrowserOverlay');
}

function clampFileBrowserRect(rect) {
  const vw = window.innerWidth || 1200;
  const vh = window.innerHeight || 800;
  const margin = 8;
  const maxW = Math.max(300, vw - margin * 2);
  const maxH = Math.max(240, vh - margin * 2);
  const minW = Math.min(520, maxW);
  const minH = Math.min(360, maxH);
  const width = Math.round(Math.min(maxW, Math.max(minW, Number(rect?.width) || 880)));
  const height = Math.round(Math.min(maxH, Math.max(minH, Number(rect?.height) || 560)));
  const left = Math.round(Math.min(Math.max(margin, vw - width - margin), Math.max(margin, Number(rect?.left) || 0)));
  const top = Math.round(Math.min(Math.max(margin, vh - height - margin), Math.max(margin, Number(rect?.top) || 0)));
  return { left, top, width, height };
}

function applyFileBrowserRect(rect) {
  const modal = el('fileBrowser');
  if (!modal || !rect) return null;
  const clamped = clampFileBrowserRect(rect);
  modal.classList?.add('fb-positioned');
  modal.style.left = clamped.left + 'px';
  modal.style.top = clamped.top + 'px';
  modal.style.width = clamped.width + 'px';
  modal.style.height = clamped.height + 'px';
  FB_WINDOW_RECT = clamped;
  return clamped;
}

function resetFileBrowserRect() {
  const modal = el('fileBrowser');
  FB_WINDOW_RECT = null;
  if (!modal) return;
  modal.classList?.remove('fb-positioned');
  modal.style.left = '';
  modal.style.top = '';
  modal.style.width = '';
  modal.style.height = '';
}

function setupFileBrowserWindow() {
  const modal = el('fileBrowser');
  if (!modal || typeof modal.addEventListener !== 'function') return;
  if (modal.dataset?.fbWindowBound === '1') return;
  if (modal.dataset) modal.dataset.fbWindowBound = '1';
  let activeDrag = null;
  let dragRaf = 0;
  let pendingRect = null;

  const scheduleRect = rect => {
    pendingRect = rect;
    if (typeof requestAnimationFrame !== 'function') {
      pendingRect = null;
      applyFileBrowserRect(rect);
      return;
    }
    if (dragRaf) return;
    dragRaf = requestAnimationFrame(() => {
      dragRaf = 0;
      if (pendingRect) {
        const next = pendingRect;
        pendingRect = null;
        applyFileBrowserRect(next);
      }
    });
  };

  const stopDrag = event => {
    if (!activeDrag) return;
    if (event && event.pointerId !== undefined && activeDrag.pointerId !== event.pointerId) return;
    const captureTarget = activeDrag.target;
    const pid = activeDrag.pointerId;
    activeDrag = null;
    if (dragRaf && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(dragRaf);
      dragRaf = 0;
    }
    if (pendingRect) {
      const next = pendingRect;
      pendingRect = null;
      applyFileBrowserRect(next);
    }
    document.body?.classList?.toggle('fb-resizing', false);
    try { captureTarget?.releasePointerCapture?.(pid); } catch (_) {}
  };

  const startDrag = (event, mode, dir) => {
    if (event.button !== 0 || modal.hidden) return;
    event.preventDefault();
    const box = modal.getBoundingClientRect();
    if (!box || box.width <= 0 || box.height <= 0) return;
    const initial = { left: box.left, top: box.top, width: box.width, height: box.height };
    activeDrag = {
      mode,
      dir: dir || '',
      pointerId: event.pointerId,
      target: event.currentTarget,
      startX: event.clientX,
      startY: event.clientY,
      rect: initial,
    };
    applyFileBrowserRect(initial);
    document.body?.classList?.toggle('fb-resizing', true);
    try { event.currentTarget?.setPointerCapture?.(event.pointerId); } catch (_) {}
  };

  const titlebar = modal.querySelector?.('.fb-titlebar');
  if (titlebar) {
    titlebar.addEventListener('pointerdown', event => {
      if (event.target?.closest?.('button,input,select,a')) return;
      startDrag(event, 'move', '');
    });
    titlebar.addEventListener('dblclick', event => {
      if (event.target?.closest?.('button,input,select,a')) return;
      resetFileBrowserRect();
    });
  }

  modal.querySelectorAll?.('[data-fb-resize]').forEach(handle => {
    handle.addEventListener('pointerdown', event => {
      startDrag(event, 'resize', handle.dataset.fbResize || '');
    });
  });

  window.addEventListener('pointermove', event => {
    if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
    const dx = event.clientX - activeDrag.startX;
    const dy = event.clientY - activeDrag.startY;
    const base = activeDrag.rect;
    if (activeDrag.mode === 'move') {
      scheduleRect({
        left: base.left + dx,
        top: base.top + dy,
        width: base.width,
        height: base.height,
      });
      return;
    }
    const dir = activeDrag.dir;
    const vw = window.innerWidth || 1200;
    const vh = window.innerHeight || 800;
    const margin = 8;
    const maxW = Math.max(300, vw - margin * 2);
    const maxH = Math.max(240, vh - margin * 2);
    const minW = Math.min(520, maxW);
    const minH = Math.min(360, maxH);
    let left = base.left;
    let top = base.top;
    let width = base.width;
    let height = base.height;
    const right = base.left + base.width;
    const bottom = base.top + base.height;

    if (dir.includes('e')) {
      width = Math.min(maxW, Math.max(minW, Math.min(vw - margin - base.left, base.width + dx)));
    }
    if (dir.includes('s')) {
      height = Math.min(maxH, Math.max(minH, Math.min(vh - margin - base.top, base.height + dy)));
    }
    if (dir.includes('w')) {
      const maxLeft = right - minW;
      const minLeft = Math.max(margin, right - maxW);
      left = Math.min(maxLeft, Math.max(minLeft, base.left + dx));
      width = right - left;
    }
    if (dir.includes('n')) {
      const maxTop = bottom - minH;
      const minTop = Math.max(margin, bottom - maxH);
      top = Math.min(maxTop, Math.max(minTop, base.top + dy));
      height = bottom - top;
    }
    scheduleRect({ left, top, width, height });
  });
  window.addEventListener('pointerup', stopDrag);
  window.addEventListener('pointercancel', stopDrag);
}

function toggleBrowsePathEdit(editing) {
  if (el('browseBreadcrumbsWrap')) el('browseBreadcrumbsWrap').hidden = !!editing;
  if (el('browsePathWrap')) el('browsePathWrap').hidden = !editing;
  if (editing && el('browsePath')) {
    const inp = el('browsePath');
    inp.focus();
    // Leave Linux X11 PRIMARY selection untouched by moving caret to the end instead of calling select().
    if (typeof inp.setSelectionRange === 'function') {
      const len = String(inp.value || '').length;
      inp.setSelectionRange(len, len);
    }
  }
}

function clearBrowsePathInput() {
  const inp = el('browsePath');
  if (!inp) return;
  inp.value = '';
  fieldError(inp);
  inp.focus();
}

function handleBrowsePathKey(event) {
  if (event.key === 'Enter') {
    event.preventDefault();
    browseFiles(event.target.value, BROWSE_KIND);
  } else if (event.key === 'Escape') {
    event.stopPropagation();
    toggleBrowsePathEdit(false);
  }
}

function handleFileBrowserOverlayMouseDown(event) {
  if (el('browsePathWrap') && !el('browsePathWrap').hidden &&
      !el('browseAddressBar')?.contains?.(event.target) &&
      el('browsePathError')?.hidden !== false) {
    toggleBrowsePathEdit(false);
  }
}

function updateBrowseNavButtons() {
  if (el('btnBrowseBack')) el('btnBrowseBack').disabled = BROWSE_HISTORY_INDEX <= 0;
  if (el('btnBrowseForward')) {
    el('btnBrowseForward').disabled = BROWSE_HISTORY_INDEX < 0 || BROWSE_HISTORY_INDEX >= BROWSE_HISTORY.length - 1;
  }
  if (el('btnBrowseParent')) el('btnBrowseParent').disabled = !BROWSE_DIRECTORY || BROWSE_PARENT === BROWSE_DIRECTORY;
}

function splitPathBreadcrumbs(dirPath) {
  const text = String(dirPath || '').trim();
  if (!text) return [];
  const winMatch = /^([A-Za-z]:[\\/])(.*)$/.exec(text);
  if (winMatch) {
    const root = winMatch[1].replace(/\//g, '\\');
    const parts = winMatch[2].split(/[\\/]+/).filter(Boolean);
    const crumbs = [{label: root.slice(0, 2) + '\\', path: root}];
    let acc = root;
    for (const seg of parts) {
      acc = (acc.endsWith('\\') ? acc : acc + '\\') + seg;
      crumbs.push({label: seg, path: acc});
    }
    return crumbs;
  }
  const posixAbs = text.startsWith('/');
  const parts = text.split('/').filter(Boolean);
  const crumbs = posixAbs ? [{label: '/', path: '/'}] : [];
  let acc = '';
  for (const seg of parts) {
    acc = posixAbs ? (acc + '/' + seg) : (acc ? acc + '/' + seg : seg);
    crumbs.push({label: seg, path: acc});
  }
  return crumbs;
}

function renderBrowseBreadcrumbs(dirPath) {
  const nav = el('browseBreadcrumbs');
  if (!nav) return;
  const crumbs = splitPathBreadcrumbs(dirPath);
  nav.innerHTML = crumbs.map((c, idx) =>
    (idx ? '<span class="fb-crumb-sep" aria-hidden="true">›</span>' : '') +
    `<button type="button" class="fb-crumb${idx === crumbs.length - 1 ? ' current' : ''}" data-crumb="${idx}" title="${escHtml(c.path)}">${escHtml(c.label)}</button>`
  ).join('');
  nav.querySelectorAll('button').forEach(btn => {
    const crumb = crumbs[Number(btn.dataset.crumb)];
    if (crumb) btn.onclick = () => browseFiles(crumb.path, BROWSE_KIND);
  });
  nav.scrollLeft = nav.scrollWidth;
}

function renderBrowseShortcuts(shortcuts, currentPath) {
  const box = el('browseShortcuts');
  if (!box) return;
  const iconFor = id => id === 'home' ? 'home' : id === 'archives' ? 'database' : 'folder';
  const visibleShortcuts = (shortcuts || []).filter(s => s.id !== 'archives' || BROWSE_KIND === 'archive');
  const items = visibleShortcuts.map((s, idx) =>
    `<button type="button" class="fb-shortcut${s.path === currentPath ? ' active' : ''}" data-shortcut="${idx}" title="${escHtml(s.path)}" ${s.exists === false ? 'disabled' : ''}>` +
    `<span class="fb-shortcut-icon" aria-hidden="true">${waveIconSvg(iconFor(s.id))}</span><span>${escHtml(s.label)}</span></button>`
  ).join('');
  const recentDirs = (getOpenDraft().recentDirs || []).slice(0, 5);
  const recentHtml = recentDirs.length ? (
    '<div class="fb-shortcut-group-label">Recent Folders</div>' +
    recentDirs.map((dirPath, i) => {
      const name = dirPath.split(/[\\/]/).filter(Boolean).pop() || dirPath;
      return `<button type="button" class="fb-shortcut${dirPath === currentPath ? ' active' : ''}" data-recent-dir="${i}" title="${escHtml(dirPath)}">` +
        `<span class="fb-shortcut-icon" aria-hidden="true">${waveIconSvg('folder')}</span><span>${escHtml(name)}</span></button>`;
    }).join('')
  ) : '';
  box.innerHTML = (items ? '<div class="fb-shortcut-group-label">Places</div>' + items : '') + recentHtml;
  box.querySelectorAll('[data-shortcut]').forEach(btn => {
    const item = visibleShortcuts[Number(btn.dataset.shortcut)];
    if (item && item.exists !== false) btn.onclick = () => browseFiles(item.path, BROWSE_KIND);
  });
  box.querySelectorAll('[data-recent-dir]').forEach(btn => {
    const dirPath = recentDirs[Number(btn.dataset.recentDir)];
    if (dirPath) btn.onclick = () => browseFiles(dirPath, BROWSE_KIND);
  });
}

function formatBrowseBytes(bytes) {
  if (bytes == null || !Number.isFinite(bytes)) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(1) + ' GB';
}

function formatBrowseMtime(sec) {
  if (sec == null || !Number.isFinite(sec)) return '';
  const d = new Date(sec * 1000);
  if (Number.isNaN(d.getTime())) return '';
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function entryIconInfo(entry) {
  if (entry.directory) return {name: 'folder', cls: 'is-dir'};
  const lower = String(entry.name || '').toLowerCase();
  if (lower.endsWith('.py')) return {name: 'file-code', cls: 'is-py'};
  if (lower.endsWith('.sqlite3')) return {name: 'database', cls: 'is-db'};
  return {name: 'file-text', cls: ''};
}

function setBrowseSort(col) {
  if (BROWSE_SORT === col) BROWSE_SORT_ASC = !BROWSE_SORT_ASC;
  else {
    BROWSE_SORT = col;
    BROWSE_SORT_ASC = col === 'name';
  }
  BROWSE_SELECTED_INDEX = -1;
  renderBrowseList();
}

function setBrowseFilter(text) {
  BROWSE_FILTER = String(text || '');
  BROWSE_SELECTED_INDEX = -1;
  renderBrowseList();
}

function sortAndFilterBrowseEntries() {
  const needle = BROWSE_FILTER.trim().toLowerCase();
  const filtered = needle
    ? BROWSE_ENTRIES.filter(e => String(e.name || '').toLowerCase().includes(needle))
    : BROWSE_ENTRIES.slice();
  const dir = BROWSE_SORT_ASC ? 1 : -1;
  filtered.sort((a, b) => {
    if (!!a.directory !== !!b.directory) return a.directory ? -1 : 1;
    let cmp = 0;
    if (BROWSE_SORT === 'mtime') cmp = ((a.mtime ?? 0) - (b.mtime ?? 0));
    else if (BROWSE_SORT === 'size') cmp = ((a.size ?? -1) - (b.size ?? -1));
    if (cmp === 0) cmp = String(a.name || '').localeCompare(String(b.name || ''), undefined, {sensitivity: 'base'});
    return cmp * dir;
  });
  return filtered;
}

function renderBrowseSortHeaders() {
  const bar = el('browseSortBar');
  if (!bar) return;
  const checkHead = el('browseCheckColHead');
  if (checkHead) checkHead.hidden = BROWSE_KIND !== 'logs';
  bar.querySelectorAll?.('button[data-sort]').forEach(btn => {
    const col = btn.dataset.sort;
    const base = col === 'mtime' ? 'Modified' : col === 'size' ? 'Size' : 'Name';
    const active = BROWSE_SORT === col;
    btn.classList?.toggle('active', active);
    btn.textContent = active ? `${base} ${BROWSE_SORT_ASC ? '▲' : '▼'}` : base;
  });
}

function updateBrowseFooter() {
  const status = el('browseSelectionStatus');
  const addSel = el('btnAddSelectedLogs');
  const useFile = el('btnSelectBrowseFile');
  const selectedEntry = BROWSE_VISIBLE[BROWSE_SELECTED_INDEX] || null;
  if (BROWSE_KIND === 'logs') {
    const count = BROWSE_CHECKED.size;
    if (addSel) {
      addSel.hidden = !count;
      addSel.textContent = count ? `Add Selected (${count})` : 'Add Selected';
    }
    if (useFile) useFile.hidden = true;
    if (status) {
      status.textContent = count
        ? `${count} checked — click Add Selected, or click a file to add immediately.`
        : selectedEntry?.directory
          ? `Selected folder: ${selectedEntry.name}/ — double-click or press Enter to open.`
          : `${BROWSE_VISIBLE.length} item(s)`;
    }
  } else {
    if (addSel) addSel.hidden = true;
    if (useFile) {
      useFile.hidden = !selectedEntry;
      useFile.textContent = selectedEntry?.directory ? 'Open Folder' : 'Select File';
    }
    if (status) {
      status.textContent = selectedEntry
        ? (selectedEntry.directory
          ? `Selected folder: ${selectedEntry.name}/ (double-click or Enter to open)`
          : `Selected: ${selectedEntry.name}`)
        : `${BROWSE_VISIBLE.length} item(s)`;
    }
  }
}

function applyPickedSingleFile(entry, closeAfter = false) {
  if (!entry || entry.directory) return;
  const purpose = BROWSE_KIND || OPEN_MODE;
  if (purpose === 'logs') {
    addLogTarget(entry.path, 'file');
    return;
  }
  const targetId = purpose === 'flow' ? 'logFlowPath' : purpose === 'archive' ? 'archivePath' : 'workPath';
  const target = el(targetId);
  target.value = entry.path;
  fieldError(target);
  if (purpose === 'archive') updateArchiveCliHint();
  if (closeAfter) {
    hideFileBrowser();
    const actionId = purpose === 'flow' ? 'logFlowPath' : purpose === 'archive' ? 'btnOpenArchive' : 'btnLoadFile';
    el(actionId).focus?.();
  }
}

function renderBrowseList() {
  const listing = el('fileList');
  const mode = BROWSE_KIND === 'logs' ? 'logs' : BROWSE_KIND === 'archive' ? 'archive' : 'work';
  BROWSE_VISIBLE = sortAndFilterBrowseEntries();
  renderBrowseSortHeaders();
  if (BROWSE_SELECTED_INDEX >= BROWSE_VISIBLE.length) BROWSE_SELECTED_INDEX = BROWSE_VISIBLE.length - 1;
  const emptyMsg = BROWSE_ENTRIES.length && !BROWSE_VISIBLE.length
    ? 'No matching items for filter.'
    : mode === 'logs'
      ? 'No files or folders here.'
      : mode === 'archive'
        ? 'No directories or .sqlite3 archives here.'
        : 'No directories or Python files.';
  listing.innerHTML = BROWSE_VISIBLE.map((entry, i) => {
    const info = entryIconInfo(entry);
    const checked = BROWSE_CHECKED.has(entry.path);
    const selectedCls = i === BROWSE_SELECTED_INDEX ? ' selected' : '';
    const checkBox = BROWSE_KIND === 'logs'
      ? `<input type="checkbox" class="fb-check" data-check="${i}" aria-label="Select ${escHtml(entry.name)}" ${checked ? 'checked' : ''}>`
      : '';
    return `<div class="fb-row-wrap${selectedCls}">${checkBox}<button type="button" class="fb-row ${info.cls}" data-entry="${i}" title="${escHtml(entry.path)}">` +
      `<span class="fb-entry-name-cell"><span class="fb-entry-icon" aria-hidden="true">${waveIconSvg(info.name)}</span>` +
      `<span class="fb-entry-name">${escHtml(entry.name)}${entry.directory ? '/' : ''}</span></span>` +
      `<span class="fb-entry-mtime">${escHtml(formatBrowseMtime(entry.mtime))}</span>` +
      `<span class="fb-entry-size">${escHtml(entry.directory ? '' : formatBrowseBytes(entry.size))}</span>` +
      `</button></div>`;
  }).join('') || `<div class="help-text fb-empty" style="padding:8px">${emptyMsg}</div>`;

  const rowButtons = listing.querySelectorAll('button');
  rowButtons.forEach(button => {
    const idx = Number(button.dataset.entry);
    button.onclick = () => {
      const entry = BROWSE_VISIBLE[idx];
      if (!entry) return;
      BROWSE_SELECTED_INDEX = idx;
      listing.querySelectorAll?.('.fb-row-wrap').forEach((wrap, wi) => wrap.classList?.toggle('selected', wi === idx));
      if (!entry.directory) applyPickedSingleFile(entry, false);
      updateBrowseFooter();
    };
    button.ondblclick = () => {
      const entry = BROWSE_VISIBLE[idx];
      if (!entry) return;
      if (entry.directory) return browseFiles(entry.path, BROWSE_KIND);
      applyPickedSingleFile(entry, BROWSE_KIND !== 'logs');
    };
  });
  listing.querySelectorAll?.('input[data-check]').forEach(box => {
    box.onchange = () => {
      const entry = BROWSE_VISIBLE[Number(box.dataset.check)];
      if (!entry) return;
      if (box.checked) BROWSE_CHECKED.add(entry.path);
      else BROWSE_CHECKED.delete(entry.path);
      updateBrowseFooter();
    };
  });
  updateBrowseFooter();
}

function addSelectedBrowsedEntries() {
  if (BROWSE_KIND !== 'logs' || !BROWSE_CHECKED.size) return;
  for (const entry of BROWSE_ENTRIES) {
    if (BROWSE_CHECKED.has(entry.path)) addLogTarget(entry.path, entry.directory ? 'dir' : 'file');
  }
  BROWSE_CHECKED.clear();
  renderBrowseList();
}

function confirmBrowseSelection() {
  const entry = BROWSE_VISIBLE[BROWSE_SELECTED_INDEX];
  if (!entry) return;
  if (entry.directory) browseFiles(entry.path, BROWSE_KIND);
  else applyPickedSingleFile(entry, true);
}

function handleFileListKey(event) {
  if (event.key === 'Backspace') {
    event.preventDefault();
    browseParent();
    return;
  }
  if (!BROWSE_VISIBLE.length) return;
  const listing = el('fileList');
  const activeEl = typeof document !== 'undefined' ? document.activeElement : null;
  const resolveEntryIdx = node => {
    const viaClosest = node?.closest?.('[data-entry]');
    if (viaClosest && Number.isFinite(Number(viaClosest.dataset?.entry))) return Number(viaClosest.dataset.entry);
    let cur = node;
    while (cur) {
      if (cur.dataset?.entry !== undefined && cur.dataset.entry !== '' && Number.isFinite(Number(cur.dataset.entry))) {
        return Number(cur.dataset.entry);
      }
      cur = cur.parentElement || null;
    }
    return -1;
  };
  let focusedIdx = resolveEntryIdx(event.target);
  if (focusedIdx < 0) focusedIdx = resolveEntryIdx(activeEl);
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const baseIdx = focusedIdx >= 0 ? focusedIdx : BROWSE_SELECTED_INDEX;
    const nextIdx = event.key === 'ArrowDown'
      ? Math.min(BROWSE_VISIBLE.length - 1, baseIdx + 1)
      : Math.max(0, (baseIdx < 0 ? 0 : baseIdx) - 1);
    BROWSE_SELECTED_INDEX = nextIdx;
    const rowWraps = [...(listing?.querySelectorAll?.('.fb-row-wrap') || [])];
    rowWraps.forEach((wrap, wi) => wrap.classList?.toggle?.('selected', wi === nextIdx));
    const entryBtns = [...(listing?.querySelectorAll?.('[data-entry]') || [])];
    const targetBtn = entryBtns.find(b => Number(b.dataset?.entry) === nextIdx) || entryBtns[nextIdx];
    targetBtn?.focus?.();
    (rowWraps[nextIdx] || targetBtn)?.scrollIntoView?.({block: 'nearest'});
    updateBrowseFooter();
  } else if (event.key === 'Enter') {
    const idx = focusedIdx >= 0 ? focusedIdx : BROWSE_SELECTED_INDEX;
    if (idx < 0) return;
    event.preventDefault();
    BROWSE_SELECTED_INDEX = idx;
    const entry = BROWSE_VISIBLE[idx];
    if (!entry) return;
    if (entry.directory) browseFiles(entry.path, BROWSE_KIND);
    else applyPickedSingleFile(entry, BROWSE_KIND !== 'logs');
  }
}

async function browseFiles(path, purpose = BROWSE_KIND || OPEN_MODE, navOpts = {}) {
  if (!CONNECTED || BUSY) return;
  const request = ++BROWSE_REQUEST;
  const mode = purpose === 'logs' ? 'logs' : purpose === 'archive' ? 'archive' : 'work';
  const input = el('browsePath'), browser = el('fileBrowser'), listing = el('fileList');
  const firstVisit = browser.hidden || BROWSE_KIND !== purpose;
  if (BROWSE_KIND !== purpose && !navOpts.historyNav) {
    BROWSE_HISTORY = [];
    BROWSE_HISTORY_INDEX = -1;
  }
  const active = document.activeElement;
  const restoreFocus = firstVisit || listing.contains(active);
  BROWSE_PARENT = BROWSE_DIRECTORY = null;
  BROWSE_SELECTED_INDEX = -1;
  BROWSE_CHECKED.clear();
  el('btnAddFolder').hidden = true;
  if (el('btnAddSelectedLogs')) el('btnAddSelectedLogs').hidden = true;
  if (el('btnSelectBrowseFile')) el('btnSelectBrowseFile').hidden = true;
  updateBrowseNavButtons();
  listing.replaceChildren();
  if (firstVisit) {
    el('browseTitle').textContent = purpose === 'flow' ? 'Browse Flow (.py)' :
      purpose === 'logs' ? 'Browse Logs & Folders' :
      purpose === 'archive' ? 'Browse Saved Archives (.sqlite3)' : 'Browse Work File (.py)';
    toggleBrowsePathEdit(false);
  }
  BROWSE_KIND = purpose;
  const defaultDir = purpose === 'archive'
    ? (STATE.archive_dir && STATE.archive_dir_exists !== false ? STATE.archive_dir : getOpenDraft().directory)
    : getOpenDraft().directory;
  const requestedPath = path ?? defaultDir;
  input.value = requestedPath;
  fieldError(input);
  el('loadError').textContent = '';
  browser.hidden = false;
  if (el('fileBrowserOverlay')?.hidden) openModal('fileBrowserOverlay');
  if (FB_WINDOW_RECT) applyFileBrowserRect(FB_WINDOW_RECT);
  try {
    const r = await fetchJson('/api/files?path=' + encodeURIComponent(requestedPath) + '&mode=' + mode);
    if (request !== BROWSE_REQUEST || el('workOverlay').hidden) return;
    input.value = r.path;
    BROWSE_PARENT = r.parent;
    BROWSE_DIRECTORY = r.path;
    BROWSE_ENTRIES = Array.isArray(r.entries) ? r.entries : [];
    BROWSE_SHORTCUTS = Array.isArray(r.shortcuts) ? r.shortcuts : [];
    if (!navOpts.historyNav && BROWSE_HISTORY[BROWSE_HISTORY_INDEX] !== r.path) {
      BROWSE_HISTORY = BROWSE_HISTORY.slice(0, BROWSE_HISTORY_INDEX + 1);
      BROWSE_HISTORY.push(r.path);
      if (BROWSE_HISTORY.length > 30) BROWSE_HISTORY.shift();
      BROWSE_HISTORY_INDEX = BROWSE_HISTORY.length - 1;
    }
    updateBrowseNavButtons();
    if (el('browseFilterInput')) {
      const folderName = r.path.split(/[\\/]/).filter(Boolean).pop() || r.path;
      el('browseFilterInput').placeholder = `Search ${folderName}…`;
    }
    if (purpose !== 'archive') getOpenDraft().directory = r.path;
    rememberBrowseDirectory(r.path);
    el('btnAddFolder').hidden = purpose !== 'logs';
    renderBrowseBreadcrumbs(r.path);
    renderBrowseShortcuts(BROWSE_SHORTCUTS, r.path);
    toggleBrowsePathEdit(false);
    renderBrowseList();
    if (restoreFocus && (document.activeElement === active || document.activeElement === document.body || el('fileBrowserOverlay')?.contains?.(document.activeElement))) {
      listing.focus?.();
    }
  } catch (error) {
    if (request === BROWSE_REQUEST && !el('workOverlay').hidden) {
      BROWSE_ENTRIES = [];
      BROWSE_VISIBLE = [];
      updateBrowseNavButtons();
      toggleBrowsePathEdit(true);
      showFormError(error, activeOpenFields(), 'browsePathError');
      if (el('browsePathError')) el('browsePathError').hidden = !el('browsePathError').textContent;
    }
  }
}

function browseBack() {
  if (BROWSE_HISTORY_INDEX <= 0) return;
  --BROWSE_HISTORY_INDEX;
  browseFiles(BROWSE_HISTORY[BROWSE_HISTORY_INDEX], BROWSE_KIND, {historyNav: true});
}

function browseForward() {
  if (BROWSE_HISTORY_INDEX < 0 || BROWSE_HISTORY_INDEX >= BROWSE_HISTORY.length - 1) return;
  ++BROWSE_HISTORY_INDEX;
  browseFiles(BROWSE_HISTORY[BROWSE_HISTORY_INDEX], BROWSE_KIND, {historyNav: true});
}

function refreshBrowseFiles() {
  if (BROWSE_DIRECTORY != null) browseFiles(BROWSE_DIRECTORY, BROWSE_KIND, {historyNav: true});
  else browseFiles(undefined, BROWSE_KIND || OPEN_MODE);
}

function browseParent() {
  if (BROWSE_PARENT && BROWSE_PARENT !== BROWSE_DIRECTORY) browseFiles(BROWSE_PARENT, BROWSE_KIND);
}

function archiveCliCommand(pathText) {
  const target = (pathText || '').trim() || '<archive.sqlite3>';
  return `rpk-wave --archive "${target.replace(/"/g, '\\"')}" --open`;
}

function updateArchiveCliHint() {
  const cmdNode = el('archiveOpenCliCmd');
  if (!cmdNode) return;
  const typed = (el('archivePath')?.value || '').trim();
  const fallback = ARCHIVE_CATALOG[0]?.path || '';
  cmdNode.textContent = archiveCliCommand(typed || fallback);
  const current = typed;
  el('archiveRecentList')?.querySelectorAll?.('.archive-recent-row').forEach((rowEl, idx) => {
    const item = ARCHIVE_CATALOG[idx];
    rowEl.classList?.toggle('selected', !!item && item.path === current);
  });
}

function updateArchiveBusyUi() {
  const busy = hasUnfinishedBatch(STATE);
  if (el('archiveBusyHint')) el('archiveBusyHint').hidden = !busy;
  if (el('btnOpenArchive')) {
    el('btnOpenArchive').disabled = !CONNECTED || BUSY || busy;
    el('btnOpenArchive').title = busy
      ? 'Active batch has unfinished jobs or sources; use the command above in a new tab.'
      : 'Open saved SQLite archive in read-only mode';
  }
  updateArchiveCliHint();
}

async function copyArchiveOpenCmd() {
  const cmd = (el('archiveOpenCliCmd')?.textContent || '').trim();
  if (!cmd) return;
  let copied = false;
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(cmd);
      copied = true;
    } catch { /* fallback below */ }
  }
  if (!copied && typeof document.createElement === 'function' && document.body?.appendChild) {
    const box = document.createElement('textarea');
    box.value = cmd;
    box.setAttribute?.('readonly', 'true');
    box.style.position = 'fixed';
    box.style.left = '-9999px';
    box.style.opacity = '0';
    document.body.appendChild(box);
    box.focus?.({ preventScroll: true });
    box.setSelectionRange?.(0, cmd.length);
    try { copied = !!document.execCommand?.('copy'); } catch { copied = false; }
    document.body.removeChild(box);
  }
  if (copied) recordFeedback('Copied archive command to clipboard.');
  else recordFeedback('Copy failed; select the command manually.', 'warn');
}

function archiveRowKey(row) {
  return row ? String(row.run_id || row.path || '') : '';
}

function renderRecentArchivesList() {
  const list = el('archiveRecentList');
  if (!list) return;
  if (!ARCHIVE_CATALOG.length) {
    ARCHIVE_DELETE_CONFIRM_ID = null;
    list.innerHTML = '<div class="help-text" style="padding:8px">No saved archives found in archive directory yet.</div>';
    updateArchiveCliHint();
    return;
  }
  const liveRunId = STATE.archive?.run_id || null;
  list.innerHTML = ARCHIVE_CATALOG.map((row, idx) => {
    const rowKey = archiveRowKey(row);
    const name = row.filename || `${row.run_id}.sqlite3`;
    if (ARCHIVE_DELETE_CONFIRM_ID && ARCHIVE_DELETE_CONFIRM_ID === rowKey) {
      return `<div class="archive-recent-row archive-confirm-row" role="group" aria-label="Confirm archive deletion">` +
        `<span class="archive-confirm-text">Delete <strong>${escHtml(name)}</strong> permanently?</span>` +
        `<span class="archive-confirm-actions">` +
        `<button type="button" class="danger" data-archive-confirm="${idx}">Confirm Delete</button>` +
        `<button type="button" data-archive-cancel="${idx}">Cancel</button>` +
        `</span></div>`;
    }
    const outcome = row.run_id === liveRunId ? 'LIVE' : (row.run_outcome || (row.closed_cleanly ? 'CLOSED' : 'UNCLOSED'));
    const srcLabel = row.source_path ? (row.source_path.split(/[\\/]/).pop() || row.source_path) : 'No source path';
    const stamp = formatBrowseMtime(row.closed_at || row.created);
    const sizeStr = formatBrowseBytes(row.size);
    const canDelete = row.run_id !== liveRunId && row.run_id !== STATE.opened_run_id && row.closed_cleanly !== false;
    const metaParts = [stamp, sizeStr, srcLabel].filter(Boolean).join(' · ');
    const delBtn = canDelete
      ? `<button type="button" class="icon-button archive-delete-btn" data-archive-delete="${idx}" aria-label="Delete ${escHtml(name)}" title="Delete archive ${escHtml(name)}${sizeStr ? ' (' + escHtml(sizeStr) + ')' : ''}">${waveIconSvg('trash') || '×'}</button>`
      : '';
    return `<div class="archive-recent-row">` +
      `<button type="button" class="archive-recent-item" data-archive-idx="${idx}" title="${escHtml(row.path || '')}">` +
      `<div class="archive-recent-main"><span class="archive-recent-name">${escHtml(name)}</span>` +
      `<span class="archive-recent-meta">${escHtml(metaParts)}</span></div>` +
      `<span class="archive-recent-badges"><span class="pill">${escHtml(outcome)}</span></span></button>${delBtn}</div>`;
  }).join('');
  list.querySelectorAll?.('button[data-archive-idx]').forEach(btn => {
    const item = ARCHIVE_CATALOG[Number(btn.dataset.archiveIdx)];
    if (!item) return;
    btn.onclick = () => {
      el('archivePath').value = item.path || '';
      fieldError(el('archivePath'));
      updateArchiveCliHint();
    };
    btn.ondblclick = () => {
      el('archivePath').value = item.path || '';
      fieldError(el('archivePath'));
      updateArchiveCliHint();
      if (!hasUnfinishedBatch(STATE)) openArchiveFile();
    };
  });
  list.querySelectorAll?.('button[data-archive-delete]').forEach(btn => {
    const item = ARCHIVE_CATALOG[Number(btn.dataset.archiveDelete)];
    if (item) {
      btn.onclick = () => {
        ARCHIVE_DELETE_CONFIRM_ID = archiveRowKey(item);
        renderRecentArchivesList();
        const cancelBtn = [...(list.querySelectorAll?.('button[data-archive-cancel]') || [])].find(
          b => Number(b.dataset?.archiveCancel) === Number(btn.dataset.archiveDelete)
        );
        cancelBtn?.focus?.();
      };
    }
  });
  list.querySelectorAll?.('button[data-archive-confirm]').forEach(btn => {
    const item = ARCHIVE_CATALOG[Number(btn.dataset.archiveConfirm)];
    if (item) btn.onclick = () => deleteRecentArchive(item);
  });
  list.querySelectorAll?.('button[data-archive-cancel]').forEach(btn => {
    btn.onclick = () => {
      const idx = Number(btn.dataset.archiveCancel);
      ARCHIVE_DELETE_CONFIRM_ID = null;
      renderRecentArchivesList();
      const restored = [...(list.querySelectorAll?.('button[data-archive-delete]') || [])].find(
        b => Number(b.dataset?.archiveDelete) === idx
      );
      restored?.focus?.();
    };
  });
  updateArchiveCliHint();
}

async function deleteRecentArchive(row) {
  if (!row || !row.run_id || !CONNECTED || BUSY) return;
  ARCHIVE_DELETE_CONFIRM_ID = null;
  if (el('loadError')) el('loadError').textContent = '';
  BUSY = true;
  updateControls();
  let deleted = false;
  try {
    const res = await fetchJson('/api/archive/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({workspace_id: STATE.workspace_id, run_id: row.run_id})
    });
    if (el('archivePath') && el('archivePath').value.trim() === row.path) {
      el('archivePath').value = '';
      fieldError(el('archivePath'));
    }
    recordFeedback(res.message || `Deleted archive ${row.filename || row.run_id}.`);
    deleted = true;
  } catch (error) {
    const msg = 'Delete archive failed: ' + (error?.message || 'request failed');
    if (el('loadError')) el('loadError').textContent = msg;
    recordFeedback(msg, 'error');
  } finally {
    BUSY = false;
    updateControls();
  }
  if (deleted) await loadRecentArchives();
  else renderRecentArchivesList();
}

async function loadRecentArchives() {
  ARCHIVE_CATALOG_CONTROLLER?.abort?.();
  ARCHIVE_CATALOG_CONTROLLER = null;
  ARCHIVE_DELETE_CONFIRM_ID = null;
  const reqEpoch = ++ARCHIVE_CATALOG_EPOCH;
  const list = el('archiveRecentList');
  const countEl = el('archiveRecentCount');
  if (!list) return;
  if (!CONNECTED || !STATE.workspace_id) {
    if (countEl) countEl.textContent = '';
    list.innerHTML = '<div class="help-text" style="padding:8px">Connect to Wave to list recent archives.</div>';
    return;
  }
  if (countEl) countEl.textContent = '';
  list.innerHTML = '<div class="help-text" style="padding:8px">Loading recent archives…</div>';
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  ARCHIVE_CATALOG_CONTROLLER = controller;
  try {
    const r = await fetchJson(
      '/api/archive?view=runs&all=true&limit=20&workspace_id=' + encodeURIComponent(STATE.workspace_id),
      controller ? {signal: controller.signal} : undefined
    );
    if (reqEpoch !== ARCHIVE_CATALOG_EPOCH || el('workOverlay').hidden || OPEN_MODE !== 'archive') return;
    ARCHIVE_CATALOG = Array.isArray(r.rows) ? r.rows : [];
    const totalFiles = Number(r.total_files || 0);
    if (countEl) {
      countEl.textContent = totalFiles > ARCHIVE_CATALOG.length
        ? (r.partial
          ? `Showing ${ARCHIVE_CATALOG.length} newest of ${totalFiles}`
          : `Showing ${ARCHIVE_CATALOG.length} of ${totalFiles}`)
        : (ARCHIVE_CATALOG.length ? `${ARCHIVE_CATALOG.length} saved` : '');
    }
    renderRecentArchivesList();
  } catch (error) {
    if (error?.name === 'AbortError' || error?.isAborted || reqEpoch !== ARCHIVE_CATALOG_EPOCH) return;
    if (!el('workOverlay').hidden && OPEN_MODE === 'archive') {
      list.innerHTML = `<div class="error-text" style="padding:8px">${escHtml(error.message || 'Could not load recent archives')}</div>`;
    }
  } finally {
    if (ARCHIVE_CATALOG_CONTROLLER === controller) ARCHIVE_CATALOG_CONTROLLER = null;
  }
}

async function openArchiveFile() {
  if (!CONNECTED || BUSY) return;
  clearFormErrors('workOverlay', 'loadError');
  if (hasUnfinishedBatch(STATE)) {
    updateArchiveBusyUi();
    el('btnCopyArchiveCmd')?.focus?.();
    return;
  }
  const path = el('archivePath').value.trim();
  if (!path) {
    showFieldError(el('archivePath'), 'Enter a saved archive (.sqlite3) path.');
    return;
  }
  await loadWorkspacePreview('/api/archive/open', {path});
}

async function loadWorkFile() {
  if (!CONNECTED || BUSY) return;
  clearFormErrors('workOverlay', 'loadError');
  const path = el('workPath').value.trim();
  if (!path) {
    showFieldError(el('workPath'), 'Enter a Python work file path.');
    return;
  }
  await loadWorkspacePreview('/api/work/load', {path});
}

async function loadLogsSource() {
  if (!CONNECTED || BUSY) return;
  clearFormErrors('workOverlay', 'loadError');
  if (el('logTargetAddInput')?.value.trim()) addLogTargetFromInput();
  const targets = readLogTargets();
  const globs = el('logGlobs').value.split(',').map(pattern => pattern.trim());
  const emptyGlob = globs.some(pattern => !pattern);
  if (!targets.length || emptyGlob) {
    if (!targets.length) fieldError(el('logTargets'), 'Add a log file or folder.');
    if (emptyGlob) {
      if (el('logFolderOptions')) el('logFolderOptions').hidden = false;
      fieldError(el('logGlobs'), 'Enter nonempty glob patterns.');
    }
    const targetEmptyFocus = canFocus(el('logTargetAddInput')) ? el('logTargetAddInput') : el('logTargets');
    (targets.length ? el('logGlobs') : targetEmptyFocus).focus();
    return;
  }
  const once = el('logsMode').value === 'static';
  const payload = {targets, once, globs, recursive: el('logRecursive').checked};
  if (!once) payload.start = el('logStart').value;
  const flow = el('logFlowPath').value.trim();
  if (flow) payload.flow_path = flow;
  await loadWorkspacePreview('/api/logs/load', payload);
}

async function loadWorkspacePreview(route, payload) {
  const mode = OPEN_MODE, epoch = OPEN_DIALOG_EPOCH;
  const isCurrentDialog = () => !el('workOverlay').hidden && epoch === OPEN_DIALOG_EPOCH && mode === OPEN_MODE;
  BUSY = true;
  ++BROWSE_REQUEST;
  el('loadError').textContent = 'Loading and validating…';
  updateControls();
  try {
    // Opening a replacement is also allowed from a read-only archive.
    const r = await fetchJson(route, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        workspace_id: STATE.workspace_id,
        ...payload
      })
    });
    ++REQUEST_ID;
    resetWorkspaceSelection();
    if (route === '/api/work/load') rememberLoadedPaths('work', [payload.path]);
    else if (route === '/api/logs/load') rememberLoadedPaths('logs', payload.targets);
    else if (route === '/api/archive/open') rememberLoadedPaths('archive', [payload.path]);
    if (r.warning?.kind === 'previous_workspace_cleanup') addWarning('Previous workspace cleanup', r.warning.message);
    await fetchState();
    if (route === '/api/work/reload' && WORKSPACE_LAYOUT === 'charts') setWorkspaceLayout('jobs');
    if (isCurrentDialog()) closeWorkFile();
    else if (!el('workOverlay').hidden) {
      el('loadError').textContent = '';
      renderRecentPaths(OPEN_MODE);
    }
    recordFeedback(r.message);
  } catch (e) {
    if (isCurrentDialog()) showFormError(e, activeOpenFields(), 'loadError');
    else {
      const prefix = route === '/api/work/reload' ? 'Reload failed: ' : 'Workspace load failed: ';
      addWarning(prefix.slice(0, -2), e.message);
      recordFeedback(prefix + e.message, 'error');
    }
  } finally {
    BUSY = false;
    updateControls();
  }
}
document.addEventListener('input', event => {
  if (event.target.id === 'logTargetAddInput') fieldError(el('logTargets'));
  if (event.target.getAttribute?.('aria-invalid') === 'true') fieldError(event.target);
});
setupMonitorLayout();
pollState();
