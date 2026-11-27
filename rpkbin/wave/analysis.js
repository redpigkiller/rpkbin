/* Disk-backed reading. Windows are bounded; moving off either edge reads another
 * keyset page. Source identities never depend on the current process selection. */
const AnalysisWorkspace = (() => {
  const MAX_ROWS = 600, MAX_BYTES = 6 * 256 * 1024, MAX_MATCHES = 5000;
  const ANSI_FG = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'];
  const ANSI_ESCAPE_RE = /\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])/g;
  const node = id => document.getElementById(id);
  const escape = value => escHtml(String(value ?? ''));
  const stream = id => ({id, pages: [], forward: null, backward: null,
    moreForward: false, moreBackward: false, pending: false, epoch: 0, controller: null,
    resume: null, paused: false, endSeq: 0, checkedLines: -1,
    replaceOnResume: false, replaceAnchor: null, archiveSettled: false});
  const main = stream('analysisRows'), nearby = stream('analysisContextRows');
  let detail = null, identity = '', logSelected = null, initialized = false;
  let contextPosition = 'right', contextView = 'log', contextDetail = null, contextIdentity = '';
  let contextSeq = null, contextOpener = null;
  let lastPoll = 0, lastLines = -1, settings = {}, programmaticUntil = 0;
  let matches = [], activeMatchIndex = -1, findTimer = null, loadedFilterKey = '';
  let ansiColorEnabled = true;
  const archived = () => typeof RunHistory !== 'undefined' ? RunHistory.selection : null;
  const readOnly = () => typeof isHistory === 'function' ? isHistory() : !!archived();
  const currentRun = () => archived()?.run_id || STATE.archive?.run_id || '';
  const key = d => [STATE.workspace_id, currentRun(), d?.id, d?.attempt_id].join(':');
  const enabled = () => !!(archived()?.health || STATE.archive)?.enabled;
  const rows = s => s.pages.flatMap(page => page.rows);
  const rowKey = row => `${row.seq}:`;
  const status = text => { if (node('analysisStatus')) node('analysisStatus').textContent = text; };

  function isToggleChecked(id) {
    const el = node(id);
    return !!(el?.checked || el?.getAttribute?.('aria-pressed') === 'true');
  }

  function stripAnsi(raw) {
    const text = String(raw ?? '');
    return text.includes('\x1b') ? text.replace(ANSI_ESCAPE_RE, '') : text;
  }

  function parseAnsi(raw) {
    const text = String(raw ?? '');
    if (!text.includes('\x1b')) {
      return {plain: text, runs: text.length ? [{start: 0, end: text.length, classes: ''}] : []};
    }
    let plain = '', lastIndex = 0;
    let fg = '', bg = '', bold = false, dim = false, italic = false, underline = false;
    const runs = [];
    const currentClasses = () => {
      if (!ansiColorEnabled || (typeof DISPLAY_PREFS !== 'undefined' && DISPLAY_PREFS?.ansiColor === false)) return '';
      const list = [];
      if (bold) list.push('ansi-bold');
      if (dim) list.push('ansi-dim');
      if (italic) list.push('ansi-italic');
      if (underline) list.push('ansi-underline');
      if (fg) list.push(fg);
      if (bg) list.push(bg);
      return list.join(' ');
    };
    const pushChunk = chunk => {
      if (!chunk) return;
      const start = plain.length;
      plain += chunk;
      const classes = currentClasses();
      const prev = runs.at(-1);
      if (prev && prev.classes === classes && prev.end === start) prev.end = plain.length;
      else runs.push({start, end: plain.length, classes});
    };
    ANSI_ESCAPE_RE.lastIndex = 0;
    let match;
    while ((match = ANSI_ESCAPE_RE.exec(text)) !== null) {
      if (match.index > lastIndex) pushChunk(text.slice(lastIndex, match.index));
      const seq = match[0];
      if (seq.startsWith('\x1b[') && seq.endsWith('m')) {
        const body = seq.slice(2, -1);
        if (/^[0-9;]*$/.test(body)) {
          const codes = body === '' ? [0] : body.split(';').map(c => c === '' ? 0 : Number(c));
          for (let i = 0; i < codes.length; i++) {
            const code = codes[i];
            if (code === 0) { fg = bg = ''; bold = dim = italic = underline = false; }
            else if (code === 1) bold = true;
            else if (code === 2) dim = true;
            else if (code === 3) italic = true;
            else if (code === 4) underline = true;
            else if (code === 22) { bold = false; dim = false; }
            else if (code === 23) italic = false;
            else if (code === 24) underline = false;
            else if (code >= 30 && code <= 37) fg = 'ansi-fg-' + ANSI_FG[code - 30];
            else if (code === 39) fg = '';
            else if (code >= 40 && code <= 47) bg = 'ansi-bg-' + ANSI_FG[code - 40];
            else if (code === 49) bg = '';
            else if (code >= 90 && code <= 97) fg = 'ansi-fg-bright-' + ANSI_FG[code - 90];
            else if (code >= 100 && code <= 107) bg = 'ansi-bg-bright-' + ANSI_FG[code - 100];
            else if (code === 38 || code === 48) {
              const prefix = code === 38 ? 'ansi-fg-' : 'ansi-bg-';
              if (codes[i + 1] === 5) {
                const idx = codes[i + 2];
                if (idx >= 0 && idx <= 7) {
                  if (code === 38) fg = prefix + ANSI_FG[idx]; else bg = prefix + ANSI_FG[idx];
                } else if (idx >= 8 && idx <= 15) {
                  if (code === 38) fg = prefix + 'bright-' + ANSI_FG[idx - 8]; else bg = prefix + 'bright-' + ANSI_FG[idx - 8];
                }
                i += 2;
              } else if (codes[i + 1] === 2) {
                i += 4;
              }
            }
          }
        }
      }
      lastIndex = ANSI_ESCAPE_RE.lastIndex;
    }
    if (lastIndex < text.length) pushChunk(text.slice(lastIndex));
    return {plain, runs};
  }

  function renderRunsSlice(plain, runs, sliceStart, sliceEnd) {
    if (sliceStart >= sliceEnd) return '';
    if (!runs.length) return escape(plain.slice(sliceStart, sliceEnd));
    let out = '';
    for (let i = 0; i < runs.length; i++) {
      const r = runs[i];
      if (r.end <= sliceStart) continue;
      if (r.start >= sliceEnd) break;
      const s = Math.max(sliceStart, r.start), e = Math.min(sliceEnd, r.end);
      const content = escape(plain.slice(s, e));
      out += r.classes ? `<span class="${r.classes}">${content}</span>` : content;
    }
    return out;
  }

  function formatRowText(plain, runs, rowMatches, currentActiveIndex) {
    if (!rowMatches || !rowMatches.length) {
      return renderRunsSlice(plain, runs, 0, plain.length);
    }
    let out = '', pos = 0;
    for (let i = 0; i < rowMatches.length; i++) {
      const m = rowMatches[i];
      if (m.start > pos) out += renderRunsSlice(plain, runs, pos, m.start);
      const active = m.matchIndex === currentActiveIndex ? ' active' : '';
      out += `<mark class="log-hit${active}">${renderRunsSlice(plain, runs, m.start, m.end)}</mark>`;
      pos = m.end;
    }
    if (pos < plain.length) out += renderRunsSlice(plain, runs, pos, plain.length);
    return out;
  }

  function compileMatcher(s = settings) {
    const q = s?.query || '';
    if (!q) return {regex: null, error: null};
    const core = s.regex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = s.whole_word ? `\\b(?:${core})\\b` : core;
    const flags = s.case_sensitive ? 'g' : 'gi';
    try {
      return {regex: new RegExp(pattern, flags), error: null};
    } catch (err) {
      return {regex: null, error: err.message || 'Invalid regular expression'};
    }
  }

  function updateMatchCount(error = null) {
    const countNode = node('analysisMatchCount');
    if (!countNode) return;
    countNode.classList?.toggle?.('error-text', !!error);
    if (!settings.query) {
      countNode.textContent = '';
      return;
    }
    if (error) {
      countNode.textContent = `Invalid regex: ${error}`;
      return;
    }
    if (!matches.length) {
      countNode.textContent = main.pending && !rows(main).length ? 'Searching…' : 'No results';
      return;
    }
    const more = !!(main.moreForward || main.moreBackward);
    countNode.textContent = `${activeMatchIndex + 1} of ${matches.length}${more ? '+' : ''}`;
  }

  function backendFilterKey(s = settings) {
    return JSON.stringify({
      query: s.filter ? (s.query || '') : '',
      regex: s.filter ? !!s.regex : false,
      case_sensitive: s.filter ? !!s.case_sensitive : false,
      whole_word: s.filter ? !!s.whole_word : false,
      start: s.start,
      end: s.end
    });
  }

  function cancel(s) {
    ++s.epoch;
    s.controller?.abort();
    s.controller = null;
    s.pending = false;
  }

  function clear(s) {
    cancel(s);
    s.pages = [];
    s.forward = s.backward = null;
    s.moreForward = s.moreBackward = false;
    s.resume = null;
    s.paused = false;
    s.endSeq = 0;
    s.checkedLines = -1;
    s.replaceOnResume = false;
    s.replaceAnchor = null;
    s.archiveSettled = false;
    const box = node(s.id);
    if (box) {
      box.replaceChildren();
      delete box._analysisMarkup;
      delete box._analysisCanJump;
      delete box._analysisSeqRange;
      delete box._analysisSelected;
      delete box._analysisAnsi;
      delete box._analysisGutterWidth;
      if (box.dataset) delete box.dataset.markup;
      box.scrollTop = 0;
    }
    if (s === main) {
      matches = [];
      activeMatchIndex = -1;
      updateMatchCount();
    }
  }

  function reset() {
    ++textEpoch;
    clearTimeout(findTimer);
    findTimer = null;
    clear(main);
    if (contextView === 'log') closeContext();
    identity = '';
    detail = null;
    logSelected = null;
    lastLines = -1;
    loadedFilterKey = '';
    cancelControl('Cancel', false);
  }

  function stop() {
    cancel(main);
    cancel(nearby);
    if (node('analysisFollow')) node('analysisFollow').checked = false;
    main.paused = nearby.paused = true;
    const resumable = !!main.resume;
    cancelControl(resumable ? 'Continue' : 'Cancel', resumable);
    node('analysisPanel')?.setAttribute?.('aria-busy', 'false');
    status(resumable ? 'Reading paused. Continue resumes this search.' : 'Reading paused. Search to start again.');
  }

  function readSettings() {
    const filters = {
      query: node('analysisQuery')?.value || '',
      regex: isToggleChecked('analysisRegex'),
      case_sensitive: isToggleChecked('analysisCase'),
      whole_word: isToggleChecked('analysisWord'),
      filter: isToggleChecked('analysisFilterMode')
    };
    if (node('analysisStart')?.value !== '' && node('analysisStart')?.value != null) filters.start = node('analysisStart').value;
    if (node('analysisEnd')?.value !== '' && node('analysisEnd')?.value != null) filters.end = node('analysisEnd').value;
    return filters;
  }

  function cancelControl(label, visible) {
    const button = node('analysisCancel');
    if (!button) return;
    button.textContent = label;
    button.hidden = !visible;
    button.disabled = !visible;
    button.setAttribute('aria-label', label === 'Continue' ? 'Continue reading' : 'Cancel reading');
  }

  function renderFilters() {
    const chips = [];
    if (settings.start !== undefined || settings.end !== undefined) {
      chips.push(['time', '', `${settings.start || '0'}–${settings.end || 'end'} s`]);
    }
    const box = node('analysisFilterChips');
    if (!box) return;
    const markup = chips.map(([type, value, label]) =>
      `<button type="button" class="analysis-filter-chip" data-filter-type="${type}" data-filter-value="${escape(value)}"` +
      ` aria-label="Remove ${escape(label)}" title="Remove ${escape(label)}">` +
      `<span class="analysis-filter-text">${escape(label)}</span><span aria-hidden="true">×</span></button>`).join('');
    if (box.dataset.markup !== markup) {
      box.innerHTML = markup;
      box.dataset.markup = markup;
      box.querySelectorAll('[data-filter-type]').forEach(button => button.onclick = () =>
        removeFilter(button.dataset.filterType, button.dataset.filterValue));
    }
    box.hidden = !chips.length;
    if (node('analysisFilterCount')) node('analysisFilterCount').textContent = chips.length ? String(chips.length) : '';
    node('logOptionsMenu')?.setAttribute?.('data-active', String(chips.length > 0));
  }

  function removeFilter(type, value) {
    if (type === 'time') {
      if (node('analysisStart')) node('analysisStart').value = '';
      if (node('analysisEnd')) node('analysisEnd').value = '';
    } else return;
    return apply();
  }

  function parameters(s, options, d) {
    const base = s === nearby
      ? {query: '', regex: false}
      : settings.filter
        ? {
            query: settings.query || '',
            regex: !!settings.regex,
            case_sensitive: !!settings.case_sensitive,
            whole_word: !!settings.whole_word,
            ...(settings.start !== undefined ? {start: settings.start} : {}),
            ...(settings.end !== undefined ? {end: settings.end} : {})
          }
        : {
            query: '',
            regex: false,
            case_sensitive: false,
            whole_word: false,
            ...(settings.start !== undefined ? {start: settings.start} : {}),
            ...(settings.end !== undefined ? {end: settings.end} : {})
          };
    return {workspace_id: STATE.workspace_id, run_id: currentRun(),
      job_id: d.id, attempt_id: d.attempt_id, view: 'reader',
      limit: 100, ...base, ...options};
  }

  function capture(s) {
    const box = node(s.id), top = box.getBoundingClientRect().top;
    const found = [...box.children].find(row => row.dataset?.key && row.getBoundingClientRect().bottom > top);
    return found ? {key: found.dataset.key, top: found.getBoundingClientRect().top} : null;
  }

  function renderRowHtml(row, parsed, rowMatches, selected) {
    const key = rowKey(row), focus = row.seq === selected ? ' analysis-selected' : '';
    const gutter = `<span class="analysis-gutter" aria-hidden="true">${row.seq}</span>`;
    const textHtml = formatRowText(parsed.plain, parsed.runs, rowMatches, activeMatchIndex);
    return `<div class="analysis-row${focus}" data-key="${escape(key)}" data-seq="${row.seq}">` +
      gutter + `<span class="analysis-text">${textHtml}</span>` +
      (row.truncated ? `<button type="button" class="analysis-truncated" data-full="${row.seq}">Preview · read full</button>` : '') + '</div>';
  }

  function render(s, {bottom = false, anchor = capture(s), scrollToMatch = false} = {}) {
    const box = node(s.id), data = rows(s);
    const active = box.contains?.(document.activeElement) ? document.activeElement : null;
    const focused = active?.dataset && {jump: active.dataset.jump, full: active.dataset.full};
    const selected = s === main ? logSelected : contextSeq;
    const canJump = s === main && (!!settings.query || settings.start !== undefined || settings.end !== undefined);
    const prevRange = box._analysisSeqRange;
    const newFirst = data.length ? data[0].seq : 0;
    const newLast = data.length ? data[data.length - 1].seq : 0;
    const canIncremental = typeof box.insertAdjacentHTML === 'function' &&
      data.length > 0 && Array.isArray(prevRange) &&
      (s !== main || !settings.query) &&
      box._analysisSelected === selected &&
      box._analysisAnsi === ansiColorEnabled &&
      box._analysisCanJump === canJump &&
      box.firstElementChild?.classList?.contains?.('analysis-row') &&
      newFirst >= prevRange[0] && newLast >= prevRange[1] && newFirst <= prevRange[1];

    let parsedRows = null;
    let rowMatchMap = null;
    if (s === main) {
      matches = [];
      const {regex, error} = compileMatcher(settings);
      if (regex) {
        parsedRows = data.map(row => ({row, parsed: parseAnsi(row.text)}));
        rowMatchMap = new Map();
        for (const {row, parsed} of parsedRows) {
          const text = parsed.plain;
          const list = [];
          regex.lastIndex = 0;
          let m;
          while ((m = regex.exec(text)) !== null) {
            if (m[0].length === 0) {
              regex.lastIndex++;
              continue;
            }
            const entry = {seq: row.seq, matchIndex: matches.length, start: m.index, end: m.index + m[0].length};
            matches.push(entry);
            list.push(entry);
            if (matches.length >= MAX_MATCHES) break;
          }
          if (!list.length && settings.filter && settings.query) {
            matches.push({seq: row.seq, matchIndex: matches.length, start: 0, end: 0, rowOnly: true});
          }
          if (list.length) rowMatchMap.set(row.seq, list);
          if (matches.length >= MAX_MATCHES) break;
        }
      }
      if (!matches.length) {
        activeMatchIndex = -1;
      } else if (activeMatchIndex < 0 || activeMatchIndex >= matches.length) {
        activeMatchIndex = 0;
      }
      updateMatchCount(error);
    }
    if (data.length && box.style?.setProperty) {
      const maxSeq = Math.max(0, Number(data[data.length - 1]?.seq) || 0, Number(s.endSeq) || 0);
      const gutterPx = `${Math.max(2, String(maxSeq).length) * 7 + 4}px`;
      if (box._analysisGutterWidth !== gutterPx) {
        box._analysisGutterWidth = gutterPx;
        box.style.setProperty('--log-gutter-w', gutterPx);
      }
    }

    if (canIncremental) {
      if (newFirst !== prevRange[0] || newLast !== prevRange[1]) {
        while (box.firstElementChild && Number(box.firstElementChild.dataset?.seq) < newFirst) {
          box.firstElementChild.remove();
        }
        const appendRows = data.filter(row => row.seq > prevRange[1]).map(row => ({row, parsed: parseAnsi(row.text)}));
        if (appendRows.length) {
          box.insertAdjacentHTML('beforeend',
            appendRows.map(({row, parsed}) => renderRowHtml(row, parsed, null, selected)).join(''));
        }
        box._analysisSeqRange = [newFirst, newLast];
        box._analysisMarkup = null;
        if (box.dataset) delete box.dataset.markup;
      }
    } else {
      if (!parsedRows) parsedRows = data.map(row => ({row, parsed: parseAnsi(row.text)}));
      const markup = parsedRows.map(({row, parsed}) =>
        renderRowHtml(row, parsed, s === main ? rowMatchMap?.get(row.seq) : null, selected)
      ).join('') || `<div class="log-empty">${s.pending && s.resume ? 'Searching saved records…' : 'No matching records.'}</div>`;
      const changedMarkup = box._analysisMarkup !== markup;
      if (changedMarkup) {
        box.innerHTML = markup;
        box._analysisMarkup = markup;
        if (box.dataset) box.dataset.markup = markup;
      }
      box._analysisSeqRange = data.length ? [newFirst, newLast] : null;
      box._analysisSelected = selected;
      box._analysisAnsi = ansiColorEnabled;
      if (changedMarkup || box._analysisCanJump !== canJump) {
        box._analysisCanJump = canJump;
        if (!box._analysisDelegated) {
          box.querySelectorAll('[data-jump]').forEach(button => button.onclick = () => jump(Number(button.dataset.jump)));
          box.querySelectorAll('[data-full]').forEach(button => button.onclick = () => {
            readFull(Number(button.dataset.full), 0, s);
          });
        }
        box.querySelectorAll('[data-seq]').forEach(row => row.ondblclick = canJump ? event => {
          if (!event.target.closest?.('button, a, input, select')) return jump(Number(row.dataset.seq), event);
        } : null);
      }
    }
    if (focused?.jump || focused?.full) {
      const replacement = [...box.querySelectorAll('button')].find(button =>
        button.dataset.jump === focused.jump && button.dataset.full === focused.full);
      (replacement || box).focus?.({preventScroll: true});
    }
    programmaticUntil = Date.now() + 250;
    if (scrollToMatch && s === main && activeMatchIndex >= 0 && matches[activeMatchIndex]) {
      const targetSeq = matches[activeMatchIndex].seq;
      const hitMark = box.querySelector?.('.log-hit.active');
      const hitRow = [...(box.children || [])].find(row => Number(row.dataset?.seq) === targetSeq);
      if (hitMark?.scrollIntoView) hitMark.scrollIntoView({block: 'nearest', inline: 'nearest'});
      else if (hitRow?.scrollIntoView) hitRow.scrollIntoView({block: 'nearest'});
      if (hitRow && box.getBoundingClientRect) {
        const boxRect = box.getBoundingClientRect(), rowRect = hitRow.getBoundingClientRect?.();
        if (rowRect && (rowRect.top < boxRect.top || rowRect.bottom > boxRect.bottom)) {
          box.scrollTop += rowRect.top - boxRect.top - Math.max(0, (box.clientHeight - (rowRect.bottom - rowRect.top)) / 2);
        }
      }
    } else if (bottom) {
      box.scrollTop = box.scrollHeight;
    } else if (anchor) {
      const target = [...box.children].find(row => row.dataset?.key === anchor.key);
      if (target) box.scrollTop += target.getBoundingClientRect().top - anchor.top;
    }
  }

  function trim(s, direction) {
    // Sparse searches may return a single row per request. Bound rows and
    // payload rather than the number of response objects, so these can fill the
    // viewport without cycling through six one-row pages forever.
    while (s.pages.length > 1 && (rows(s).length > MAX_ROWS ||
      s.pages.reduce((bytes, page) => bytes + (page.readerBytes || 0), 0) > MAX_BYTES)) {
      if (direction === 'forward') {
        s.pages.shift();
        s.backward = s.pages[0].first;
        s.moreBackward = true;
      } else {
        s.pages.pop();
        s.forward = s.pages.at(-1).last;
        s.moreForward = true;
      }
    }
  }

  function needsFill(s) {
    const box = node(s.id);
    return box.clientHeight > 0 && box.scrollHeight <= box.clientHeight + 1;
  }

  async function load(s, options = {}, replace = false) {
    const targetDetail = () => s === main ? detail : contextDetail;
    const d = targetDetail();
    if (!d || s.pending || !enabled() || (s === nearby && contextSeq == null)) return;
    const direction = options.direction || 'forward', token = ++s.epoch, requestIdentity = key(d);
    const current = () => token === s.epoch && !!targetDetail() && key(targetDetail()) === requestIdentity;
    const initial = replace;
    const controller = new AbortController();
    s.controller = controller;
    s.pending = true;
    s.paused = false;
    if (replace && !s.replaceOnResume) s.replaceAnchor = options.anchor;
    s.replaceOnResume = replace;
    const anchor = replace ? null : capture(s);
    const toBottom = replace && direction === 'backward' ||
      (s === main && direction === 'forward' &&
        (node('analysisFollow')?.checked || archived()?.follow === 'following'));
    const {archiveFollow, ...requestOptions} = options;
    let cursor = options.cursor, request = {...requestOptions, direction};
    if (s === main) {
      loadedFilterKey = backendFilterKey(settings);
      cancelControl('Cancel', !node('analysisFollow')?.checked || !main.pages.length);
      node('analysisPanel')?.setAttribute?.('aria-busy', 'true');
      if (!main.pages.length || !node('analysisFollow')?.checked) status('Reading saved records…');
    }
    try {
      while (true) {
        s.resume = {...request};
        const requestedLines = targetDetail().total_lines;
        const data = await fetchJson('/api/archive?' + new URLSearchParams(parameters(s, request, d)),
          {cache: 'no-store', signal: controller.signal});
        if (!current()) return;
        s.endSeq = Math.max(s.endSeq, data.end_seq || 0);
        s.checkedLines = Math.max(s.checkedLines, requestedLines || 0, s.endSeq);
        const more = !!data.has_more, continuation = data.next || null;
        if (replace && (data.rows.length || !more)) {
          s.pages = [];
          s.moreBackward = direction === 'forward' && (s.replaceAnchor > 1 || (data.rows[0]?.seq || 1) > 1);
          s.moreForward = direction === 'backward' && !!s.replaceAnchor;
          replace = false;
          s.replaceOnResume = false;
          s.replaceAnchor = null;
        }
        if (data.rows.length) {
          data.readerBytes = JSON.stringify(data.rows).length * 2;
          if (direction === 'forward') s.pages.push(data);
          else s.pages.unshift(data);
          if (s.pages.length === 1) {
            s.backward = data.first;
            s.forward = data.last;
          }
        }
        if (!replace) {
          if (direction === 'forward') {
            s.forward = continuation || data.last || s.forward;
            s.moreForward = more;
          } else {
            s.backward = continuation || data.first || s.backward;
            s.moreBackward = more;
          }
        }
        trim(s, direction);
        s.resume = more && continuation ? {direction, cursor: continuation} : null;
        if (s === main) status(`${rows(s).length} loaded · ${direction === 'backward' ? 'read back to' : 'scanned to'} #${data.scanned_seq || 0} / ${data.end_seq || 0}` +
          (more ? ' · scroll for more' : direction === 'backward' ? ' · beginning reached' : ' · end reached') +
          (data.health?.error ? ' · history save failed; records may be incomplete' : ''));
        render(s, {anchor, bottom: toBottom});
        // Sparse pages must fill enough of the viewport to permit scrolling.
        // Even empty chunks advance; cancellation retains the next cursor.
        const approachingTarget = s === nearby && initial && rows(s).every(row => row.seq < contextSeq);
        if ((!approachingTarget && !needsFill(s) && data.rows.length) || !more || !continuation || continuation === cursor ||
            (archiveFollow && archived()?.follow !== 'following' && !archived()?.catchUp)) break;
        cursor = continuation;
        request = {direction, cursor};
        if (s === main) cancelControl('Cancel', true);
        await new Promise(resolve => setTimeout(resolve, 0));
        if (!current()) return;
      }
      render(s, {anchor, bottom: toBottom});
      if (s === nearby && initial && contextSeq != null) {
        const target = [...node(s.id).children].find(row => Number(row.dataset?.seq) === contextSeq);
        if (target) node(s.id).scrollTop += target.getBoundingClientRect().top - node(s.id).getBoundingClientRect().top - 40;
        if (!rows(nearby).some(row => row.seq === contextSeq)) {
          node('analysisContextTitle').textContent = `Original Log · #${contextSeq} unavailable`;
        }
      }
    } catch (error) {
      if (!current() || controller.signal.aborted || error?.name === 'AbortError' || error?.isAborted) return;
      if (s === main) {
        s.resume = null;
        if (node('analysisFollow')?.checked) lastLines = -1;
        status('History query failed: ' + error.message);
      }
      else node('analysisContextTitle').textContent = 'Context failed: ' + error.message;
    } finally {
      if (token === s.epoch) {
        s.pending = false;
        s.controller = null;
        if (s === main) {
          cancelControl('Cancel', false);
          node('analysisPanel')?.setAttribute?.('aria-busy', 'false');
          updateMatchCount();
        }
      }
    }
  }

  function fallback() {
    const {regex} = compileMatcher(settings);
    const all = detail?.log_records || [];
    const retained = settings.filter && settings.query
      ? all.filter(row => {
          if (!regex) return false;
          regex.lastIndex = 0;
          return regex.test(stripAnsi(row.text));
        })
      : all;
    main.pages = [{rows: retained.slice(-600).map(row => ({...row, seq: row.line_no}))}];
    render(main, {scrollToMatch: !!settings.query});
    status('History unavailable · retained live window only · literal search');
  }

  let textEpoch = 0;
  async function readFull(seq, offset = 0, stream = main) {
    const d = stream === main ? detail : contextDetail;
    if (!d || !Number.isSafeInteger(seq) || seq < 1) return;
    if (typeof FlowWorkspace !== 'undefined') FlowWorkspace.closeInspection();
    const token = ++textEpoch, requestIdentity = key(d);
    node('flowSpecTitle').textContent = `Raw Log · #${seq}`;
    node('flowSpecFacts').textContent = '';
    node('flowSpecCode').textContent = 'Reading saved text…';
    node('flowSpecMore').replaceChildren();
    openModal('flowSpecOverlay');
    try {
      const data = await RunHistory.query({view:'text', seq, offset}, d);
      if (token !== textEpoch || key(d) !== requestIdentity || node('flowSpecOverlay').hidden) return;
      node('flowSpecCode').textContent = data.text;
      node('flowSpecFacts').textContent = `${data.offset}–${data.next_offset} / ${data.total} ${data.offset_unit}`;
      const more = node('flowSpecMore');
      more.innerHTML = '<button type="button" data-log-start>Start</button> <button type="button" data-log-next>Next chunk</button> <button type="button" data-log-context>Original Log context</button>';
      more.querySelector('[data-log-start]').disabled = !offset;
      more.querySelector('[data-log-start]').onclick = () => readFull(seq, 0, stream);
      more.querySelector('[data-log-next]').disabled = !data.has_more;
      more.querySelector('[data-log-next]').onclick = () => readFull(seq, data.next_offset, stream);
      more.querySelector('[data-log-context]').onclick = () => {
        closeModal('flowSpecOverlay');
        if (stream === main) jump(seq);
        else openContext(d, seq);
      };
    } catch (error) {
      if (token === textEpoch && key(d) === requestIdentity) node('flowSpecCode').textContent = error.message;
    }
  }

  async function apply(options = {}) {
    const fromInput = !!options?.fromInput;
    clearTimeout(findTimer);
    findTimer = null;
    cancel(main);
    const prevQuery = settings.query || '';
    settings = readSettings();
    renderFilters();
    if (!detail) {
      updateMatchCount();
      return;
    }
    if (node('analysisFollow') && (!fromInput || settings.query)) {
      node('analysisFollow').checked = false;
    }
    activeMatchIndex = 0;
    if (!enabled()) return fallback();
    const nextFilterKey = backendFilterKey(settings);
    const {error} = compileMatcher(settings);
    if (error && !settings.filter) {
      clear(main);
      status('History query failed: Invalid regular expression: ' + error);
      updateMatchCount(error);
      return;
    }
    if (!settings.filter && main.pages.length > 0 && loadedFilterKey === nextFilterKey &&
        (fromInput || settings.query !== '' || prevQuery !== '')) {
      render(main, {scrollToMatch: !!settings.query});
      if (settings.query && matches[activeMatchIndex]) {
        logSelected = matches[activeMatchIndex].seq;
      }
      if (!fromInput && settings.query && !matches.length && (main.moreForward || main.moreBackward)) {
        return stepMatch(1);
      }
      return;
    }
    clear(main);
    return load(main, {direction: 'forward'}, true);
  }

  async function stepMatch(dir = 1) {
    const step = dir < 0 ? -1 : 1;
    const latest = readSettings();
    if (latest.query !== settings.query || latest.case_sensitive !== settings.case_sensitive ||
        latest.whole_word !== settings.whole_word || latest.regex !== settings.regex ||
        latest.filter !== settings.filter) {
      settings = latest;
      if (settings.filter && loadedFilterKey !== backendFilterKey(settings)) {
        return apply();
      }
      render(main, {scrollToMatch: true});
    }
    if (!settings.query || main.pending || !detail) return;
    if (node('analysisFollow')) node('analysisFollow').checked = false;

    const loaded = rows(main);
    const total = matches.length;
    const nextIdx = total ? (activeMatchIndex < 0 ? (step > 0 ? 0 : total - 1) : activeMatchIndex + step) : -1;

    if (total && nextIdx >= 0 && nextIdx < total) {
      activeMatchIndex = nextIdx;
      logSelected = matches[activeMatchIndex].seq;
      render(main, {scrollToMatch: true});
      return;
    }

    const canAdvanceForward = step > 0 && enabled() && (main.moreForward ||
      (loaded.length > 0 && (detail.total_lines || main.endSeq || 0) > loaded.at(-1).seq));
    const canAdvanceBackward = step < 0 && enabled() && (main.moreBackward ||
      (loaded.length > 0 && loaded[0].seq > 1));

    if (settings.filter) {
      if (canAdvanceForward && main.moreForward) {
        const boundarySeq = loaded.length ? loaded.at(-1).seq : 0;
        await edge('forward');
        const nextIdx = matches.findIndex(m => m.seq > boundarySeq);
        if (nextIdx >= 0) activeMatchIndex = nextIdx;
        else if (matches.length) activeMatchIndex = 0;
      } else if (canAdvanceBackward && main.moreBackward) {
        const boundarySeq = loaded.length ? loaded[0].seq : Number.MAX_SAFE_INTEGER;
        await edge('backward');
        let prevIdx = -1;
        for (let i = matches.length - 1; i >= 0; i--) {
          if (matches[i].seq < boundarySeq) { prevIdx = i; break; }
        }
        if (prevIdx >= 0) activeMatchIndex = prevIdx;
        else if (matches.length) activeMatchIndex = matches.length - 1;
      } else if (matches.length) {
        activeMatchIndex = step > 0 ? 0 : matches.length - 1;
      }
      if (matches[activeMatchIndex]) logSelected = matches[activeMatchIndex].seq;
      render(main, {scrollToMatch: true});
      return;
    }

    if (canAdvanceForward || canAdvanceBackward) {
      const direction = step > 0 ? 'forward' : 'backward';
      const startAnchor = step > 0
        ? (loaded.length ? loaded.at(-1).seq + 1 : 1)
        : (loaded.length ? Math.max(1, loaded[0].seq - 1) : Math.max(1, detail.total_lines || 1));
      const token = ++main.epoch, requestIdentity = key(detail);
      const current = () => token === main.epoch && !!detail && key(detail) === requestIdentity;
      const controller = new AbortController();
      main.controller = controller;
      main.pending = true;
      main.paused = false;
      cancelControl('Cancel', true);
      node('analysisPanel')?.setAttribute?.('aria-busy', 'true');
      let hitRow = null;
      try {
        let cursor = null;
        let request = {
          direction,
          anchor: startAnchor,
          query: settings.query,
          regex: !!settings.regex,
          case_sensitive: !!settings.case_sensitive,
          whole_word: !!settings.whole_word
        };
        while (true) {
          const data = await fetchJson('/api/archive?' + new URLSearchParams(parameters(main, request, detail)),
            {cache: 'no-store', signal: controller.signal});
          if (!current()) return;
          if (data.rows?.length) {
            hitRow = step > 0 ? data.rows[0] : data.rows.at(-1);
            break;
          }
          if (!data.has_more || !data.next || data.next === cursor) break;
          cursor = data.next;
          request = {
            direction,
            cursor,
            query: settings.query,
            regex: !!settings.regex,
            case_sensitive: !!settings.case_sensitive,
            whole_word: !!settings.whole_word
          };
          await new Promise(resolve => setTimeout(resolve, 0));
          if (!current()) return;
        }
      } catch (error) {
        if (!current() || controller.signal.aborted || error?.name === 'AbortError' || error?.isAborted) return;
        status('History query failed: ' + error.message);
        return;
      } finally {
        if (token === main.epoch) {
          main.pending = false;
          main.controller = null;
          cancelControl('Cancel', false);
          node('analysisPanel')?.setAttribute?.('aria-busy', 'false');
        }
      }
      if (!current()) return;
      if (hitRow) {
        const hitSeq = hitRow.seq;
        logSelected = hitSeq;
        await load(main, {direction: 'forward', anchor: Math.max(1, hitSeq - 10)}, true);
        if (!rows(main).some(r => r.seq === hitSeq)) {
          main.pages = [{rows: [hitRow], first: 'c' + hitSeq, last: 'c' + hitSeq}];
        }
        render(main);
        const candidates = [];
        for (let i = 0; i < matches.length; i++) {
          if (matches[i].seq === hitSeq) candidates.push(i);
        }
        if (candidates.length) {
          activeMatchIndex = step > 0 ? candidates[0] : candidates.at(-1);
        } else if (matches.length) {
          const after = matches.findIndex(m => m.seq >= hitSeq);
          activeMatchIndex = after >= 0 ? after : 0;
        }
        if (matches[activeMatchIndex]) logSelected = matches[activeMatchIndex].seq;
        render(main, {scrollToMatch: true});
        return;
      }
    }

    if (matches.length) {
      activeMatchIndex = step > 0 ? 0 : matches.length - 1;
      logSelected = matches[activeMatchIndex].seq;
      render(main, {scrollToMatch: true});
    } else {
      updateMatchCount();
    }
  }

  function nextMatch() {
    return stepMatch(1);
  }

  function prevMatch() {
    return stepMatch(-1);
  }

  function openFind() {
    initialize();
    const widget = node('vscodeFindWidget');
    if (widget) widget.hidden = false;
    node('btnToggleFind')?.setAttribute?.('aria-pressed', 'true');
    node('btnToggleFind')?.setAttribute?.('aria-expanded', 'true');
    const q = node('analysisQuery');
    if (q) {
      q.focus?.();
      const end = String(q.value || '').length;
      q.setSelectionRange?.(end, end);
    }
  }

  function closeFind() {
    if (main.pending) stop();
    const widget = node('vscodeFindWidget');
    const hadFocus = !!(widget?.contains?.(document.activeElement));
    if (widget) widget.hidden = true;
    node('btnToggleFind')?.setAttribute?.('aria-pressed', 'false');
    node('btnToggleFind')?.setAttribute?.('aria-expanded', 'false');
    if (hadFocus) node('btnToggleFind')?.focus?.({preventScroll: true});
  }

  function toggleFind(force) {
    const widget = node('vscodeFindWidget');
    const shouldOpen = typeof force === 'boolean' ? force : !!widget?.hidden;
    if (shouldOpen) openFind();
    else closeFind();
  }

  async function edge(where, s = main) {
    if (!(s === main ? detail : contextDetail)) return;
    if (s.pending) {
      if (where === 'latest') cancel(s);
      else return;
    }
    if (where === 'continue' && s.resume) return load(s, s.resume, s.replaceOnResume);
    if (where === 'start' || where === 'latest') {
      cancel(s);
      if (s === main && node('analysisFollow')) node('analysisFollow').checked = where === 'latest' && !readOnly();
      // A Follow refresh keeps the current reading visible until its bounded
      // replacement arrives. Clearing first paints a blank frame every poll.
      if (where === 'start') clear(s);
      return load(s, {direction: where === 'latest' ? 'backward' : 'forward'}, true);
    }
    const direction = where === 'backward' ? 'backward' : 'forward';
    const more = direction === 'forward' ? s.moreForward : s.moreBackward;
    const cursor = direction === 'forward' ? s.forward : s.backward;
    if (!more || (!cursor && !(direction === 'forward' && archived()?.follow))) return;
    if (s === main && direction === 'backward' && node('analysisFollow')) node('analysisFollow').checked = false;
    return load(s, {direction, cursor});
  }

  function updateContext() {
    const drawer = node('analysisContext');
    const home = node('logReadingArea');
    const host = contextView === 'charts' ? node('chartReadingArea') :
      ['parsed', 'analysis', 'automation'].includes(contextView) ? node('flowReadingArea') : home;
    if (drawer.parentElement !== host) {
      if (drawer.parentElement) drawer.parentElement.dataset.context = 'hidden';
      host.appendChild(drawer);
    }
    const mode = contextSeq !== null && contextDetail ? contextPosition : 'hidden';
    drawer.hidden = mode === 'hidden';
    host.dataset.context = mode;
    node('analysisPanel').dataset.context = host === home ? mode : 'hidden';
    const button = node('btnContextPosition'), target = contextPosition === 'right' ? 'below' : 'right';
    button.title = 'Move original Log ' + target;
    button.setAttribute('aria-label', button.title);
    if (typeof WaveIcons !== 'undefined') button.innerHTML = WaveIcons.svg('pane-' + target);
    if (typeof ReadingLayout !== 'undefined') ReadingLayout.context();
  }

  function closeContext(restoreFocus = false) {
    contextSeq = null;
    clear(nearby);
    updateContext();
    if (restoreFocus && contextOpener?.isConnected) contextOpener.focus({preventScroll: true});
    contextOpener = null;
  }

  function setContextView(view, d) {
    const scope = d ? key(d) : '';
    if (view !== contextView || scope !== contextIdentity) {
      ++textEpoch;
      contextView = view;
      contextDetail = d;
      contextIdentity = scope;
      closeContext();
    } else contextDetail = d;
  }

  function context(position) {
    if (position === 'hidden') return closeContext(true);
    if (position === 'right' || position === 'below') contextPosition = position;
    updateContext();
  }

  function openContext(d, seq) {
    if (!d || !Number.isSafeInteger(seq) || seq < 1 || !enabled()) return;
    initialize();
    if (contextView === 'details' || contextView === 'hidden' || key(d) !== contextIdentity) return;
    ++textEpoch;
    contextSeq = seq;
    if (!node('analysisContext').contains?.(document.activeElement)) contextOpener = document.activeElement;
    updateContext();
    node('analysisContextTitle').textContent = `Original Log · #${seq} · unfiltered`;
    clear(nearby);
    return load(nearby, {direction: 'forward', anchor: Math.max(1, seq - 10)}, true);
  }

  function jump(seq, event = null) {
    if (event) {
      if (event._waveJumpHandled) return;
      event._waveJumpHandled = true;
    }
    if (!detail || !Number.isSafeInteger(seq) || seq < 1 || !enabled()) return;
    logSelected = seq;
    render(main);
    return openContext(detail, seq);
  }

  function initialize() {
    if (initialized) return;
    initialized = true;
    node('analysisContextRows')?.setAttribute?.('aria-labelledby', 'analysisContextTitle');
    node('analysisSearch')?.addEventListener?.('click', () => apply());
    node('analysisCancel')?.addEventListener?.('click', () => main.paused ? edge('continue') : stop());
    node('btnToggleFind')?.addEventListener?.('click', () => { if (enabled()) toggleFind(); });
    node('btnCloseFind')?.addEventListener?.('click', () => {
      if (!enabled()) return;
      if (main.pending) stop();
      else closeFind();
    });
    node('analysisFindNext')?.addEventListener?.('click', () => { if (enabled()) nextMatch(); });
    node('analysisFindPrev')?.addEventListener?.('click', () => { if (enabled()) prevMatch(); });
    for (const id of ['analysisStart', 'analysisEnd']) node(id)?.addEventListener?.('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault?.();
        const disclosure = event.target?.closest?.('details') || node('logOptionsMenu');
        if (disclosure) disclosure.open = false;
        apply();
      }
    });
    node('vscodeFindWidget')?.addEventListener?.('keydown', event => {
      if (event.key === 'Escape' && enabled()) {
        event.preventDefault?.();
        event.stopPropagation?.();
        if (main.pending) stop();
        else closeFind();
      }
    });
    (node('readerToolbar') || node('analysisPanel'))?.addEventListener?.('keydown', event => {
      if (event.key === 'Escape') {
        const disclosure = event.target.closest?.('details[open]');
        if (disclosure) { event.preventDefault(); disclosure.open = false; disclosure.querySelector('summary')?.focus(); }
      }
    });
    node('analysisQuery')?.addEventListener?.('keydown', event => {
      if (!enabled()) return;
      if (event.key === 'Enter') {
        event.preventDefault?.();
        if (event.shiftKey) {
          prevMatch();
        } else if (findTimer || (node('analysisQuery')?.value || '') !== (settings.query || '') ||
                   loadedFilterKey !== backendFilterKey(readSettings()) || !main.pages.length || !matches.length) {
          apply();
        } else {
          nextMatch();
        }
      } else if (event.key === 'Escape') {
        event.preventDefault?.();
        event.stopPropagation?.();
        if (main.pending) stop();
        else closeFind();
      }
    });
    node('analysisQuery')?.addEventListener?.('input', () => {
      clearTimeout(findTimer);
      findTimer = null;
      if (!enabled()) return;
      const value = node('analysisQuery')?.value || '';
      if (!value) {
        apply({fromInput: true});
        return;
      }
      if (!isToggleChecked('analysisFilterMode')) {
        findTimer = setTimeout(() => {
          findTimer = null;
          apply({fromInput: true});
        }, 150);
      }
    });
    for (const id of ['analysisCase', 'analysisWord', 'analysisRegex']) {
      node(id)?.addEventListener?.('change', () => {
        if (!enabled()) return;
        if (node('analysisQuery')?.value) apply();
        else settings = readSettings();
      });
    }
    node('analysisFilterMode')?.addEventListener?.('change', () => { if (enabled()) apply(); });
    node('analysisFollow')?.addEventListener?.('change', () => {
      if (!node('analysisFollow').checked) return;
      const box = node(main.id);
      const upToDate = detail && !main.moreForward && main.pages.length > 0 &&
        !main.pending && !main.paused && !main.replaceOnResume &&
        (detail.total_lines || 0) <= Math.max(main.endSeq, main.checkedLines, 0);
      if (upToDate && box) {
        programmaticUntil = Date.now() + 250;
        box.scrollTop = box.scrollHeight;
      } else {
        edge('latest');
      }
    });
    for (const s of [main, nearby]) {
      const box = node(s.id);
      if (box && typeof box.addEventListener === 'function') {
        box._analysisDelegated = true;
        box.addEventListener('click', event => {
          const fullBtn = event.target?.closest?.('[data-full]');
          if (fullBtn) {
            readFull(Number(fullBtn.dataset.full), 0, s);
            return;
          }
          const jumpBtn = event.target?.closest?.('[data-jump]');
          if (jumpBtn) jump(Number(jumpBtn.dataset.jump), event);
        });
        box.addEventListener('dblclick', event => {
          if (!box._analysisCanJump || event.target?.closest?.('button, a, input, select')) return;
          const rowEl = event.target?.closest?.('[data-seq]');
          if (rowEl) jump(Number(rowEl.dataset.seq), event);
        });
      }
    }
    for (const s of [main, nearby]) node(s.id)?.addEventListener?.('scroll', () => {
      const box = node(s.id);
      if (s.pending) return;
      const distFromBottom = box.scrollHeight - box.scrollTop - box.clientHeight;
      if (s === main && Date.now() >= programmaticUntil && node('analysisFollow')) {
        if (distFromBottom > 80) node('analysisFollow').checked = false;
        else if (distFromBottom <= 8 && box.scrollTop > 0 && !readOnly() && !s.paused && !s.moreForward &&
            !settings.query && !settings.filter && settings.start === undefined && settings.end === undefined) {
          node('analysisFollow').checked = true;
        }
      }
      if (box.scrollTop < 100) edge('backward', s);
      else if (distFromBottom < 150) edge('forward', s);
    });
    for (const s of [main, nearby]) node(s.id)?.addEventListener?.('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && ['Home', 'End'].includes(event.key)) {
        event.preventDefault();
        edge(event.key === 'Home' ? 'start' : 'latest', s);
        return;
      }
      if (s === main && ['ArrowUp', 'PageUp', 'Home'].includes(event.key) && node('analysisFollow')) {
        node('analysisFollow').checked = false;
      }
      if (event.key === 'Enter' && event.target === node(s.id)) {
        const first = capture(s);
        if (first && s === main) jump(Number(first.key.split(':')[0]));
      }
    });
    node('analysisContext')?.addEventListener?.('keydown', event => {
      if (event.key === 'Escape' && !node('analysisContext').hidden) {
        event.stopPropagation();
        closeContext(true);
      }
    });
    node(main.id)?.addEventListener?.('wheel', event => {
      if (event.deltaY < 0 && node('analysisFollow')) node('analysisFollow').checked = false;
    }, {passive: true});
    node(main.id)?.addEventListener?.('focusin', event => {
      if (event.target !== node(main.id) && node('analysisFollow')) node('analysisFollow').checked = false;
    });
  }

  function loadedTailSeq(s) {
    const pages = s && s.pages ? s.pages : [];
    for (let i = pages.length - 1; i >= 0; i--) {
      const pageRows = pages[i] && pages[i].rows;
      if (pageRows && pageRows.length) return Number(pageRows[pageRows.length - 1].seq) || 0;
    }
    return 0;
  }

  async function refresh(d) {
    if (typeof WORKSPACE_LAYOUT !== 'undefined' && (WORKSPACE_LAYOUT === 'jobs' || WORKSPACE_LAYOUT === 'charts')) return;
    initialize();
    if (!d) {
      if (identity) reset();
      status('Select a job to read Raw Log.');
      return;
    }
    const changed = identity !== key(d);
    const archivedMode = enabled();
    for (const id of ['analysisStart', 'analysisEnd', 'btnAnalysisElapsedApply']) {
      const ctrl = node(id);
      if (ctrl) ctrl.disabled = !archivedMode;
    }
    if (changed) {
      reset();
      detail = d;
      identity = key(d);
      settings = readSettings();
      loadedFilterKey = backendFilterKey(settings);
      renderFilters();
      if (contextView === 'log') setContextView('log', d);
      if (node('analysisFollow')) {
        node('analysisFollow').disabled = readOnly();
        if (readOnly()) node('analysisFollow').checked = false;
      }
      lastLines = d.total_lines;
      lastPoll = Date.now();
      if (!archivedMode) return fallback();
      return load(main, {direction: node('analysisFollow')?.checked ? 'backward' : 'forward'}, true);
    }
    detail = d;
    if (!archivedMode) return fallback();
    const saved = archived();
    if (saved?.follow) {
      const target = d.total_lines || 0;
      const follow = saved.follow === 'following' || (saved.catchUp && !main.archiveSettled);
      // The forward cursor is the last scanned row, even when a filter
      // matched nothing. An empty archive starts with a null cursor.
      if (target > main.endSeq) main.moreForward = true;
      if (follow && main.moreForward && !main.pending && !main.paused) {
        return load(main, {direction: 'forward', cursor: main.forward, archiveFollow: true});
      }
      if (saved.follow === 'finished' && !main.pending && !main.moreForward) main.archiveSettled = true;
      return;
    }
    // A reader parked at the former end remains a reader: new output reopens
    // its forward edge without turning Follow on or moving its visible anchor.
    let newTail = false;
    if (!readOnly()) for (const s of [main, nearby]) {
      if (s === nearby && contextSeq == null) continue;
      if (d.total_lines > Math.max(s.endSeq, s.checkedLines)) {
        s.checkedLines = d.total_lines;
        if (s.forward) s.moreForward = true;
        if (s === main) newTail = true;
      }
    }
    if (node('analysisFollow')?.checked && !readOnly() && !main.pending && !main.paused) {
      if (d.total_lines !== lastLines && d.total_lines > lastLines && main.pages.length && main.forward && !main.replaceOnResume) {
        lastPoll = Date.now();
        lastLines = d.total_lines;
        main.moreForward = true;
        await edge('forward');
        if (node('analysisFollow')?.checked && !main.pending && !main.paused && main.moreForward && (d.total_lines || 0) > loadedTailSeq(main)) {
          lastPoll = Date.now();
          return edge('latest');
        }
        return;
      }
      if (d.total_lines !== lastLines && (!main.pages.length || Date.now() - lastPoll >= 1200)) {
        lastPoll = Date.now();
        lastLines = d.total_lines;
        return edge('latest');
      }
      if (main.moreForward && (d.total_lines || 0) > main.endSeq) {
        lastPoll = Date.now();
        return edge('latest');
      }
    }
    if (!main.pending && !main.paused && needsFill(main) && main.resume) return edge('continue');
    if (newTail && !main.pending && !main.paused) {
      if (!main.forward) return load(main, {direction: 'forward'}, !main.pages.length);
      if (needsFill(main)) return edge('forward');
    }
  }

  return {refresh, apply, stop, jump, edge, context, reset, setContextView, openContext,
    removeFilter, readFull, openFind, closeFind, toggleFind, nextMatch, prevMatch, stepMatch,
    parseAnsi, stripAnsi, closeInspection() { ++textEpoch; },
    get contextPosition() { return contextPosition; },
    get ansiColor() { return ansiColorEnabled; },
    set ansiColor(value) { ansiColorEnabled = !!value; if (detail) render(main); },
    get matches() { return matches.slice(); },
    get activeMatchIndex() { return activeMatchIndex; }};
})();
