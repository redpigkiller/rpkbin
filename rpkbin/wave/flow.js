/* Saved outputs only: this reader never evaluates Python, verdicts or controls. */
const FlowWorkspace = (() => {
  const node=id=>document.getElementById(id), escape=v=>escHtml(String(v??''));
  const MAX_ROWS=600, MAX_BYTES=1536*1024, MAX_COLUMNS=256, DEFAULT_VISIBLE_COLUMNS=12;
  let detail=null,identity='',initialized=false,parser='',analyzer='',automation='',reportName='';
  let readerPage='parsed',priorityEditor=null;
  let rows=[],forward=0,backward=null,moreForward=true,moreBackward=false,pending=false,epoch=0,controller=null,lastCount=-1;
  let archiveSettled=false;
  let externalFacts=null,copyRequest=0,copyFeedbackTimer;
  let error='',dataKey='',namesToken=0,inspectionEpoch=0,programmaticUntil=0,reportCatalog=[];
  const defaults=()=>({parsed:{mode:'latest',policy:'record',follow:false},analysis:{mode:'latest',policy:'record',follow:false},automation:{mode:'latest',policy:'record',follow:false}});
  let views=defaults(),positions={parsed:0,analysis:0,automation:0,details:0},anchors={},restoreAnchor=null,restoreTop=null;
  let selectedRows={parsed:null,analysis:null,automation:null};
  const preferences=new Map(),knownFields=new Set();
  const hiddenLayout=()=>typeof WORKSPACE_LAYOUT!=='undefined'&&(WORKSPACE_LAYOUT==='jobs'||WORKSPACE_LAYOUT==='charts');
  const archived=()=>RunHistory.selection;
  const key=d=>[STATE.workspace_id,archived()?.run_id||STATE.archive?.run_id,d?.id,d?.attempt_id].join(':');
  const hasFlow=()=>!!detail?.flow, stageList=()=>detail?.flow?.parsers||[];
  const mark=(id,html)=>{const host=node(id);if(host._flowMarkup!==html){host.innerHTML=html;host._flowMarkup=html;}};
  const scalar=v=>v===undefined?'—':v===null?'null':typeof v==='object'?JSON.stringify(v):String(v);
  function preview(value, limit = 240, depth = 0) {
    if (value === null || typeof value !== 'object') {
      const text = value === undefined ? '—' : value === null ? 'null' : String(value); return text.length > limit ? text.slice(0, limit) + '…' : text;
    }
    if (depth > 2) return Array.isArray(value) ? '[…]' : '{…}';
    const parts = []; let length = 2, count = 0, clipped = false;
    for (const name in value) {
      if (!Object.prototype.hasOwnProperty.call(value, name)) continue;
      if (++count > 8 || length >= limit) { clipped = true; break; }
      const part = (Array.isArray(value) ? '' : JSON.stringify(name) + ': ') + preview(value[name], Math.max(8, limit - length), depth + 1);
      parts.push(part); length += part.length + 2;
    }
    return ((Array.isArray(value) ? '[' : '{') + parts.join(', ') + (clipped ? ', …' : '') +
      (Array.isArray(value) ? ']' : '}')).slice(0, limit + 3);
  }


  const numericFact={numeric:true};
  function facts(values,live=false){
    return '<table class="flow-facts"><tbody>'+values.map(([name,value,options={}],index)=>{
      const numeric=options.numeric?' class="flow-fact-number"':'';
      const dynamic=live&&options.markup===undefined?` data-flow-live-fact="${index}"`:'';
      return `<tr><th scope="row">${escape(name)}</th><td${numeric}${dynamic}>${options.markup??escape(preview(value))}</td></tr>`;
    }).join('')+'</tbody></table>';
  }
  const seqOf=row=>Number(row?.seq)||0;
  const health=()=>RunHistory.healthText(archived()?.health||STATE.archive);
  const status=text=>{node('flowStatus').textContent=text+' · '+health();};
  const currentPage=()=>['analysis','automation','details'].includes(DETAIL_VIEW)?DETAIL_VIEW:'parsed';
  const reading=()=>['parsed','analysis','automation'].includes(DETAIL_VIEW);
  const readOnly=()=>typeof isHistory==='function'?isHistory():!!archived();
  const config=()=>views[readerPage], stage=()=>readerPage==='parsed'?'parser':readerPage==='automation'?'automation':'analyzer';
  const producer=()=>readerPage==='parsed'?parser:readerPage==='automation'?automation:analyzer;
  const list=()=>readerPage==='parsed'?stageList():readerPage==='automation'?detail?.flow?.automations||[]:detail?.flow?.analyzers||[];
  const positionOf=row=>readerPage==='automation'?Number(row?.request_id||row?.data?.request_id)||seqOf(row):seqOf(row);
  const rowBytes=row=>JSON.stringify(row).length*2;
  const outputKind=()=>readerPage==='automation'?'action':readerPage==='parsed'?'record':'report';
  const count=()=>list().filter(s=>!producer()||s.name===producer()).reduce((n,s)=>n+(readerPage==='automation'?(s.publication_count||0):outputKind()==='report'?(s.report_count||0):(s.count||0)),0);
  const box=()=>node(readerPage==='parsed'?'flowParsedScroll':readerPage==='automation'?'flowAutomationScroll':'flowAnalysisScroll');
  const host=()=>node(readerPage==='parsed'?'flowParsedRows':readerPage==='automation'?'flowAutomationRows':'flowAnalysisRows');
  function rememberLayout(){
    if(hiddenLayout())return;
    const page=currentPage(),el=node(`flow${page[0].toUpperCase()+page.slice(1)}Scroll`);
    if(el?.clientHeight)positions[page]=el.scrollTop;
    if(reading()&&readerPage===page&&el?.clientHeight&&el.getBoundingClientRect){
      const header=host().querySelector('thead'),top=el.getBoundingClientRect().top;
      const contentTop=header?.getBoundingClientRect?.()?.bottom??top;
      const visible=[...host().querySelectorAll('[data-flow-row]')].find(r=>r.getBoundingClientRect().bottom>contentTop);
      if(visible)anchors[page]={seq:Number(visible.dataset.flowPosition||visible.dataset.flowRow),offset:visible.getBoundingClientRect().top-top};
    }
  }
  function remember(){
    rememberLayout();if(!identity)return;
    preferences.delete(identity);preferences.set(identity,{parser,analyzer,automation,reportName,views:JSON.parse(JSON.stringify(views)),positions:{...positions},anchors:{...anchors}});
    while(preferences.size>24)preferences.delete(preferences.keys().next().value);
  }
  function cancel(){++epoch;controller?.abort();controller=null;pending=false;}
  function clearRows(){cancel();rows=[];forward=0;backward=null;moreForward=true;moreBackward=false;lastCount=-1;archiveSettled=false;error='';restoreAnchor=null;restoreTop=null;}
  function reset(){remember();clearRows();detail=null;identity='';dataKey='';externalFacts=null;++copyRequest;knownFields.clear();selectedRows={parsed:null,analysis:null,automation:null};++namesToken;closeInspection();if(!node('flowReportOverlay').hidden)closeModal('flowReportOverlay');}
  function display(view){
    initialize();
    if(!['parsed','analysis','automation'].includes(view)&&pending){cancel();lastCount=-1;}
    node('flowParsedTools').hidden=view!=='parsed';node('flowAnalysisTools').hidden=view!=='analysis';node('flowAutomationTools').hidden=view!=='automation';node('flowDetailsNav').hidden=view!=='details';
    for(const page of ['parsed','analysis','automation','details'])node(`flow${page[0].toUpperCase()+page.slice(1)}Scroll`).hidden=page!==view;
    if(['parsed','analysis','automation'].includes(view)){
      const actions=node('flowOutputActions'),target=node(view==='parsed'?'flowParsedTools':view==='automation'?'flowAutomationTools':'flowAnalysisTools');
      if(actions.parentElement!==target)target.appendChild(actions);
      syncOutputControls(view);
    }
    if(priorityEditor){const target=view==='details'?node('flowPrioritySlot'):node('flowPriorityHome').parentElement;if(priorityEditor.parentElement!==target)target.appendChild(priorityEditor);}
  }
  function layoutChanged(){
    if(hiddenLayout())return;
    const page=currentPage();node(`flow${page[0].toUpperCase()+page.slice(1)}Scroll`).scrollTop=positions[page]||0;
    if(hasFlow()&&reading()&&!rows.length&&moreForward&&!pending)load();
  }
  function open(page){rememberLayout();setDetailView(page);layoutChanged();}
  const extLinkSvg=()=>(typeof WaveIcons!=='undefined'&&typeof WaveIcons.svg==='function'&&WaveIcons.svg('external-link'))||
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>';
  function sourceButton(evidence,label,withIcon=false){
    const line=evidence?.line_no||evidence?.line_start;
    if(!Number.isSafeInteger(line)||line<=0)return '<span class="help-text">—</span>';
    const text=escape(label||('L'+line));
    const icon=withIcon?` <span class="flow-ext-icon" data-wave-icon="external-link" aria-hidden="true">${extLinkSvg()}</span>`:'';
    return `<button type="button" class="flow-link${withIcon?' flow-log-btn':''}" data-flow-evidence="${line}" title="Read original Log">${text}${icon}</button>`;
  }
  function reportResultMarkup(result){
    if(result===null||result===undefined||result==='')return '—';
    const text=String(result),upper=text.trim().toUpperCase();
    const tone=upper==='PASS'||upper==='PASSED'||upper==='OK'?'PASS':
      upper==='FAIL'||upper==='FAILED'?'FAIL':
      upper==='ERROR'||upper==='ERR'||upper==='FAULT'?'ERROR':
      upper==='WARN'||upper==='WARNING'||upper==='INCOMPLETE'?'WARN':'CUSTOM';
    return `<span class="status-badge result-${tone}">${escape(text)}</span>`;
  }
  function evidence(line){
    if(!Number.isSafeInteger(line)||line<1||!detail)return;
    return AnalysisWorkspace.openContext(detail,line);
  }
  function fieldTitle(name){
    const path=outputKind()==='report'?'values.'+name:name;
    const descriptions=list().filter(s=>!producer()||s.name===producer()).map(s=>s.outputs?.[name]).filter(v=>v!==undefined);
    const desc=descriptions.length&&descriptions.every(v=>JSON.stringify(v)===JSON.stringify(descriptions[0]))?descriptions[0]:undefined;
    return path+(desc&&typeof desc==='object'?' · '+(desc.label||name)+(desc.unit?' ('+desc.unit+')':''):'');
  }
  function columnHandle(scope,key,label,fixed=false,fallback=160){
    const size=typeof ReadingLayout!=='undefined'?ReadingLayout.width(scope,key,fixed,fallback):fallback;
    return `<span class="reading-column-resize" data-column-resize role="separator" aria-label="Resize ${escape(label)}" aria-orientation="vertical" aria-valuemin="56" aria-valuemax="1200" aria-valuenow="${size}"${fixed?' tabindex="0"':''} title="Drag or double-click to resize"></span>`;
  }
  const fixedHeaders=(scope,columns)=>columns.map(c=>`<th scope="col" data-reading-fixed="${escape(c.key)}" title="${escape(c.title||c.label)}">${escape(c.label)}${columnHandle(scope,c.key,c.label,true,c.width)}</th>`).join('');
  function renderReader(){
    if(!detail)return;
    if(readerPage==='automation'){renderAutomation();return;}
    const layout=typeof ReadingLayout!=='undefined'?ReadingLayout:null;
    const existingTable=host()?.querySelector?.('table');
    if(existingTable&&layout?.isResizing?.(existingTable))return;
    const reports=outputKind()==='report',dataRows=rows;
    for(const s of list().filter(s=>!producer()||s.name===producer()))for(const k of Object.keys(s.outputs||{}))if(knownFields.size<MAX_COLUMNS)knownFields.add(k);
    for(const row of dataRows){
      for(const k of row.known_fields||[])if(knownFields.size<MAX_COLUMNS)knownFields.add(k);
      const data=reports?row.data?.values:row.data;
      for(const k of Object.keys(data||{}))if(knownFields.size<MAX_COLUMNS)knownFields.add(k);
    }
    const scope=JSON.stringify([detail.flow.name,detail.flow.version,readerPage,producer()]);
    const available=[...knownFields].slice(0,MAX_COLUMNS),showProducer=!producer();
    layout?.register(available,scope,DEFAULT_VISIBLE_COLUMNS);
    const allOrdered=layout?layout.order(available,scope):available;
    const fields=layout?allOrdered.filter(k=>layout.isVisible(scope,k)):available.slice(0,DEFAULT_VISIBLE_COLUMNS);
    const fixed=[{key:'source',label:config().mode==='latest'&&config().policy==='fields'?'As of':'Source',width:96,title:'Wave: original Log line; merged values may have earlier sources'},
      ...(showProducer?[{key:'producer',label:readerPage==='analysis'?'Analyzer':'Parser',width:140}]:[]),
      ...(reports?[{key:'name',label:'Name',title:'Report.name',width:140},{key:'result',label:'Result',title:'Report.result: user-supplied text',width:140},{key:'reason',label:'Reason',title:'Report.reason',width:260}]:[])];
    const columns=layout?.columns(fields,scope,fixed);
    const heading=fixedHeaders(scope,fixed)+fields.map(k=>`<th scope="col" title="${escape(fieldTitle(k))} · Drag to reorder; Alt+Left/Right" tabindex="0" data-reading-field="${escape(k)}">${escape(k)}${columnHandle(scope,k,k)}</th>`).join('')+'<th class="col-filler" aria-hidden="true"></th>';
    const body=dataRows.map(row=>{
      const seq=seqOf(row),line=row.evidence?.line_no||row.evidence?.line_start||0;
      const isSelected=selectedRows[readerPage]===seq;
      const projection=config().mode==='latest'&&config().policy==='fields'?'fields':'';
      const full=`<button type="button" class="flow-link" data-flow-full="${seq}" data-flow-projection="${projection}" aria-label="Inspect full output" title="Inspect full output and field sources">…</button>`;
      const prefix=`<tr${isSelected?' class="selected"':''} data-flow-row="${seq}" data-flow-source="${line}"><td>${sourceButton(row.evidence,undefined,true)} ${full}</td>`;
      const span=fields.length+(reports?3:0)+(showProducer?1:0);
      if(row.truncated)return prefix+(span>0?`<td colspan="${span}">${escape(row.data_preview||'Saved output')}…</td>`:'')+'<td class="col-filler"></td></tr>';
      if(reports&&row.data?.version!==1)return prefix+`<td colspan="${fields.length+3+(showProducer?1:0)}">Unsupported Report format; inspect full output.</td><td class="col-filler"></td></tr>`;
      const values=reports?row.data?.values:row.data;
      return prefix+(showProducer?`<td>${escape(row.producer)}</td>`:'')+(reports?`<td>${escape(row.data.name)}</td><td>${reportResultMarkup(row.data.result)}</td><td title="${escape(row.data.reason)}">${escape(preview(row.data.reason))}</td>`:'')+fields.map(k=>`<td title="${escape(fieldTitle(k)+(row.field_sources?.[k]?' · saved publication '+row.field_sources[k]:''))}">${escape(preview(Object.hasOwn(values||{},k)?values[k]:undefined))}</td>`).join('')+'<td class="col-filler"></td></tr>';
    }).join('');
    const failures=list().filter(s=>(!producer()||s.name===producer())&&s.error).map(s=>`<div class="flow-error">${escape(s.name)}: ${escape(s.error.message||scalar(s.error))}</div>`).join('');
    mark(host().id,failures+(error?'<div class="flow-error">'+escape(error)+'</div>':'')+(dataRows.length?'<table class="flow-records'+(columns?' reading-columns':'')+'"'+(columns?' data-reading-scope="'+escape(scope)+'" style="width:100%;min-width:'+columns.size+'px"':'')+'>'+(columns?.html||'')+'<thead><tr>'+heading+'</tr></thead><tbody>'+body+'</tbody></table>':'<div class="flow-no-data">'+(pending?'Reading saved outputs…':reports?'No matching Reports published.':'No saved outputs.')+'</div>'));
    if(dataRows.length)layout?.autoFitInitial?.(host()?.querySelector?.('table'),scope,{maxWidth:220,maxByField:{source:130,reason:300}});
    syncOutputControls(readerPage);
    const suffix=config().mode==='latest'&&config().policy==='fields'?' · fields may come from earlier outputs':'';
    const colInfo=allOrdered.length>fields.length?` · ${fields.length}/${allOrdered.length} columns shown`:'';
    status(`${producer()||'All '+(readerPage==='analysis'?'analyzers':'parsers')} · ${dataRows.length} loaded ${reports?'Reports':'records'}${pending?' · reading…':''}${suffix}${colInfo}`);
  }
  async function load(direction='forward',replace=false){
    if(hiddenLayout())return;
    if(pending||!detail?.flow||!reading())return;
    if(readerPage==='automation'&&config().mode==='latest'){renderAutomation();return;}
    if(direction==='forward'&&!moreForward||direction==='backward'&&!moreBackward)return;
    const token=++epoch,execution=identity,pageName=readerPage,requestedCount=count();
    const latest=config().mode==='latest';controller?.abort();const reqController=new AbortController();controller=reqController;pending=true;error='';
    const previousTop=box().scrollTop,previousHeight=box().scrollHeight;
    const options={view:readerPage==='automation'?'automation_history':latest?'flow_latest':'flow',...(readerPage==='automation'?{}:{stage:stage(),...(latest?{mode:config().policy}:{kind:outputKind()})}),limit:100,...(producer()?{producer:producer()}:{}),...(readerPage==='analysis'&&reportName?{report_name:reportName}:{}),...(direction==='backward'?{before:backward}:{after:replace?0:forward})};
    try{
      const page=await RunHistory.query(options,detail,{signal:reqController.signal});
      if(token!==epoch||execution!==identity||pageName!==readerPage||DETAIL_VIEW!==pageName)return;
      const incoming=Array.isArray(page.rows)?page.rows:[];
      const rowKey=row=>latest?JSON.stringify([row.producer,row.report_name??(stage()==='analyzer'?row.data?.name:null)]):positionOf(row);
      const map=new Map((replace?[]:rows).concat(incoming).map(row=>[rowKey(row),row]));rows=[...map.values()].sort((a,b)=>positionOf(a)-positionOf(b));
      if(direction==='forward'){forward=Number(page.next_seq)||positionOf(incoming.at(-1))||forward;moreForward=!!page.has_more||count()!==requestedCount;if(backward===null&&rows.length)backward=positionOf(rows[0]);}
      else{backward=Number(page.next_seq)||positionOf(incoming[0])||backward;moreBackward=!!page.has_more;}
      if(direction==='backward'&&replace){forward=positionOf(incoming.at(-1));moreForward=count()!==requestedCount;}
      boundRows(direction);
      pending=false;renderReader();
      if(restoreAnchor){const target=host().querySelector(`[data-flow-${readerPage==='automation'?'position':'row'}="${restoreAnchor.seq}"]`);if(target?.getBoundingClientRect&&box().getBoundingClientRect){box().scrollTop+=target.getBoundingClientRect().top-box().getBoundingClientRect().top-restoreAnchor.offset;positions[readerPage]=box().scrollTop;}restoreAnchor=null;restoreTop=null;}
      else if(restoreTop!==null){box().scrollTop=restoreTop;restoreTop=null;}
      programmaticUntil=Date.now()+100;
      if(config().mode==='history'&&(config().follow||archived()?.follow==='following')){box().scrollTop=box().scrollHeight;positions[readerPage]=box().scrollTop;}
      else if(direction==='backward')box().scrollTop=previousTop+box().scrollHeight-previousHeight;
      const enough=!box().clientHeight||box().scrollHeight>box().clientHeight+50;
      const shouldKeepFilling=(!enough&&moreForward&&forward>0)||(direction==='backward'&&!enough&&moreBackward&&backward>0);
      if(shouldKeepFilling)setTimeout(()=>{
        const saved=archived();
        if(execution===identity&&token===epoch&&reading()&&(!saved?.follow||saved.follow==='following'||saved.catchUp))load(direction);
      },0);
    }catch(exc){if(reqController.signal.aborted||exc?.name==='AbortError'||exc?.isAborted){if(controller===reqController)pending=false;return;}if(token===epoch&&execution===identity){pending=false;error=exc.message;renderReader();}}
  }
  function reload(){clearRows();knownFields.clear();dataKey='';anchors[readerPage]=null;positions[readerPage]=0;box().scrollTop=0;refresh(detail);remember();}
  function syncOutputControls(page){
    const cfg=views[page];if(!cfg)return;
    const flow=hasFlow();
    node('flowHistoryMode').disabled=node('flowLatestMode').disabled=!flow;
    node('flowHistoryMode').setAttribute('aria-pressed',String(cfg.mode==='history'));
    node('flowLatestMode').setAttribute('aria-pressed',String(cfg.mode==='latest'));
    if(node('flowReadingSettings'))node('flowReadingSettings').hidden=true;
    const showPolicy=flow&&cfg.mode==='latest'&&(page==='parsed'||page==='analysis');
    const policyGroup=node('flowLatestPolicyGroup');
    if(policyGroup)policyGroup.hidden=!showPolicy;
    const recBtn=node('flowPolicyRecord'),fldBtn=node('flowPolicyFields');
    if(recBtn){recBtn.hidden=!showPolicy;recBtn.disabled=!showPolicy;recBtn.setAttribute('aria-pressed',String(cfg.policy==='record'));}
    if(fldBtn){fldBtn.hidden=!showPolicy;fldBtn.disabled=!showPolicy;fldBtn.setAttribute('aria-pressed',String(cfg.policy==='fields'));}
    const policy=node('flowLatestPolicy');
    if(policy){policy.hidden=!showPolicy;policy.disabled=!showPolicy;policy.value=cfg.policy;}
    const showFollow=flow&&cfg.mode==='history'&&!readOnly();
    node('flowFollowLabel').hidden=!showFollow;
    node('flowFollow').checked=cfg.mode==='history'&&!!cfg.follow;
    node('flowFollow').disabled=!showFollow;
    node('flowFollowLabel').title=cfg.mode==='history'?'Follow latest output':'Follow latest output · History only';
  }
  function changePolicy(nextPolicy){
    const page=currentPage();if(!['parsed','analysis'].includes(page))return;
    const normalized=nextPolicy==='fields'?'fields':'record';
    views[page].policy=normalized;
    if(node('flowLatestPolicy'))node('flowLatestPolicy').value=normalized;
    readerPage=page;reload();
  }
  function changeMode(mode){
    const page=currentPage();if(!reading()||views[page].mode===mode)return;
    views[page].mode=mode;readerPage=page;reload();
  }
  function pauseFollow(page=readerPage){if(!views[page]?.follow)return;views[page].follow=false;if(page===currentPage())syncOutputControls(page);remember();}
  function followTail(){
    clearRows();knownFields.clear();backward=Number.MAX_SAFE_INTEGER;moreBackward=true;moreForward=false;
    return load('backward',true);
  }
  function boundRows(direction='forward'){
    let bytes=rows.reduce((n,row)=>n+rowBytes(row),0);
    while(rows.length>1&&(rows.length>MAX_ROWS||bytes>MAX_BYTES)){
      const removed=direction==='backward'?rows.pop():rows.shift();bytes-=rowBytes(removed);
      if(direction==='backward'){moreForward=true;forward=positionOf(rows.at(-1));}else{moreBackward=true;backward=positionOf(rows[0]);}
    }
  }
  async function refreshPendingActions(){
    if(hiddenLayout())return;
    const unsettled=rows.filter(row=>row.truncated||row.data?.phase==='requested');
    if(pending||!unsettled.length)return;
    const token=++epoch,execution=identity,requestedCount=count(),end=Math.max(...unsettled.map(positionOf));
    let after=Math.min(...unsettled.map(positionOf))-1;
    controller?.abort();const reqController=new AbortController();controller=reqController;pending=true;error='';
    try{
      for(let visited=0;visited<MAX_ROWS;){
        const page=await RunHistory.query({view:'automation_history',after,limit:Math.min(100,MAX_ROWS-visited),...(automation?{producer:automation}:{})},detail,{signal:reqController.signal});
        if(token!==epoch||execution!==identity||readerPage!=='automation'||DETAIL_VIEW!=='automation')return;
        const updates=new Map((page.rows||[]).map(row=>[positionOf(row),row]));
        visited+=(page.rows||[]).length;
        rows=rows.map(row=>updates.get(positionOf(row))||row);
        boundRows();
        const next=Number(page.next_seq)||after;
        if(!page.rows?.length||!page.has_more||next>=end||next<=after)break;
        after=next;
      }
      pending=false;lastCount=requestedCount;renderAutomation();
      if(moreForward&&box().scrollHeight-box().scrollTop-box().clientHeight<150)setTimeout(()=>{if(token===epoch&&execution===identity&&reading())load();},0);
    }catch(exc){if(reqController.signal.aborted||exc?.name==='AbortError'||exc?.isAborted){if(controller===reqController)pending=false;return;}if(token===epoch&&execution===identity){pending=false;error=exc.message;renderAutomation();}}
  }
  async function reportNames(){
    if(!detail?.flow||hiddenLayout())return;
    const token=++namesToken,execution=identity,selected=analyzer;
    try{const page=await RunHistory.query({view:'flow_report_names',...(analyzer?{producer:analyzer}:{}),limit:100},detail);
      if(token!==namesToken||execution!==identity||selected!==analyzer)return;
      reportCatalog=(page.rows||[]).slice(0,100);renderReportChoices();reportSummary();
      node('flowReportName').title=page.has_more?'First 100 names; type an exact report name to read any other item.':'Choose or type an exact Report.name';
      node('flowReportHint').textContent=page.has_more?'First 100 names shown. Enter any exact name to find other reports.':'Choose a published name or enter an exact name.';
    }catch(_){/* Exact name entry remains available after a catalog query fails. */}
  }
  function renderReportChoices(){
    const query=node('flowReportName').value.toLocaleLowerCase();
    mark('flowReportNames','<button type="button" data-flow-report-name="">All reports</button>'+reportCatalog.filter(name=>name.toLocaleLowerCase().includes(query)).map(name=>`<button type="button" data-flow-report-name="${escape(name)}">${escape(name)}</button>`).join(''));
  }
  function reportSummary(){
    const names=[...new Set([...reportCatalog,...(reportName?[reportName]:[])])];
    mark('flowReportSelect','<option value="">All reports</option>'+names.map(name=>`<option value="name:${escape(name)}">${escape(name)}</option>`).join('')+'<option value="find">Find or enter name…</option>');
    node('flowReportSelect').value=reportName?'name:'+reportName:'';
    node('flowReportSelect').title=reportName||'All published Report names';
  }
  function chooseReport(name){
    const changed=name!==reportName;reportName=name;node('flowReportName').value=name;reportSummary();
    closeModal('flowReportOverlay');node('flowReportSelect').focus();
    if(changed){readerPage='analysis';reload();}
  }
  function allSpecs(label,items){
    node('flowSpecTitle').textContent='All '+label;
    node('flowSpecFacts').innerHTML=items.map(item=>facts([['Name',item.name],['Inputs',item.inputs||'Log source'],['Output description',item.outputs||{}],['Error',item.error||'—']])).join('')||'<div class="flow-no-data">No attached definitions.</div>';
    node('flowSpecCode').textContent=items.map(item=>`# ${item.name}\n${item.code||'Source code is not available for this callable.'}`).join('\n\n');
    node('flowSpecMore').replaceChildren();openModal('flowSpecOverlay');
  }
  function sourcePathMarkup(path) {
    if(!path)return '—';
    return `<span class="flow-source-path">${escape(path)}</span>
      <button type="button" id="flowCopyPath" class="flow-copy-path">Copy Path</button>
      <span id="flowCopyStatus" class="flow-copy-status" role="status"></span>
      <div id="flowCopyFallback" class="flow-copy-fallback" hidden>
        <input id="flowCopyField" type="text" readonly aria-label="Source file path" aria-describedby="flowCopyHint">
        <span id="flowCopyHint" class="help-text">Press Ctrl+C to copy the path.</span>
      </div>`;
  }
  function renderExternalFacts(d,run,logSave) {
    const source=d.source_descriptor;
    const path=source?.path;
    const values=[
      ['File',d.name],['Origin',d.origin_label||d.source_kind],...run,
      ['Completion',JobsColumns.completionLabel(d.completion_reason)||'—'],
      ['Source file',null,{markup:sourcePathMarkup(path)}],
      ['Offset',source?.end_offset??'—',numericFact],['Generation',source?.generation??0,numericFact],
      ['Elapsed',d.elapsed_str,numericFact],['Encoding',d.encoding],logSave
    ];
    const host=node('flowExecutionFacts');
    if(externalFacts?.identity!==identity||externalFacts.path!==path){
      host.innerHTML=facts(values,true);
      host._flowMarkup=null;
      externalFacts={identity,path,cells:host.querySelectorAll('[data-flow-live-fact]')};
      return;
    }
    // Update cell text without replacing the focused Copy Path button or input.
    for(const cell of externalFacts.cells){
      const text=preview(values[Number(cell.dataset.flowLiveFact)][1]);
      if(cell.textContent!==text)cell.textContent=text;
    }
  }
  async function copySourcePath(button) {
    const path=detail?.source_descriptor?.path;
    if(!path)return;
    const request=++copyRequest, execution=identity, focused=document.activeElement;
    clearTimeout(copyFeedbackTimer);
    const status=node('flowCopyStatus');
    status.textContent='';
    let copied=false;
    try {
      await navigator.clipboard.writeText(path);
      copied=true;
    } catch (_) { /* Access can be unavailable or denied. */ }
    if(request!==copyRequest||execution!==identity||DETAIL_VIEW!=='details'||
      detail?.source_descriptor?.path!==path||node('flowCopyPath')!==button)return;
    const fallback=node('flowCopyFallback');
    if(copied){
      fallback.hidden=true;
      status.textContent='Path copied.';
      copyFeedbackTimer=setTimeout(()=>{status.textContent='';},2200);
      return;
    }
    fallback.hidden=false;
    const field=node('flowCopyField');
    field.value=path;
    status.textContent='Copy unavailable.';
    if(document.activeElement===focused){
      field.focus();
      field.select();
    }
  }
  function renderDetails() {
    if(!detail)return;
    const d=detail,f=d.flow;
    const external=d.source_kind&&d.source_kind!=='process';
    const run=[['Run',d.attempt_number,numericFact],['State',displayState(d.status,d.source_kind)]];
    const logSave=['Log save',d.log_file_error?.message||'No save error reported'];
    if(external){
      renderExternalFacts(d,run,logSave);
    }else{
      externalFacts=null;
      const completion=(typeof JobsColumns!=='undefined'?JobsColumns.completionLabel(d.completion_reason):d.completion_reason)||'—';
      const rawSummary={job:d.name,attempt_number:d.attempt_number,status:d.status,completion_reason:d.completion_reason??null,cmd:d.cmd,cwd:d.cwd,returncode:d.returncode??null,priority:d.priority,timeout:d.timeout??null,encoding:d.encoding,pty:!!d.pty,max_retries:d.max_retries??0,stop_policy:d.stop_policy};
      mark('flowExecutionFacts',facts([['Job',d.name],...run,['Completion',completion],['Command',Array.isArray(d.cmd)?d.cmd.join(' '):d.cmd],['Elapsed',d.elapsed_str,numericFact],['Exit code',d.returncode??'—',numericFact],['Working directory',d.cwd],['Priority',d.priority,numericFact],['Resources',d.resources],['Timeout',d.timeout??'—',numericFact],['Encoding',d.encoding],['PTY',d.pty],['Retries',d.max_retries,numericFact],['Stop policy',d.stop_policy],logSave])+'<button type="button" class="flow-link" data-flow-spec="execution">Environment, prompt and stop settings</button>'+`<details class="flow-raw-details"><summary>Raw execution configuration (JSON)</summary><pre class="flow-spec-code">${escape(JSON.stringify(rawSummary,null,2))}</pre></details>`);
    }
    const stages=(label,list,kind)=>'<tr><th scope="row">'+label+'</th><td>'+(list.length?list.map(s=>`<button type="button" class="flow-link" data-flow-spec="${kind}:${escape(s.name)}">${escape(s.name)}${s.version?' · v'+escape(s.version):''}</button>`).join('<br>'):'<span class="help-text">—</span>')+'</td></tr>';
    if (!f) { mark('flowDefinitionFacts','<div class="flow-no-data">No Flow attached to this job.</div><button type="button" class="flow-link" data-flow-spec="diagnostics">Execution diagnostics</button>'); status('Job execution settings · no Flow attached'); return; }
    const rawFlow={name:f.name,version:f.version,result_source:f.result_source||'default',error_count:f.error_count||0,automation_error_count:f.automation_error_count||0,parsers:(f.parsers||[]).map(s=>s.name),analyzers:(f.analyzers||[]).map(s=>s.name),automations:(f.automations||[]).map(s=>s.name)};
    mark('flowDefinitionFacts','<table class="flow-facts"><tbody><tr><th scope="row">Flow</th><td>'+escape(f.name)+' · v'+escape(f.version)+'</td></tr><tr><th scope="row">Result policy</th><td>'+escape(f.result_source||'default')+'</td></tr>'+stages('Parsers',f.parsers||[],'parser')+stages('Analyzers',f.analyzers||[],'analyzer')+stages('Automations',f.automations||[],'automation')+'</tbody></table><button type="button" class="flow-link" data-flow-spec="diagnostics">Diagnostics: '+escape(f.error_count||0)+' analysis / '+escape(f.automation_error_count||0)+' automation errors</button>'+`<details class="flow-raw-details"><summary>Raw Flow configuration (JSON)</summary><pre class="flow-spec-code">${escape(JSON.stringify(rawFlow,null,2))}</pre></details>`);
    status('Job execution and attached Flow configuration');
  }
  function automationDefs(latest=config().mode==='latest'){
    return latest?[{key:'inputs',label:'Inputs',width:170},{key:'state',label:'State',width:80},{key:'operation',label:'Operation',width:160},{key:'receipt',label:'Receipt',width:190}]:
      [{key:'source',label:'Source',width:180},{key:'operation',label:'Operation',width:100},{key:'receipt',label:'Receipt',width:170},{key:'reason',label:'Reason',width:160}];
  }
  function renderAutomation() {
    const layout=typeof ReadingLayout!=='undefined'?ReadingLayout:null;
    const existingTable=node('flowAutomationRows')?.querySelector?.('table');
    if(existingTable&&layout?.isResizing?.(existingTable))return;
    const automations=detail?.flow?.automations||[],latest=config().mode==='latest';
    const scope=JSON.stringify([detail?.flow?.name,detail?.flow?.version,'automation',automation,latest?'latest':'history']);
    const fixed=[{key:'automation',label:'Automation',width:170}];
    const defs=automationDefs(latest),defMap=new Map(defs.map(d=>[d.key,d])),available=defs.map(d=>d.key);
    layout?.register(available,scope);
    const ordered=layout?layout.order(available,scope):available;
    const fields=layout?ordered.filter(k=>layout.isVisible(scope,k)):available;
    const columns=layout?layout.columns(fields.map(k=>defMap.get(k)),scope,fixed):null;
    const items=latest?automations.filter(a=>!automation||a.name===automation).map(a=>({item:a,row:a.last})):
      rows.map(row=>({item:automations.find(a=>a.name===row.producer)||{name:row.producer},row}));
    const clip=text=>`<span class="flow-clip" title="${escape(text)}">${escape(text)}</span>`;
    const heading=fixedHeaders(scope,fixed)+fields.map(k=>{
      const c=defMap.get(k);
      return `<th scope="col" data-reading-field="${escape(c.key)}" tabindex="0" title="${escape(c.title||c.label)} · Drag to reorder; Alt+Left/Right">${escape(c.label)}${columnHandle(scope,c.key,c.label,false,c.width)}</th>`;
    }).join('')+'<th class="col-filler" aria-hidden="true"></th>';
    const body=items.map(({item,row})=>{
      const data=row?.data||{},saved=Number.isSafeInteger(row?.seq)&&row.seq>0;
      const isSelected=saved&&selectedRows.automation===row.seq;
      const state=latest?(item.error?'Error':item.enabled===false?'Disabled':'Enabled'):(data.status??'—');
      const source=data.source,sourceSeq=Number(source?.seq);
      const sourceLink=Number.isSafeInteger(sourceSeq)&&sourceSeq>0?
        ` <button type="button" class="flow-link flow-action-source" data-flow-publication="${sourceSeq}" title="Inspect source publication and its evidence">${escape(source.name||source.producer||source.stage||'Source')} ↗</button>`:
        source?.stage==='job'?` <button type="button" class="flow-link flow-action-source" data-flow-full="${row.seq}" title="Inspect saved Job event snapshot and operation">${escape('job.'+source.name)} ↗</button>`:'';
      const operation=!saved?'No operation':row.truncated?'Saved operation preview':data.action??'—';
      const operationLink=saved?`<button type="button" class="flow-link" data-flow-full="${row.seq}" title="Inspect exact saved operation, arguments and receipt">${escape(operation)}</button>`:escape(operation);
      const receipt=!saved?'—':row.truncated?(row.data_preview||'Preview; inspect full output'):
        `${data.status??'—'}${data.receipt===undefined||data.receipt===null?'':' · '+preview(data.receipt)}${data.error?' · '+data.error:''}`;
      const cellFor=k=>{
        if(k==='inputs')return `<td>${clip([...(item.inputs||[]),...(item.on_job_event?['Job: started, finished']:[])].join(', ')||'—')}</td>`;
        if(k==='state')return `<td title="${escape(item.error?.message||item.error||state)}">${escape(state)}</td>`;
        if(k==='operation')return `<td>${operationLink}${latest?sourceLink:''}</td>`;
        if(k==='receipt')return `<td>${clip(receipt)}</td>`;
        if(k==='source')return `<td>${sourceLink||'—'}</td>`;
        if(k==='reason')return `<td title="Operation kwargs.reason, when provided">${clip(data.kwargs?.reason??'—')}</td>`;
        return '<td>—</td>';
      };
      return `<tr${isSelected?' class="selected"':''}${saved?` data-flow-row="${row.seq}" data-flow-position="${Number(row.request_id||data.request_id)||row.seq}" data-flow-source="${row.evidence?.line_no||row.evidence?.line_start||0}"`:''}><td><button type="button" class="flow-link" data-flow-spec="automation:${escape(item.name)}" title="Inspect Automation specification">${clip(item.name)}</button></td>`+
        fields.map(cellFor).join('')+'<td class="col-filler"></td></tr>';
    }).join('');
    const active=document.activeElement,activeRow=active?.closest?.('[data-flow-position]');
    const focused=activeRow?{request:activeRow.dataset.flowPosition,kind:['flowFull','flowSpec','flowPublication'].find(key=>active.dataset?.[key])}:null;
    mark('flowAutomationRows',(error?'<div class="flow-error">'+escape(error)+'</div>':'')+(items.length?'<table class="flow-records flow-automation-table'+(columns?' reading-columns':'')+'"'+(columns?' data-reading-scope="'+escape(scope)+'" style="width:100%;min-width:'+columns.size+'px"':'')+'>'+(columns?.html||'')+'<thead><tr>'+heading+'</tr></thead><tbody>'+body+'</tbody></table>':`<div class="flow-no-data">${pending?'Reading saved operations…':hasFlow()?(latest?'No Automations attached.':'No saved operations.'):'No Flow attached. No Automation definitions.'}</div>`));
    if(items.length)layout?.autoFitInitial?.(node('flowAutomationRows')?.querySelector?.('table'),scope,{maxWidth:220,maxByField:{automation:220,inputs:240,receipt:280,reason:280,source:180}});
    if(focused?.kind){const row=node('flowAutomationRows').querySelector(`[data-flow-position="${Number(focused.request)}"]`);[...(row?.querySelectorAll('button')||[])].find(button=>button.dataset[focused.kind])?.focus({preventScroll:true});}
    syncOutputControls('automation');
    status(`${automation||'All Automations'} · ${items.length} ${latest?'attached':'loaded operations'}${pending?' · reading…':''}`);
  }
  function openColumnMenu(event,th,table){
    if(typeof ReadingLayout==='undefined'||typeof ReadingLayout.openColumnMenu!=='function'||!detail?.flow)return null;
    const targetTh=th||event?.target?.closest?.('th');
    const targetTable=table||targetTh?.closest?.('table')||host()?.querySelector?.('table');
    let scope,allColumns;
    if(readerPage==='automation'){
      const latest=config().mode==='latest',defs=automationDefs(latest);
      scope=JSON.stringify([detail.flow.name,detail.flow.version,'automation',automation,latest?'latest':'history']);
      allColumns=[{key:'automation',label:'Automation',fixed:true},...defs.map(d=>({key:d.key,label:d.label,fixed:false}))];
    }else{
      const reports=outputKind()==='report',showProducer=!producer();
      scope=JSON.stringify([detail.flow.name,detail.flow.version,readerPage,producer()]);
      const fixed=[{key:'source',label:config().mode==='latest'&&config().policy==='fields'?'As of':'Source',fixed:true},
        ...(showProducer?[{key:'producer',label:readerPage==='analysis'?'Analyzer':'Parser',fixed:true}]:[]),
        ...(reports?[{key:'name',label:'Name',fixed:true},{key:'result',label:'Result',fixed:true},{key:'reason',label:'Reason',fixed:true}]:[])];
      const available=[...knownFields].slice(0,MAX_COLUMNS);
      ReadingLayout.register(available,scope,DEFAULT_VISIBLE_COLUMNS);
      allColumns=[...fixed,...available.map(k=>({key:k,label:k,fixed:false}))];
    }
    return ReadingLayout.openColumnMenu(event,{
      table:targetTable,
      th:targetTh,
      key:scope,
      allColumns,
      onChange:()=>{if(reading())renderReader();},
      reset:()=>{ReadingLayout.reset(scope);if(reading())renderReader();}
    });
  }
  function spec(kind) {
    if(!detail)return;
    closeInspection();
    if(kind==='execution'){
      node('flowSpecTitle').textContent='Execution configuration';
      const settings={};for(const key of ['cmd','cwd','env','prompts','pty','encoding','resources','priority','timeout','max_retries','retry_index','stop_policy','stop_cmd','stop_ctrl_c','stop_timeout','max_log_lines','attempt_max_log_lines','log_count','total_lines','history_truncated','completion_reason'])if(Object.prototype.hasOwnProperty.call(detail,key))settings[key]=detail[key];
      const completion=(typeof JobsColumns!=='undefined'?JobsColumns.completionLabel(detail.completion_reason):detail.completion_reason)||'—';
      node('flowSpecFacts').innerHTML=facts([['Job',detail.name],['Completion',completion],['Stop policy',detail.stop_policy],['Stop command',detail.stop_cmd],['Prompts',detail.prompts],['Environment',detail.env]]);
      node('flowSpecCode').textContent=JSON.stringify(settings,null,2);node('flowSpecMore').replaceChildren();openModal('flowSpecOverlay');return;
    }
    if (!detail.flow && kind !== 'diagnostics') return;
    let list,name;
    if(kind==='parser'){list=stageList();if(!parser){allSpecs('Parsers',list);return;}name=parser;}
    else if(kind==='analyzer'){list=detail.flow.analyzers;if(!analyzer){allSpecs('Analyzers',list);return;}name=analyzer;}
    else if(kind==='diagnostics'){
      node('flowSpecTitle').textContent='Flow / execution diagnostics';
      const completion=(typeof JobsColumns!=='undefined'?JobsColumns.completionLabel(detail.completion_reason):detail.completion_reason)||'—';
      node('flowSpecFacts').innerHTML=facts([['Result policy',detail.flow?.result_source||'default'],['Completion',completion],['Analysis errors',detail.flow?.error_count||0,numericFact],['Automation errors',detail.flow?.automation_error_count||0,numericFact],['Log save',detail.log_file_error?.message||'No save error reported']]);
      node('flowSpecCode').textContent=JSON.stringify({analysis:detail.flow?.diagnostics||[],aggregation_error:detail.flow?.aggregation_error||null,result_policy:detail.flow?.result_source||'default',result_code:detail.flow?.result_code||null,publication_error:detail.flow?.publication_error||null,runner:[...(detail.errors||[]),...(detail.diagnostics||[])],termination:detail.termination||[],completion_reason:detail.completion_reason||null,log_save:detail.log_file_error||null,archive:archived()?.health||STATE.archive},null,2);node('flowSpecMore').replaceChildren();openModal('flowSpecOverlay');return;
    }else{const [type,...parts]=kind.split(':');list=type==='parser'?stageList():type==='analyzer'?detail.flow.analyzers:detail.flow?.automations;name=parts.join(':');if(type==='automation'&&!name){allSpecs('Automations',list||[]);return;}}
    const item=list?.find(s=>s.name===name);if(!item)return;
    node('flowSpecTitle').textContent=item.name;
    const automation=(detail.flow?.automations||[]).includes(item);
    node('flowSpecFacts').innerHTML=facts(automation?[['Inputs',item.inputs||[]],['Enabled',item.enabled?'Yes':'No'],['Report callback',item.on_report?'Attached':'—'],['Job event callback',item.on_job_event?'Attached':'—'],['Processed callbacks',item.processed||0],['Control operations',item.action_count||0],['Saved publications',item.publication_count||0],['Error',item.error||'—']]:[['Version',item.version||'1'],['Inputs',item.inputs||'Log source'],['Output description',item.outputs||{}],['Overall verdict',item.result],['Overall reason',item.reason],...(stageList().includes(item)?[['Latest field values',item.latest]]:[]),['Current notices',item.issues],['Occurrences',item.events],['Error',item.error||'—']]);
    node('flowSpecCode').textContent=item.code||'Source code is not available for this callable.';node('flowSpecMore').replaceChildren();openModal('flowSpecOverlay');
  }
  async function full(seq,offset=0,projection='',publication=null) {
    if(!detail||!seq)return;closeInspection();const token=identity,inspection=++inspectionEpoch;
    node('flowSpecTitle').textContent='Saved publication '+seq;node('flowSpecFacts').innerHTML=publication?facts([['Stage',publication.stage],['Producer',publication.producer],['Kind',publication.kind]])+sourceButton(publication.evidence):'';node('flowSpecCode').textContent='Reading…';node('flowSpecMore').replaceChildren();openModal('flowSpecOverlay');
    try{
      const chunk=await RunHistory.query({view:projection?'flow_latest_text':'flow_text',seq,offset,...(projection?{mode:projection}:{})},detail);if(token!==identity||inspection!==inspectionEpoch)return;
      node('flowSpecCode').textContent=chunk.text||'';
      node('flowSpecMore').innerHTML=`<span class="help-text">${escape(chunk.offset||0)}–${escape(chunk.next_offset||0)} / ${escape(chunk.total||0)} ${escape(chunk.offset_unit||'characters')}</span>`+(chunk.has_more?` <button type="button" data-flow-chunk="${seq}" data-flow-projection="${projection}" data-offset="${chunk.next_offset}">Next chunk</button>`:'');
    }catch(error){if(token===identity&&inspection===inspectionEpoch)node('flowSpecCode').textContent='Could not read saved output: '+error.message;}
  }
  function closeInspection(){++inspectionEpoch;if(typeof AnalysisWorkspace.closeInspection==='function')AnalysisWorkspace.closeInspection();}

  async function sourcePublication(seq){
    if(!detail||!Number.isSafeInteger(seq)||seq<1)return;
    const execution=identity,token=++inspectionEpoch;
    try{
      const page=await RunHistory.query({view:'flow',after:seq-1,limit:1},detail);
      if(execution!==identity||token!==inspectionEpoch)return;
      const row=page.rows?.find(row=>row.seq===seq);
      if(row)await full(seq,0,'',row);else await full(seq);
    }catch(exc){if(execution===identity&&token===inspectionEpoch){node('flowSpecTitle').textContent='Saved publication '+seq;node('flowSpecFacts').innerHTML='';node('flowSpecCode').textContent='Could not read source: '+exc.message;openModal('flowSpecOverlay');}}
  }

  function selectAnalyzer(name){analyzer=name;reportName='';reportCatalog=[];node('flowReportName').value='';reportSummary();dataKey='';clearRows();open('analysis');refresh(detail);}
  function findings(kind){
    if(!detail)return;
    closeInspection();
    const selected=analyzer?detail.flow.analyzers.filter(a=>a.name===analyzer):detail.flow.analyzers;
    node('flowSpecTitle').textContent=kind==='warnings'?'Published notices':'Analysis diagnostics';node('flowSpecFacts').innerHTML='';
    node('flowSpecCode').textContent=JSON.stringify(kind==='warnings'?selected.map(a=>({analyzer:a.name,issues:a.issues||[]})):{analyzers:selected,diagnostics:detail.flow?.diagnostics,errors:detail.errors},null,2);
    node('flowSpecMore').replaceChildren();openModal('flowSpecOverlay');
  }
  function ensurePolicySegmentedControl(){
    const settings=node('flowReadingSettings');
    if(settings)settings.hidden=true;
    const actions=node('flowOutputActions');
    if(actions&&!node('flowLatestPolicyGroup')&&typeof document!=='undefined'&&typeof document.createElement==='function'){
      const group=document.createElement('span');
      group.id='flowLatestPolicyGroup';
      group.className='flow-policy-group';
      group.setAttribute?.('role','group');
      group.setAttribute?.('aria-label','Latest projection mode');
      group.innerHTML='<span class="flow-policy-label">Latest:</span>'+
        '<button type="button" id="flowPolicyRecord" data-flow-policy="record" aria-pressed="true" title="Show latest whole record">Record</button>'+
        '<button type="button" id="flowPolicyFields" data-flow-policy="fields" aria-pressed="false" title="Merge latest value per field across records">Merged</button>';
      actions.appendChild(group);
    }
    node('flowPolicyRecord')?.addEventListener?.('click',()=>changePolicy('record'));
    node('flowPolicyFields')?.addEventListener?.('click',()=>changePolicy('fields'));
  }
  function initialize(){
    if(initialized)return;initialized=true;priorityEditor=node('priorityEditor');
    ensurePolicySegmentedControl();
    if(typeof ReadingLayout!=='undefined')ReadingLayout.initialize(node('flowWorkspaceView'),()=>{if(reading())renderReader();});
    node('flowParser').addEventListener('change',()=>{parser=node('flowParser').value;reload();});
    node('flowAnalyzer').addEventListener('change',()=>{analyzer=node('flowAnalyzer').value;reportName='';reportCatalog=[];node('flowReportName').value='';reportSummary();reload();reportNames();});
    node('flowAutomation').addEventListener('change',()=>{automation=node('flowAutomation').value;reload();});
    node('flowAutomationSpec').addEventListener('click',()=>spec('automation:'+automation));
    const applyName=()=>chooseReport(node('flowReportName').value);
    node('flowReportSelect').addEventListener('change',()=>{
      const value=node('flowReportSelect').value;
      if(value==='find'){reportSummary();renderReportChoices();openModal('flowReportOverlay','flowReportName');}
      else chooseReport(value.startsWith('name:')?value.slice(5):'');
    });
    node('flowReportName').addEventListener('input',renderReportChoices);
    node('flowReportName').addEventListener('keydown',e=>{if(e.key==='Enter')applyName();});
    node('flowReportApply').addEventListener('click',applyName);
    node('flowHistoryMode').addEventListener('click',()=>changeMode('history'));
    node('flowLatestMode').addEventListener('click',()=>changeMode('latest'));
    node('flowLatestPolicy').addEventListener('change',()=>changePolicy(node('flowLatestPolicy').value));
    node('flowFollow').addEventListener('change',()=>{const page=currentPage();if(!reading())return;readerPage=page;views[page].follow=node('flowFollow').checked&&!readOnly();syncOutputControls(page);if(views[page].follow&&config().mode==='history')followTail();remember();});
    node('flowParserSpec').addEventListener('click',()=>spec('parser'));node('flowAnalyzerSpec').addEventListener('click',()=>spec('analyzer'));
    for(const page of ['parsed','analysis','automation'])node(`flow${page[0].toUpperCase()+page.slice(1)}Scroll`).addEventListener('scroll',()=>{
      if(pending||DETAIL_VIEW!==page)return;positions[page]=box().scrollTop;
      if(Date.now()>=programmaticUntil&&box().scrollHeight-box().scrollTop-box().clientHeight>150)pauseFollow(page);
      if(page==='automation'&&config().mode==='latest')return;
      if(box().scrollTop<80&&moreBackward)load('backward');else if(box().scrollHeight-box().scrollTop-box().clientHeight<150&&moreForward)load();
    });
    for(const page of ['parsed','analysis','automation']){
      const scroll=node(`flow${page[0].toUpperCase()+page.slice(1)}Scroll`);
      scroll.addEventListener('wheel',e=>{if(e.deltaY<0)pauseFollow(page);},{passive:true});
      scroll.addEventListener('keydown',e=>{if(['ArrowUp','PageUp','Home'].includes(e.key))pauseFollow(page);});
    }
    node('flowDetailsScroll').addEventListener('scroll',()=>{if(DETAIL_VIEW==='details')positions.details=node('flowDetailsScroll').scrollTop;});
    node('flowWorkspaceView').addEventListener('dblclick',e=>{const row=e.target.closest?.('[data-flow-row]');if(row&&!e.target.closest('button,input,select'))evidence(Number(row.dataset.flowSource));});
    node('flowWorkspaceView').addEventListener('contextmenu',e=>{
      const th=e.target?.closest?.('th');
      const thead=th?.closest?.('thead')||e.target?.closest?.('thead');
      const table=thead?.closest?.('table');
      if(thead&&table)openColumnMenu(e,th,table);
    });
    node('flowWorkspaceView').addEventListener('keydown',e=>{
      if((e.key==='F10'&&e.shiftKey)||e.key==='ContextMenu'){
        const th=e.target?.closest?.('th');
        const table=th?.closest?.('table');
        if(th&&table)openColumnMenu(e,th,table);
      }
    });
    document.addEventListener('click',e=>{
      const b=e.target.closest?.('button');
      if(b){
        if(b.id==='flowCopyPath')copySourcePath(b);
        else if(Object.hasOwn(b.dataset,'flowReportName'))chooseReport(b.dataset.flowReportName);
        else if(b.dataset.flowEvidence){closeModal('flowSpecOverlay');evidence(Number(b.dataset.flowEvidence));}
        else if(b.dataset.flowSpec)spec(b.dataset.flowSpec);
        else if(b.dataset.flowPublication)sourcePublication(Number(b.dataset.flowPublication));
        else if(b.dataset.flowFull)full(Number(b.dataset.flowFull),0,b.dataset.flowProjection||'');
        else if(b.dataset.flowChunk)full(Number(b.dataset.flowChunk),Number(b.dataset.offset),b.dataset.flowProjection||'');
        else if(b.dataset.flowSection){const target=node({execution:'flowExecutionSection',definition:'flowDefinitionSection'}[b.dataset.flowSection]);node('flowDetailsScroll').scrollTop=target.offsetTop-node('flowDetailsScroll').offsetTop;}
        return;
      }
      if(!reading())return;
      const row=e.target?.closest?.('[data-flow-row]');
      if(row&&!e.target?.closest?.('button,input,select,a')){
        const seq=Number(row.dataset?.flowRow);
        if(Number.isFinite(seq)&&seq>0){
          const next=selectedRows[readerPage]===seq?null:seq;
          selectedRows[readerPage]=next;
          const liveRows=Array.from(host()?.querySelectorAll?.('tr[data-flow-row]')||[]);
          if(liveRows.length>0){
            for(const tr of liveRows)tr.classList?.toggle?.('selected',next!==null&&Number(tr.dataset?.flowRow)===next);
          }else{
            row.classList?.toggle?.('selected',next===seq);
            renderReader();
          }
        }
      }
    });
  }
  function refresh(d){
    initialize();if(!d){if(identity)reset();return;}
    const changed=key(d)!==identity;
    if(changed){remember();clearRows();identity=key(d);dataKey='';detail=d;++copyRequest;knownFields.clear();selectedRows={parsed:null,analysis:null,automation:null};if(!node('flowReportOverlay').hidden)closeModal('flowReportOverlay');
      const stored=preferences.get(identity);parser=stored?.parser||'';analyzer=stored?.analyzer||'';automation=stored?.automation||'';reportName=stored?.reportName||'';reportCatalog=[];
      views={...defaults(),...stored?.views};positions=stored?.positions||{parsed:0,analysis:0,automation:0,details:0};anchors=stored?.anchors||{};
    }else detail=d;
    for(const id of ['flowParser','flowAnalyzer','flowAutomation','flowReportSelect'])node(id).disabled=!d.flow;
    if(!d.flow){for(const id of ['flowParser','flowAnalyzer','flowAutomation'])mark(id,'<option value="">No Flow attached</option>');for(const id of ['flowParserSpec','flowAnalyzerSpec','flowAutomationSpec'])node(id).disabled=true;}
    if(d.flow){
      if(parser&&!stageList().some(s=>s.name===parser))parser='';
      if(analyzer&&!d.flow.analyzers.some(s=>s.name===analyzer))analyzer='';
      if(automation&&!(d.flow.automations||[]).some(s=>s.name===automation))automation='';
      const options=(items,label)=>'<option value="">All '+label+'</option>'+items.map(s=>`<option value="${escape(s.name)}">${escape(s.name)}</option>`).join('');
      mark('flowParser',options(stageList(),'parsers'));node('flowParser').value=parser;
      mark('flowAnalyzer',options(d.flow.analyzers,'analyzers'));node('flowAnalyzer').value=analyzer;
      mark('flowAutomation',options(d.flow.automations||[],'Automations'));node('flowAutomation').value=automation;node('flowAutomationSpec').disabled=!(d.flow.automations||[]).length;
      if(changed){node('flowReportName').value=reportName;reportSummary();}node('flowAnalyzerSpec').disabled=!d.flow.analyzers.length;node('flowParserSpec').disabled=!stageList().length;
    }
    display(DETAIL_VIEW);
    if(reading()){
      if(!d.flow){readerPage=DETAIL_VIEW;mark(readerPage==='parsed'?'flowParsedRows':readerPage==='analysis'?'flowAnalysisRows':'flowAutomationRows','<div class="flow-no-data">No Flow attached. No '+(readerPage==='parsed'?'Parser':readerPage==='analysis'?'Analyzer':'Automation')+' definitions.</div>');status('No Flow attached to this job');return;}
      if(DETAIL_VIEW==='analysis'&&analyzer&&d.flow.analyzers.some(a=>a.name===analyzer&&!(a.report_count>0)&&a.count>0))views.analysis.mode='history';
      const next=[DETAIL_VIEW,producerFor(DETAIL_VIEW),reportName,views[DETAIL_VIEW].mode,views[DETAIL_VIEW].policy].join('\u0000');
      const queryChanged=next!==dataKey;
      if(queryChanged){clearRows();knownFields.clear();readerPage=DETAIL_VIEW;dataKey=next;restoreAnchor=anchors[readerPage]||null;restoreTop=positions[readerPage]||0;
        if(restoreAnchor?.seq>0){forward=restoreAnchor.seq-1;backward=restoreAnchor.seq;moreBackward=restoreAnchor.seq>1;}
      }
      const total=count(),growth=total!==lastCount;if(growth)moreForward=true;
      renderReader();
      if(hiddenLayout()){if(changed)layoutChanged();return;}
      if(readerPage==='automation'&&config().mode==='latest'){lastCount=total;if(changed)layoutChanged();return;}
      const saved=archived();
      if(saved?.follow){
        const follow=saved.follow==='following'||(saved.catchUp&&!archiveSettled);
        if(!pending&&(queryChanged||follow&&moreForward))load();
        else if(!pending&&saved.follow==='finished')archiveSettled=true;
      }else if(!pending&&config().mode==='history'&&config().follow&&!readOnly()&&(queryChanged||growth))followTail();
      else if(!pending&&readerPage==='automation'&&growth&&!queryChanged&&rows.some(row=>row.truncated||row.data?.phase==='requested'))refreshPendingActions();
      else if(!pending&&(queryChanged||!rows.length&&forward===0||growth&&(config().mode==='latest'||box().scrollHeight-box().scrollTop-box().clientHeight<150)))load('forward',config().mode==='latest'&&growth&&!queryChanged);
      lastCount=total;if(readerPage==='analysis'&&(changed||queryChanged||growth))reportNames();
    }else if(DETAIL_VIEW==='details')renderDetails();
    if(changed)layoutChanged();
  }
  function producerFor(page){return page==='parsed'?parser:page==='automation'?automation:analyzer;}
  return {refresh,open,display,reset,hasFlow,evidence,selectAnalyzer,findings,closeInspection,rememberLayout,layoutChanged,openColumnMenu,changePolicy,diagnostics:()=>spec('diagnostics')};
})();
