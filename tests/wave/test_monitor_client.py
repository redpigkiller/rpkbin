"""Focused behavior checks for the embedded job table and global controls.

DOM boundary fakes exercise cell identity and logical focus recovery; real
browser acceptance remains necessary for native focus and responsive geometry.
"""

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


WAVE_DIR = Path(__file__).parents[2] / "rpkbin" / "wave"
APP_JS = WAVE_DIR / "static" / "app.js"
INDEX_HTML = WAVE_DIR / "static" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def run_page_checks(checks):
    source = APP_JS.read_text(encoding="utf-8")
    script, removed = re.subn(
        r"\nsetupMonitorLayout\(\);\s*\npollState\(\);\s*$", "\n", source
    )
    assert removed == 1
    script = (WAVE_DIR / "jobs.js").read_text(encoding="utf-8") + "\n" + script
    harness = (
        "const vm = require('node:vm');\n"
        "const assert = require('node:assert/strict');\n"
        "const document = { addEventListener() {} }; const getComputedStyle = () => ({visibility:'visible'});\n"
        "vm.runInNewContext("
        + repr(script + "\n" + checks)
        + ", { assert, document, getComputedStyle, window: {}, console, process });\n"
    )
    result = subprocess.run(
        [NODE, "-"],
        input=harness.encode("utf-8"),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode(
        "utf-8", errors="replace"
    )


def test_chart_controls_start_disabled_and_find_value_explains_decimal_input():
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.by_id = {}

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if "id" in attributes:
                self.by_id[attributes["id"]] = (tag, attributes)

    html = INDEX_HTML.read_text(encoding="utf-8")
    parser = Elements()
    parser.feed(html)
    for element_id in (
        "chartZoomIn", "chartZoomOut", "chartPanLeft", "chartPanRight", "chartResetWindow", "chartLastView"
    ):
        tag, attributes = parser.by_id[element_id]
        assert tag == "button"
        assert "disabled" in attributes, f"{element_id} must not be actionable before range data exists"
    for element_id in (
        "chartOverlayCanvas",
        "chartSignalPane",
        "chartContextMenu",
        "chartCursorSummary",
        "chartCursorAInput",
        "chartCursorBInput",
        "chartHScroll",
        "chartHScrollThumb",
        "chartSelectedPath",
        "chartHierarchyTree",
        "chartAddSignals",
        "chartSelectAllSignals",
        "chartHierToggle",
        "chartControlSlot",
        "chartOpenRcBtn",
        "chartSaveRcBtn",
        "chartExportVcdBtn",
        "chartParamModal",
        "chartFileModal",
        "chartFileBack",
        "chartFileForward",
        "chartFileUp",
        "chartFileRefresh",
        "chartColRatioBtn",
        "chartCutSignal",
        "chartCopySignal",
        "chartPasteSignal",
        "chartTimeUnit",
        "chartBrowserSplitter",
        "chartScopeRecursive",
        "chartSignalCase",
        "chartSignalWord",
        "chartSignalRegex",
    ):
        assert element_id in parser.by_id
    assert "Value @ A" not in html
    assert 'class="wf-col-val"><span>Value</span>' in html
    assert 'id="chartViewSelectWrap">' in html
    assert "chartPaneAddGroup" not in parser.by_id
    assert 'id="chartExportVcdBtn"' in html
    vcd_pos = html.index('id="chartExportVcdBtn"')
    sig_pos = html.index('id="chartSignalsToggle"')
    assert 'class="wf-toolbar-sep"' in html[vcd_pos:sig_pos]
    assert "chartFindLane" not in parser.by_id
    assert "chartDensityToggle" not in parser.by_id
    assert "chartOpenLog" not in parser.by_id
    tag, attributes = parser.by_id["chartFindValue"]
    assert tag == "input"
    assert attributes["placeholder"] == "Value (dec/0x/0b/X/Z)"


def test_exact_integer_strings_sort_with_safe_numbers_and_fractions():
    run_page_checks(
        r"""
const values = ['9007199254740993', 1.5, '9007199254740992',
  9007199254740991, '-9007199254740992', '-9007199254740993', -9007199254740991];
assert.deepStrictEqual(values.sort(compareJobValues).map(String), [
  '-9007199254740993', '-9007199254740992', '-9007199254740991',
  '1.5', '9007199254740991', '9007199254740992', '9007199254740993'
]);
assert.equal(compareJobValues('2', 2), 0);
assert.equal(compareJobValues('-2', -1.5), -1);
assert.equal(compareJobValues('2', 1.5), 1);
assert.ok(compareJobValues('worker10', 'worker2') > 0);
"""
    )


def test_detail_tabs_have_one_owner_for_keyboard_activation_and_panels():
    run_page_checks(r"""
const nodes = {};
document.getElementById = id => nodes[id] ||= {
  attributes:{}, setAttribute(key,value){this.attributes[key]=value},
  focus(){document.activeElement=this}, scrollIntoView(){this.revealed=true}
};
renderLogs=()=>{}; markProgrammaticScroll=()=>{};
assert.equal(DETAIL_TABS.length, 5);
assert.equal(DETAIL_TABS.some(([name])=>name==='charts'), false);
readerTabKey({key:'ArrowRight',preventDefault(){}},'analysisParsedTab');
assert.equal(DETAIL_VIEW,'analysis');
assert.equal(document.activeElement,el('analysisResultsTab'));
assert.equal(el('flowWorkspaceView').hidden,false);
assert.equal(el('logView').hidden,true);
assert.equal(el('analysisFilterControls').hidden,true);
assert.equal(DETAIL_TABS.filter(([,id])=>el(id).attributes['aria-selected']==='true').length,1);
readerTabKey({key:'End',preventDefault(){}},'analysisResultsTab');
assert.equal(DETAIL_VIEW,'details');
assert.equal(el('flowWorkspaceView').attributes['aria-labelledby'],'btnDetailsTab');
readerTabKey({key:'Home',preventDefault(){}},'btnDetailsTab');
assert.equal(DETAIL_VIEW,'log');
assert.equal(el('analysisFilterControls').hidden,false);
assert.equal(el('analysisLogTab').tabIndex,0);
""")


def test_log_font_zoom_preserves_visible_row_anchor_and_other_preferences():
    run_page_checks(r"""
const boxes={};
for(const id of ['analysisRows','analysisContextRows']){
 const box={scrollTop:0,getBoundingClientRect:()=>({top:10})};
 const row={getBoundingClientRect:()=>({top:DISPLAY_PREFS.logFontSize*2-box.scrollTop,bottom:DISPLAY_PREFS.logFontSize*2-box.scrollTop+20})};
 box.querySelectorAll=()=>[row];boxes[id]=box;
}
document.getElementById=id=>boxes[id];
let saved=0;saveDisplayPreferences=()=>saved++;syncDisplayPrefsControls=()=>{};applyDisplayPreferences=()=>{};
const split=DISPLAY_PREFS.splitWidth;setLogFontSize(18);
assert.equal(DISPLAY_PREFS.logFontSize,18);assert.equal(DISPLAY_PREFS.splitWidth,split);
assert.equal(boxes.analysisRows.scrollTop,10);assert.equal(boxes.analysisContextRows.scrollTop,10);
setLogFontSize(99);assert.equal(DISPLAY_PREFS.logFontSize,18);assert.equal(saved,1);
""")


def test_popup_outside_dismiss_keeps_parent_and_closes_other_menus():
    run_page_checks(r"""
const filterTarget={},nestedTarget={},outside={};
const filters={open:true,contains:t=>t===filterTarget||t===nestedTarget};
const queue={open:true,contains:()=>false};
document.querySelectorAll=()=>[filters,queue].filter(n=>n.open);
dismissControlMenusOutside(nestedTarget);
assert.equal(filters.open,true,'nested field choices do not dismiss their parent');
assert.equal(queue.open,false,'other open command menu closes');
dismissControlMenusOutside(outside);assert.equal(filters.open,false);
""")


def test_toast_expires_without_clearing_failures_or_workspace_warning():
    run_page_checks(r"""
const nodes = {};
document.getElementById=id=>nodes[id] ??= {
  hidden:true, dataset:{}, textContent:'', innerHTML:'',
  contains:()=>false, classList:{toggle(){}},
  insertAdjacentHTML(_, markup){this.innerHTML+=markup},
  replaceChildren(){this.innerHTML=''}
};
const timers=new Map();let serial=0;
setTimeout=(callback,ms)=>{timers.set(++serial,{callback,ms});return serial};
clearTimeout=id=>timers.delete(id);
CONNECTED = true;
STATE.watcher_error = '<long failure>';
addWarning('Previous workspace cleanup', 'old archive could not close');
const results = [{name:'alpha', status:'failed', reason:'<signal failure>', ok:false}];
report({action:'stop', results});
assert.equal(el('actionToast').hidden, true);
assert.equal(el('btnLastReport').hidden, false);
assert.equal(el('btnLastReport').textContent, 'Report (1)');
assert.match(el('actionReport').innerHTML, /&lt;signal failure&gt;/);
const failedReport = el('actionReport').innerHTML;
report({action:'stop', results:[{name:'beta',status:'accepted',reason:'Stopped',ok:true}]});
assert.match(el('toastText').textContent, /1 succeeded/);
assert.equal(el('actionToast').hidden,false);
assert.equal(timers.get(window.feedbackTimer).ms,4000);
timers.get(window.feedbackTimer).callback();
assert.equal(el('actionToast').hidden,true);
assert.equal(el('btnLastReport').hidden,false);
assert.equal(el('actionReport').innerHTML, failedReport);
assert.match(el('connectionNotice').textContent, /watcher_error.*Previous workspace cleanup/);
assert.match(el('statusWarningText').textContent, /old archive could not close/);
acknowledgeWarnings();
assert.equal(el('connectionNotice').textContent, 'watcher_error');
acknowledgeReports();
assert.equal(el('btnLastReport').hidden,true);
assert.equal(el('actionReport').innerHTML,'');
""")


def test_workbench_shortcut_and_escape_respect_context_and_modal_focus():
    run_page_checks(r"""
const nodes = {};
function node(id) {
 return nodes[id] ??= {id,isConnected:true,hidden:false,tabIndex:0,
   getClientRects(){return this.hidden ? [] : [{}]},
   matches(){return false},closest(){return null},
   focus(){document.activeElement=this}};
}
document.getElementById=node;
document.querySelector=()=>null;
const drive = (key,target,options={}) => {
 document.activeElement=target;
 const event={key,target,preventDefault(){this.defaultPrevented=true},stopImmediatePropagation(){},...options};
 handleWorkbenchKey(event);
 return event;
};
const job=node('job');
job.closest = selector => selector==='#paneLeft' ? node('paneLeft') : null;
assert.equal(drive('/',job).defaultPrevented,true);
assert.equal(document.activeElement,node('jobSearch'));
assert.equal(drive('/',job,{isComposing:true}).defaultPrevented,undefined);
assert.equal(drive('/',job,{ctrlKey:true}).defaultPrevented,undefined);
assert.equal(drive('/',job,{shiftKey:true}).defaultPrevented,undefined);
job.isContentEditable=true;
assert.equal(drive('/',job).defaultPrevented,undefined);
job.isContentEditable=false;
const output=node('raw output');
output.closest = selector => selector==='#logView,#readerToolbar' ? node('logView') : null;
drive('/',output);
assert.equal(document.activeElement,node('analysisQuery'));
DETAIL_VIEW='parsed';
assert.equal(drive('/',output).defaultPrevented,undefined);

const menu=node('queueMenu'), summary=node('more'), settings=node('btnSettings');
menu.open=true; menu.matches=s=>s==='details'; menu.querySelector=()=>summary;
settings.closest=s=>s==='details' || (s==='details[open]' && menu.open) ? menu : null;
document.querySelector=s=>s==='details[open]' && menu.open ? menu : null;
assert.equal(drive('/',job).defaultPrevented,undefined);
drive('Escape',settings);
assert.equal(document.activeElement,summary);
assert.equal(menu.open,false);

const overlay=node('settingsOverlay'), first=node('first'), last=node('last'), dialog=node('dialog');
overlay.hidden=true;
overlay.querySelectorAll=()=>[first,last];
overlay.querySelector=s=>s==='[role="dialog"]' ? dialog : null;
document.activeElement=settings;
openModal('settingsOverlay');
assert.equal(document.activeElement,first);
assert.equal(drive('/',job).defaultPrevented,undefined);
drive('Tab',job,{shiftKey:true});
assert.equal(document.activeElement,last);
drive('Tab',job);
assert.equal(document.activeElement,first);
drive('Tab',first,{shiftKey:true});
assert.equal(document.activeElement,last);
overlay.querySelectorAll=()=>[];
drive('Tab',last);
assert.equal(document.activeElement,dialog);
drive('Escape',dialog);
assert.equal(overlay.hidden,true);
assert.equal(document.activeElement,summary);
settings.isConnected=false;
summary.isConnected=false;
document.activeElement=settings;
openModal('settingsOverlay');
drive('Escape',dialog);
assert.equal(document.activeElement,node('btnOpenWorkspace'));
""")


def test_jobs_empty_state_actions_follow_workspace_context():
    run_page_checks(r"""
assert.match(emptyJobsMarkup(), /openWorkFile\('work'\).*Open Work File/);
assert.match(emptyJobsMarkup(), /openWorkFile\('logs'\).*Open Logs or Folder/);
STATE.sources={active_watchers:1,configured_watchers:1};
assert.match(emptyJobsMarkup(), /waiting for new logs/);
STATE.jobs=[{}];
assert.match(emptyJobsMarkup(), /Clear filters/);
STATE.jobs=[];STATE.opened_run_id='saved';
assert.equal(emptyJobsMarkup(), 'No jobs in this saved run.');
""")


def test_reader_tabs_keyboard_reaches_details_and_preserves_focus():
    run_page_checks(r"""
const calls=[], focused=[];
setDetailView=view=>calls.push(view);
document.getElementById=id=>({focus(){focused.push(id)}});
let prevented=0;const press=(key,id)=>readerTabKey({key,preventDefault(){prevented++}},id);
press('ArrowRight','analysisParsedTab');
press('ArrowRight','analysisResultsTab');
press('ArrowRight','automationTab');
press('ArrowRight','btnDetailsTab');
press('End','analysisLogTab');
press('Home','btnDetailsTab');
assert.deepEqual(calls,['analysis','automation','details','log','details','log']);
assert.deepEqual(focused,['analysisResultsTab','automationTab','btnDetailsTab','analysisLogTab','btnDetailsTab','analysisLogTab']);
assert.equal(prevented,6);
""")


def test_all_job_filters_does_not_clear_analysis_selection_sort_or_checked_targets():
    run_page_checks(r"""
const nodes={stateFilter:{value:'PENDING'},resultFilter:{value:'FAIL'},warningFilter:{checked:true},
jobSearch:{value:'worker'},analysisQuery:{value:'state|aagc'}};
document.getElementById=id=>nodes[id];let renders=0;renderMonitor=()=>renders++;
CHECKED_JOBS.add('worker');SELECTED_JOB_ID='worker';SORT_KEY='aagc';SORT_ASC=false;
clearFilters();
assert.equal(nodes.stateFilter.value,'');assert.equal(nodes.resultFilter.value,'');
assert.equal(nodes.warningFilter.checked,false);assert.equal(nodes.jobSearch.value,'');
assert.equal(nodes.analysisQuery.value,'state|aagc');assert.equal(SELECTED_JOB_ID,'worker');
assert.equal(CHECKED_JOBS.has('worker'),true);assert.equal(SORT_KEY,'aagc');assert.equal(SORT_ASC,false);
assert.equal(renders,1);
""")


def test_selecting_job_preserves_current_reader_view_and_same_job_does_not_reload():
    run_page_checks(r"""
(async()=>{
const called=[];document.getElementById=()=>({hidden:false});SELECTED_JOB_ID='old';DETAIL_VIEW='details';
setDetailView=view=>called.push(view);renderDetail=()=>{};updateControls=()=>{};fetchState=async()=>{};
await selectJob('fresh');await selectJob('fresh');
assert.deepEqual(called,['details']);assert.equal(SELECTED_JOB_ID,'fresh');
// Explicit navigation (for example right-clicking the selected job) still works.
DETAIL_VIEW='log';await selectJob('fresh','details');
assert.deepEqual(called,['details','details']);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_inspect_mode_owns_jobs_collapse_and_overview_cannot_collapse():
    run_page_checks(r"""
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{hidden:false,scrollTop:42,clientHeight:200,attributes:{},dataset:{},
  classes:new Set(),classList:{toggle(name,value){value?node(id).classes.add(name):node(id).classes.delete(name)},
  contains(name){return node(id).classes.has(name)}},
  setAttribute(k,v){this.attributes[k]=v},appendChild(child){child.parent=id}});return nodes.get(id)}
