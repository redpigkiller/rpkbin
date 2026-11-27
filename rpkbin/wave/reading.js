/* Display-only reading layout. No data interpretation or process actions. */
const ReadingLayout = (() => {
  const STORAGE='wave_reading_layout_v1', MAX_PROFILES=32, MIN_WIDTH=56, MAX_WIDTH=1200, MAX_AUTOFIT=480, INITIAL_MAX_AUTOFIT=240;
  const profiles=new Map(), autoFittedScopes=new Set();
  let ready=false, split={right:44,below:40}, resize=null, moving=null, justDragged=false, draggingContext=false;
  let activeMenu=null, menuGlobalBound=false, measureCtx=null;
  const listeners=new Map();
  const changed=()=>{for(const callback of listeners.values())callback();};
  const node=id=>document.getElementById(id), clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
  const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function load(){
    try{
      const saved=JSON.parse(window.localStorage.getItem(STORAGE)||'{}');
      for(const mode of ['right','below'])if(Number.isFinite(saved.split?.[mode]))split[mode]=clamp(saved.split[mode],15,85);
      for(const [key,value] of (Array.isArray(saved.profiles)?saved.profiles:[]).slice(-MAX_PROFILES)){
        if(typeof key!=='string'||key.length>2048||!value)continue;
        profiles.set(key,{
          order:Array.isArray(value.order)?[...new Set(value.order.filter(k=>typeof k==='string'))].slice(0,256):[],
          hidden:Array.isArray(value.hidden)?[...new Set(value.hidden.filter(k=>typeof k==='string'))].slice(0,256):[],
          widths:new Map((Array.isArray(value.widths)?value.widths:[]).filter(p=>Array.isArray(p)&&typeof p[0]==='string'&&Number.isFinite(p[1])).slice(0,256).map(([k,v])=>[k,clamp(v,MIN_WIDTH,MAX_WIDTH)])),
          fixedWidths:new Map((Array.isArray(value.fixedWidths)?value.fixedWidths:[]).filter(p=>Array.isArray(p)&&typeof p[0]==='string'&&Number.isFinite(p[1])).slice(0,32).map(([k,v])=>[k,clamp(v,MIN_WIDTH,MAX_WIDTH)])),
          autoWidths:new Map(),
          autoFixedWidths:new Map()
        });
      }
    }catch(_){/* Optional display preferences never block the reader. */}
  }
  function save(){
    while(profiles.size>MAX_PROFILES)profiles.delete(profiles.keys().next().value);
    try{window.localStorage.setItem(STORAGE,JSON.stringify({split,profiles:[...profiles].map(([k,p])=>[k,{order:p.order,hidden:p.hidden,widths:[...p.widths],fixedWidths:[...p.fixedWidths]}])}));}catch(_){}
  }
  function hasProfile(key){
    if(!ready){load();ready=true;}
    return profiles.has(key);
  }
  function profile(key){
    if(!ready){load();ready=true;}
    if(!profiles.has(key))profiles.set(key,{order:[],hidden:[],widths:new Map(),fixedWidths:new Map(),autoWidths:new Map(),autoFixedWidths:new Map()});
    return profiles.get(key);
  }
  function order(fields,key){
    const saved=profile(key).order;
    return [...saved.filter(k=>fields.includes(k)),...fields.filter(k=>!saved.includes(k))];
  }
  function hidden(key){
    return [...profile(key).hidden];
  }
  function isVisible(key,field){
    return !profile(key).hidden.includes(field);
  }
  function setVisible(key,field,show=true){
    if(typeof field!=='string'||typeof show!=='boolean')return;
    const p=profile(key),idx=p.hidden.indexOf(field);
    if(show&&idx>=0){p.hidden.splice(idx,1);save();changed();}
    else if(!show&&idx<0){if(p.hidden.length<256)p.hidden.push(field);save();changed();}
  }
  const width=(key,field,fixed=false,fallback=160)=>{
    const p=profile(key);
    const explicit=(fixed?p.fixedWidths:p.widths).get(field);
    if(explicit!==undefined)return explicit;
    const auto=(fixed?p.autoFixedWidths:p.autoWidths).get(field);
    return auto!==undefined?auto:fallback;
  };
  function columns(fields,key,fixed=[]){
    const sizes=[
      ...fixed.map(item=>typeof item==='number'?item:width(key,item.key,true,item.width)),
      ...fields.map(item=>typeof item==='object'&&item!==null?width(key,item.key,false,item.width):width(key,item))
    ];
    return {size:sizes.reduce((n,v)=>n+v,0),html:'<colgroup>'+sizes.map(v=>`<col style="width:${v}px">`).join('')+'<col class="col-filler"></colgroup>'};
  }
  function reorder(key,from,to,side){
    const p=profile(key),items=p.order;
    if(from===to||!items.includes(from)||!items.includes(to))return;
    const fromIndex=items.indexOf(from),oldToIndex=items.indexOf(to);
    items.splice(fromIndex,1);
    const toIndex=items.indexOf(to);
    const insertIndex=side==='right'?toIndex+1:side==='left'?toIndex:(fromIndex<oldToIndex?toIndex+1:toIndex);
    items.splice(insertIndex,0,from);save();changed();
  }
  function register(fields,key,defaultVisible){
    const p=profile(key);
    if(defaultVisible!==undefined){
      if(typeof defaultVisible==='number'){
        let visibleCount=p.order.filter(f=>!p.hidden.includes(f)).length;
        for(const f of fields){
          if(typeof f!=='string'||p.order.includes(f)||p.hidden.includes(f))continue;
          if(visibleCount>=defaultVisible){if(p.hidden.length<256)p.hidden.push(f);}
          else visibleCount++;
        }
      }else{
        const visSet=defaultVisible instanceof Set?defaultVisible:new Set(Array.isArray(defaultVisible)?defaultVisible:[]);
        for(const f of fields){
          if(typeof f!=='string'||p.order.includes(f)||p.hidden.includes(f))continue;
          if(!visSet.has(f)&&p.hidden.length<256)p.hidden.push(f);
        }
      }
    }
    p.order=order(fields,key).slice(0,256);
  }
  function currentColumnWidth(table,th,key,field,fixed){
    const headers=[...(table?.querySelectorAll?.('thead th')||[])];
    const index=headers.findIndex(h=>h===th||(fixed?h.dataset?.readingFixed:h.dataset?.readingField)===field);
    const cols=[...(table?.querySelectorAll?.('col')||[])];
    const colW=index>=0&&cols[index]?parseFloat(cols[index].style?.width||'0'):0;
    if(colW>0)return colW;
    return width(key,field,fixed,th?.getBoundingClientRect?.().width||160);
  }
  function resizeColumn(table,key,field,size,fixed=false){
    const clamped=clamp(Math.round(size),MIN_WIDTH,MAX_WIDTH);
    (fixed?profile(key).fixedWidths:profile(key).widths).set(field,clamped);
    if(!table)return clamped;
    const headers=[...(table.querySelectorAll?.('thead th')||[])],index=headers.findIndex(h=>(fixed?h.dataset?.readingFixed:h.dataset?.readingField)===field);
    const cols=[...(table.querySelectorAll?.('col')||[])];
    if(index>=0&&cols[index])cols[index].style.width=width(key,field,fixed)+'px';
    const total=cols.reduce((n,c)=>n+(c.classList?.contains?.('col-filler')?0:parseFloat(c.style?.width||'0')),0);
    if(table.style){table.style.width=total+'px';table.style.minWidth=total+'px';}
    headers[index]?.querySelector?.('[data-column-resize]')?.setAttribute?.('aria-valuenow',String(Math.round(width(key,field,fixed))));
    return clamped;
  }
  function measureTextPx(text,sourceEl){
    const str=String(text??'').trim();
    if(!str)return 0;
    try{
      if(!measureCtx&&typeof document!=='undefined'&&typeof document.createElement==='function'){
        const canvas=document.createElement('canvas');
        if(canvas&&typeof canvas.getContext==='function')measureCtx=canvas.getContext('2d');
      }
      if(measureCtx){
        const style=typeof getComputedStyle==='function'&&sourceEl?getComputedStyle(sourceEl):null;
        measureCtx.font=(style&&style.font)||'12px monospace';
        const m=measureCtx.measureText(str);
        if(m&&Number.isFinite(m.width)&&m.width>0)return m.width;
      }
    }catch(_){}
    return str.length*7.5;
  }
  function measureFittedWidth(table,th,index,field,maxCap=MAX_AUTOFIT,maxRows=200){
    const headerText=String(th?.textContent??field).replace(/[↑↓]/g,'').trim();
    let best=measureTextPx(headerText,th)+28;
    if(table&&index>=0){
      const rows=[...(table.querySelectorAll?.('tbody tr')||[])].filter(tr=>!tr.classList?.contains?.('jobs-empty')).slice(0,maxRows);
      for(const tr of rows){
        const cells=tr.children||tr.querySelectorAll?.('td')||[];
        const td=cells[index];
        if(!td||(td.colSpan&&td.colSpan>1))continue;
        const cellText=String(td.textContent??'').trim();
        const extra=td.querySelectorAll?(td.querySelectorAll('button,svg,.status-badge').length*10):0;
        const w=measureTextPx(cellText,td)+20+extra;
        if(w>best)best=w;
      }
    }
    return clamp(Math.ceil(best),MIN_WIDTH,maxCap);
  }
  function autoFitColumn(table,key,field,fixed=false,silent=false){
    const scopeKey=key||table?.dataset?.readingScope;
    if(!scopeKey||!field)return MIN_WIDTH;
    const headers=[...(table?.querySelectorAll?.('thead th')||[])];
    const index=headers.findIndex(h=>(fixed?h.dataset?.readingFixed:h.dataset?.readingField)===field);
    const th=index>=0?headers[index]:null;
    const fitted=measureFittedWidth(table,th,index,field,MAX_AUTOFIT,200);
    resizeColumn(table,scopeKey,field,fitted,fixed);
    if(!silent){save();changed();}
    return fitted;
  }
  function autoFitInitial(table,key,options={}){
    const scopeKey=key||table?.dataset?.readingScope;
    if(!table||!scopeKey||autoFittedScopes.has(scopeKey))return false;
    if(resize?.table===table||moving?.table===table)return false;
    const dataRows=[...(table.querySelectorAll?.('tbody tr')||[])].filter(tr=>!tr.classList?.contains?.('jobs-empty'));
    if(!dataRows.length)return false;
    autoFittedScopes.add(scopeKey);
    const p=profile(scopeKey);
    const headers=[...(table.querySelectorAll?.('thead th')||[])];
    const cols=[...(table.querySelectorAll?.('col')||[])];
    const defaultMax=options.maxWidth||INITIAL_MAX_AUTOFIT;
    const maxRows=options.maxRows||100;
    headers.forEach((th,index)=>{
      const fixed=th.dataset?.readingFixed!==undefined;
      const field=fixed?th.dataset.readingFixed:th.dataset?.readingField;
      if(!field)return;
      if((fixed?p.fixedWidths:p.widths).has(field))return;
      const maxCap=(options.maxByField&&Object.hasOwn(options.maxByField,field)&&Number.isFinite(options.maxByField[field]))?options.maxByField[field]:defaultMax;
      const fitted=measureFittedWidth(table,th,index,field,maxCap,maxRows);
      (fixed?p.autoFixedWidths:p.autoWidths).set(field,fitted);
      if(cols[index]?.style)cols[index].style.width=fitted+'px';
      th.querySelector?.('[data-column-resize]')?.setAttribute?.('aria-valuenow',String(fitted));
    });
    if(cols.length&&table.style){
      const total=cols.reduce((n,c)=>n+(c.classList?.contains?.('col-filler')?0:parseFloat(c.style?.width||'0')),0);
      if(total>0)table.style.minWidth=total+'px';
    }
    return true;
  }
  function autoFitAll(table,key){
    const scopeKey=key||table?.dataset?.readingScope;
    if(!scopeKey||!table)return;
    const headers=[...(table.querySelectorAll?.('thead th')||[])];
    for(const th of headers){
      if(th.dataset?.readingFixed!==undefined)autoFitColumn(table,scopeKey,th.dataset.readingFixed,true,true);
      else if(th.dataset?.readingField!==undefined)autoFitColumn(table,scopeKey,th.dataset.readingField,false,true);
    }
    save();changed();
  }
  function closeColumnMenu(restoreFocus=false){
    const opener=activeMenu?.opener;
    const menu=node('waveColumnMenu');
    if(menu)menu.hidden=true;
    activeMenu=null;
    if(restoreFocus&&opener&&opener.isConnected!==false){
      opener.focus?.({preventScroll:true});
    }
  }
  function renderColumnMenu(menu,preserveFilter=false){
    if(!menu||!activeMenu)return;
    const {key,clickedField,clickedLabel,clickedFixed,allColumns=[],filterText=''}=activeMenu;
    const movableKeys=allColumns.filter(c=>!c.fixed).map(c=>c.key);
    if(key&&movableKeys.length)register(movableKeys,key);
    const orderedMovable=key?order(movableKeys,key):movableKeys;
    const visibleMovable=key?orderedMovable.filter(k=>isVisible(key,k)):orderedMovable;
    const byKey=new Map(allColumns.map(c=>[c.key,c]));
    const orderedColumns=[...allColumns.filter(c=>c.fixed),...orderedMovable.map(k=>byKey.get(k)).filter(Boolean)];
    const hasClicked=!!clickedField;
    const isFixed=!!clickedFixed||!hasClicked||!!byKey.get(clickedField)?.fixed;
    const label=clickedLabel||byKey.get(clickedField)?.label||clickedField||'Column';
    const idx=!isFixed&&hasClicked?visibleMovable.indexOf(clickedField):-1;
    const canHide=!isFixed&&hasClicked&&(key?isVisible(key,clickedField):true)&&visibleMovable.length>0;
    const canLeft=!isFixed&&idx>0;
    const canRight=!isFixed&&idx>=0&&idx<visibleMovable.length-1;
    const q=filterText.trim().toLowerCase();
    const filteredCols=orderedColumns.filter(c=>!q||String(c.label||'').toLowerCase().includes(q)||String(c.key||'').toLowerCase().includes(q));
    const listHtml=filteredCols.length?filteredCols.map(c=>{
      const checked=c.fixed||!key||isVisible(key,c.key);
      return `<label class="column-menu-check"><input type="checkbox" data-menu-column="${escapeHtml(c.key)}"${checked?' checked':''}${c.fixed?' disabled':''}><span>${escapeHtml(c.label)}</span></label>`;
    }).join(''):'<div class="column-menu-empty help-text">No matching columns</div>';
    if(preserveFilter){
      const listEl=menu.querySelector?.('.column-menu-list');
      if(listEl&&listEl.id!=='context-home'){listEl.innerHTML=listHtml;}
    }
    const iconHtml=(name,fallback)=>(typeof WaveIcons!=='undefined'&&typeof WaveIcons.svg==='function'&&WaveIcons.svg(name))||fallback;
    const colActionsHtml=hasClicked?
      `<button type="button" class="column-menu-item" role="menuitem" data-menu-action="autofit-column">Auto-fit "${escapeHtml(label)}"</button>`+
      `<button type="button" class="column-menu-item" role="menuitem" data-menu-action="hide-column"${canHide?'':' disabled'}>Hide "${escapeHtml(label)}"</button>`+
      `<div class="column-menu-move-row">`+
      `<button type="button" class="column-menu-item column-menu-icon-btn" role="menuitem" data-menu-action="move-left" aria-label="Move Left" title="Move Left"${canLeft?'':' disabled'}>${iconHtml('arrow-left','←')}</button>`+
      `<button type="button" class="column-menu-item column-menu-icon-btn" role="menuitem" data-menu-action="move-right" aria-label="Move Right" title="Move Right"${canRight?'':' disabled'}>${iconHtml('arrow-right','→')}</button>`+
      `</div>`:'';
    const fullHtml=
      colActionsHtml+
      `<button type="button" class="column-menu-item" role="menuitem" data-menu-action="autofit-all">Auto-fit all columns</button>`+
      `<hr class="column-menu-divider">`+
      (allColumns.length>12?`<div class="column-menu-search"><input type="search" class="column-menu-filter" data-menu-filter placeholder="Filter columns…" aria-label="Filter columns" value="${escapeHtml(filterText)}"></div>`:'')+
      `<div class="column-menu-list" role="group" aria-label="Columns">${listHtml}</div>`+
      `<hr class="column-menu-divider">`+
      `<button type="button" class="column-menu-item column-menu-reset" role="menuitem" data-menu-action="reset-columns">Reset columns to default</button>`;
    if(!preserveFilter||!menu.querySelector?.('.column-menu-list')||menu.querySelector?.('.column-menu-list')?.id==='context-home'){
      menu.innerHTML=fullHtml;
    }else{
      const hideBtn=menu.querySelector?.('[data-menu-action="hide-column"]');
      if(hideBtn)hideBtn.disabled=!canHide;
      const leftBtn=menu.querySelector?.('[data-menu-action="move-left"]');
      if(leftBtn)leftBtn.disabled=!canLeft;
      const rightBtn=menu.querySelector?.('[data-menu-action="move-right"]');
      if(rightBtn)rightBtn.disabled=!canRight;
    }
  }
  function openColumnMenu(event,options={}){
    event?.preventDefault?.();event?.stopPropagation?.();
    let menu=node('waveColumnMenu');
    if(!menu&&typeof document!=='undefined'&&typeof document.createElement==='function'){
      menu=document.createElement('div');
      menu.id='waveColumnMenu';
      document.body?.appendChild?.(menu);
    }
    if(!menu)return null;
    menu.className='column-context-menu';
    menu.setAttribute?.('role','menu');
    menu.hidden=false;
    const th=options.th||event?.target?.closest?.('th');
    const table=options.table||th?.closest?.('table')||event?.target?.closest?.('table');
    const key=options.key??table?.dataset?.readingScope;
    const clickedFixed=options.clickedFixed!==undefined?!!options.clickedFixed:(th?.dataset?.readingFixed!==undefined);
    const clickedField=options.clickedField??(clickedFixed?th?.dataset?.readingFixed:th?.dataset?.readingField);
    const allColumns=Array.isArray(options.allColumns)?options.allColumns:[];
    const clickedLabel=options.clickedLabel||allColumns.find(c=>c.key===clickedField)?.label||clickedField||'';
    const opener=options.opener||th||(typeof document!=='undefined'?document.activeElement:null);
    activeMenu={...options,table,key,clickedField,clickedLabel,clickedFixed,allColumns,opener,filterText:''};
    const rect=th?.getBoundingClientRect?.();
    const rawX=Number.isFinite(Number(event?.clientX))&&Number(event.clientX)>0?Number(event.clientX):(rect?Math.round(rect.left):8);
    const rawY=Number.isFinite(Number(event?.clientY))&&Number(event.clientY)>0?Number(event.clientY):(rect?Math.round(rect.bottom||rect.top):8);
    const vw=(typeof window!=='undefined'&&window.innerWidth)||document.documentElement?.clientWidth||1024;
    const vh=(typeof window!=='undefined'&&window.innerHeight)||document.documentElement?.clientHeight||768;
    const x=clamp(rawX,8,Math.max(8,vw-248));
    const y=clamp(rawY,8,Math.max(8,vh-368));
    if(menu.style){menu.style.position='fixed';menu.style.left=x+'px';menu.style.top=y+'px';}
    if(!menu._columnMenuBound){
      menu._columnMenuBound=true;
      menu.addEventListener?.('click',e=>{
        const btn=e.target?.closest?.('[data-menu-action]');
        if(!btn||btn.disabled||!activeMenu)return;
        const action=btn.dataset.menuAction;
        const cur=activeMenu;
        if(action==='autofit-column'){
          if(cur.clickedField){autoFitColumn(cur.table,cur.key,cur.clickedField,!!cur.clickedFixed);cur.onChange?.();}
          closeColumnMenu(true);
        }else if(action==='autofit-all'){
          autoFitAll(cur.table,cur.key);cur.onChange?.();
          closeColumnMenu(true);
        }else if(action==='hide-column'){
          if(cur.clickedField&&!cur.clickedFixed&&cur.key){setVisible(cur.key,cur.clickedField,false);cur.onChange?.();}
          closeColumnMenu(true);
        }else if(action==='move-left'||action==='move-right'){
          if(cur.clickedField&&!cur.clickedFixed&&cur.key){
            const movableKeys=(cur.allColumns||[]).filter(c=>!c.fixed).map(c=>c.key);
            register(movableKeys,cur.key);
            const vis=order(movableKeys,cur.key).filter(k=>isVisible(cur.key,k));
            const idx=vis.indexOf(cur.clickedField),dir=action==='move-left'?-1:1;
            const target=vis[idx+dir];
            if(target!==undefined){
              reorder(cur.key,cur.clickedField,target,dir<0?'left':'right');
              cur.onChange?.();
              renderColumnMenu(menu,true);
              const preferred=menu.querySelector?.(`[data-menu-action="${action}"]`);
              const fallback=menu.querySelector?.(`[data-menu-action="${action==='move-left'?'move-right':'move-left'}"]`);
              const focusTarget=(preferred&&!preferred.disabled)?preferred:(fallback&&!fallback.disabled)?fallback:menu.querySelector?.('[role="menuitem"]:not(:disabled)');
              focusTarget?.focus?.({preventScroll:true});
            }
          }
        }else if(action==='reset-columns'){
          if(typeof cur.reset==='function')cur.reset();
          else if(cur.key)reset(cur.key);
          cur.onChange?.();
          closeColumnMenu(true);
        }
      });
      menu.addEventListener?.('keydown',e=>{
        if(!activeMenu)return;
        if(e.key==='Escape'){
          e.preventDefault?.();e.stopPropagation?.();
          closeColumnMenu(true);
          return;
        }
        if(e.key==='ArrowDown'||e.key==='ArrowUp'||e.key==='Home'||e.key==='End'){
          const items=[...(menu.querySelectorAll?.('button[role="menuitem"]:not(:disabled), input[type="search"], input[type="checkbox"]:not(:disabled)')||[])];
          if(!items.length)return;
          e.preventDefault?.();
          const idx=items.indexOf(document.activeElement);
          let next=0;
          if(e.key==='End')next=items.length-1;
          else if(e.key==='ArrowDown')next=idx<0?0:(idx+1)%items.length;
          else if(e.key==='ArrowUp')next=idx<=0?items.length-1:idx-1;
          items[next]?.focus?.({preventScroll:true});
        }
      });
      menu.addEventListener?.('change',e=>{
        const cb=e.target?.closest?.('[data-menu-column]');
        if(!cb||cb.disabled||!activeMenu)return;
        const colKey=cb.dataset.menuColumn;
        const colDef=(activeMenu.allColumns||[]).find(c=>c.key===colKey);
        if(!colDef||colDef.fixed||!activeMenu.key)return;
        setVisible(activeMenu.key,colKey,!!cb.checked);
        activeMenu.onChange?.();
        renderColumnMenu(menu,true);
      });
      menu.addEventListener?.('input',e=>{
        const input=e.target?.closest?.('[data-menu-filter]');
        if(!input||!activeMenu)return;
        activeMenu.filterText=input.value||'';
        renderColumnMenu(menu,true);
      });
    }
    if(!menuGlobalBound&&typeof document!=='undefined'&&typeof document.addEventListener==='function'){
      menuGlobalBound=true;
      document.addEventListener('pointerdown',e=>{
        const m=node('waveColumnMenu');
        if(!m||m.hidden)return;
        if(m.contains?.(e.target)||e.target?.closest?.('#waveColumnMenu'))return;
        closeColumnMenu();
      });
      document.addEventListener('keydown',e=>{
        const m=node('waveColumnMenu');
        if(!m||m.hidden)return;
        if(e.key==='Escape'){
          e.preventDefault?.();e.stopPropagation?.();
          closeColumnMenu(true);
        }
      },true);
    }
    renderColumnMenu(menu);
    const firstItem=menu.querySelector?.('button[role="menuitem"]:not(:disabled), [data-menu-filter], input:not(:disabled)');
    firstItem?.focus?.({preventScroll:true});
    return menu;
  }
  function context(){
    if(!ready){load();ready=true;}
    const pane=node('analysisContext'),divider=node('analysisContextDivider');if(!pane||!divider)return;
    const host=pane.parentElement;
    if(host.classList?.contains('flow-reading'))host.dataset.contextNarrow=String(host.clientWidth<550);
    const vertical=getComputedStyle(host).flexDirection!=='column',mode=vertical?'right':'below';
    pane.dataset.splitAxis=mode;pane.style.flexBasis=split[mode]+'%';
    divider.setAttribute('aria-orientation',vertical?'vertical':'horizontal');
    divider.setAttribute('aria-valuenow',String(Math.round(split[mode])));
  }
  function resizeContext(percent){
    const pane=node('analysisContext'),mode=pane.dataset.splitAxis||'right';
    split[mode]=clamp(percent,15,85);context();
  }
  function initialize(root,onChange){
    if(onChange)listeners.set(root,onChange);
    if(root._readingLayout)return;root._readingLayout=true;
    const divider=node('analysisContextDivider');
    if(divider&&!divider._readingContext){
      divider._readingContext=true;
      divider.addEventListener('pointerdown',e=>{if(e.button!==0)return;draggingContext=true;context();divider.setPointerCapture(e.pointerId);e.preventDefault();});
      divider.addEventListener('pointermove',e=>{if(!divider.hasPointerCapture(e.pointerId))return;const pane=node('analysisContext'),r=pane.parentElement.getBoundingClientRect();resizeContext(pane.dataset.splitAxis==='right'?(r.right-e.clientX)/r.width*100:(r.bottom-e.clientY)/r.height*100);});
      const finish=e=>{draggingContext=false;if(divider.hasPointerCapture(e.pointerId))divider.releasePointerCapture(e.pointerId);save();};
      divider.addEventListener('pointerup',finish);divider.addEventListener('pointercancel',finish);
      divider.addEventListener('dblclick',()=>{resizeContext(node('analysisContext').dataset.splitAxis==='right'?44:40);save();});
      divider.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home'].includes(e.key))return;e.preventDefault();const mode=node('analysisContext').dataset.splitAxis||'right';resizeContext(e.key==='Home'?(mode==='right'?44:40):split[mode]+(['ArrowLeft','ArrowUp'].includes(e.key)?3:-3));save();});
      let ctxRaf=0;
      if(typeof ResizeObserver!=='undefined')new ResizeObserver(()=>{
        if(typeof requestAnimationFrame!=='function'){context();return;}
        if(ctxRaf)return;
        ctxRaf=requestAnimationFrame(()=>{ctxRaf=0;context();});
      }).observe(node('paneRight')||document.querySelector?.('.pane-right')||root);
    }
    root.addEventListener('pointerdown',e=>{
      if(e.button!==0)return;
      const handle=e.target.closest?.('[data-column-resize]');
      if(handle){
        const th=handle.closest('th'),table=handle.closest('table');
        const fixed=th.dataset.readingFixed!==undefined;
        const field=fixed?th.dataset.readingFixed:th.dataset.readingField;
        const key=table.dataset.readingScope;
        resize={key,field,fixed,x:e.clientX,size:currentColumnWidth(table,th,key,field,fixed),table};
        handle.setPointerCapture?.(e.pointerId);e.preventDefault();e.stopPropagation();
        return;
      }
      if(e.target.closest?.('input[type="checkbox"],input'))return;
      const move=e.target.closest?.('[data-column-move]');
      const th=e.target.closest?.('th[data-reading-field]')||(move?move.closest?.('th'):null);
      if(th&&th.dataset?.readingField){
        const table=th.closest('table');
        if(!table)return;
        moving={key:table.dataset.readingScope,field:th.dataset.readingField,th,table,x:e.clientX,active:false,target:null,side:null,pointerId:e.pointerId,captureTarget:move||th};
        if(move){move.setPointerCapture?.(e.pointerId);e.preventDefault();e.stopPropagation();}
      }
    });
    const moveTarget=e=>{
      const th=document.elementFromPoint?.(e.clientX,e.clientY)?.closest?.('th[data-reading-field]');
      return th?.closest?.('table')===moving.table?th:null;
    };
    const computeDropSide=(target,e)=>{
      const rect=target?.getBoundingClientRect?.();
      if(rect&&Number.isFinite(rect.left)&&Number.isFinite(rect.width)&&rect.width>0){
        return e.clientX<rect.left+rect.width/2?'left':'right';
      }
      return e.clientX<moving.x?'left':'right';
    };
    root.addEventListener('pointermove',e=>{
      if(resize)resizeColumn(resize.table,resize.key,resize.field,resize.size+e.clientX-resize.x,resize.fixed);
      if(moving&&Math.abs(e.clientX-moving.x)>5){
        if(!moving.active){
          moving.active=true;
          moving.captureTarget?.setPointerCapture?.(moving.pointerId);
        }
        moving.target?.removeAttribute?.('data-column-drop');
        const target=moveTarget(e);
        if(target&&target.dataset?.readingField!==moving.field){
          moving.target=target;
          moving.side=computeDropSide(target,e);
          target.setAttribute?.('data-column-drop',moving.side);
        }else{
          moving.target=null;
          moving.side=null;
        }
      }
    });
    const finish=e=>{
      if(moving){
        const move=moving,wasActive=move.active||Math.abs((e.clientX??move.x)-move.x)>5;
        const target=e.type==='pointercancel'||!wasActive?null:(moveTarget(e)||move.target);
        const side=target?computeDropSide(target,e):move.side;
        move.target?.removeAttribute?.('data-column-drop');
        move.captureTarget?.releasePointerCapture?.(move.pointerId);
        moving=null;
        if(wasActive){
          justDragged=true;
          if(typeof setTimeout==='function')setTimeout(()=>{justDragged=false;},0);
          if(target&&target.dataset?.readingField&&target.dataset.readingField!==move.field){
            reorder(move.key,move.field,target.dataset.readingField,side);
          }
        }
      }
      if(resize){resize=null;save();changed();}
    };
    root.addEventListener('pointerup',finish);root.addEventListener('pointercancel',finish);
    root.addEventListener('click',e=>{
      if(justDragged){
        justDragged=false;
        if(e.target?.closest?.('.sort-button,th[data-reading-field]')){
          e.preventDefault?.();e.stopPropagation?.();
        }
      }
    },true);
    root.addEventListener('dblclick',e=>{
      const handle=e.target?.closest?.('[data-column-resize]');
      if(!handle)return;
      const th=handle.closest('th'),table=handle.closest('table');
      if(!th||!table)return;
      const fixed=th.dataset.readingFixed!==undefined;
      const field=fixed?th.dataset.readingFixed:th.dataset.readingField;
      if(!field)return;
      e.preventDefault?.();e.stopPropagation?.();
      autoFitColumn(table,table.dataset.readingScope,field,fixed);
    });
    root.addEventListener('keydown',e=>{
      const th=e.target.closest?.('th[data-reading-field]')||e.target.closest?.('th[data-reading-fixed]');if(!th||!['ArrowLeft','ArrowRight'].includes(e.key))return;
      const table=th.closest('table'),key=table.dataset.readingScope,fixed=th.dataset.readingFixed!==undefined,field=fixed?th.dataset.readingFixed:th.dataset.readingField;
      if(e.altKey&&!fixed){
        e.preventDefault();
        const vis=order(profile(key).order,key).filter(k=>isVisible(key,k));
        const next=vis[vis.indexOf(field)+(e.key==='ArrowLeft'?-1:1)];
        if(next!==undefined)reorder(key,field,next,e.key==='ArrowLeft'?'left':'right');
      }else if(!e.altKey&&(e.target.closest?.('[data-column-resize]')||e.target===th)){
        e.preventDefault();
        resizeColumn(table,key,field,currentColumnWidth(table,th,key,field,fixed)+(e.key==='ArrowLeft'?-16:16),fixed);
        save();
      }
    });
    context();
  }
  function reset(key){profiles.delete(key);autoFittedScopes.delete(key);save();changed();}
  return {
    initialize,context,order,columns,register,width,reorder,reset,
    hasProfile,hidden,isVisible,setVisible,
    autoFitColumn,autoFitAll,autoFitInitial,openColumnMenu,closeColumnMenu,
    isResizing:table=>!!(table&&(resize?.table===table||moving?.table===table)),
    isAnyDragging:()=>!!(resize||moving||draggingContext)
  };
})();
