/* Jobs execution columns. Display preferences never change scheduling or Flow. */
const JobsColumns = (() => {
  const SCOPE_OVERVIEW = 'jobs-overview', SCOPE_SPLIT = 'jobs-split', STORAGE = 'wave_jobs_columns_v1';
  const definitions = [
    ['name', 'Job', 220], ['status', 'State', 120], ['result', 'Result', 104],
    ['warning_count', 'Warn', 72], ['elapsed', 'Elapsed', 96], ['priority', 'Priority', 88],
    ['attempt_number', 'Attempt', 80], ['returncode', 'Exit code', 80], ['pid', 'PID', 120],
    ['resources', 'Resources', 160], ['pending_reason', 'Reason', 260]
  ];
  const defaultsOverview = ['name', 'status', 'result', 'warning_count', 'elapsed', 'priority'];
  const defaultsSplit = ['name', 'status', 'result'];
  const numeric = new Set(['warning_count', 'elapsed', 'priority', 'attempt_number', 'returncode', 'pid']);
  const byName = new Map(definitions.map(([key, label, width]) => [key, {label, width}]));
  const fields = definitions.map(([key]) => key), movable = fields.filter(key => key !== 'name');
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const completionLabel = reason => reason === 'user_stopped' ? 'Stopped by user · incomplete' : reason;
  const pauseSvg = () => (typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function' && WaveIcons.svg('pause')) ||
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false" stroke="currentColor" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>';
  const initializedScopes = new Set();
  const fallbackVisible = new Map([
    [SCOPE_OVERVIEW, new Set(defaultsOverview)],
    [SCOPE_SPLIT, new Set(defaultsSplit)]
  ]);
  let changed = () => {};

  function scope() {
    return typeof WORKSPACE_LAYOUT !== 'undefined' && WORKSPACE_LAYOUT === 'split' ? SCOPE_SPLIT : SCOPE_OVERVIEW;
  }
  function defaultsFor(s = scope()) {
    return s === SCOPE_SPLIT ? defaultsSplit : defaultsOverview;
  }
  function syncTableScope(s = scope()) {
    if (typeof document !== 'undefined' && typeof document.getElementById === 'function') {
      const tbl = document.getElementById('jobsTable');
      if (tbl && tbl.dataset) tbl.dataset.readingScope = s;
    }
  }
  function readLegacyVisible() {
    try {
      const text = window.localStorage.getItem(STORAGE);
      if (!text || text.length > 8192) return null;
      const saved = JSON.parse(text);
      if (saved?.version !== 1 || !Array.isArray(saved.visible)) return null;
      return new Set(['name', ...saved.visible.slice(0, fields.length * 2).filter(key => byName.has(key))]);
    } catch (_) {
      return null;
    }
  }
  function saveLegacy(s = scope()) {
    if (s !== SCOPE_OVERVIEW) return;
    try {
      const vis = visibleSet(s);
      window.localStorage.setItem(STORAGE, JSON.stringify({version: 1, visible: fields.filter(key => vis.has(key))}));
    } catch (_) {}
  }
  function ensureScope(s = scope()) {
    syncTableScope(s);
    const defVis = defaultsFor(s);
    if (typeof ReadingLayout === 'undefined') {
      if (!initializedScopes.has(s)) {
        initializedScopes.add(s);
        const legacy = s === SCOPE_OVERVIEW ? readLegacyVisible() : null;
        fallbackVisible.set(s, legacy || new Set(defVis));
      }
      return;
    }
    if (!initializedScopes.has(s)) {
      initializedScopes.add(s);
      const hasSavedProfile = typeof ReadingLayout.hasProfile === 'function' && ReadingLayout.hasProfile(s);
      const legacy = !hasSavedProfile && s === SCOPE_OVERVIEW ? readLegacyVisible() : null;
      ReadingLayout.register(movable, s, legacy || defVis);
    } else {
      ReadingLayout.register(movable, s, defVis);
    }
  }
  function visibleSet(s = scope()) {
    ensureScope(s);
    if (typeof ReadingLayout === 'undefined') return fallbackVisible.get(s) || new Set(defaultsFor(s));
    return new Set(['name', ...movable.filter(key => ReadingLayout.isVisible(s, key))]);
  }
  function ordered(s = scope()) {
    ensureScope(s);
    if (typeof ReadingLayout === 'undefined') return movable;
    return ReadingLayout.order(movable, s);
  }
  function columns() {
    const s = scope(), vis = visibleSet(s);
    return ['name', ...ordered(s).filter(key => vis.has(key))];
  }
  function size(field) {
    const s = scope(), definition = byName.get(field);
    return typeof ReadingLayout === 'undefined' ? definition.width : ReadingLayout.width(s, field, field === 'name', definition.width);
  }
  function layout(selected = columns()) {
    syncTableScope();
    const sizes = [28, ...selected.filter(key => byName.has(key)).map(size)];
    return {
      size: sizes.reduce((sum, value) => sum + value, 0),
      html: '<colgroup>' + sizes.map(value => `<col style="width:${value}px">`).join('') + '<col class="col-filler"></colgroup>'
    };
  }
  function header(field) {
    if (!byName.has(field)) return '';
    syncTableScope();
    const {label} = byName.get(field), fixed = field === 'name';
    const sorted = typeof SORT_KEY !== 'undefined' && SORT_KEY === field;
    const ascending = typeof SORT_ASC === 'undefined' || SORT_ASC;
    const cls = field === 'status' ? ' class="col-state"' : numeric.has(field) ? ' class="job-numeric"' : '';
    const description = fixed ? 'Job identifier' : 'Drag to reorder; Alt+Left/Right also moves this column';
    return `<th scope="col"${cls} data-reading-${fixed ? 'fixed' : 'field'}="${field}"${fixed ? '' : ' tabindex="0"'} aria-sort="${sorted ? ascending ? 'ascending' : 'descending' : 'none'}" title="${description}">` +
      `<button type="button" class="sort-button" data-sort="${field}">${label}${sorted ? ascending ? ' ↑' : ' ↓' : ''}</button>` +
      `<span class="reading-column-resize" data-column-resize role="separator" aria-label="Resize ${label}" aria-orientation="vertical" aria-valuemin="56" aria-valuemax="1200" aria-valuenow="${size(field)}" tabindex="0" title="Drag or double-click to resize"></span></th>`;
  }
  function cell(job, field) {
    if (!byName.has(field)) return '';
    if (field === 'name') {
      const name = String(job.name ?? ''), suffix = typeof WORKSPACE_LAYOUT !== 'undefined' && WORKSPACE_LAYOUT === 'jobs' ? 12 : 5;
      const split = Math.max(0, name.length - suffix);
      const path = job.source_descriptor?.path;
      return `<td><strong class="job-name" title="${escape(path ? name + ' — ' + path : name)}"><span class="job-name-start">${escape(name.slice(0, split))}</span><span class="job-name-end">${escape(name.slice(split))}</span></strong></td>`;
    }
    if (field === 'status') {
      const held = job.status === 'PENDING' && !!job.held;
      const stopped = job.completion_reason === 'user_stopped';
      const label = held ? 'HELD' : stopped ? 'Stopped by user' :
        typeof displayState === 'function' ? displayState(job.status, job.source_kind) : job.status;
      const badgeCls = held ? 'HELD' : job.status;
      const title = held ? ' title="Held / excluded from dispatch"' : stopped ? ` title="${completionLabel(job.completion_reason)}"` : '';
      const content = held ? `${pauseSvg()} ${escape(label)}` : escape(label);
      return `<td class="col-state"><span class="status-badge st-${escape(badgeCls)}"${title}>${content}</span></td>`;
    }
    if (field === 'result') {
      const result = job.result, cls = String(result || '').replace(/[^A-Z]/g, '') || '-';
      const warnIcon = typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function' ? WaveIcons.svg('alert-triangle') : '⚠';
      return `<td><span class="status-badge result-${escape(cls)}">${escape(result ?? '—')}</span>` +
        (job.error_count ? `<button type="button" class="error-badge-btn" title="Monitoring/control errors occurred (${escape(job.error_count)}) — click to inspect Details/Analysis">${warnIcon} ${escape(job.error_count)} err</button>` : '') +
        (job.log_file_error ? `<button type="button" class="warn log-save-error" aria-label="Log save failed" title="Log save failed: output could not be saved to ${escape(job.log_file_error.path)} — inspect Details/Analysis">Save err</button>` : '') + '</td>';
    }
    if (field === 'warning_count') return `<td class="job-numeric job-warning-cell">${job.warning_count ? `<button type="button" class="warn warning-count" title="View ${escape(job.warning_count)} warning(s) in Analysis">${escape(job.warning_count)}</button>` : '<span class="help-text">0</span>'}</td>`;
    if (field === 'priority') {
      if (job.capabilities?.has_process === false) return '<td class="job-numeric"></td>';
      const editable = job.status === 'PENDING';
      const rawEdit = typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function' ? WaveIcons.svg('edit') : '';
      const edit = rawEdit ? rawEdit.replace('<svg ', '<svg class="job-priority-edit-icon" ') : '<svg class="job-priority-edit-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m16 3 5 5-12 12-6 1 1-6Z"/><path d="m13 6 5 5"/></svg>';
      return `<td class="job-numeric"><button type="button" class="job-priority" data-job-priority="${escape(job.id)}" aria-label="${editable ? 'Edit priority for ' : 'Priority for '}${escape(job.name)}" title="${editable ? 'Click to change pending priority for ' : 'Priority can change only while pending: '}${escape(job.name)}"${editable ? '' : ' disabled'}><span>${escape(job.priority ?? '—')}</span>${editable ? edit : ''}</button></td>`;
    }
    if (field === 'pending_reason') {
      const reason = (job.status === 'PENDING' && job.held) ? 'Excluded from queue (Held)' : (job.pending_reason || job.flow_summary?.reason || '—');
      return `<td title="${escape(reason)}">${job.flow_summary?.evidence ? `<button type="button" class="job-reason" title="Open analysis and evidence">${escape(reason)}</button>` : escape(reason)}</td>`;
    }
    if (field === 'resources') {
      const resource = job.resources && typeof job.resources === 'object' ? Object.entries(job.resources).map(([name, amount]) => `${name}: ${amount}`).join(' · ') : '';
      return `<td title="${escape(resource)}">${escape(resource || '—')}</td>`;
    }
    const value = field === 'pid' ? job.pid ?? job.origin_label ?? '—' :
      field === 'elapsed' ? job.elapsed_str ?? (job.elapsed == null ? '—' : `${job.elapsed}s`) : job[field] ?? '—';
    const cls = numeric.has(field) && !(field === 'pid' && job.pid == null && job.origin_label) ? ' class="job-numeric"' : '';
    return `<td${cls} title="${escape(value)}">${escape(value)}</td>`;
  }
  function choices() {
    const s = scope(), vis = visibleSet(s), order = ['name', ...ordered(s)];
    const arrow = direction => {
      const iconName = direction < 0 ? 'arrow-left' : 'arrow-right';
      return (typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function' && WaveIcons.svg(iconName)) ||
        `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="${direction < 0 ? 'm15 18-6-6 6-6' : 'm9 18 6-6-6-6'}"/></svg>`;
    };
    return order.map((field, index) => {
      const label = byName.get(field).label, fixed = field === 'name';
      const button = direction => `<button type="button" class="icon-button" data-jobs-move="${field}" data-direction="${direction}" aria-label="Move ${label} ${direction < 0 ? 'left' : 'right'}" title="Move ${label} ${direction < 0 ? 'left' : 'right'}"${fixed || direction < 0 && index === 1 || direction > 0 && index === order.length - 1 ? ' disabled' : ''}>${arrow(direction)}</button>`;
      return `<div class="jobs-column-choice"><label><input type="checkbox" data-jobs-visible="${field}"${vis.has(field) ? ' checked' : ''}${fixed ? ' disabled' : ''}> ${label}${fixed ? ' <span class="help-text">required</span>' : ''}</label><span>${button(-1)}${button(1)}</span></div>`;
    }).join('');
  }
  function change(field, show) {
    const s = scope();
    ensureScope(s);
    if (!byName.has(field) || field === 'name' || typeof show !== 'boolean') return;
    if (typeof ReadingLayout !== 'undefined' && typeof ReadingLayout.setVisible === 'function') {
      ReadingLayout.setVisible(s, field, show);
    } else {
      const vis = fallbackVisible.get(s);
      if (show) vis.add(field); else vis.delete(field);
    }
    saveLegacy(s);
    changed();
  }
  function move(field, direction) {
    const s = scope();
    if (!movable.includes(field) || ![-1, 1].includes(direction) || typeof ReadingLayout === 'undefined') return;
    const order = ordered(s), target = order[order.indexOf(field) + direction];
    if (target !== undefined) { ReadingLayout.reorder(s, field, target); changed(); }
  }
  function reset() {
    const s = scope();
    initializedScopes.add(s);
    fallbackVisible.set(s, new Set(defaultsFor(s)));
    if (typeof ReadingLayout !== 'undefined') {
      ReadingLayout.reset(s);
      ReadingLayout.register(movable, s, defaultsFor(s));
    }
    saveLegacy(s);
    changed();
  }
  function allColumns() {
    return definitions.map(([key, label, width]) => ({key, label, width, fixed: key === 'name'}));
  }
  function openContextMenu(event, th, table) {
    if (typeof ReadingLayout === 'undefined' || typeof ReadingLayout.openColumnMenu !== 'function') return null;
    const s = scope();
    ensureScope(s);
    const targetTh = th || event?.target?.closest?.('th');
    const targetTable = table || targetTh?.closest?.('table') || (typeof document !== 'undefined' ? document.getElementById?.('jobsTable') : null);
    if (targetTable?.dataset) targetTable.dataset.readingScope = s;
    const clickedFixed = targetTh?.dataset?.readingFixed !== undefined;
    const clickedField = clickedFixed ? targetTh?.dataset?.readingFixed : targetTh?.dataset?.readingField;
    const clickedLabel = clickedField && byName.has(clickedField) ? byName.get(clickedField).label : '';
    return ReadingLayout.openColumnMenu(event, {
      table: targetTable,
      key: s,
      clickedField,
      clickedLabel,
      clickedFixed,
      allColumns: allColumns(),
      onChange: () => { saveLegacy(s); changed(); },
      reset: () => reset()
    });
  }
  function initialize(root, onChange) {
    if (onChange) changed = onChange;
    if (typeof document !== 'undefined' && typeof document.getElementById === 'function') {
      const head = document.getElementById('jobTableHead');
      if (head && !head._jobsContextMenu) {
        head._jobsContextMenu = true;
        head.oncontextmenu = e => openContextMenu(e);
        head.addEventListener?.('keydown', e => {
          if ((e.key === 'F10' && e.shiftKey) || e.key === 'ContextMenu') {
            const th = e.target?.closest?.('th');
            if (th) openContextMenu(e, th);
          }
        });
      }
    }
    if (!root || root._jobsColumns) return;
    root._jobsColumns = true;
    root.addEventListener('change', event => {
      const checkbox = event.target.closest?.('[data-jobs-visible]');
      if (!checkbox) return;
      const field = checkbox.dataset.jobsVisible;
      change(field, checkbox.checked);
      root.querySelector?.(`[data-jobs-visible="${field}"]`)?.focus({preventScroll: true});
    });
    root.addEventListener('click', event => {
      const button = event.target.closest?.('[data-jobs-move]');
      if (!button || button.disabled) return;
      const field = button.dataset.jobsMove, direction = Number(button.dataset.direction);
      move(field, direction);
      root.querySelector?.(`[data-jobs-move="${field}"][data-direction="${direction}"]`)?.focus({preventScroll: true});
    });
  }
  return {scope, columns, header, cell, layout, choices, change, move, reset, initialize, completionLabel, openContextMenu, allColumns};
})();