document.getElementById=node;document.body=node('body');syncNarrowTableClass=()=>{};
node('btnFocusAnalysis').parent='jobListToggleReading';
setWorkspaceLayout('analysis');
assert.equal(node('layoutInspect').attributes['aria-pressed'],'true');
assert.equal(node('layoutCharts').attributes['aria-pressed'],'false');
assert.equal(node('btnFocusAnalysis').attributes['aria-pressed'],'true');
assert.equal(node('btnFocusAnalysis').parent,'jobListToggleReading');
setWorkspaceLayout('jobs');
assert.equal(node('btnFocusAnalysis').hidden,true);
assert.equal(node('layoutJobs').attributes['aria-pressed'],'true');
assert.equal(node('layoutInspect').attributes['aria-pressed'],'false');
toggleAnalysisFocus();assert.equal(WORKSPACE_LAYOUT,'jobs');
setWorkspaceLayout('charts');
assert.equal(node('btnFocusAnalysis').hidden,true);
assert.equal(node('layoutCharts').attributes['aria-pressed'],'true');
assert.equal(node('chartsView').hidden,false);
toggleAnalysisFocus();assert.equal(WORKSPACE_LAYOUT,'charts');
setWorkspaceLayout('split');
assert.equal(node('btnFocusAnalysis').hidden,false);
assert.equal(node('btnFocusAnalysis').attributes['aria-pressed'],'false');
assert.equal(node('btnFocusAnalysis').parent,'jobListToggleReading');
assert.equal(node('chartsView').hidden,true);
toggleAnalysisFocus();assert.equal(WORKSPACE_LAYOUT,'analysis');
assert.equal(node('layoutInspect').attributes['aria-pressed'],'true');
assert.equal(node('analysisRows').scrollTop,42);
""")


def test_wave_u4_phase2_dom_contracts_and_c3_shortcuts():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for required_id in (
        "layoutJobs", "layoutInspect", "layoutCharts", "stopAllGroup", "btnStopAll",
        "btnMenuStopAll", "btnForceAll", "forceAllConfirm", "btnForceAllConfirm",
        "btnToggleFind", "logOptionsMenu", "analysisFollow", "vscodeFindWidget",
        "analysisQuery", "analysisCase", "analysisWord", "analysisRegex",
        "analysisMatchCount", "analysisFindPrev", "analysisFindNext",
        "analysisFilterMode", "analysisCancel", "btnCloseFind", "logWrapToggle",
        "logAnsiToggle", "chartsHostStrip", "chartJobSelect", "chartRunSelect",
        "btnSecRetention", "btnSecDisplay", "btnApplyRetention",
        "displaySavedStatus", "attemptStatic", "btnQuickInterrupt"
    ):
        assert f'id="{required_id}"' in html, f"Missing required ID {required_id}"
    for removed_id in (
        "chartsTab", "btnJobPriority", "btnColumns", "btnSecExecution",
        "btnSecAll", "columnsOverlay", "logFilterInput", "queueStatus", "workspaceCwd",
        "chartDensityToggle", "chartOpenLog"
    ):
        assert f'id="{removed_id}"' not in html, f"Removed ID {removed_id} must not be present"
    assert "<span>Rerun</span>" in html
    assert 'data-wave-icon="reload"' in html
    assert 'step="1"' in html

    run_page_checks(r"""
const nodes = new Map(), chartRefreshes = [];
function node(id){return nodes.get(id) ?? (nodes.set(id,{id,hidden:false,tabIndex:0,isConnected:true,
  attributes:{},classes:new Set(),dataset:{},
  getClientRects(){return this.hidden?[]:[{}]},
  classList:{toggle(k,v){v?this.c.add(k):this.c.delete(k)},contains(k){return this.c.has(k)},c:new Set()},
  setAttribute(k,v){this[k]=v;this.attributes[k]=v},
  matches(){return false},closest(){return null},
  focus(){document.activeElement=this},select(){this.selected=true}}),nodes.get(id))}
document.getElementById = node;
document.body = node('body');
document.querySelector = () => null;
syncNarrowTableClass = () => {};
globalThis.ChartWorkspace = { refresh(d) { chartRefreshes.push(d); } };
STATE.detail = { id: 'j1', attempt_id: 'a1' };
setWorkspaceLayout('charts');
assert.equal(chartRefreshes.at(-1).id, 'j1');
setWorkspaceLayout('split');
assert.equal(chartRefreshes.at(-1), null);

// Ctrl+F opens vscodeFindWidget ONLY inside Inspect Log (#logView/#readerToolbar/#vscodeFindWidget); '[' toggles jobs collapse; '/' on body focuses jobSearch
DETAIL_VIEW = 'log';
node('vscodeFindWidget').hidden = true;
handleWorkbenchKey({key:'f', ctrlKey:true, target:node('body'), preventDefault(){}, stopImmediatePropagation(){}});
assert.equal(node('vscodeFindWidget').hidden, true);
const logTarget = { closest(sel) { return sel.includes('#logView') ? node('logView') : null; } };
handleWorkbenchKey({key:'f', ctrlKey:true, target:logTarget, preventDefault(){}, stopImmediatePropagation(){}});
assert.equal(node('vscodeFindWidget').hidden, false);
assert.equal(document.activeElement, node('analysisQuery'));

handleWorkbenchKey({key:'[', target:node('body'), preventDefault(){}, stopImmediatePropagation(){}});
assert.equal(WORKSPACE_LAYOUT, 'analysis');
handleWorkbenchKey({key:'[', target:node('body'), preventDefault(){}, stopImmediatePropagation(){}});
assert.equal(WORKSPACE_LAYOUT, 'split');

handleWorkbenchKey({key:'/', target:document.body, preventDefault(){}, stopImmediatePropagation(){}});
assert.equal(document.activeElement, node('jobSearch'));
""")


def test_save_failure_is_visible_in_job_row_without_changing_result():
    run_page_checks(
        r"""
const detail = { id: 'job-a', attempt_id: 'attempt-a', status: 'DONE', result: 'PASS',
  name: 'saved', data: { counter: '9007199254740993' }, warning_count: 0, error_count: 0,
  log_file_error: { path: '<bad-path>', message: 'SAVE_FAILURE_UNIQUE', count: 12 },
  diagnostics: [{ source: 'log_file', message: 'SAVE_FAILURE_UNIQUE' },
    { source: 'notification', message: 'OTHER_DIAGNOSTIC' }] };
const markup = jobRowMarkup(detail, ['name', 'result']);
assert.ok(markup.includes('Log save failed'));
assert.ok(!markup.includes('9007199254740993')); // Flow values are outside Jobs columns.
assert.ok(markup.includes('result-PASS'));
assert.ok(markup.includes('<td class="col-filler"></td>'));
assert.equal(detail.result, 'PASS');
delete detail.log_file_error;
assert.ok(!jobRowMarkup(detail, []).includes('Log save failed'));
"""
    )


def test_opening_job_from_overview_selects_target_before_showing_inspector():
    run_page_checks(r"""
(async()=>{
const calls=[];SELECTED_JOB_ID='previous';
selectJob=async id=>{calls.push(['select',id]);SELECTED_JOB_ID=id};
setWorkspaceLayout=mode=>calls.push(['layout',mode,SELECTED_JOB_ID]);
await inspectJob('clicked');
assert.deepEqual(calls,[['select','clicked'],['layout','split','clicked']]);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_global_controls_filter_targets_but_checked_controls_keep_mixed_results():
    run_page_checks(
        r"""
const job = (id, status) => ({ id, name: id, status, attempt_id: id + '-attempt',
  submission_id: id + '-submission', attempt_number: 3 });
STATE.jobs = [job('pending', 'PENDING'), job('active', 'RUNNING'), job('done', 'DONE')];
CHECKED_JOBS = new Set(STATE.jobs.map(j => j.id));
const calls = [];
jobAction = async (action, jobs) => { calls.push({ action, jobs }); };
(async () => {
  await allAction('stop');
  await allAction('force_stop');
  await batchJobAction('stop');
  assert.deepStrictEqual(calls.map(c => c.jobs.map(j => j.job_id)), [
    ['pending', 'active'], ['active'], ['pending', 'active', 'done']
  ]);
  assert.equal(calls[0].jobs[0].attempt_id, 'pending-attempt');
  assert.equal(calls[0].jobs[0].submission_id, 'pending-submission');
  assert.equal(calls[0].jobs[0].attempt_number, 3);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    )


def test_changed_status_preserves_checkbox_and_unchanged_cells():
    run_page_checks(
        r"""
const cell = initial => ({ value: initial, writes: 0,
  get innerHTML() { return this.value; },
  set innerHTML(value) { this.value = value; this.writes++; }
});
const checkbox = cell('<input>'), name = cell('same job'), status = cell('RUNNING');
const row = { children: [checkbox, name, status] };
updateJobCells(row, { children: [cell('<input checked>'), cell('same job'), cell('DONE')] });
assert.equal(row.children[0], checkbox);
assert.equal(checkbox.writes, 0);
assert.equal(name.writes, 0);
assert.equal(status.innerHTML, 'DONE');
assert.equal(status.writes, 1);
updateJobCells(row, { children: [cell('<input checked>'), cell('same job'), cell('DONE')] });
assert.equal(status.writes, 1);
"""
    )


def test_column_reorder_updates_cell_attributes_without_replacing_checkbox():
    run_page_checks(r"""
function cell(html, attributes={}) {
  const attrs=new Map(Object.entries(attributes));
  return {innerHTML:html,getAttributeNames:()=>[...attrs.keys()],hasAttribute:n=>attrs.has(n),
    getAttribute:n=>attrs.get(n)??null,setAttribute:(n,v)=>attrs.set(n,v),removeAttribute:n=>attrs.delete(n)};
}
const checkbox=cell('<input>'),old=cell('PENDING',{class:'col-state',title:'Ready to start'});
const row={children:[checkbox,old]};
updateJobCells(row,{children:[cell('<input checked>'),cell('Waiting for worker slot',{title:'Waiting for worker slot'})]});
assert.equal(row.children[0],checkbox);
assert.equal(old.getAttribute('class'),null);
assert.equal(old.getAttribute('title'),'Waiting for worker slot');
assert.equal(old.innerHTML,'Waiting for worker slot');
""")


def test_table_focus_recovers_same_job_without_stealing_other_focus():
    run_page_checks(
        r"""
const focused = { tagName: 'INPUT', isConnected: true };
const replacement = { focus() { document.activeElement = this; } };
const row = { dataset: { jobId: 'job-a' }, querySelector: () => replacement };
focused.closest = () => row;
const head = { contains: () => false };
const body = { contains: () => true, querySelectorAll: () => [row] };
document.body = {};
document.getElementById = id => id === 'jobTableHead' ? head : body;
document.activeElement = focused;
const saved = tableFocus();
assert.equal(saved.jobId, 'job-a');
assert.equal(saved.control, 'input');
focused.isConnected = false;
document.activeElement = document.body;
restoreTableFocus(saved);
assert.equal(document.activeElement, replacement);
const outsideInput = {};
document.activeElement = outsideInput;
restoreTableFocus(saved);
assert.equal(document.activeElement, outsideInput);
body.querySelectorAll = () => [];
document.activeElement = document.body;
restoreTableFocus(saved);
assert.equal(document.activeElement, document.body);
"""
    )


def test_phase21_stdin_history_signals_copy_modal_autofit_and_drag_deferral():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for required_id in (
        "qeHeaderInline", "jobSummaryRow", "jobsHeadingTitle",
        "btnCopyFlowSpec", "flowSpecCopyStatus",
        "stdinSignalGroup", "btnStdinCtrlC", "stdinSignalMenu",
        "btnSendEmptyEnter", "btnSendCtrlD", "stdinPosixSection", "stdinPosixSignals",
        "stdinHistoryMenu", "btnStdinHistory", "stdinHistoryList", "btnQuickForceStop",
    ):
        assert f'id="{required_id}"' in html, f"Missing required ID {required_id}"
    assert 'id="stdinFeedback"' not in html, "Inline stdin receipt moved to bottom status bar"
    assert '<input type="text" class="mono" id="stdinInput" autocomplete="off" spellcheck="false"' in html
    assert '<button type="button" id="flowLatestMode" aria-pressed="true">Latest</button>' in html
    assert '<div class="analysis-status-bar">' in html
    cancel_idx = html.index('id="analysisCancel"')
    status_bar_idx = html.index('class="analysis-status-bar"')
    widget_end_idx = html.index('id="analysisPanel"')
    assert status_bar_idx < cancel_idx and widget_end_idx < cancel_idx
    footer_start = html.index('<footer class="status-bar"')
    footer_end = html.index('</footer>', footer_start)
    footer_html = html[footer_start:footer_end]
    assert 'id="actionToast"' in footer_html and 'id="stdinHistoryMenu"' in footer_html
    assert "Stdin history (0)" in footer_html and 'data-wave-icon="trash"' in footer_html
    css = (WAVE_DIR / "static" / "app.css").read_text(encoding="utf-8")
    assert re.search(r"th\.col-filler\s*\{[^}]*pointer-events:\s*auto\s*;", css)
    assert re.search(r"#tableInfo\s*\{[^}]*flex:\s*1\b", css) is None
    assert re.search(r'input\[type="number"\]\s*\{[^}]*height:\s*22px\s*;', css)
    assert "::-webkit-inner-spin-button" in css

    run_page_checks(r"""
(async()=>{
const nodes = new Map(), posts = [], feedbacks = [];
function node(id){return nodes.get(id) ?? (nodes.set(id,{id,hidden:false,disabled:false,open:false,value:'',
  textContent:'',innerHTML:'',attributes:{},classes:new Set(),dataset:{},style:{setProperty(){}},isConnected:true,
  listeners:{},
  classList:{toggle(k,v){const on=v===undefined?!this.c.has(k):!!v;on?this.c.add(k):this.c.delete(k)},contains(k){return this.c.has(k)},c:new Set()},
  setAttribute(k,v){this[k]=v;this.attributes[k]=v},removeAttribute(k){delete this[k];delete this.attributes[k]},
  addEventListener(k,fn){this.listeners[k]=fn},
  querySelector(){return null},querySelectorAll(){return []},closest(){return null},
  getBoundingClientRect(){return {width:800,height:600}},getClientRects(){return [{}]},
  focus(){document.activeElement=this},select(){this.selected=true},remove(){this.removed=true}
}),nodes.get(id))}
document.getElementById = node;
document.body = node('body');
document.querySelector = () => null;
document.querySelectorAll = () => [];
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => {};
const created = [];
document.createElement = tag => { const n = node('created_' + tag + '_' + created.length); created.push(n); return n; };

CONNECTED = true;
STATE = {
  workspace_id: 'ws', started: true, paused: false, platform: 'posix',
  supported_signals: ['SIGINT', 'SIGUSR1'],
  jobs: [{ id: 'j1', name: 'j1', attempt_id: 'a1', submission_id: 's1', attempt_number: 1,
    status: 'RUNNING', pty: true, capabilities: { has_process: true, can_send: true, can_interrupt: true, can_stop: true, can_kill: true } }],
  detail: { id: 'j1', name: 'j1', attempt_id: 'a1', submission_id: 's1', attempt_number: 1, status: 'RUNNING', pty: true }
};
SELECTED_JOB_ID = 'j1';
fetchState = async () => {};
renderDetail = () => { renderStdinHistory(); };
recordFeedback = (...args) => feedbacks.push(args);
fetchJson = async (path, opts) => {
  const body = JSON.parse(opts.body);
  posts.push({ path, body });
  return { ok: true, action: body.action || path, affected: 1, message: 'ok', results: [{ job_id: 'j1', ok: true, status: 'accepted', reason: 'ok' }] };
};

// #7 + Note *2: Stdin history (ArrowUp/ArrowDown + status-bar Stdin history (N) menu), silent toast, K1 leaves Ctrl+C
const inp = node('stdinInput');
inp.focus();
inp.value = 'first cmd';
handleStdinKey({ key: 'Enter', preventDefault(){} });
for (let i = 0; i < 8; i++) await Promise.resolve();
assert.equal(posts.at(-1).body.text, 'first cmd');
assert.equal(posts.at(-1).body.newline, true);
assert.equal(inp.value, '');
assert.equal(STDIN_HISTORY_BY_ATTEMPT['a1'].length, 1);
assert.equal(node('stdinHistoryMenu').hidden, false);
assert.equal(node('btnStdinHistory').textContent, 'Stdin history (1)');
assert.ok(node('stdinHistoryList').innerHTML.includes('first cmd'));
assert.equal(feedbacks.length, 0, 'successful stdin suppresses blocking toast');

inp.value = 'second cmd';
await sendStdinCommand();
assert.equal(node('btnStdinHistory').textContent, 'Stdin history (2)');
inp.value = 'draft';
handleStdinKey({ key: 'ArrowUp', preventDefault(){} });
assert.equal(inp.value, 'second cmd');
handleStdinKey({ key: 'ArrowUp', preventDefault(){} });
assert.equal(inp.value, 'first cmd');
handleStdinKey({ key: 'ArrowDown', preventDefault(){} });
assert.equal(inp.value, 'second cmd');
handleStdinKey({ key: 'ArrowDown', preventDefault(){} });
assert.equal(inp.value, 'draft');

// Clear button clears current attempt history and hides Stdin history (0)
clearSelectedStdinHistory();
assert.equal(node('stdinHistoryMenu').hidden, true);
assert.equal(node('btnStdinHistory').textContent, 'Stdin history (0)');

// S1: Send Enter, Ctrl+D (EOF), and POSIX signal close menu and record per-attempt signal history
node('stdinSignalMenu').open = true;
await sendStdinEmptyEnter();
assert.equal(node('stdinSignalMenu').open, false);
assert.equal(posts.at(-1).body.text, '');
assert.equal(posts.at(-1).body.newline, true);

node('stdinSignalMenu').open = true;
await sendStdinCtrlD();
assert.equal(node('stdinSignalMenu').open, false);
assert.equal(posts.at(-1).body.text, '\x04');
assert.equal(posts.at(-1).body.newline, false);

node('stdinSignalMenu').open = true;
await sendJobSignal('SIGUSR1');
assert.equal(node('stdinSignalMenu').open, false);
assert.equal(posts.at(-1).body.action, 'signal');
assert.equal(posts.at(-1).body.signal, 'SIGUSR1');
assert.equal(feedbacks.length, 0);
assert.equal(node('btnStdinHistory').textContent, 'Stdin history (3)');

let ctrlCPrevented = false;
handleWorkbenchKey({ key: 'c', ctrlKey: true, target: node('body'), preventDefault(){ ctrlCPrevented = true; }, stopImmediatePropagation(){} });
assert.equal(ctrlCPrevented, false, 'K1: Ctrl+C remains browser Copy');

// #10: Modal Copy uses navigator.clipboard with execCommand fallback
node('flowSpecCode').textContent = 'SELECT 42;';
const copied = [];
globalThis.navigator = { clipboard: { writeText: async t => copied.push(t) } };
await copyFlowSpecCode();
assert.deepEqual(copied, ['SELECT 42;']);
assert.equal(node('flowSpecCopyStatus').textContent, 'Copied');

globalThis.navigator = {};
document.execCommand = cmd => cmd === 'copy';
await copyFlowSpecCode();
assert.equal(created.at(-1).value, 'SELECT 42;');
assert.equal(node('flowSpecCopyStatus').textContent, 'Copied');

// #13: Split drag flag defers renders until flushDeferredRender
IS_DRAGGING_SPLIT = true;
assert.equal(isAnyLayoutDragging(), true);
let topRenders = 0, monRenders = 0;
const origRenderTopBar = renderTopBar;
const origRenderMonitor = renderMonitor;
renderTopBar = () => topRenders++;
renderMonitor = () => monRenders++;
PENDING_POST_DRAG_RENDER = true;
flushDeferredRender();
assert.equal(topRenders, 0);
IS_DRAGGING_SPLIT = false;
flushDeferredRender();
assert.equal(topRenders, 1);
assert.equal(monRenders, 1);
assert.equal(PENDING_POST_DRAG_RENDER, false);
renderTopBar = origRenderTopBar;
renderMonitor = origRenderMonitor;

// Phase 2.2 #3: Jobs double-click opens Details; double-click in Inspect keeps current tab
const selectedCalls = [];
const origSelectJob = selectJob;
selectJob = async (id, view = DETAIL_VIEW) => selectedCalls.push([id, view]);
setWorkspaceLayout = () => {};
WORKSPACE_LAYOUT = 'jobs';
DETAIL_VIEW = 'log';
await inspectJob('j1');
assert.deepEqual(selectedCalls.at(-1), ['j1', 'details']);
WORKSPACE_LAYOUT = 'split';
DETAIL_VIEW = 'parsed';
await inspectJob('j1');
assert.deepEqual(selectedCalls.at(-1), ['j1', 'parsed']);
selectJob = origSelectJob;

// Phase 2.2 #5-A/D/E: Pause Queue label, managerAction double-click guard, allAction('stop') skips HELD & sends pause_dispatch, STOPPING shows Force Stop
STATE.paused = false;
renderTopBar();
assert.equal(node('btnPauseResume').textContent, 'Pause Queue');
STATE.paused = true;
renderTopBar();
assert.equal(node('btnPauseResume').textContent, 'Resume Queue');

const postCountBeforeBusy = posts.length;
BUSY = true;
await managerAction('pause');
assert.equal(posts.length, postCountBeforeBusy, 'managerAction ignores click while BUSY');
BUSY = false;

STATE.jobs = [
  { id: 'r1', name: 'r1', attempt_id: 'ra1', submission_id: 'rs1', attempt_number: 1,
    status: 'STOPPING', held: false, capabilities: { has_process: true, can_stop: true, can_kill: true } },
  { id: 'h1', name: 'h1', attempt_id: 'ha1', submission_id: 'hs1', attempt_number: 1,
    status: 'PENDING', held: true, capabilities: { has_process: true, can_stop: true, can_kill: true } },
];
SELECTED_JOB_ID = 'r1';
STATE.detail = STATE.jobs[0];
const stopBtn = { dataset: { singleAction: 'stop' } };
const forceBtn = { id: 'btnQuickForceStop', dataset: { singleAction: 'force_stop' } };
document.querySelectorAll = sel => sel === '[data-single-action]' ? [stopBtn, forceBtn] : [];
updateControls();
assert.equal(stopBtn.textContent, 'Stopping…');
assert.equal(stopBtn.disabled, true);
assert.equal(forceBtn.hidden, false);
assert.equal(forceBtn.disabled, false);

posts.length = 0;
await allAction('stop');
assert.equal(posts.length, 1);
assert.deepEqual(posts[0].body.targets.map(t => t.job_id), ['r1'], 'Stop All preserves HELD job h1');
assert.equal(posts[0].body.pause_dispatch, true);
})().catch(e => { console.error(e); process.exitCode = 1; });
""")


def test_phase25_history_entry_removed_and_reload_batch_guards():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="btnOpenHistory"' not in html
    assert 'id="btnOpenWorkspace"' in html
    assert 'id="openArchiveTab"' in html
    assert 'id="archiveOverlay"' in html
    assert 'id="fileBrowserOverlay"' in html
    assert 'id="browseListScroll"' in html
    assert 'data-fb-resize="se"' in html
    assert 'id="archiveRecentCount"' in html
    assert 'id="btnBrowseBack"' in html
    assert 'id="btnBrowseForward"' in html
    assert 'class="open-modal-footer"' in html
    css = (WAVE_DIR / "static" / "app.css").read_text(encoding="utf-8")
    assert "#btnLoadLogs, #btnOpenArchive" not in css
    assert "height: min(560px, 82vh);" in css
    assert re.search(r"\.fb-edit-path-btn,\s*\.fb-clear-path-btn\s*\{[^}]*min-height:\s*0;", css)
    assert re.search(r"\.fb-col-mtime\s*\{[^}]*text-align:\s*left;", css)
    assert re.search(r"\.fb-col-size\s*\{[^}]*text-align:\s*left;", css)
    assert re.search(r"\.fb-entry-mtime\s*\{[^}]*text-align:\s*left;", css)
    assert re.search(r"\.fb-entry-size\s*\{[^}]*text-align:\s*left;", css)

    run_page_checks(r"""
(async () => {
const nodes = new Map(), feedbacks = [];
function node(id){return nodes.get(id) ?? (nodes.set(id,{id,hidden:false,disabled:false,open:false,value:'',
  textContent:'',innerHTML:'',attributes:{},classes:new Set(),dataset:{},style:{setProperty(){}},isConnected:true,
  classList:{toggle(){},contains(){return false}},
  setAttribute(k,v){this[k]=v;this.attributes[k]=v},removeAttribute(k){delete this[k];delete this.attributes[k]},
  querySelector(){return null},querySelectorAll(){return []},closest(){return null},
  getBoundingClientRect(){return {width:800,height:600}},getClientRects(){return [{}]},
  focus(){document.activeElement=this}
}),nodes.get(id))}
document.getElementById = node;
document.body = node('body');
document.querySelector = () => null;
document.querySelectorAll = () => [];
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => {};
recordFeedback = (...args) => feedbacks.push(args);
node('workOverlay').hidden = true;

// Started batch with HELD/PENDING jobs disables #btnReloadWorkspace (even though hasActiveWork() is false)
CONNECTED = true;
BUSY = false;
STATE = {
  workspace_id: 'ws-1',
  source_path: 'D:/proj/work.py',
  opened_run_id: null,
  started: true,
  sources: { active_readers: 0, active_watchers: 0 },
  jobs: [{ id: 'h1', name: 'h1', status: 'PENDING', held: true, capabilities: {} }],
  detail: null,
};
assert.equal(hasActiveWork(STATE), false);
assert.equal(hasUnfinishedBatch(STATE), true);
updateControls();
assert.equal(node('btnReloadWorkspace').disabled, true);
assert.match(node('btnReloadWorkspace').title, /Finish or cancel/);

// Finished or not-started batch re-enables #btnReloadWorkspace
STATE.jobs = [{ id: 'd1', name: 'd1', status: 'DONE', held: false, capabilities: {} }];
assert.equal(hasUnfinishedBatch(STATE), false);
updateControls();
assert.equal(node('btnReloadWorkspace').disabled, false);

// Workspace switch in real fetchState keeps CONNECTED=true (no brief disconnected flicker)
globalThis.URLSearchParams = class {
  constructor() { this.p = []; }
  set(k, v) { this.p.push(k + '=' + v); }
  toString() { return this.p.join('&'); }
};
const seenConnected = [];
const realUpdateControls = updateControls;
updateControls = () => { seenConnected.push(CONNECTED); realUpdateControls(); };
resetWorkspaceSelection = () => {};
closePanels = () => {};
renderTopBar = () => {};
renderMonitor = () => {};
node('settingsOverlay').hidden = true;
STATE = { workspace_id: 'ws-1', jobs: [], detail: null };
let seq = 0;
fetchJson = async () => {
  seq += 1;
  return {
    workspace_id: 'ws-2', jobs: [], detail: null, columns: [], available_columns: [],
    configured_columns: null, started: false,
  };
};
await fetchState();
assert.equal(seq, 2);
assert.equal(STATE.workspace_id, 'ws-2');
assert.ok(seenConnected.length >= 2 && seenConnected.every(v => v === true));

// Reload from charts layout switches to jobs layout and surfaces errors via toast
WORKSPACE_LAYOUT = 'charts';
let switchedTo = null;
setWorkspaceLayout = next => { switchedTo = next; WORKSPACE_LAYOUT = next; };
fetchState = async () => {};
fetchJson = async () => ({ ok: true, message: 'Reloaded work file. Review the preview, then select Start.' });
await loadWorkspacePreview('/api/work/reload', {});
assert.equal(switchedTo, 'jobs');
assert.match(feedbacks.at(-1)[0], /Reloaded work file/);

fetchJson = async () => { throw new Error('SyntaxError in work.py'); };
await loadWorkspacePreview('/api/work/reload', {});
assert.equal(feedbacks.at(-1)[1], 'error');
assert.match(feedbacks.at(-1)[0], /Reload failed: SyntaxError in work.py/);

// loadRecentArchives aborts superseded request, ignores AbortError, and updates #archiveRecentCount
const origAbortController = globalThis.AbortController;
globalThis.AbortController = class {
  constructor() {
    this.signal = {
      aborted: false,
      listeners: [],
      addEventListener(_ev, fn) { this.listeners.push(fn); },
      removeEventListener() {},
    };
  }
  abort() {
    if (this.signal.aborted) return;
    this.signal.aborted = true;
    for (const fn of this.signal.listeners) fn();
  }
};
node('workOverlay').hidden = false;
OPEN_MODE = 'archive';
const archiveSignals = [];
let archiveCallNo = 0;
fetchJson = (_url, opts) => {
  archiveCallNo += 1;
  const sig = opts?.signal;
  archiveSignals.push(sig);
  if (archiveCallNo === 1) {
    return new Promise((_resolve, reject) => {
      sig.listeners.push(() => reject(Object.assign(new Error('Request cancelled'), { name: 'AbortError', isAborted: true })));
    });
  }
  return Promise.resolve({
    rows: [{
      run_id: 'abcd' + '0'.repeat(28),
      filename: '20261122_110000_work_abcd0000.sqlite3',
      path: 'D:/archives/20261122_110000_work_abcd0000.sqlite3',
      source_path: 'D:/proj/work.py',
      size: 2048,
      created: 1700000100,
      run_outcome: 'PASS',
      closed_cleanly: true,
    }],
    total_files: 50,
    partial: false,
  });
};
const firstLoad = loadRecentArchives();
const secondLoad = loadRecentArchives();
await Promise.all([firstLoad, secondLoad]);
assert.equal(archiveSignals[0]?.aborted, true);
assert.equal(archiveSignals[1]?.aborted, false);
assert.equal(node('archiveRecentCount').textContent, 'Showing 1 of 50');
assert.ok(!node('archiveRecentList').innerHTML.includes('error-text'));
globalThis.AbortController = origAbortController;
})().catch(e => { console.error(e); process.exitCode = 1; });
""")


def test_workbench_escape_defers_to_waveform_targets():
    run_page_checks(r"""
const nodes = {
  broadcastPanel: { hidden: true },
  actionToast: { hidden: false },
};
document.getElementById = id => nodes[id] || null;
document.querySelector = () => null;
let dismissed = 0;
dismissFeedback = () => { dismissed++; nodes.actionToast.hidden = true; };
let waveHasTarget = true;
ChartWorkspace = { hasEscapeTarget: () => waveHasTarget };
WORKSPACE_LAYOUT = 'charts';
MODAL_STACK.length = 0;
const fireEsc = () => {
  let prevented = 0, stopped = 0;
  handleWorkbenchKey({
    key: 'Escape',
    defaultPrevented: false,
    isComposing: false,
    target: { closest(){ return null; } },
    preventDefault(){ prevented++; },
    stopImmediatePropagation(){ stopped++; },
  });
  return { prevented, stopped };
};

const first = fireEsc();
assert.equal(dismissed, 0);
assert.equal(first.stopped, 0);

waveHasTarget = false;
const second = fireEsc();
assert.equal(dismissed, 1);
assert.equal(second.stopped, 1);
""")
