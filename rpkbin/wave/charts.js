/* Archive charts. Canvas geometry and typed values are independent of the DOM. */
const ChartMath = (() => {
  function isUnknown(value) {
    return value == null || value === 'X' ||
      (typeof value === 'string' && value.startsWith('4s:') && value.includes('X'));
  }

  function isHighZ(value) {
    return value === 'Z' ||
      (typeof value === 'string' && value.startsWith('4s:') && value.includes('Z') && !value.includes('X'));
  }

  function format4StateHex(bitsStr) {
    let out = '';
    for (let hi = bitsStr.length; hi > 0; hi -= 4) {
      const lo = Math.max(0, hi - 4);
      const chunk = bitsStr.slice(lo, hi).padStart(4, '0');
      if (chunk.includes('X')) out = 'X' + out;
      else if (chunk.includes('Z')) out = 'Z' + out;
      else out = parseInt(chunk, 2).toString(16).toUpperCase() + out;
    }
    return '0x' + out;
  }

  function bits(value, definition) {
    const width = definition.bits;
    if (!Number.isInteger(width) || width < 1 || width > 256 ||
        typeof value !== 'string' || !/^-?[0-9]{1,78}$/.test(value)) return null;
    const parsed = BigInt(value), size = 1n << BigInt(width);
    const limit = definition.signed ? size >> 1n : size;
    if (parsed < (definition.signed ? -limit : 0n) || parsed >= limit) return null;
    return BigInt.asUintN(width, parsed);
  }

  function formatIntegerRadix(unsigned, width, signedVal, radix, fracBits) {
    if (radix === 'hex') return '0x' + unsigned.toString(16).toUpperCase().padStart(Math.ceil(width / 4), '0');
    if (radix === 'bin') return '0b' + unsigned.toString(2).padStart(width, '0');
    if (radix === 'signed') return String(signedVal);
    if (radix !== 'fxp') return String(unsigned);
    const frac = fracBits ?? 0;
    if (!Number.isInteger(frac) || frac < 0 || frac > 256) return 'Unknown';
    const decimal = String((signedVal < 0n ? -signedVal : signedVal) * 5n ** BigInt(frac)).padStart(frac + 1, '0');
    const scaled = frac ? decimal.slice(0, -frac) + '.' + decimal.slice(-frac) : decimal;
    return (signedVal < 0n ? '-' : '') + scaled.replace(/(\.[0-9]*?)0+$/, '$1').replace(/\.$/, '');
  }

  function format(value, definition, radix = definition.radix) {
    if (value == null) return 'Unknown';
    if (value === 'X' || value === 'Z') return value;
    const fxpSigned = definition.fxpSigned !== undefined ? !!definition.fxpSigned : !!definition.signed;
    const useSigned = radix === 'signed' || (radix === 'fxp' ? fxpSigned : !!definition.signed);
    if (typeof value === 'string' && value.startsWith('4s:')) {
      const raw = value.slice(3);
      if (!raw.includes('X') && !raw.includes('Z')) {
        const numeric = BigInt('0b' + raw);
        const width = definition.bits || raw.length;
        const signed = BigInt.asIntN(width, numeric);
        return formatIntegerRadix(numeric, width, useSigned ? signed : numeric, radix, definition.frac_bits);
      }
      if (radix === 'bin') return '0b' + raw;
      if (radix === 'hex') return format4StateHex(raw);
      if (/^X+$/.test(raw)) return 'X';
      if (/^Z+$/.test(raw)) return 'Z';
      return format4StateHex(raw);
    }
    if (!definition.bits) {
      if (radix && radix !== definition.radix && /^-?[0-9]{1,78}$/.test(String(value))) {
        const val = BigInt(String(value));
        const width = definition.type === 'bit' ? 1 : 64;
        const unsigned = BigInt.asUintN(width, val);
        const signed = BigInt.asIntN(width, unsigned);
        return formatIntegerRadix(unsigned, width, useSigned ? signed : unsigned, radix, definition.frac_bits);
      }
      return String(value);
    }
    const unsigned = bits(value, definition);
    if (unsigned === null) return 'Unknown';
    const width = definition.bits, signed = BigInt.asIntN(width, unsigned);
    return formatIntegerRadix(unsigned, width, useSigned ? signed : unsigned, radix, definition.frac_bits);
  }

  function parseSlice(text, bitsWidth) {
    const match = /^\[(\d{1,3})(?::(\d{1,3}))?\]$/.exec(text.trim());
    if (!match) return null;
    const msb = Number(match[1]), lsb = Number(match[2] ?? match[1]);
    return msb < bitsWidth && msb >= lsb ? [msb, lsb] : null;
  }

  function slice(value, definition, [msb, lsb]) {
    if (value == null) return null;
    if (value === 'X' || value === 'Z') return value;
    if (typeof value === 'string' && value.startsWith('4s:')) {
      const raw = value.slice(3);
      const start = raw.length - 1 - msb, stop = raw.length - lsb;
      if (start < 0 || stop > raw.length || start >= stop) return null;
      const sub = raw.slice(start, stop);
      if (!sub.includes('X') && !sub.includes('Z')) return BigInt('0b' + sub).toString();
      if (/^X+$/.test(sub)) return 'X';
      if (/^Z+$/.test(sub)) return 'Z';
      return '4s:' + sub;
    }
    const bus = bits(value, definition);
    if (bus === null) return null;
    return String(BigInt.asUintN(msb - lsb + 1, bus >> BigInt(lsb)));
  }

  const pointsBySeriesCache = new WeakMap();

  function pointsForSeries(data, seriesId) {
    const points = data?.points;
    if (!Array.isArray(points) || !points.length) return [];
    let cached = pointsBySeriesCache.get(data);
    if (!cached || cached.points !== points || cached.len !== points.length) {
      const bySeries = new Map();
      for (const point of points) {
        const sid = point?.series_id;
        if (sid == null) continue;
        let arr = bySeries.get(sid);
        if (!arr) {
          arr = [];
          bySeries.set(sid, arr);
        }
        arr.push(point);
      }
      cached = {points, len: points.length, bySeries};
      pointsBySeriesCache.set(data, cached);
    }
    return cached.bySeries.get(seriesId) ?? [];
  }

  function firstSpanIndex(spans, loX) {
    if (!Array.isArray(spans) || !spans.length) return 0;
    let lo = 0, hi = spans.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (spans[mid].hi < loX) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function frontier(data) {
    return data.complete ? data.range[1] : data.coverage?.[0] ?? data.range[0];
  }

  function waveSegments(data, lane) {
    const [lo, hi] = data.range, end = frontier(data);
    const parent = lane.parent ?? lane;
    const value = point => lane.bitSlice ? slice(point?.value, parent, lane.bitSlice) : point?.value ?? null;
    const list = [];
    let at = lo, current = data.left?.[parent.id] ?? null;

    function hold(next) {
      if (next >= at) {
        const val = value(current);
        list.push({lo: at, hi: next, value: val, isX: isUnknown(val), isZ: isHighZ(val), point: current});
      }
      at = next;
    }
    function event(point) {
      if (!point) return;
      if (current && current.flow_seq === point.flow_seq && current.x === point.x) return;
      if (current && value(current) === value(point) && lane.bitSlice) return;
      if (point.x > at || (point.x === at && current)) hold(point.x);
      current = point;
      at = Math.max(at, point.x);
    }
    if (data.buckets) {
      const step = (hi - lo) / data.bucket_count;
      for (const bucket of data.buckets[parent.id] ?? []) {
        if (bucket.transitions > 1) {
          const change = bucket.first_change ?? bucket.first;
          const lower = Math.max(at, change.x);
          const upper = Math.min(end, lo + (bucket.index + 1) * step);
          if (lower > at) hold(lower);
          list.push({lo: lower, hi: Math.max(lower, upper), dense: true,
            unknown: bucket.unknown, isX: !!bucket.unknown, isZ: false, point: change});
          at = Math.max(lower, upper);
          current = bucket.last;
        } else {
          event(bucket.first);
          event(bucket.first_change);
          if (value(current) !== value(bucket.last)) event(bucket.last);
        }
      }
    } else {
      for (const point of pointsForSeries(data, parent.id)) event(point);
    }
    if (end >= at) hold(end);
    return list;
  }

  function holdAt(spans, x) {
    let lo = 0, hi = spans.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (spans[mid].lo <= x) lo = mid + 1;
      else hi = mid;
    }
    const span = spans[lo - 1];
    return span && x <= span.hi ? span : null;
  }

  function snapToEdge(spans, x, maxDeltaX) {
    if (!Array.isArray(spans) || !spans.length || !Number.isFinite(x)) {
      return {x, snapped: false, span: null, prevSpan: null};
    }
    let bestDelta = maxDeltaX;
    let bestX = x, bestSpan = null, bestPrev = null, snapped = false;
    for (let i = 1; i < spans.length; i++) {
      const prev = spans[i - 1], curr = spans[i];
      if (!curr.dense && !prev.dense && prev.value === curr.value) continue;
      const edgeX = curr.lo;
      const delta = Math.abs(edgeX - x);
      if (delta <= bestDelta) {
        bestDelta = delta;
        bestX = edgeX;
        bestSpan = curr;
        bestPrev = prev;
        snapped = true;
      }
    }
    if (snapped) return {x: bestX, snapped: true, span: bestSpan, prevSpan: bestPrev};
    return {x, snapped: false, span: holdAt(spans, x), prevSpan: null};
  }

  function valueAtCursor(spans, x, lane, formatFn, edgeEpsilon = 0, pref = null) {
    if (!Array.isArray(spans) || !spans.length || !Number.isFinite(x)) {
      return {text: '—', transitionText: '—', isTransition: false, kind: 'empty', point: null, prevPoint: null};
    }
    let lo = 0, hi = spans.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (spans[mid].lo <= x) lo = mid + 1;
      else hi = mid;
    }
    const idx = lo - 1;
    const span = idx >= 0 && x <= spans[idx].hi ? spans[idx] : null;
    if (!span) {
      return {text: '—', transitionText: '—', isTransition: false, kind: 'empty', point: null, prevPoint: null};
    }
    const formatVal = s => {
      if (!s) return '—';
      if (s.dense) return 'Dense';
      if (s.value == null) return 'X';
      return formatFn(lane, s.value);
    };
    const text = formatVal(span);
    const prevSpan = idx > 0 ? spans[idx - 1] : null;
    const prevText = prevSpan ? formatVal(prevSpan) : '';
    const atStartEdge = !!(prevSpan && Math.abs(x - span.lo) <= edgeEpsilon);
    const nextSpan = !atStartEdge && idx + 1 < spans.length &&
      Math.abs(x - span.hi) <= edgeEpsilon && spans[idx + 1].lo <= span.hi + edgeEpsilon
      ? spans[idx + 1] : null;
    const edgeFrom = atStartEdge ? prevSpan : nextSpan ? span : null;
    const edgeTo = atStartEdge ? span : nextSpan ? nextSpan : null;
    const fromText = formatVal(edgeFrom), toText = formatVal(edgeTo);
    const isTransition = !!(edgeFrom && edgeTo && fromText !== toText);
    let lastDir = '';
    const dirFrom = isTransition ? edgeFrom : prevSpan;
    const dirTo = isTransition ? edgeTo : span;
    if (dirFrom && dirTo && formatVal(dirFrom) !== formatVal(dirTo)) {
      const cNum = analogNumeric(dirTo.value, lane, pref), pNum = analogNumeric(dirFrom.value, lane, pref);
      if (cNum !== null && pNum !== null && cNum !== pNum) lastDir = cNum > pNum ? '↑' : '↓';
      else lastDir = '↔';
    }
    const kind = span.dense ? 'dense' : isUnknown(span.value) ? 'x' : isHighZ(span.value) ? 'z' : 'ok';
    return {
      text,
      transitionText: isTransition ? `${fromText} → ${toText}` : text,
      isTransition,
      lastDir,
      prevText,
      kind,
      point: (edgeTo?.point ?? span.point) || null,
      prevPoint: edgeFrom?.point ?? null,
    };
  }

  function analogNumeric(value, definition, pref = null) {
    if (value == null || isUnknown(value) || isHighZ(value)) return null;
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    const text = String(value).trim();
    if (!text) return null;
    const effRadix = pref?.radix || definition?.radix;
    const fxpSigned = pref?.fxpSigned !== undefined ? !!pref.fxpSigned
      : (definition?.fxpSigned !== undefined ? !!definition.fxpSigned : !!definition?.signed);
    const useSigned = effRadix === 'signed'
      ? true
      : effRadix === 'fxp'
        ? fxpSigned
        : (effRadix === 'unsigned' || effRadix === 'hex' || effRadix === 'bin')
          ? false
          : !!definition?.signed;
    const frac = effRadix === 'fxp'
      ? (pref?.frac ?? definition?.frac_bits ?? 0)
      : (!pref?.radix && definition?.type === 'fxp' ? (definition?.frac_bits ?? 0) : 0);
    const divisor = Number.isInteger(frac) && frac > 0 && frac <= 256 ? Math.pow(2, frac) : 1;
    if (text.startsWith('4s:')) {
      const raw = text.slice(3);
      if (!raw || raw.includes('X') || raw.includes('Z')) return null;
      const unsigned = BigInt('0b' + raw);
      const width = definition?.bits || raw.length;
      const numeric = useSigned ? BigInt.asIntN(width, unsigned) : unsigned;
      const out = Number(numeric) / divisor;
      return Number.isFinite(out) ? out : null;
    }
    if (definition?.bits) {
      const unsigned = bits(text, definition);
      if (unsigned === null) return null;
      const numeric = useSigned ? BigInt.asIntN(definition.bits, unsigned) : unsigned;
      const out = Number(numeric) / divisor;
      return Number.isFinite(out) ? out : null;
    }
    if (/^-?[0-9]+(?:\.[0-9]+)?$/.test(text)) {
      const out = Number(text) / (effRadix === 'fxp' ? divisor : 1);
      return Number.isFinite(out) ? out : null;
    }
    return null;
  }

  function analogRange(spans, lane, range, pref = null) {
    let min = null, max = null;
    const list = spans ?? [];
    const startIdx = range ? firstSpanIndex(list, range[0]) : 0;
    for (let i = startIdx; i < list.length; i++) {
      const span = list[i];
      if (range && span.lo > range[1]) break;
      const v = analogNumeric(span.value, lane, pref);
      if (v === null) continue;
      if (min === null || v < min) min = v;
      if (max === null || v > max) max = v;
    }
    if (min === null) return [-1, 1];
    if (min === max) {
      const pad = Math.max(0.5, Math.abs(min) * 0.05);
      return [min - pad, max + pad];
    }
    const pad = (max - min) * 0.06;
    return [min - pad, max + pad];
  }

  function classifyDragStroke(dx, dy) {
    const absX = Math.abs(dx), absY = Math.abs(dy);
    if (dx > 20 && dy < -28 && absY >= absX * 0.45) return 'zoom_out';
    if (dx < -20 && dy > 28 && absY >= absX * 0.45) return 'zoom_fit';
    return 'zoom_in';
  }

  function laneRows(items, definitions, settings, baseRowH = 23) {
    const rows = [];
    let y = 0;
    const pushRow = row => {
      rows.push({...row, y});
      y += row.height;
    };
    const getDef = id => definitions instanceof Map ? definitions.get(id) : definitions?.[id];
    const getPref = key => (settings instanceof Map ? settings.get(key) : settings?.[key]) || {};
    const clampH = v => Math.max(18, Math.min(180, Math.round(v)));
    function pushSignalRows(token, groupObj = null, isLastInGroup = false) {
      const baseId = typeof token === 'string' ? token.split('@@')[0] : '';
      const rawDef = baseId ? getDef(baseId) : null;
      if (!rawDef) return;
      const def = token === baseId ? rawDef : {...rawDef, id: baseId, instanceId: token};
      const key = JSON.stringify([token, null]);
      const pref = getPref(key);
      const isAnalog = pref.display === 'analog_step' || pref.display === 'analog_linear';
      const defaultScale = isAnalog ? 2 : 1;
      const scaleH = Math.max(1, Math.min(6, Number(pref.heightScale) || defaultScale));
      const customH = Number(pref.customHeight);
      const rowH = Number.isFinite(customH) && customH >= 18 ? clampH(customH) : baseRowH * scaleH;
      const baseIndent = groupObj ? 1 : 0;
      const treePrefix = groupObj ? (isLastInGroup ? '└─ ' : '├─ ') : '';
      pushRow({
        kind: 'signal',
        key,
        lane: def,
        parent: def,
        label: def.label,
        height: rowH,
        indent: baseIndent,
        groupId: groupObj?.id ?? null,
        treePrefix,
        pref,
      });
      if (def.bits && pref.expanded && def.bits <= 64) {
        for (let b = def.bits - 1; b >= 0; b--) {
          const bitSlice = [b, b];
          const subLane = {
            ...def, label: `[${b}]`, bitSlice, parent: def, instanceId: token,
            signed: false, type: 'bit', bits: undefined, frac_bits: 0,
          };
          const subKey = JSON.stringify([token, bitSlice]);
          const subPref = getPref(subKey);
          const bitScale = Math.max(1, Math.min(6, Number(subPref.heightScale) || 1));
          const bitCustom = Number(subPref.customHeight);
          const bitH = Number.isFinite(bitCustom) && bitCustom >= 18 ? clampH(bitCustom) : baseRowH * bitScale;
          pushRow({
            kind: 'expanded_bit',
            key: subKey,
            lane: subLane,
            parent: def,
            label: subLane.label,
            height: bitH,
            indent: baseIndent + 1,
            groupId: groupObj?.id ?? null,
            pref: subPref,
          });
        }
      }
      const slices = Array.isArray(pref.slices) ? pref.slices : [];
      for (const text of slices.slice(0, 16)) {
        const bitSlice = typeof text === 'string' && parseSlice(text, def.bits);
        if (!bitSlice) continue;
        const subBits = bitSlice[0] - bitSlice[1] + 1;
        const subLane = {
          ...def, label: def.label + text, bitSlice, parent: def, instanceId: token,
          signed: false, type: subBits === 1 ? 'bit' : 'bus',
          bits: subBits === 1 ? undefined : subBits, frac_bits: 0,
        };
        const subKey = JSON.stringify([token, bitSlice]);
        const subPref = getPref(subKey);
        const subAnalog = subPref.display === 'analog_step' || subPref.display === 'analog_linear';
        const subScale = Math.max(1, Math.min(6, Number(subPref.heightScale) || (subAnalog ? 2 : 1)));
        const subCustom = Number(subPref.customHeight);
        const subH = Number.isFinite(subCustom) && subCustom >= 18 ? clampH(subCustom) : baseRowH * subScale;
        pushRow({
          kind: 'slice',
          key: subKey,
          lane: subLane,
          parent: def,
          label: subLane.label,
          height: subH,
          indent: baseIndent + 1,
          groupId: groupObj?.id ?? null,
          pref: subPref,
        });
      }
    }
    for (const entry of items ?? []) {
      if (entry && typeof entry === 'object' && (entry.type === 'group' || entry.type === 'divider')) {
        const sigs = Array.isArray(entry.signals) ? entry.signals.filter(s => typeof s === 'string') : [];
        pushRow({
          kind: entry.type === 'group' ? 'group' : 'divider',
          key: 'div:' + entry.id,
          group: entry,
          divider: entry,
          label: entry.label || 'G1',
          collapsed: !!entry.collapsed,
          count: sigs.length,
          signalCount: sigs.length,
          height: baseRowH,
          indent: 0,
        });
        if (!entry.collapsed) {
          sigs.forEach((sigId, sIdx) => pushSignalRows(sigId, entry, sIdx === sigs.length - 1));
        }
        continue;
      }
      const id = typeof entry === 'string' ? entry : entry?.id;
      pushSignalRows(id, null, false);
    }
    return {rows, totalHeight: y};
  }

  function xyOverviewCell(cell, seriesId = null) {
    const tag = point => !point || !seriesId || point.series_id != null ? point : {...point, series_id: seriesId};
    const points = [], seen = new Set();
    for (const point of [cell.first, cell.min, cell.max, cell.last]) {
      if (!point) continue;
      const key = JSON.stringify([point.flow_seq, point.x]);
      if (seen.has(key)) continue;
      seen.add(key);
      points.push(tag(point));
    }
    points.sort((a, b) => a.x - b.x || a.flow_seq - b.flow_seq);
    const violationKey = cell.violation && JSON.stringify([cell.violation.flow_seq, cell.violation.x]);
    return {points, gap: cell.has_gap, violation: violationKey && seen.has(violationKey) ? null : tag(cell.violation)};
  }

  return {
    isUnknown,
    isHighZ,
    format,
    parseSlice,
    slice,
    firstSpanIndex,
    frontier,
    waveSegments,
    holdAt,
    snapToEdge,
    valueAtCursor,
    analogNumeric,
    analogRange,
    classifyDragStroke,
    laneRows,
    xyOverviewCell,
  };
})();

const ChartControls = (() => {
  function resetFind(operation, value) {
    operation.value = 'any';
    value.value = '';
    value.hidden = true;
  }

  function windowActions(kind, domain, range) {
    const valid = Array.isArray(domain) && Array.isArray(range) && domain.length === 2 && range.length === 2 &&
      domain.every(Number.isFinite) && range.every(Number.isFinite) && domain[1] > domain[0] &&
      range[1] > range[0] && range[0] >= domain[0] && range[1] <= domain[1];
    const reset = valid && (range[0] !== domain[0] || range[1] !== domain[1]);
    const zoom = valid && (kind === 'waveform' || kind === 'xy_line');
    return {zoom, pan: zoom, reset};
  }

  function syncWindowButtons(kind, domain, range, buttons) {
    const actions = windowActions(kind, domain, range);
    buttons.zoomIn.disabled = !actions.zoom;
    buttons.zoomOut.disabled = !actions.zoom;
    buttons.panLeft.disabled = !actions.pan;
    buttons.panRight.disabled = !actions.pan;
    buttons.reset.disabled = !actions.reset;
    return actions;
  }

  function matchesPicked(picked, point, viewId, axis) {
    return !!picked && !!point && picked.view_id === viewId && picked.axis === axis &&
      picked.series_id != null && picked.series_id === point.series_id &&
      picked.flow_seq === point.flow_seq && picked.x === point.x;
  }

  function drawPicked(ctx, picked, point, viewId, axis, x, y) {
    if (!matchesPicked(picked, point, viewId, axis)) return false;
    ctx.save();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.strokeStyle = '#e2b36b';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(x, y, 4.5, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.restore();
    return true;
  }

  return Object.freeze({resetFind, windowActions, syncWindowButtons, matchesPicked, drawPicked});
})();

const ChartWorkspace = (() => {
  const SCI_COLORS = ['#58cbd4', '#e2b36b', '#b69af3', '#8bd298', '#f18eaa', '#8ab1f7'];
  const WAVE_PALETTE = ['#22c55e', '#facc15', '#38bdf8', '#fb923c', '#c084fc', '#f472b6', '#e2e8f0'];
  const RADICES = ['hex', 'bin', 'unsigned', 'signed', 'fxp'];
  const UNIT_FACTORS = {fs: 1e-15, ps: 1e-12, ns: 1e-9, us: 1e-6, ms: 1e-3, s: 1};
  const MAX_LANES = 64;
  const RULER_HEIGHT = 24;
  const PROFILE_KEY = 'wave.charts.preferences.2';
  const node = id => document.getElementById(id);
  const fmt = value => Number.isFinite(value) ?
    Number(value.toPrecision(7)).toString() : '—';
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const clipped = (text, limit) => text.length > limit ? text.slice(0, limit - 1) + '…' : text;
  const baseSignalId = token => typeof token === 'string' ? token.split('@@')[0] : '';
  const savedRun = () => RunHistory.selection;
  const scope = d => [STATE.workspace_id, savedRun()?.run_id || STATE.archive?.run_id,
    d?.id, d?.attempt_id].join(':');

  let ready = false, detail = null, identity = '', liveStamp = '', liveTimer = 0;
  let metadata, view, axis, domain, range, data, pageOptions, cursor;
  let selected = null;
  let directory = [], directoryCursor = null, definitions = new Map(), activePrefix = [], scopePaths = new Set();
  let collapsedScopes = new Set(), scopeRecursive = true, catalogBackendQuery = '';
  let pendingSignals = new Set();
  let settings = new Map(), profileKey = '', memory = [];
  let box, canvas, ctx, overlayCanvas, overlayCtx, baseFrame = null, overlayFrame = null;
  let width = 0, height = 0, scale = 1;
  let timer = 0, searchTimer = 0, coverageText = '', findCursor = null, findRequest = null;
  let profilesLoaded = false, waveGeometry = new Map();
  let hover = null, drag = null, picked = null, rendered = null;
  let cursorA = null, cursorB = null, cursorAAnchor = null, cursorBAnchor = null;
  let snapEnabled = true, compactMode = true, showFullHierarchy = false, displayTimeUnit = null;
  let selectedRowKey = null, rowRefs = new Map(), syncScrollLock = false;
  let activeRows = {rows: [], totalHeight: 0}, divCounter = 0;
  let dragReorder = null, viewHistory = [], signalClipboard = null;
  let currentRcPath = '', lastRcDir = '', fileModalMode = '', colRatioMode = 0, paramModalApplyFn = null;
  let fileHistory = [], fileHistoryIndex = -1, fileEntries = [], fileSelectedIndex = -1;
  let lastWheelAt = 0, contextMenuOpener = null;
  const modalReturnFocus = new Map();
  const DEFAULT_SAVE_RC_TITLE = 'Save waveform .wave.rc config (first save asks path; Shift+click to Save As…)';
  const MAX_HISTORY = 32;
  const requests = new Map();
  const isGroupRow = row => !!row && (row.kind === 'group' || row.kind === 'divider');

  function mountWaveIcon(target, name, size = 14) {
    if (!target) return;
    if (typeof WaveIcons !== 'undefined' && typeof WaveIcons.svg === 'function') {
      const markup = WaveIcons.svg(name);
      if (markup) {
        target.innerHTML = size ? markup.replace('<svg ', `<svg width="${size}" height="${size}" `) : markup;
        if (target.style) {
          target.style.display = 'inline-flex';
          target.style.alignItems = 'center';
          target.style.justifyContent = 'center';
          target.style.flex = 'none';
        }
        return;
      }
    }
    target.textContent = '';
  }

  function canFocusEl(el) {
    if (!el || el.disabled) return false;
    if (el.isConnected === false) return false;
    if (typeof el.getClientRects === 'function' && !el.getClientRects().length) return false;
    if (typeof getComputedStyle === 'function') {
      try { if (getComputedStyle(el).visibility !== 'visible') return false; } catch (_) {}
    }
    return true;
  }

  function captureModalOpener(modalId, fallback = null) {
    const active = typeof document !== 'undefined' ? document.activeElement : null;
    const modal = node(modalId), menu = node('chartContextMenu');
    let opener = active;
    if (!opener || (modal && modal.contains?.(opener)) || (menu && menu.contains?.(opener))) {
      opener = (menu && menu.contains?.(active) && contextMenuOpener) ? contextMenuOpener : fallback;
    }
    if (opener) modalReturnFocus.set(modalId, opener);
  }

  function restoreModalFocus(modalId, fallback = null) {
    const opener = modalReturnFocus.get(modalId);
    modalReturnFocus.delete(modalId);
    const target = [opener, fallback, canvas].find(canFocusEl);
    target?.focus?.({preventScroll: true});
  }

  function activeChartModal() {
    for (const id of ['chartFileModal', 'chartParamModal', 'chartSignals']) {
      const m = node(id);
      if (m && !m.hidden) return m;
    }
    return null;
  }

  function modalFocusableControls(modalEl) {
    if (!modalEl || typeof modalEl.querySelectorAll !== 'function') return [];
    const list = Array.from(modalEl.querySelectorAll('button,input,select,textarea,summary,a[href],[tabindex]') || []);
    return list.filter(item => {
      if (!item || item.disabled || item.hidden || item.tabIndex === -1) return false;
      let cur = item;
      while (cur && cur !== modalEl) {
        if (cur.hidden) return false;
        cur = cur.parentElement;
      }
      return canFocusEl(item);
    });
  }

  function trapModalTab(modalEl, event) {
    const controls = modalFocusableControls(modalEl);
    if (!controls.length) return false;
    const active = typeof document !== 'undefined' ? document.activeElement : null;
    const idx = controls.indexOf(active);
    const nextIdx = event.shiftKey
      ? (idx <= 0 ? controls.length - 1 : idx - 1)
      : (idx < 0 || idx >= controls.length - 1 ? 0 : idx + 1);
    controls[nextIdx]?.focus?.({preventScroll: true});
    event.preventDefault?.();
    event.stopPropagation?.();
    return true;
  }

  function clipboardSignalIds() {
    if (!signalClipboard) return [];
    if (signalClipboard.kind === 'group') {
      return (signalClipboard.signals || []).map(s => s?.baseId).filter(Boolean);
    }
    return signalClipboard.baseId ? [signalClipboard.baseId] : [];
  }

  function sanitizeSelectionList(rawList) {
    const seenGroups = new Set(), seenTokens = new Set(), cleaned = [];
    let tokenBudget = 0;
    for (const item of rawList ?? []) {
      const clean = sanitizeSelectedItem(item);
      if (!clean) continue;
      if (typeof clean === 'string') {
        if (seenTokens.has(clean) || tokenBudget >= MAX_LANES) continue;
        seenTokens.add(clean);
        tokenBudget++;
        cleaned.push(clean);
      } else {
        if (seenGroups.has(clean.id)) continue;
        seenGroups.add(clean.id);
        const kept = [];
        for (const s of clean.signals) {
          if (seenTokens.has(s) || tokenBudget >= MAX_LANES) continue;
          seenTokens.add(s);
          tokenBudget++;
          kept.push(s);
        }
        clean.signals = kept;
        cleaned.push(clean);
      }
    }
    return cleaned.slice(0, MAX_LANES * 2);
  }

  function baseRowH() {
    return compactMode ? 23 : 28;
  }

  function make(tag, text, props = {}) {
    const element = document.createElement(tag);
    Object.assign(element, props);
    if (text != null) element.textContent = text;
    return element;
  }

  function button(text, label, action) {
    const item = make('button', text, {type: 'button', title: label});
    item.setAttribute('aria-label', label);
    item.addEventListener('click', action);
    return item;
  }

  function options(id, entries, value) {
    const select = node(id);
    if (!select) return;
    select.replaceChildren(...entries.map(([key, label]) => make('option', label, {value: key})));
    select.value = value ?? '';
    select.disabled = !entries.length;
  }

  function status(message, coverage = coverageText) {
    coverageText = coverage;
    const statusEl = node('chartStatus');
    if (!statusEl) return;
    statusEl.textContent = [message, coverage].filter(Boolean).join(' · ');
    statusEl.title = statusEl.textContent;
  }

  function cancel(channel) {
    requests.get(channel)?.abort();
    requests.delete(channel);
  }

  async function request(channel, optionsObj, accept, reject = error => status(error.message)) {
    cancel(channel);
    const generation = identity, attempt = detail;
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    requests.set(channel, controller);
    const current = () => requests.get(channel) === controller && generation === identity &&
      scope(attempt) === generation && !controller?.signal?.aborted;
    try {
      const response = await RunHistory.query(
        optionsObj, attempt, controller ? {signal: controller.signal} : undefined,
      );
      if (current()) accept(response);
    } catch (error) {
      if (current() && error?.name !== 'AbortError' && !error?.isAborted) reject(error);
    } finally {
      if (requests.get(channel) === controller) requests.delete(channel);
    }
  }

  function profile(key) {
    if (!profilesLoaded) {
      profilesLoaded = true;
      try {
        const raw = localStorage.getItem(PROFILE_KEY);
        if (raw && raw.length <= 2000000) {
          const result = JSON.parse(raw);
          if (Array.isArray(result)) memory = result.slice(0, 32);
        }
      } catch (_) { /* Session preferences still work with storage disabled. */ }
    }
    return memory.find(item => item?.key === key);
  }

  function saveProfile() {
    if (!profileKey) return;
    const allowed = new Set(allKnownRowKeys());
    for (const key of settings.keys()) if (!allowed.has(key)) settings.delete(key);
    const preferences = {key: profileKey, selected, compactMode, showFullHierarchy, settings: [...settings]};
    memory = [preferences, ...memory.filter(item => item?.key !== profileKey)].slice(0, 32);
    try { localStorage.setItem(PROFILE_KEY, JSON.stringify(memory)); } catch (_) { /* Session only. */ }
  }

  function resetRequests() {
    for (const channel of requests.keys()) cancel(channel);
    clearTimeout(timer);
    clearTimeout(searchTimer);
    clearTimeout(liveTimer);
    liveTimer = 0;
  }

  function closeContextMenu(restoreFocus = false) {
    const menu = node('chartContextMenu');
    if (menu) {
      menu.hidden = true;
      menu.replaceChildren();
    }
    const opener = contextMenuOpener;
    contextMenuOpener = null;
    if (restoreFocus) {
      const target = [opener, node('chartSignalRows'), canvas].find(canFocusEl);
      target?.focus?.({preventScroll: true});
    }
  }

  function hasEscapeTarget() {
    return !node('chartFileModal')?.hidden ||
      !node('chartParamModal')?.hidden ||
      !node('chartContextMenu')?.hidden ||
      !node('chartSignals')?.hidden ||
      (view?.kind === 'waveform' && cursorB != null);
  }

  function refresh(d) {
    if (typeof WORKSPACE_LAYOUT !== 'undefined' && WORKSPACE_LAYOUT !== 'charts') d = null;
    if (!d && !ready) return;
    initialize();
    if (d && box?.clientWidth && box?.clientHeight && (!width || !height)) resize();
    const next = d ? scope(d) : '';
    detail = d;
    if (next === identity) {
      if (!d || liveTimer || savedRun()?.health?.closed_cleanly || !(savedRun()?.health || STATE.archive)?.enabled) return;
      const stamp = String(d.total_lines) + ':' + d.status;
      if (stamp !== liveStamp) {
        liveStamp = stamp;
        liveTimer = setTimeout(() => { liveTimer = 0; loadMeta(true); }, 325);
      }
      return;
    }
    identity = next;
    resetRequests();
    closeContextMenu();
    metadata = view = null;
    clearView();
    options('chartViewSelect', []);
    options('chartAxis', []);
    if (node('chartSignalsToggle')) node('chartSignalsToggle').disabled = true;
    if (node('chartNewData')) node('chartNewData').hidden = true;
    liveStamp = d ? String(d.total_lines) + ':' + d.status : '';
    if (!d) status('Select a job to view its charts.', '');
    else if (!(savedRun()?.health || STATE.archive)?.enabled) status('Charts require a Run Archive.', '');
    else loadMeta(false);
  }

  function computeAxisDomain(v, ax) {
    const bounds = v?.domains?.[ax];
    let d = bounds && bounds[0] === bounds[1] ?
      [bounds[0] - Math.max(.5, Math.abs(bounds[0]) * Number.EPSILON),
        bounds[1] + Math.max(.5, Math.abs(bounds[1]) * Number.EPSILON)] : bounds;
    if (bounds && (bounds[0] === bounds[1]) && ax === 'seq') d = [bounds[0] - 1, bounds[0] + 1];
    return d;
  }

  function loadMeta(probe, preserveState = false) {
    if (!detail) return;
    const oldView = view?.id;
    const snap = (preserveState && view) ? {
      viewId: view.id,
      axis,
      range: range ? range.slice() : null,
      domain: domain ? domain.slice() : null,
      cursorA,
      cursorB,
      cursorAAnchor,
      cursorBAnchor,
      viewHistory: viewHistory.slice(),
      displayTimeUnit,
      currentRcPath,
      saveTitle: node('chartSaveRcBtn')?.title || '',
      selected: selected ? selected.slice() : null,
      settings: new Map(settings),
      showFullHierarchy,
      selectedRowKey,
      compactMode,
    } : null;
    if (!probe) status('Reading chart metadata…', '');
    request('meta', {view: 'chart_meta'}, result => {
      if (probe && metadata?.views.some(item => item.series_count)) {
        if (node('chartNewData')) node('chartNewData').hidden = result.as_of_seq <= metadata.as_of_seq;
        return;
      }
      metadata = result;
      if (node('chartNewData')) node('chartNewData').hidden = true;
      options('chartViewSelect', result.views.map(item => [item.id, item.title]),
        result.views.some(item => item.id === oldView) ? oldView : result.views[0]?.id);
      if (snap && result.views.some(item => item.id === snap.viewId)) {
        const nextView = result.views.find(item => item.id === snap.viewId);
        view = nextView;
        const d = detail.flow;
        profileKey = JSON.stringify([d?.name, d?.version, metadata.spec_version, metadata.digest, nextView.id]);
        selected = snap.selected;
        settings = snap.settings;
        showFullHierarchy = snap.showFullHierarchy;
        selectedRowKey = snap.selectedRowKey;
        compactMode = snap.compactMode;
        displayTimeUnit = snap.displayTimeUnit;
        currentRcPath = snap.currentRcPath;
        viewHistory = snap.viewHistory;
        if (node('chartSaveRcBtn') && snap.saveTitle) node('chartSaveRcBtn').title = snap.saveTitle;
        applyHierarchyToggle();
        if (displayTimeUnit) syncTimeUnitSelect(displayTimeUnit);
        const nextAxis = nextView.axes.includes(snap.axis) ? snap.axis : nextView.default_axis;
        options('chartAxis', nextView.axes.map(name => [name, axisTitle(name)]), nextAxis);
        if (node('chartSignalsToggle')) node('chartSignalsToggle').disabled = !nextView.series_count;
        axis = nextAxis;
        domain = computeAxisDomain(nextView, axis);
        let nextRange = domain ? domain.slice() : null;
        if (snap.range && snap.axis === axis && domain) {
          const rLo = clamp(snap.range[0], domain[0], domain[1]);
          const rHi = clamp(snap.range[1], domain[0], domain[1]);
          const wasFull = snap.domain && Math.abs(snap.range[0] - snap.domain[0]) <= 1e-12 &&
            Math.abs(snap.range[1] - snap.domain[1]) <= 1e-12;
          if (!wasFull && rHi > rLo) nextRange = [rLo, rHi];
        }
        cursorA = (snap.cursorA != null && domain) ? clamp(snap.cursorA, domain[0], domain[1]) : snap.cursorA;
        cursorB = (snap.cursorB != null && domain) ? clamp(snap.cursorB, domain[0], domain[1]) : snap.cursorB;
        cursorAAnchor = snap.cursorAAnchor;
        cursorBAnchor = snap.cursorBAnchor;
        data = null;
        waveGeometry.clear();
        syncModeVisibility();
        if (node('chartSignals')) setBrowserVisible(false, false);
        updateCursorSummary();
        renderLanes();
        if (nextView.series_count && !directory.length) loadDirectory(false, false);
        changeRange(nextRange, true, false, {skipHistory: true});
        return;
      }
      if (result.views.length) chooseView(node('chartViewSelect')?.value);
      else status(result.diagnostics[0] || 'No chart samples yet.', '');
    });
  }

  function syncModeVisibility() {
    const isWave = view?.kind === 'waveform';
    const isHistogram = view?.kind === 'histogram';
    if (node('chartCursorBar')) node('chartCursorBar').hidden = !isWave;
    if (node('chartFindTools')) node('chartFindTools').hidden = !isWave;
    if (node('chartSnapToggle')) node('chartSnapToggle').hidden = !isWave;
    if (node('chartHierToggle')) node('chartHierToggle').hidden = !isWave;
    if (node('chartLastView')) node('chartLastView').hidden = !isWave;
    if (node('chartAddDivider')) node('chartAddDivider').hidden = !isWave;
    if (node('chartCutSignal')) node('chartCutSignal').hidden = !isWave;
    if (node('chartCopySignal')) node('chartCopySignal').hidden = !isWave;
    if (node('chartPasteSignal')) {
      node('chartPasteSignal').hidden = !isWave;
      node('chartPasteSignal').disabled = !signalClipboard;
    }
    if (node('chartOpenRcBtn')) node('chartOpenRcBtn').hidden = !isWave;
    if (node('chartSaveRcBtn')) node('chartSaveRcBtn').hidden = !isWave;
    if (node('chartExportVcdBtn')) node('chartExportVcdBtn').hidden = !isWave;
    if (node('chartCursorSummary')) node('chartCursorSummary').hidden = !isWave;
    if (node('chartSignalPane')) node('chartSignalPane').hidden = !isWave;
    if (node('chartSignalPaneResizer')) node('chartSignalPaneResizer').hidden = !isWave;
    if (node('chartLanes')) node('chartLanes').hidden = !!isWave;
    if (node('chartReadout')) node('chartReadout').hidden = !!isWave;
    if (node('chartSelectAllSignals')) {
      node('chartSelectAllSignals').hidden = isHistogram;
      node('chartSelectAllSignals').disabled = isHistogram;
    }
    if (overlayCanvas) overlayCanvas.hidden = !isWave;
    syncHorizontalScrollbar();
  }

  function clearView() {
    for (const channel of ['catalog', 'definitions', 'data', 'find']) cancel(channel);
    clearTimeout(timer);
    clearTimeout(searchTimer);
    closeContextMenu();
    view = null;
    data = range = domain = cursor = picked = hover = null;
    cursorA = cursorB = cursorAAnchor = cursorBAnchor = null;
    selectedRowKey = null;
    selected = null;
    settings = new Map();
    waveGeometry.clear();
    profileKey = '';
    directory = [];
    directoryCursor = null;
    activePrefix = [];
    scopePaths = new Set();
    collapsedScopes = new Set();
    pendingSignals = new Set();
    viewHistory = [];
    lastWheelAt = 0;
    showFullHierarchy = false;
    displayTimeUnit = null;
    currentRcPath = '';
    if (node('chartSaveRcBtn')) node('chartSaveRcBtn').title = DEFAULT_SAVE_RC_TITLE;
    fileHistory = [];
    fileHistoryIndex = -1;
    fileEntries = [];
    fileSelectedIndex = -1;
    modalReturnFocus.clear();
    applyHierarchyToggle();
    definitions = new Map();
    activeRows = {rows: [], totalHeight: 0};
    rowRefs = new Map();
    if (node('chartSignalSearch')) node('chartSignalSearch').value = '';
    if (node('chartSignals')) node('chartSignals').hidden = true;
    if (node('chartParamModal')) node('chartParamModal').hidden = true;
    if (node('chartFileModal')) node('chartFileModal').hidden = true;
    if (node('chartSignalsToggle')) node('chartSignalsToggle').setAttribute('aria-expanded', 'false');
    if (node('chartContinue')) {
      node('chartContinue').disabled = true;
      node('chartContinue').hidden = true;
    }
    if (node('chartSelectedPath')) {
      node('chartSelectedPath').textContent = '';
      node('chartSelectedPath').title = '';
    }
    if (node('chartHScroll')) node('chartHScroll').hidden = true;
    if (node('chartTarget')) node('chartTarget').hidden = true;
    if (node('chartSignalRows')) node('chartSignalRows').replaceChildren();
    if (node('chartLanes')) node('chartLanes').replaceChildren();
    if (node('chartHierarchyTree')) node('chartHierarchyTree').replaceChildren();
    if (node('chartSignalTree')) node('chartSignalTree').replaceChildren();
    if (node('chartAddSignals')) node('chartAddSignals').disabled = true;
    syncModeVisibility();
    updateCursorSummary();
    if (node('chartFindOp') && node('chartFindValue')) {
      ChartControls.resetFind(node('chartFindOp'), node('chartFindValue'));
    }
    clearFind();
    syncWindowControls();
    scheduleBasePaint();
  }

  function sanitizeSelectedItem(entry) {
    if (typeof entry === 'string' && entry.length <= 512) return entry;
    if (entry && typeof entry === 'object' && (entry.type === 'group' || entry.type === 'divider') && typeof entry.id === 'string') {
      const sigs = Array.isArray(entry.signals)
        ? [...new Set(entry.signals.filter(s => typeof s === 'string' && s.length <= 512))].slice(0, MAX_LANES)
        : [];
      return {
        type: 'group',
        id: entry.id.slice(0, 64),
        label: String(entry.label || 'G1').slice(0, 64),
        collapsed: !!entry.collapsed,
        signals: sigs,
      };
    }
    return null;
  }

  function applyHierarchyToggle() {
    const btn = node('chartHierToggle');
    if (btn) btn.setAttribute('aria-pressed', String(!!showFullHierarchy));
  }

  function parseTimeScaleSpec(spec) {
    const raw = String(spec || '').trim().replace(/^x\s*/i, '');
    const m = /^([0-9]*\.?[0-9]+)?\s*(fs|ps|ns|us|ms|s)?$/i.exec(raw);
    if (!m || (!m[1] && !m[2])) return null;
    const mult = m[1] ? Number(m[1]) : 1;
    const unit = m[2] ? m[2].toLowerCase() : null;
    if (!Number.isFinite(mult) || mult <= 0) return null;
    return {mult, unit};
  }

  function timeUnitScale() {
    const spec = parseTimeScaleSpec(displayTimeUnit);
    if (!spec) return 1;
    if (axis === 'time' && spec.unit) {
      const nat = String(view?.axis?.unit || 'ns').toLowerCase();
      const fNat = UNIT_FACTORS[nat], fSpec = UNIT_FACTORS[spec.unit];
      if (fNat && fSpec) return fNat / (spec.mult * fSpec);
    }
    return 1 / spec.mult;
  }

  function syncTimeUnitSelect(val) {
    const sel = node('chartTimeUnit');
    if (!sel) return;
    const opts = sel.options || [];
    const hasMatch = Array.from(opts).some(o => o.value === val);
    if (!hasMatch && typeof document !== 'undefined' && typeof sel.insertBefore === 'function') {
      const customOpt = Array.from(opts).find(o => o.value === 'custom') || null;
      const optEl = make('option', 'x ' + val, {value: val});
      sel.insertBefore(optEl, customOpt);
    }
    sel.value = val;
  }

  function activeTimeScaleLabel() {
    if (!displayTimeUnit) return view?.axis?.unit || '';
    const spec = parseTimeScaleSpec(displayTimeUnit);
    if (!spec) return String(displayTimeUnit);
    if (axis === 'time') return spec.mult === 1 && spec.unit ? spec.unit : `${spec.mult}${spec.unit || (view?.axis?.unit || '')}`;
    return spec.mult === 1 ? '' : `/${spec.mult}`;
  }

  function fmtAxisVal(xVal) {
    return fmt(xVal * timeUnitScale());
  }

  function toggleHierarchyNames() {
    showFullHierarchy = !showFullHierarchy;
    applyHierarchyToggle();
    saveProfile();
    renderLanes();
  }

  function chooseView(id) {
    const next = metadata.views.find(item => item.id === id);
    clearView();
    view = next;
    if (!view) return;
    const d = detail.flow;
    profileKey = JSON.stringify([d?.name, d?.version, metadata.spec_version, metadata.digest, id]);
    const stored = profile(profileKey);
    if (Array.isArray(stored?.settings)) {
      for (const entry of stored.settings.slice(0, MAX_LANES * 6)) {
        if (Array.isArray(entry) && typeof entry[0] === 'string' && entry[1] && typeof entry[1] === 'object') {
          settings.set(entry[0], entry[1]);
        }
      }
    }
    compactMode = stored?.compactMode !== undefined ? !!stored.compactMode : true;
    const viewEl = node('chartsView');
    if (viewEl) viewEl.classList.toggle('wf-normal', !compactMode);
    showFullHierarchy = !!stored?.showFullHierarchy;
    applyHierarchyToggle();
    const natUnit = String(view.axis?.unit || 'ns').toLowerCase();
    displayTimeUnit = UNIT_FACTORS[natUnit] ? '1' + natUnit : '1ns';
    syncTimeUnitSelect(displayTimeUnit);
    if (Array.isArray(stored?.selected)) {
      selected = sanitizeSelectionList(stored.selected);
    } else {
      selected = null;
    }
    options('chartAxis', view.axes.map(name => [name, axisTitle(name)]), view.default_axis);
    if (node('chartSignalsToggle')) node('chartSignalsToggle').disabled = !view.series_count;
    syncModeVisibility();
    if (canvas) canvas.setAttribute('aria-label', view.title + ' ' + view.kind.replace('_', ' ') + ' chart');
    if (view.kind === 'waveform' && view.series_count && node('chartSignals')) {
      setBrowserVisible(false, false);
    }
    setAxis(view.default_axis, false);
    if (view.series_count) {
      status('Reading signals…', '');
      loadDirectory(false, true);
    } else status('No committed samples for this view yet.', '');
  }

  function setBrowserVisible(visible, restoreFocus = true) {
    const aside = node('chartSignals'), btn = node('chartSignalsToggle');
    if (aside) {
      const wasHidden = !!aside.hidden;
      if (visible && wasHidden) captureModalOpener('chartSignals', btn);
      aside.hidden = !visible;
      if (!visible && !wasHidden && restoreFocus) restoreModalFocus('chartSignals', btn);
    }
    if (btn) btn.setAttribute('aria-expanded', String(!!visible));
  }

  function axisTitle(name) {
    if (name === 'elapsed') return 'Wave elapsed (s)';
    if (name === 'seq') return 'Publication seq';
    const a = view?.axis;
    return a ? a.label + (a.unit ? ' (' + a.unit + ')' : '') : name;
  }

  function captureCursorAnchor(xVal, point = null) {
    if (!Number.isFinite(xVal)) return null;
    const ratio = domain && domain[1] > domain[0] ? clamp((xVal - domain[0]) / (domain[1] - domain[0]), 0, 1) : null;
    const eps = range && range[1] > range[0] ? (range[1] - range[0]) * 1e-4 : 1e-6;
    let flowSeq = (Number.isInteger(point?.flow_seq) && Number.isFinite(point?.x) && Math.abs(point.x - xVal) <= eps)
      ? point.flow_seq : null;
    if (flowSeq == null && data) {
      for (const p of data.points ?? []) {
        if (Number.isInteger(p.flow_seq) && Number.isFinite(p.x) && Math.abs(p.x - xVal) <= eps) {
          flowSeq = p.flow_seq;
          break;
        }
      }
      if (flowSeq == null) {
        for (const p of Object.values(data.left ?? {})) {
          if (Number.isInteger(p?.flow_seq) && Number.isFinite(p?.x) && Math.abs(p.x - xVal) <= eps) {
            flowSeq = p.flow_seq;
            break;
          }
        }
      }
      if (flowSeq == null && axis === 'seq') flowSeq = Math.round(xVal);
    }
    return {axis, x: xVal, coords: {[axis]: xVal}, flow_seq: flowSeq, ratio};
  }

  function resolveAnchorOnData(anchor) {
    if (!anchor || !domain) return null;
    if (data && Number.isInteger(anchor.flow_seq)) {
      for (const p of data.points ?? []) {
        if (p.flow_seq === anchor.flow_seq && Number.isFinite(p.x)) return p.x;
      }
      for (const p of Object.values(data.left ?? {})) {
        if (p?.flow_seq === anchor.flow_seq && Number.isFinite(p.x)) return p.x;
      }
      if (data.buckets) {
        for (const cells of Object.values(data.buckets)) {
          for (const c of cells ?? []) {
            for (const p of [c.first, c.first_change, c.last, c.min, c.max]) {
              if (p?.flow_seq === anchor.flow_seq && Number.isFinite(p.x)) return p.x;
            }
          }
        }
      }
    }
    if (Number.isFinite(anchor.coords?.[axis])) {
      return clamp(anchor.coords[axis], domain[0], domain[1]);
    }
    if (axis === 'seq' && Number.isInteger(anchor.flow_seq)) {
      return clamp(anchor.flow_seq, domain[0], domain[1]);
    }
    if (Number.isFinite(anchor.ratio)) {
      return domain[0] + anchor.ratio * (domain[1] - domain[0]);
    }
    return null;
  }

  function setAxis(next, preserveCursors = true) {
    const prevA = preserveCursors && cursorA != null ? (cursorAAnchor || captureCursorAnchor(cursorA)) : null;
    const prevB = preserveCursors && cursorB != null ? (cursorBAnchor || captureCursorAnchor(cursorB)) : null;
    axis = next;
    picked = hover = null;
    viewHistory = [];
    lastWheelAt = 0;
    setTarget(null);
    domain = computeAxisDomain(view, axis);
    data = null;
    cursorAAnchor = prevA;
    cursorBAnchor = prevB;
    cursorA = prevA ? resolveAnchorOnData(prevA) : null;
    cursorB = prevB ? resolveAnchorOnData(prevB) : null;
    if (node('chartTimeUnit')) node('chartTimeUnit').disabled = false;
    updateCursorSummary();
    changeRange(domain?.slice() ?? null, true, false, {skipHistory: true});
  }

  function params(viewName) {
    return {view: viewName, view_id: view.id, as_of_seq: metadata.as_of_seq};
  }

  function allSelectedTokens() {
    const out = [];
    for (const item of selected ?? []) {
      if (typeof item === 'string') out.push(item);
      else if (item && typeof item === 'object' && Array.isArray(item.signals)) {
        for (const sigTok of item.signals) if (typeof sigTok === 'string') out.push(sigTok);
      }
    }
    return out;
  }

  function selectedSignalIds() {
    const ids = [], seen = new Set();
    for (const tok of allSelectedTokens()) {
      const bid = baseSignalId(tok);
      if (bid && !seen.has(bid)) { seen.add(bid); ids.push(bid); }
    }
    return ids;
  }

  function allocateSignalToken(rawId, reserved = null) {
    const bid = baseSignalId(rawId);
    if (!bid) return null;
    const used = reserved || new Set(allSelectedTokens());
    if (used.size >= MAX_LANES) return null;
    let tok = bid;
    if (used.has(tok)) {
      let k = 2;
      while (used.has(bid + '@@' + k)) k++;
      tok = bid + '@@' + k;
    }
    used.add(tok);
    return tok;
  }

  function chosen() {
    return selectedSignalIds().map(id => definitions.get(id)).filter(Boolean);
  }

  function allKnownRowKeys() {
    return ChartMath.laneRows(selected ?? [], definitions, settings, baseRowH()).rows.map(r => r.key);
  }

  function lanes() {
    return ChartMath.laneRows(selected ?? [], definitions, settings, baseRowH()).rows
      .filter(r => !isGroupRow(r))
      .map(r => r.lane);
  }

  function laneKey(lane) {
    return JSON.stringify([lane.instanceId ?? lane.id, lane.bitSlice ?? null]);
  }

  function laneFormat(lane, value) {
    const pref = settings.get(laneKey(lane)) || {};
    const fxpSigned = pref.fxpSigned !== undefined ? pref.fxpSigned : lane.signed;
    return ChartMath.format(value, {...lane, frac_bits: pref.frac ?? lane.frac_bits, fxpSigned},
      RADICES.includes(pref.radix) ? pref.radix : lane.radix);
  }

  function mergeScopes(seriesList) {
    for (const item of seriesList) {
      const parts = Array.isArray(item.path) ? item.path.slice(0, -1) : [];
      for (let i = 1; i <= parts.length; i++) {
        scopePaths.add(JSON.stringify(parts.slice(0, i)));
      }
    }
  }

  function useClientSignalFilter() {
    return !!(node('chartSignalCase')?.checked || node('chartSignalWord')?.checked || node('chartSignalRegex')?.checked);
  }

  function loadDirectory(more, initial = false, opts = {}) {
    if (!view) return;
    if (!more) directory = [];
    if (node('chartMoreSignals')) {
      node('chartMoreSignals').disabled = true;
      node('chartMoreSignals').hidden = true;
    }
    const rawQuery = node('chartSignalSearch')?.value || '';
    const query = useClientSignalFilter() ? '' : rawQuery;
    catalogBackendQuery = query;
    const queryOpts = {
      ...params('chart_series'),
      limit: 100,
      query,
      ...(activePrefix.length ? {prefix: JSON.stringify(activePrefix)} : {}),
      ...(more && directoryCursor ? {cursor: directoryCursor} : {}),
    };
    request('catalog', queryOpts, result => {
      directory.push(...result.series);
      directoryCursor = result.next_cursor;
      mergeScopes(result.series);
      for (const item of result.series) definitions.set(item.id, item);
      const retained = new Set([
        ...directory.map(item => item.id),
        ...selectedSignalIds(),
        ...clipboardSignalIds(),
        ...view.series.defaults,
      ]);
      for (const id of definitions.keys()) if (!retained.has(id)) definitions.delete(id);
      renderDirectory();
      if (directoryCursor && (opts.autoPage || 0) > 0 && useClientSignalFilter() && (node('chartSignalSearch')?.value || '')) {
        loadDirectory(true, false, {autoPage: opts.autoPage - 1});
      }
      if (!initial) return;
      if (selected === null) {
        selected = view.series.defaults.length ? view.series.defaults.slice() :
          view.series.mode === 'long' ? directory.slice(0, 8).map(item => item.id) : [];
      }
      if (view.kind === 'histogram') selected = selectedSignalIds().slice(0, 1);
      const unresolved = selectedSignalIds().filter(id => !definitions.has(id));
      if (!unresolved.length) return selectionChanged(false);
      request('definitions', {...params('chart_series'), ids: JSON.stringify(unresolved)}, defResult => {
        mergeScopes(defResult.series);
        for (const item of defResult.series) definitions.set(item.id, item);
        selectionChanged(false);
      }, error => { selectionChanged(false); status(error.message); });
    }, error => {
      if (node('chartSignalStatus')) node('chartSignalStatus').textContent = error.message;
      status(error.message);
    });
  }

  function renderHierarchyTree() {
    const scopeAllBtn = node('chartScopeAll');
    if (scopeAllBtn) scopeAllBtn.classList?.toggle?.('active', activePrefix.length === 0);
    const tree = node('chartHierarchyTree');
    if (!tree) return;
    const activeEl = typeof document !== 'undefined' ? document.activeElement : null;
    const focusedScopeKey = tree.contains?.(activeEl) ? activeEl?.dataset?.scopeKey : null;
    tree.replaceChildren();
    const activeKey = JSON.stringify(activePrefix);
    const sortedParts = Array.from(scopePaths)
      .map(raw => JSON.parse(raw))
      .sort((a, b) => a.length - b.length || a.join('/').localeCompare(b.join('/')));
    const rootChildren = [], byKey = new Map();
    for (const parts of sortedParts) {
      const key = JSON.stringify(parts);
      if (byKey.has(key)) continue;
      const nodeObj = {parts, key, name: parts[parts.length - 1], children: []};
      byKey.set(key, nodeObj);
      const parentKey = parts.length > 1 ? JSON.stringify(parts.slice(0, -1)) : null;
      if (parentKey && byKey.has(parentKey)) byKey.get(parentKey).children.push(nodeObj);
      else rootChildren.push(nodeObj);
    }
    const renderedButtons = [];
    function buildScopeNode(nodeObj) {
      const wrap = make('div', null, {className: 'wf-scope-node'});
      const hasKids = nodeObj.children.length > 0;
      const isCollapsed = collapsedScopes.has(nodeObj.key);
      const isActive = nodeObj.key === activeKey;
      const btn = make('button', null, {
        type: 'button',
        className: 'wf-scope-btn' + (isActive ? ' active' : ''),
        title: nodeObj.parts.join('.'),
        tabIndex: -1,
      });
      btn.setAttribute('role', 'treeitem');
      btn.setAttribute('aria-selected', String(isActive));
      if (hasKids) btn.setAttribute('aria-expanded', String(!isCollapsed));
      btn.dataset.scopeName = nodeObj.name;
      btn.dataset.scopeKey = nodeObj.key;
      const caret = make('span', null, {className: 'wf-scope-caret'});
      caret.setAttribute('aria-hidden', 'true');
      if (hasKids) mountWaveIcon(caret, isCollapsed ? 'chevron-right' : 'chevron-down', 11);
      const iconSpan = make('span', null, {className: 'wf-scope-icon'});
      iconSpan.setAttribute('aria-hidden', 'true');
      iconSpan.setAttribute('data-wave-icon', 'folder');
      btn.append(caret, iconSpan, make('span', nodeObj.name, {className: 'wf-scope-name'}));
      btn.addEventListener('click', e => {
        if (hasKids && (e.target === caret || caret.contains?.(e.target))) {
          if (collapsedScopes.has(nodeObj.key)) collapsedScopes.delete(nodeObj.key);
          else collapsedScopes.add(nodeObj.key);
          renderHierarchyTree();
          const rebuilt = tree.querySelectorAll?.('[role="treeitem"]');
          const match = rebuilt && Array.from(rebuilt).find(b => b.dataset?.scopeKey === nodeObj.key);
          match?.focus?.({preventScroll: true});
          return;
        }
        activePrefix = nodeObj.parts;
        loadDirectory(false, false, {autoPage: useClientSignalFilter() ? 50 : 0});
      });
      renderedButtons.push(btn);
      wrap.append(btn);
      if (hasKids && !isCollapsed) {
        nodeObj.children.sort((a, b) => a.name.localeCompare(b.name));
        const childBox = make('div', null, {className: 'wf-scope-children'});
        childBox.setAttribute('role', 'group');
        for (const ch of nodeObj.children) childBox.append(buildScopeNode(ch));
        wrap.append(childBox);
      }
      return wrap;
    }
    rootChildren.sort((a, b) => a.name.localeCompare(b.name));
    for (const rootNode of rootChildren) tree.append(buildScopeNode(rootNode));
    const focusTargetKey = focusedScopeKey || activeKey;
    let tabTarget = renderedButtons.find(b => b.dataset.scopeKey === focusTargetKey) || renderedButtons[0] || null;
    if (tabTarget) tabTarget.tabIndex = 0;
    if (typeof WaveIcons !== 'undefined') WaveIcons.mount?.(tree);
    if (focusedScopeKey && tabTarget && tabTarget.dataset.scopeKey === focusedScopeKey) {
      tabTarget.focus?.({preventScroll: true});
    }
  }

  function filterSignals(list) {
    const searchInp = node('chartSignalSearch');
    const rawQuery = searchInp?.value || '';
    const matchCase = !!node('chartSignalCase')?.checked;
    const wholeWord = !!node('chartSignalWord')?.checked;
    const useRegex = !!node('chartSignalRegex')?.checked;
    let scoped = list;
    if (activePrefix.length) {
      scoped = list.filter(item => {
        const path = Array.isArray(item.path) ? item.path : [];
        if (!activePrefix.every((p, i) => path[i] === p)) return false;
        return scopeRecursive ? path.length > activePrefix.length : path.length === activePrefix.length + 1;
      });
    } else if (!scopeRecursive) {
      scoped = list.filter(item => !Array.isArray(item.path) || item.path.length <= 1);
    }
    if (!rawQuery) {
      searchInp?.removeAttribute?.('aria-invalid');
      return {items: scoped, regexError: null};
    }
    let rx = null;
    try {
      let pattern = useRegex ? rawQuery : rawQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      if (wholeWord) pattern = `\\b(?:${pattern})\\b`;
      rx = new RegExp(pattern, matchCase ? '' : 'i');
      searchInp?.removeAttribute?.('aria-invalid');
    } catch (err) {
      searchInp?.setAttribute?.('aria-invalid', 'true');
      return {items: [], regexError: err.message || 'Invalid regular expression'};
    }
    return {
      items: scoped.filter(item => rx.test(item.label || '') || rx.test(item.id || '') ||
        (Array.isArray(item.path) && rx.test(item.path.join('.')))),
      regexError: null,
    };
  }

  function updateAddSignalsButton() {
    const addBtn = node('chartAddSignals');
    if (!addBtn) return;
    const count = pendingSignals.size;
    addBtn.disabled = !count;
    addBtn.textContent = count ? `Add (${count})` : 'Add';
  }

  function addSignalsToWave(ids) {
    if (!ids.length) return;
    if (view.kind === 'histogram') {
      selected = [baseSignalId(ids[0])];
      pendingSignals.clear();
      selectionChanged();
      return;
    }
    const reserved = new Set(allSelectedTokens());
    const next = (selected ?? []).slice();
    for (const id of ids) {
      const tok = allocateSignalToken(id, reserved);
      if (!tok) break;
      next.push(tok);
    }
    selected = next;
    for (const id of ids) pendingSignals.delete(id);
    selectionChanged();
  }

  function removeSignalsFromWave(ids) {
    const rm = new Set(ids.map(baseSignalId));
    selected = (selected ?? [])
      .filter(entry => typeof entry !== 'string' || !rm.has(baseSignalId(entry)))
      .map(entry => (entry && typeof entry === 'object' && Array.isArray(entry.signals))
        ? {...entry, signals: entry.signals.filter(s => !rm.has(baseSignalId(s)))}
        : entry);
    selectionChanged();
  }

  function renderDirectory() {
    renderHierarchyTree();
    const root = node('chartSignalTree');
    if (!root) return [];
    root.replaceChildren();
    const inWave = new Set(selectedSignalIds());
    const {items: shown, regexError} = filterSignals(directory);
    for (const item of shown) {
      const checked = pendingSignals.has(item.id);
      const isAdded = inWave.has(item.id);
      const rowLabel = make('label', null, {
        className: isAdded ? 'in-wave' : '',
        title: item.id + (isAdded ? ' (in waveform — double-click or Add to append another copy)' : ''),
      });
      const input = make('input', null, {
        type: view.kind === 'histogram' ? 'radio' : 'checkbox',
        name: 'chartBrowserSignal',
        checked,
      });
      input.dataset.signal = item.id;
      input.addEventListener('change', () => {
        if (view.kind === 'histogram') {
          pendingSignals.clear();
          if (input.checked) pendingSignals.add(item.id);
        } else if (input.checked) {
          pendingSignals.add(item.id);
        } else {
          pendingSignals.delete(item.id);
        }
        updateAddSignalsButton();
      });
      const nameText = activePrefix.length
        ? item.path.slice(activePrefix.length).join('.') || item.label
        : (item.path.length > 1 ? item.path.slice(0, -1).join('.') + '.' + item.label : item.label);
      rowLabel.append(input, make('span', nameText, {className: 'wf-sig-name'}));
      const typeTag = item.bits ? `[${item.bits - 1}:0]` : item.type;
      if (typeTag) rowLabel.append(make('span', typeTag, {className: 'wf-sig-type'}));
      rowLabel.addEventListener('click', event => {
        if (event.target === input) return;
        event.preventDefault();
        if (view.kind === 'histogram') {
          pendingSignals.clear();
          pendingSignals.add(item.id);
          input.checked = true;
        } else {
          if (pendingSignals.has(item.id)) pendingSignals.delete(item.id);
          else pendingSignals.add(item.id);
          input.checked = pendingSignals.has(item.id);
        }
        updateAddSignalsButton();
      });
      rowLabel.addEventListener('dblclick', event => {
        event.preventDefault();
        addSignalsToWave([item.id]);
      });
      rowLabel.addEventListener('contextmenu', event => {
        event.preventDefault();
        openBrowserContextMenu(event.clientX, event.clientY, item, isAdded);
      });
      root.append(rowLabel);
    }
    updateAddSignalsButton();
    const isHistogram = view?.kind === 'histogram';
    if (node('chartSelectAllSignals')) {
      node('chartSelectAllSignals').hidden = isHistogram;
      node('chartSelectAllSignals').disabled = isHistogram || !shown.length;
    }
    const moreBtn = node('chartMoreSignals');
    if (moreBtn) {
      moreBtn.disabled = !directoryCursor;
      moreBtn.hidden = !directoryCursor;
    }
    const sigStatus = node('chartSignalStatus');
    if (sigStatus) {
      sigStatus.classList?.toggle?.('error-text', !!regexError);
      sigStatus.setAttribute?.('role', regexError ? 'alert' : 'status');
      sigStatus.textContent = regexError
        ? 'Invalid regex: ' + regexError
        : shown.length
          ? `${shown.length} listed · ${allSelectedTokens().length}/${MAX_LANES} rows`
          : directoryCursor ? 'No matches on this page.' : 'No signals match current scope/filter.';
    }
    return shown;
  }

  function selectionChanged(save = true) {
    if (!selected) selected = [];
    if (save) saveProfile();
    renderDirectory();
    renderLanes();
    data = null;
    changeRange(range, true, false, {skipHistory: true});
  }

  function moveTopItem(itemOrId, step) {
    const list = (selected ?? []).slice();
    const index = list.findIndex(item => typeof item === 'string'
      ? item === itemOrId
      : (itemOrId && (item.type === 'group' || item.type === 'divider') && item.id === itemOrId.id));
    if (index >= 0) {
      const dest = index + step;
      if (dest < 0 || dest >= list.length) return;
      [list[index], list[dest]] = [list[dest], list[index]];
      selected = list;
      selectionChanged();
      return;
    }
    if (typeof itemOrId === 'string') {
      for (const entry of list) {
        if (entry && typeof entry === 'object' && Array.isArray(entry.signals)) {
          const gi = entry.signals.indexOf(itemOrId);
          const gDest = gi + step;
          if (gi >= 0 && gDest >= 0 && gDest < entry.signals.length) {
            const copy = entry.signals.slice();
            [copy[gi], copy[gDest]] = [copy[gDest], copy[gi]];
            entry.signals = copy;
            selected = list;
            saveProfile();
            renderLanes();
            return;
          }
        }
      }
    }
  }

  function moveSignalToTop(sigId, afterGroupId = null) {
    const list = (selected ?? []).map(item => item && typeof item === 'object'
      ? {...item, signals: (item.signals || []).filter(s => s !== sigId)}
      : item);
    if (!list.includes(sigId)) {
      const idx = afterGroupId ? list.findIndex(it => it && typeof it === 'object' && it.id === afterGroupId) : -1;
      if (idx >= 0) list.splice(idx + 1, 0, sigId);
      else list.push(sigId);
    }
    selected = list;
    saveProfile();
    renderLanes();
  }

  function reorderTopItem(srcToken, targetToken, placeAfter, intoGroupId = null) {
    if (!srcToken || srcToken === targetToken) return;
    const list = (selected ?? []).map(item => (item && typeof item === 'object')
      ? {...item, signals: (item.signals || []).slice()}
      : item);
    let moved = null;
    if (srcToken.startsWith('d:')) {
      const sid = srcToken.slice(2);
      const idx = list.findIndex(it => it && typeof it === 'object' && it.id === sid);
      if (idx < 0) return;
      [moved] = list.splice(idx, 1);
      const tId = targetToken.slice(2);
      let toIdx = list.findIndex(it => targetToken.startsWith('d:')
        ? (it && typeof it === 'object' && it.id === tId)
        : (it === tId || (it && typeof it === 'object' && it.signals.includes(tId))));
      if (toIdx < 0) {
        list.push(moved);
      } else {
        list.splice(placeAfter ? toIdx + 1 : toIdx, 0, moved);
      }
      selected = list;
      saveProfile();
      renderLanes();
      return;
    }

    const srcSig = srcToken.slice(2);
    const topIdx = list.indexOf(srcSig);
    if (topIdx >= 0) {
      moved = list.splice(topIdx, 1)[0];
    } else {
      for (const it of list) {
        if (it && typeof it === 'object') {
          const gi = it.signals.indexOf(srcSig);
          if (gi >= 0) {
            moved = it.signals.splice(gi, 1)[0];
            break;
          }
        }
      }
    }
    if (!moved) return;

    if (intoGroupId) {
      const grp = list.find(it => it && typeof it === 'object' && it.id === intoGroupId);
      if (grp) {
        grp.signals.push(moved);
        grp.collapsed = false;
        selected = list;
        saveProfile();
        renderLanes();
        return;
      }
    }

    const tId = targetToken.slice(2);
    if (targetToken.startsWith('d:')) {
      const toIdx = list.findIndex(it => it && typeof it === 'object' && it.id === tId);
      if (toIdx < 0) list.push(moved);
      else list.splice(placeAfter ? toIdx + 1 : toIdx, 0, moved);
    } else {
      const topTarget = list.indexOf(tId);
      if (topTarget >= 0) {
        list.splice(placeAfter ? topTarget + 1 : topTarget, 0, moved);
      } else {
        let placed = false;
        for (const it of list) {
          if (it && typeof it === 'object') {
            const gi = it.signals.indexOf(tId);
            if (gi >= 0) {
              it.signals.splice(placeAfter ? gi + 1 : gi, 0, moved);
              placed = true;
              break;
            }
          }
        }
        if (!placed) list.push(moved);
      }
    }
    selected = list;
    saveProfile();
    renderLanes();
  }

  function insertDividerAbove(rowKey) {
    const list = (selected ?? []).map(item => (item && typeof item === 'object')
      ? {...item, signals: (item.signals || []).slice()}
      : item);
    const targetRow = activeRows.rows.find(r => r.key === rowKey);
    const grpObj = {
      type: 'group',
      id: 'div-' + Date.now() + '-' + (++divCounter),
      label: 'G' + divCounter,
      collapsed: false,
      signals: [],
    };
    if (targetRow && !isGroupRow(targetRow)) {
      const sigId = targetRow.parent?.instanceId ?? targetRow.lane?.instanceId ?? targetRow.parent?.id ?? targetRow.lane?.id;
      if (sigId) {
        grpObj.signals = [sigId];
        const topIdx = list.indexOf(sigId);
        if (topIdx >= 0) {
          list.splice(topIdx, 1, grpObj);
        } else {
          let inserted = false;
          for (let i = 0; i < list.length; i++) {
            const it = list[i];
            if (it && typeof it === 'object' && it.signals.includes(sigId)) {
              it.signals = it.signals.filter(s => s !== sigId);
              list.splice(i + 1, 0, grpObj);
              inserted = true;
              break;
            }
          }
          if (!inserted) list.push(grpObj);
        }
      } else {
        list.push(grpObj);
      }
    } else if (targetRow && isGroupRow(targetRow)) {
      const gId = (targetRow.group || targetRow.divider).id;
      const gIdx = list.findIndex(it => it && typeof it === 'object' && it.id === gId);
      if (gIdx >= 0) list.splice(gIdx, 0, grpObj);
      else list.push(grpObj);
    } else {
      list.push(grpObj);
    }
    selected = list;
    selectedRowKey = 'div:' + grpObj.id;
    saveProfile();
    renderLanes();
  }

  function toggleGroupCollapse(groupObj) {
    if (!groupObj) return;
    groupObj.collapsed = !groupObj.collapsed;
    saveProfile();
    renderLanes();
  }

  function ungroupSignals(groupObj) {
    if (!groupObj) return;
    const list = [];
    for (const item of selected ?? []) {
      if (item && typeof item === 'object' && item.id === groupObj.id) {
        for (const sigId of item.signals || []) {
          if (typeof sigId === 'string' && !list.includes(sigId)) list.push(sigId);
        }
      } else {
        list.push(item);
      }
    }
    selected = list;
    if (selectedRowKey === 'div:' + groupObj.id) selectedRowKey = null;
    saveProfile();
    renderLanes();
  }

  function removeRow(row) {
    if (!row) return;
    if (isGroupRow(row)) {
      const gid = (row.group || row.divider).id;
      selected = (selected ?? []).filter(item => !(item && typeof item === 'object' && item.id === gid));
      if (selectedRowKey === row.key) selectedRowKey = null;
      selectionChanged();
      return;
    }
    if (row.kind === 'slice') {
      const parentKey = laneKey(row.parent), parentPref = settings.get(parentKey) ?? {};
      const slices = Array.isArray(parentPref.slices) ? parentPref.slices : [];
      settings.set(parentKey, {
        ...parentPref,
        slices: slices.filter(text => String(ChartMath.parseSlice(text, row.parent.bits)) !== String(row.lane.bitSlice)),
      });
      settings.delete(row.key);
      saveProfile();
      renderLanes();
      return;
    }
    if (row.kind === 'expanded_bit') {
      const parentKey = laneKey(row.parent), parentPref = settings.get(parentKey) ?? {};
      settings.set(parentKey, {...parentPref, expanded: false});
      saveProfile();
      renderLanes();
      return;
    }
    const tok = row.lane.instanceId ?? row.lane.id;
    selected = (selected ?? [])
      .filter(entry => entry !== tok)
      .map(entry => (entry && typeof entry === 'object' && Array.isArray(entry.signals))
        ? {...entry, signals: entry.signals.filter(s => s !== tok)}
        : entry);
    if (selectedRowKey === row.key) selectedRowKey = null;
    selectionChanged();
  }

  function snapshotSignalSettings(tok) {
    const out = [];
    for (const [k, v] of settings.entries()) {
      try {
        const parsed = JSON.parse(k);
        if (Array.isArray(parsed) && parsed[0] === tok) out.push([parsed[1] ?? null, {...v}]);
      } catch (_) {}
    }
    return out;
  }

  function restoreSignalSettings(newTok, entries) {
    for (const [bitSlice, pref] of entries || []) {
      settings.set(JSON.stringify([newTok, bitSlice]), {...pref});
    }
  }

  function copySelectedRow(cut = false) {
    if (view?.kind !== 'waveform') return;
    const row = activeRows.rows.find(r => r.key === selectedRowKey) ||
      activeRows.rows.find(r => !isGroupRow(r));
    if (!row) return;
    if (isGroupRow(row)) {
      const grp = row.group || row.divider;
      const sigEntries = (grp.signals || []).map(tok => {
        const bid = baseSignalId(tok);
        return {baseId: bid, def: definitions.get(bid) || null, settings: snapshotSignalSettings(tok)};
      });
      signalClipboard = {kind: 'group', label: grp.label, collapsed: !!grp.collapsed, signals: sigEntries};
    } else {
      const tok = row.parent?.instanceId ?? row.lane?.instanceId ?? row.parent?.id ?? row.lane?.id;
      if (!tok) return;
      const bid = baseSignalId(tok);
      signalClipboard = {kind: 'signal', baseId: bid, def: definitions.get(bid) || null, settings: snapshotSignalSettings(tok)};
    }
    if (node('chartPasteSignal')) node('chartPasteSignal').disabled = false;
    if (cut) removeRow(row);
  }

  function pasteClipboardSignal() {
    if (view?.kind !== 'waveform' || !signalClipboard) return;
    const list = (selected ?? []).slice();
    const reserved = new Set(allSelectedTokens());
    let insertIdx = list.length;
    const curRow = activeRows.rows.find(r => r.key === selectedRowKey);
    if (curRow) {
      if (isGroupRow(curRow)) {
        const gid = (curRow.group || curRow.divider).id;
        const gi = list.findIndex(it => it && typeof it === 'object' && it.id === gid);
        if (gi >= 0) insertIdx = gi + 1;
      } else {
        const curTok = curRow.parent?.instanceId ?? curRow.lane?.instanceId ?? curRow.parent?.id ?? curRow.lane?.id;
        const si = list.findIndex(it => it === curTok || (it && typeof it === 'object' && it.signals?.includes(curTok)));
        if (si >= 0) insertIdx = si + 1;
      }
    }
    let newSelectedKey = null;
    if (signalClipboard.kind === 'group') {
      const grpObj = {
        type: 'group',
        id: 'div-' + Date.now() + '-' + (++divCounter),
        label: signalClipboard.label || ('G' + divCounter),
        collapsed: !!signalClipboard.collapsed,
        signals: [],
      };
      for (const sigSpec of signalClipboard.signals) {
        if (sigSpec.def && !definitions.has(sigSpec.baseId)) definitions.set(sigSpec.baseId, sigSpec.def);
        const newTok = allocateSignalToken(sigSpec.baseId, reserved);
        if (!newTok) break;
        grpObj.signals.push(newTok);
        restoreSignalSettings(newTok, sigSpec.settings);
      }
      list.splice(insertIdx, 0, grpObj);
      newSelectedKey = 'div:' + grpObj.id;
    } else {
      if (signalClipboard.def && !definitions.has(signalClipboard.baseId)) {
        definitions.set(signalClipboard.baseId, signalClipboard.def);
      }
      const newTok = allocateSignalToken(signalClipboard.baseId, reserved);
      if (!newTok) return status(`Signal limit (${MAX_LANES}) reached.`);
      restoreSignalSettings(newTok, signalClipboard.settings);
      list.splice(insertIdx, 0, newTok);
      newSelectedKey = JSON.stringify([newTok, null]);
    }
    selected = list;
    if (newSelectedKey) selectedRowKey = newSelectedKey;
    const unresolved = selectedSignalIds().filter(id => !definitions.has(id));
    if (!unresolved.length) {
      selectionChanged();
      return;
    }
    request('definitions', {...params('chart_series'), ids: JSON.stringify(unresolved)}, defResult => {
      mergeScopes(defResult.series);
      for (const item of defResult.series) definitions.set(item.id, item);
      selectionChanged();
    }, error => { selectionChanged(); status(error.message); });
  }

  function closeParamModal(restoreFocus = false) {
    const modal = node('chartParamModal');
    const wasOpen = !!(modal && !modal.hidden);
    if (modal) modal.hidden = true;
    node('chartParamBody')?.replaceChildren();
    paramModalApplyFn = null;
    if (node('chartTimeUnit')?.value === 'custom') {
      const nat = String(view?.axis?.unit || 'ns').toLowerCase();
      syncTimeUnitSelect(displayTimeUnit || (UNIT_FACTORS[nat] ? '1' + nat : '1ns'));
    }
    if (restoreFocus && wasOpen) restoreModalFocus('chartParamModal', canvas);
    else modalReturnFocus.delete('chartParamModal');
  }

  function openParamModal(title, buildBodyFn, onApply) {
    const modal = node('chartParamModal'), bodyEl = node('chartParamBody'), titleEl = node('chartParamTitle');
    if (!modal || !bodyEl) return;
    if (modal.hidden) captureModalOpener('chartParamModal', canvas);
    closeContextMenu(false);
    if (titleEl) titleEl.textContent = title;
    bodyEl.replaceChildren();
    const focusTarget = buildBodyFn(bodyEl);
    paramModalApplyFn = () => {
      if (onApply() !== false) closeParamModal(true);
    };
    modal.hidden = false;
    focusTarget?.focus?.();
  }

  function joinChartPath(dir, name) {
    const clean = String(name || '').trim();
    if (!clean) return '';
    if (/^(?:[A-Za-z]:[\\/]|[\\/]{2}|\/)/.test(clean)) return clean;
    const sep = dir.includes('\\') ? '\\' : '/';
    return dir.endsWith('\\') || dir.endsWith('/') ? dir + clean : dir + sep + clean;
  }

  function serializeVerdiRc() {
    const lines = [
      '; Wave U4 Verdi-compatible signal resource (.wave.rc)',
      'Magic 271485',
      'Revision Verdi_U4',
      '',
    ];
    const rMap = {hex: '-HEX', bin: '-BIN', unsigned: '-UDEC', signed: '-2COMP', fxp: '-UDEC'};
    const emitSig = (tok, grpName = null) => {
      const bid = baseSignalId(tok);
      const def = definitions.get(bid);
      const pref = settings.get(JSON.stringify([tok, null])) || {};
      const parts = Array.isArray(def?.path) && def.path.length ? def.path : [bid];
      const vpath = '/' + parts.join('/');
      const h = pref.customHeight || (pref.heightScale ? pref.heightScale * baseRowH() : '');
      const radix = RADICES.includes(pref.radix) ? pref.radix : (def?.radix || 'hex');
      const flags = [];
      if (h) flags.push('-h ' + h);
      if (radix && rMap[radix]) flags.push(rMap[radix]);
      if (grpName) flags.push(`; group=${grpName}`);
      lines.push(`addSignal ${flags.join(' ')} ${vpath}`.replace(/\s+/g, ' ').trim());
    };
    for (const item of selected ?? []) {
      if (typeof item === 'string') {
        emitSig(item);
      } else if (item && typeof item === 'object') {
        lines.push(`addGroup "${String(item.label || 'G1').replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`);
        for (const sigTok of item.signals || []) emitSig(sigTok, item.label);
      }
    }
    const meta = {
      format: 'wave-rc-v2',
      view_id: view?.id,
      axis,
      displayTimeUnit,
      showFullHierarchy,
      selected: selected ?? [],
      settings: [...settings.entries()],
    };
    lines.push('');
    lines.push('; @wave-rc-v2 ' + JSON.stringify(meta));
    return lines.join('\n') + '\n';
  }

  function parseVerdiRcLines(rawText) {
    const outSelected = [];
    const outSettings = new Map();
    const used = new Set();
    let curGroup = null;
    const byPath = new Map();
    for (const def of definitions.values()) {
      byPath.set(def.id, def.id);
      if (Array.isArray(def.path) && def.path.length) {
        byPath.set('/' + def.path.join('/'), def.id);
        byPath.set(def.path.join('.'), def.id);
      }
    }
    const radixFlags = {'-HEX': 'hex', '-BIN': 'bin', '-UDEC': 'unsigned', '-2COMP': 'signed'};
    for (const rawLine of rawText.split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith(';')) continue;
      const gMatch = /^addGroup\s+"((?:\\.|[^"\\])+)"/.exec(line);
      if (gMatch) {
        curGroup = {
          type: 'group',
          id: 'div-' + Date.now() + '-' + (++divCounter),
          label: gMatch[1].replace(/\\(.)/g, '$1').slice(0, 64),
          collapsed: false,
          signals: [],
        };
        outSelected.push(curGroup);
        continue;
      }
      if (/^addSignal\b/.test(line)) {
        const cleaned = line.replace(/\s*;.*$/, '');
        const parts = cleaned.split(/\s+/);
        const rawPath = parts[parts.length - 1];
        const stripped = rawPath.replace(/^\/+/, '');
        const resolvedId = byPath.get(rawPath) || byPath.get('/' + stripped) || byPath.get(stripped.replace(/\//g, '.')) || stripped;
        const tok = allocateSignalToken(resolvedId, used);
        if (!tok) continue;
        const pref = {};
        for (let i = 1; i < parts.length - 1; i++) {
          if (parts[i] === '-h' && Number.isFinite(Number(parts[i + 1]))) {
            pref.customHeight = clamp(Math.round(Number(parts[++i])), 18, 180);
          } else if (radixFlags[parts[i]]) {
            pref.radix = radixFlags[parts[i]];
          }
        }
        if (Object.keys(pref).length) outSettings.set(JSON.stringify([tok, null]), pref);
        if (curGroup) curGroup.signals.push(tok);
        else outSelected.push(tok);
      }
    }
    return outSelected.length ? {selected: outSelected, settings: [...outSettings.entries()]} : null;
  }

  function applyRcText(rawText, rcPath = '') {
    if (!view || !rawText) return false;
    try {
      const metaMatch = /^;\s*@wave-rc-v2\s+(\{.*\})\s*$/m.exec(rawText);
      let parsed = null;
      if (metaMatch) {
        parsed = JSON.parse(metaMatch[1]);
      } else if (rawText.trim().startsWith('{')) {
        parsed = JSON.parse(rawText);
      } else {
        parsed = parseVerdiRcLines(rawText);
      }
      if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.selected)) {
        throw new Error('Invalid .rc signal configuration format.');
      }
      selected = sanitizeSelectionList(parsed.selected);
      settings = new Map();
      if (Array.isArray(parsed.settings)) {
        for (const entry of parsed.settings.slice(0, MAX_LANES * 6)) {
          if (Array.isArray(entry) && typeof entry[0] === 'string' && entry[1] && typeof entry[1] === 'object') {
            settings.set(entry[0], entry[1]);
          }
        }
      }
      if (typeof parsed.showFullHierarchy === 'boolean') {
        showFullHierarchy = parsed.showFullHierarchy;
        applyHierarchyToggle();
      }
      if (parsed.displayTimeUnit && parseTimeScaleSpec(parsed.displayTimeUnit)) {
        displayTimeUnit = String(parsed.displayTimeUnit);
        syncTimeUnitSelect(displayTimeUnit);
      }
      const saveBtn = node('chartSaveRcBtn');
      if (rcPath) {
        currentRcPath = rcPath;
        if (saveBtn) saveBtn.title = `Save waveform config (${rcPath}) · Shift+click for Save As…`;
      } else {
        currentRcPath = '';
        if (saveBtn) saveBtn.title = DEFAULT_SAVE_RC_TITLE;
      }
      const unresolved = selectedSignalIds().filter(id => !definitions.has(id));
      if (!unresolved.length) {
        selectionChanged();
      } else {
        request('definitions', {...params('chart_series'), ids: JSON.stringify(unresolved)}, defResult => {
          mergeScopes(defResult.series);
          for (const item of defResult.series) definitions.set(item.id, item);
          selectionChanged();
        });
      }
      return true;
    } catch (err) {
      status(err.message || 'Failed to read .rc file.');
      return false;
    }
  }

  async function postChartFile(payload) {
    if (typeof fetchJson !== 'function') throw new Error('Server file API unavailable.');
    return fetchJson('/api/chart/file', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({workspace_id: STATE.workspace_id, ...payload}),
    });
  }

  function closeFileModal(restoreFocus = false) {
    const modal = node('chartFileModal');
    const wasOpen = !!(modal && !modal.hidden);
    if (modal) modal.hidden = true;
    fileModalMode = '';
    if (restoreFocus && wasOpen) restoreModalFocus('chartFileModal', node('chartOpenRcBtn') || canvas);
    else modalReturnFocus.delete('chartFileModal');
  }

  function syncFileNavButtons() {
    const backBtn = node('chartFileBack'), fwdBtn = node('chartFileForward'), upBtn = node('chartFileUp');
    const parent = upBtn?.dataset?.parent || '';
    if (backBtn) backBtn.disabled = fileHistoryIndex <= 0;
    if (fwdBtn) fwdBtn.disabled = fileHistoryIndex < 0 || fileHistoryIndex >= fileHistory.length - 1;
    if (upBtn) upBtn.disabled = !lastRcDir || !parent || parent === lastRcDir;
  }

  function selectChartFileRow(idx, focusRow = false) {
    const listEl = node('chartFileList');
    if (!listEl || !fileEntries.length) return;
    const clamped = clamp(idx, 0, fileEntries.length - 1);
    fileSelectedIndex = clamped;
    const rows = Array.from(listEl.children || []);
    rows.forEach((ch, i) => {
      ch.classList?.toggle?.('selected', i === clamped);
      ch.setAttribute?.('aria-selected', String(i === clamped));
    });
    const ent = fileEntries[clamped];
    if (ent && !ent.directory && node('chartFileName')) node('chartFileName').value = ent.name;
    if (focusRow && rows[clamped]) {
      rows[clamped].focus?.({preventScroll: true});
      rows[clamped].scrollIntoView?.({block: 'nearest'});
    }
  }

  async function loadFileBrowserDir(dirPath = '', navOpts = {}) {
    if (typeof fetchJson !== 'function') return;
    const errEl = node('chartFileError'), listEl = node('chartFileList'), scEl = node('chartFileShortcuts');
    if (errEl) errEl.textContent = '';
    const activeEl = typeof document !== 'undefined' ? document.activeElement : null;
    const hadListFocus = !!(listEl && (activeEl === listEl || listEl.contains?.(activeEl)));
    try {
      const r = await fetchJson('/api/files?mode=rc&path=' + encodeURIComponent(dirPath));
      if (node('chartFileDir')) node('chartFileDir').value = r.path || '';
      if (node('chartFileUp')) node('chartFileUp').dataset.parent = r.parent || '';
      lastRcDir = r.path || '';
      if (!navOpts.historyNav && fileHistory[fileHistoryIndex] !== r.path) {
        fileHistory = fileHistory.slice(0, fileHistoryIndex + 1);
        fileHistory.push(r.path);
        if (fileHistory.length > 30) fileHistory.shift();
        fileHistoryIndex = fileHistory.length - 1;
      }
      syncFileNavButtons();
      if (scEl) {
        scEl.replaceChildren();
        for (const sc of r.shortcuts || []) {
          if (sc.exists === false) continue;
          const b = make('button', sc.label, {type: 'button', title: sc.path});
          b.addEventListener('click', () => loadFileBrowserDir(sc.path));
          scEl.append(b);
        }
      }
      fileEntries = Array.isArray(r.entries) ? r.entries : [];
      fileSelectedIndex = -1;
      if (listEl) {
        listEl.setAttribute?.('role', 'listbox');
        listEl.replaceChildren();
        fileEntries.forEach((ent, idx) => {
          const btn = make('button', null, {
            type: 'button',
            className: 'wf-file-row' + (ent.directory ? ' is-dir' : ''),
            title: ent.path,
          });
          btn.dataset.index = String(idx);
          btn.setAttribute('role', 'option');
          btn.setAttribute('aria-selected', 'false');
          const iconEl = make('span', null, {className: 'wf-file-icon'});
          iconEl.setAttribute('aria-hidden', 'true');
          mountWaveIcon(iconEl, ent.directory ? 'folder' : 'file', 14);
          const nameEl = make('span', ent.name, {className: 'wf-file-name'});
          if (nameEl.style) {
            nameEl.style.overflow = 'hidden';
            nameEl.style.textOverflow = 'ellipsis';
          }
          btn.append(iconEl, nameEl);
          btn.addEventListener('click', () => selectChartFileRow(idx, false));
          btn.addEventListener('dblclick', () => {
            if (ent.directory) loadFileBrowserDir(ent.path);
            else if (fileModalMode === 'open_rc') confirmFileModal();
          });
          listEl.append(btn);
        });
        if (hadListFocus) listEl.focus?.({preventScroll: true});
      }
    } catch (err) {
      syncFileNavButtons();
      if (errEl) errEl.textContent = err.message || 'Unable to list folder.';
    }
  }

  async function confirmFileModal() {
    const dir = node('chartFileDir')?.value?.trim() || lastRcDir || '';
    const name = node('chartFileName')?.value?.trim() || '';
    const errEl = node('chartFileError');
    if (!name) {
      if (errEl) errEl.textContent = 'Enter a file name.';
      return;
    }
    const targetPath = joinChartPath(dir, name);
    try {
      if (fileModalMode === 'save_rc') {
        const res = await postChartFile({action: 'save_rc', path: targetPath, content: serializeVerdiRc()});
        currentRcPath = res.path || targetPath;
        const saveBtn = node('chartSaveRcBtn');
        if (saveBtn) saveBtn.title = `Save waveform config (${currentRcPath}) · Shift+click for Save As…`;
        closeFileModal(true);
        status('Saved ' + currentRcPath);
      } else if (fileModalMode === 'open_rc') {
        const res = await postChartFile({action: 'read_rc', path: targetPath});
        closeFileModal(true);
        if (applyRcText(res.content || '', res.path || targetPath)) {
          status('Loaded ' + (res.path || targetPath));
        }
      } else if (fileModalMode === 'export_vcd') {
        const ids = selectedSignalIds();
        const res = await postChartFile({
          action: 'export_vcd',
          path: targetPath,
          run_id: savedRun()?.run_id || STATE.archive?.run_id || undefined,
          job_id: detail?.id,
          attempt_id: detail?.attempt_id,
          view_id: view?.id,
          as_of_seq: metadata?.as_of_seq,
          axis,
          ...(ids.length ? {series: ids} : {}),
        });
        closeFileModal(true);
        status(`Exported VCD (${res.bytes ?? 0} bytes) to ${res.path}`);
      }
    } catch (err) {
      if (errEl) errEl.textContent = err.message || 'File operation failed.';
    }
  }

  function openFileModal(mode) {
    if (!view) return;
    const modal = node('chartFileModal');
    if (!modal) return;
    if (modal.hidden) captureModalOpener('chartFileModal', node('chartOpenRcBtn') || canvas);
    fileModalMode = mode;
    const titleEl = node('chartFileTitle'), nameEl = node('chartFileName');
    const confirmEl = node('chartFileConfirm'), localBtn = node('chartFileLocalBtn');
    const errEl = node('chartFileError');
    if (errEl) errEl.textContent = '';
    if (titleEl) {
      titleEl.textContent = mode === 'open_rc'
        ? 'Open Waveform Config (.rc / .wave.rc)'
        : mode === 'export_vcd'
          ? 'Export IEEE 1364 VCD (.vcd)'
          : 'Save Waveform Config (.wave.rc)';
    }
    if (confirmEl) confirmEl.textContent = mode === 'open_rc' ? 'Open' : mode === 'export_vcd' ? 'Export' : 'Save';
    if (localBtn) localBtn.hidden = mode !== 'open_rc';
    if (nameEl) {
      if (mode === 'save_rc') {
        const prevName = currentRcPath ? currentRcPath.split(/[\\/]/).pop() : '';
        nameEl.value = prevName || `${view.id || 'signals'}.wave.rc`;
      } else if (mode === 'export_vcd') {
        nameEl.value = `${detail?.id || 'wave'}_${view.id || 'rtl'}.vcd`;
      } else {
        nameEl.value = currentRcPath ? currentRcPath.split(/[\\/]/).pop() : '';
      }
    }
    fileHistory = [];
    fileHistoryIndex = -1;
    fileEntries = [];
    fileSelectedIndex = -1;
    syncFileNavButtons();
    modal.hidden = false;
    loadFileBrowserDir(lastRcDir);
    nameEl?.focus?.();
  }

  async function saveRcDirectOrPrompt(forcePrompt = false) {
    if (!view) return;
    if (!forcePrompt && currentRcPath && typeof fetchJson === 'function') {
      try {
        const res = await postChartFile({action: 'save_rc', path: currentRcPath, content: serializeVerdiRc()});
        currentRcPath = res.path || currentRcPath;
        status('Saved ' + currentRcPath);
        return;
      } catch (err) {
        status(err.message || 'Save failed; choose a file path.');
      }
    }
    openFileModal('save_rc');
  }

  function autoFitSignalColumns() {
    const viewRoot = node('chartsView');
    if (!viewRoot || view?.kind !== 'waveform') return;
    let maxName = 10, maxVal = 7;
    for (const row of activeRows.rows) {
      const lblLen = displayRowLabel(row).length + (row.indent || 0) * 2;
      if (lblLen > maxName) maxName = lblLen;
      const vtxt = rowRefs.get(row.key)?.valEl?.textContent || '';
      if (vtxt.length > maxVal) maxVal = vtxt.length;
    }
    const valW = clamp(Math.round(maxVal * 7.5 + 28), 72, 220);
    const paneW = clamp(Math.round(maxName * 7.2 + valW + 56), 190, 520);
    viewRoot.style.setProperty('--wf-val-w', valW + 'px');
    viewRoot.style.setProperty('--wf-pane-w', paneW + 'px');
    colRatioMode = 0;
    resize();
  }

  function cycleColumnRatio() {
    const viewRoot = node('chartsView');
    if (!viewRoot || view?.kind !== 'waveform') return;
    colRatioMode = (colRatioMode + 1) % 4;
    if (colRatioMode === 0) return autoFitSignalColumns();
    const paneW = node('chartSignalPane')?.getBoundingClientRect?.().width || 274;
    const ratios = [null, 0.35, 0.5, 0.25];
    const valW = clamp(Math.round(paneW * ratios[colRatioMode]), 56, Math.max(70, paneW - 70));
    viewRoot.style.setProperty('--wf-val-w', valW + 'px');
  }

  function rowColor(row, idx) {
    if (row.pref?.color) return row.pref.color;
    if (row.parent && row.key !== laneKey(row.parent)) {
      const pPref = settings.get(laneKey(row.parent));
      if (pPref?.color) return pPref.color;
    }
    return WAVE_PALETTE[0];
  }

  function updateSignalPaneValues() {
    if (!view || view.kind !== 'waveform') return;
    const probeX = cursorA ?? (hover?.x ?? null);
    const span = range ? range[1] - range[0] : 0;
    const plotRect = rendered?.rect;
    const edgeEpsilon = plotRect && plotRect.right > plotRect.left && span > 0
      ? Math.max((span / (plotRect.right - plotRect.left)) * 5, span * 0.002)
      : span * 0.006;
    for (const row of activeRows.rows) {
      const refs = rowRefs.get(row.key);
      if (!refs || isGroupRow(row)) continue;
      const valEl = refs.valEl;
      if (!data || probeX == null) {
        if (refs.lastValKey !== 'empty') {
          refs.lastValKey = 'empty';
          valEl.textContent = '—';
          valEl.title = '';
          valEl.className = 'wf-row-val';
        }
        continue;
      }
      const key = row.key;
      if (!waveGeometry.has(key)) waveGeometry.set(key, ChartMath.waveSegments(data, row.lane));
      const spans = waveGeometry.get(key);
      const rowPref = settings.get(key) ?? row.pref ?? null;
      const info = ChartMath.valueAtCursor(spans, probeX, row.lane, laneFormat, edgeEpsilon, rowPref);
      let cls = 'wf-row-val';
      if (info.isTransition) cls += ' wf-val-edge';
      else if (info.kind === 'x') cls += ' wf-val-x';
      else if (info.kind === 'z') cls += ' wf-val-z';
      const valKey = info.isTransition
        ? `T\0${info.transitionText}\0${cls}`
        : `V\0${info.text}\0${info.lastDir}\0${info.prevText}\0${cls}`;
      if (refs.lastValKey === valKey) continue;
      refs.lastValKey = valKey;
      if (info.isTransition) {
        valEl.replaceChildren(make('span', info.transitionText, {className: 'wf-val-text'}));
        valEl.title = info.transitionText;
      } else {
        const valSpan = make('span', info.text, {className: 'wf-val-text'});
        if (info.lastDir) {
          valEl.replaceChildren(valSpan, make('span', info.lastDir, {
            className: 'wf-val-dir',
            title: `Entered from ${info.prevText} → ${info.text}`,
          }));
          valEl.title = `${info.text} (${info.prevText} → ${info.text})`;
        } else {
          valEl.replaceChildren(valSpan);
          valEl.title = info.text;
        }
      }
      valEl.className = cls;
    }
  }

  function displayRowLabel(row) {
    if (isGroupRow(row)) return '= ' + row.label;
    if (!showFullHierarchy) return row.label;
    if (row.kind === 'signal') return row.lane.id;
    if (row.kind === 'expanded_bit') return (row.parent?.id || row.lane.id) + row.label;
    if (row.kind === 'slice') {
      const parent = row.parent;
      const suffix = parent?.label && row.label.startsWith(parent.label)
        ? row.label.slice(parent.label.length)
        : '';
      return (parent?.id || row.lane.id) + suffix;
    }
    return row.label;
  }

  function selectedActiveLane() {
    return activeRows.rows.find(r => r.key === selectedRowKey && r.lane)?.lane ||
      activeRows.rows.find(r => r.lane)?.lane ||
      null;
  }

  function syncFindOpState() {
    const active = selectedActiveLane();
    const bit = active?.type === 'bit';
    const findOp = node('chartFindOp');
    if (findOp) {
      for (const option of findOp.options) {
        if (option.value === 'rising' || option.value === 'falling') option.disabled = !bit;
      }
      if (findOp.selectedOptions?.[0]?.disabled) {
        findOp.value = 'any';
        if (node('chartFindValue')) node('chartFindValue').hidden = true;
      }
    }
  }

  function updateSelectedPathStatus() {
    const el = node('chartSelectedPath');
    if (!el) return;
    if (view?.kind !== 'waveform') {
      el.textContent = '';
      el.title = '';
      return;
    }
    const row = activeRows.rows.find(r => r.key === selectedRowKey) ||
      activeRows.rows.find(r => !isGroupRow(r));
    if (!row) {
      el.textContent = '';
      el.title = '';
      return;
    }
    if (isGroupRow(row)) {
      const msg = `Group = ${row.label} (${row.count ?? 0} signal${row.count === 1 ? '' : 's'})`;
      el.textContent = msg;
      el.title = msg;
      return;
    }
    const lane = row.lane;
    const pref = settings.get(row.key) ?? {};
    const baseId = row.parent?.id ?? lane.id;
    const sliceText = lane.bitSlice
      ? `[${lane.bitSlice[0]}${lane.bitSlice[0] === lane.bitSlice[1] ? '' : ':' + lane.bitSlice[1]}]`
      : '';
    const typeLabel = lane.bits ? `bus [${lane.bits - 1}:0]` : lane.type;
    const curRadix = lane.bits ? (RADICES.includes(pref.radix) ? pref.radix : lane.radix ?? 'hex') : '';
    const fxpSigned = pref.fxpSigned !== undefined ? pref.fxpSigned : lane.signed;
    const radixLabel = curRadix === 'fxp'
      ? ` · FXP (${fxpSigned ? 'Signed' : 'Unsigned'}, pt=${pref.frac ?? lane.frac_bits ?? 0})`
      : curRadix ? ` · ${curRadix}` : '';
    const msg = `${baseId}${sliceText}  (${typeLabel}${radixLabel})`;
    el.textContent = msg;
    el.title = msg;
  }

  function renderSignalPane() {
    const rowsEl = node('chartSignalRows');
    if (!rowsEl || view?.kind !== 'waveform') return;
    const activeEl = typeof document !== 'undefined' ? document.activeElement : null;
    const activeInRows = !!(activeEl && (activeEl === rowsEl || rowsEl.contains?.(activeEl)));
    const activeRowKey = activeInRows ? activeEl?.closest?.('.wf-row')?.dataset?.key : null;
    const wasCaret = !!(activeInRows && activeEl?.classList?.contains?.('wf-group-caret'));
    activeRows = ChartMath.laneRows(selected ?? [], definitions, settings, baseRowH());
    if (!selectedRowKey || !activeRows.rows.some(r => r.key === selectedRowKey)) {
      selectedRowKey = (activeRows.rows.find(r => !isGroupRow(r)) || activeRows.rows[0])?.key ?? null;
    }
    rowRefs = new Map();
    const frag = document.createDocumentFragment();
    activeRows.rows.forEach((row, idx) => {
      const grpRow = isGroupRow(row);
      const isTop = grpRow || row.kind === 'signal';
      const token = grpRow ? 'd:' + (row.group || row.divider).id : 's:' + (row.parent?.instanceId ?? row.lane.instanceId ?? row.parent?.id ?? row.lane.id);
      const rowDiv = make('div', null, {
        className: 'wf-row' +
          (grpRow ? ' wf-group' : '') +
          (row.kind === 'signal' && row.groupId ? ' wf-group-child' : '') +
          ((row.kind === 'expanded_bit' || row.kind === 'slice') ? (row.groupId ? ' wf-deep-child wf-child' : ' wf-child') : '') +
          (selectedRowKey === row.key ? ' selected' : ''),
      });
      rowDiv.style.height = row.height + 'px';
      rowDiv.dataset.key = row.key;
      if (isTop) {
        rowDiv.draggable = true;
        rowDiv.addEventListener('dragstart', e => {
          dragReorder = token;
          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/plain', token);
        });
        rowDiv.addEventListener('dragend', () => { dragReorder = null; });
      }
      rowDiv.addEventListener('dragover', e => {
        if (!dragReorder || dragReorder === token) return;
        e.preventDefault();
        const r = rowDiv.getBoundingClientRect();
        const relY = e.clientY - r.top;
        const into = grpRow && dragReorder.startsWith('s:') && relY >= r.height * 0.28 && relY <= r.height * 0.72;
        const after = !into && relY > r.height / 2;
        rowDiv.classList.toggle('drag-over-into', into);
        rowDiv.classList.toggle('drag-over-above', !into && !after);
        rowDiv.classList.toggle('drag-over-below', !into && after);
      });
      rowDiv.addEventListener('dragleave', () => {
        rowDiv.classList.remove('drag-over-above', 'drag-over-below', 'drag-over-into');
      });
      rowDiv.addEventListener('drop', e => {
        rowDiv.classList.remove('drag-over-above', 'drag-over-below', 'drag-over-into');
        if (!dragReorder || dragReorder === token) return;
        e.preventDefault();
        const r = rowDiv.getBoundingClientRect();
        const relY = e.clientY - r.top;
        const intoGroupId = grpRow && dragReorder.startsWith('s:') && relY >= r.height * 0.28 && relY <= r.height * 0.72
          ? (row.group || row.divider).id
          : null;
        const after = !intoGroupId && relY > r.height / 2;
        const src = dragReorder;
        dragReorder = null;
        reorderTopItem(src, token, after, intoGroupId);
      });
      const nameWrap = make('div', null, {className: 'wf-row-name', title: grpRow ? row.label : row.lane.id});
      if (grpRow) {
        const caret = make('button', null, {
          type: 'button',
          className: 'wf-group-caret',
          title: row.collapsed ? 'Expand group' : 'Collapse group',
        });
        if (caret.style) {
          caret.style.minHeight = '0';
          caret.style.padding = '0';
          caret.style.border = '0';
          caret.style.background = 'transparent';
        }
        caret.setAttribute('aria-expanded', String(!row.collapsed));
        caret.setAttribute('aria-label', `${row.collapsed ? 'Expand' : 'Collapse'} group ${row.label}`);
        mountWaveIcon(caret, row.collapsed ? 'chevron-right' : 'chevron-down', 12);
        caret.addEventListener('click', e => {
          e.stopPropagation();
          toggleGroupCollapse(row.group || row.divider);
        });
        nameWrap.append(caret, make('span', displayRowLabel(row), {className: 'wf-sig-label'}));
        nameWrap.append(make('span', `(${row.count ?? 0})`, {className: 'wf-group-count'}));
      } else {
        if (row.treePrefix) {
          nameWrap.append(make('span', row.treePrefix, {className: 'wf-tree-branch'}));
        }
        const swatch = make('span', null, {className: 'wf-swatch'});
        swatch.style.backgroundColor = rowColor(row, idx);
        nameWrap.append(swatch, make('span', displayRowLabel(row), {className: 'wf-sig-label'}));
      }
      const valEl = make('div', grpRow ? '' : '—', {className: 'wf-row-val'});
      rowDiv.append(nameWrap, valEl);
      rowDiv.addEventListener('click', () => selectRow(row));
      if (grpRow) {
        rowDiv.addEventListener('dblclick', () => toggleGroupCollapse(row.group || row.divider));
      }
      rowDiv.addEventListener('contextmenu', event => {
        event.preventDefault();
        selectRow(row);
        openLaneContextMenu(event.clientX, event.clientY, row, null);
      });
      if (!grpRow) {
        const rowResizer = make('div', null, {
          className: 'wf-row-resizer',
          title: 'Drag to resize row height · Double-click to auto-fit',
        });
        let rowDrag = null;
        rowResizer.addEventListener('click', e => e.stopPropagation());
        rowResizer.addEventListener('pointerdown', e => {
          if (e.button !== 0) return;
          e.preventDefault();
          e.stopPropagation();
          rowDrag = {id: e.pointerId, startY: e.clientY, startH: row.height, nextH: row.height};
          try { rowResizer.setPointerCapture?.(e.pointerId); } catch (_) {}
        });
        rowResizer.addEventListener('pointermove', e => {
          if (!rowDrag || rowDrag.id !== e.pointerId) return;
          e.stopPropagation();
          const nextH = clamp(Math.round(rowDrag.startH + (e.clientY - rowDrag.startY)), 18, 180);
          rowDrag.nextH = nextH;
          rowDiv.style.height = nextH + 'px';
          let yAcc = 0;
          for (const r of activeRows.rows) {
            if (r.key === row.key) r.height = nextH;
            r.y = yAcc;
            yAcc += r.height;
          }
          activeRows.totalHeight = yAcc;
          scheduleBasePaint();
        });
        const finishRowDrag = e => {
          if (!rowDrag || (e && rowDrag.id !== e.pointerId)) return;
          const finalH = rowDrag.nextH;
          const pid = rowDrag.id;
          rowDrag = null;
          try { rowResizer.releasePointerCapture?.(pid); } catch (_) {}
          settings.set(row.key, {...settings.get(row.key), customHeight: finalH});
          saveProfile();
          renderLanes();
        };
        rowResizer.addEventListener('pointerup', finishRowDrag);
        rowResizer.addEventListener('pointercancel', finishRowDrag);
        rowResizer.addEventListener('dblclick', e => {
          e.preventDefault();
          e.stopPropagation();
          const cur = {...settings.get(row.key)};
          delete cur.customHeight;
          delete cur.heightScale;
          if (Object.keys(cur).length) settings.set(row.key, cur);
          else settings.delete(row.key);
          saveProfile();
          renderLanes();
        });
        rowDiv.append(rowResizer);
      }
      rowRefs.set(row.key, {el: rowDiv, valEl, lastValKey: null});
      frag.append(rowDiv);
    });
    rowsEl.replaceChildren(frag);
    if (activeInRows) {
      const targetRowRef = activeRowKey ? rowRefs.get(activeRowKey)?.el : null;
      const focusEl = (wasCaret && targetRowRef?.querySelector?.('.wf-group-caret')) || rowsEl;
      focusEl?.focus?.({preventScroll: true});
    }
    updateSignalPaneValues();
    updateSelectedPathStatus();
    syncFindOpState();
  }

  function selectRow(row) {
    if (!row) return;
    selectedRowKey = row.key;
    for (const [k, refs] of rowRefs.entries()) refs.el.classList.toggle('selected', k === row.key);
    clearFind();
    syncFindOpState();
    updateSelectedPathStatus();
    scheduleOverlayPaint();
  }

  function renderLanes() {
    const list = lanes();
    if (view?.kind === 'waveform') {
      renderSignalPane();
    } else if (node('chartLanes')) {
      const labels = list.map((lane, i) => {
        const suffix = lane.unit ? ' (' + lane.unit + ')' : '';
        const dot = make('span', null, {className: 'chart-legend-dot'});
        dot.style.backgroundColor = SCI_COLORS[i % SCI_COLORS.length];
        const label = make('span', null, {className: 'chart-legend-item', title: lane.label + suffix});
        label.append(dot, make('span', lane.label + suffix));
        return label;
      });
      node('chartLanes').replaceChildren(...labels);
    }
    scheduleBasePaint();
  }

  function openBrowserContextMenu(clientX, clientY, item, isAdded) {
    const menu = node('chartContextMenu');
    if (!menu) return;
    contextMenuOpener = typeof document !== 'undefined' ? document.activeElement : null;
    menu.replaceChildren();
    menu.append(make('div', item.label, {className: 'wf-ctx-label'}));
    const act = make('button', isAdded ? 'Remove from Wave' : 'Add to Wave', {type: 'button', className: 'wf-ctx-item'});
    act.setAttribute('role', 'menuitem');
    act.addEventListener('click', () => {
      closeContextMenu(true);
      if (isAdded) removeSignalsFromWave([item.id]);
      else addSignalsToWave([item.id]);
    });
    menu.append(act);
    positionContextMenu(menu, clientX, clientY);
  }

  function positionSubmenu(menu, trigger, sub) {
    if (!sub) return;
    sub.classList?.remove?.('flip-left');
    if (sub.style) sub.style.top = '-4px';
    const vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
    const mr = menu?.getBoundingClientRect?.();
    const sr = sub.getBoundingClientRect?.();
    const tr = trigger?.getBoundingClientRect?.();
    const subW = sr?.width || 195;
    if (mr && mr.right + subW > vw && mr.left - subW >= 6) sub.classList?.add?.('flip-left');
    if (tr && sr && sr.height) {
      const desiredTop = clamp(tr.top - 4, 6, Math.max(6, vh - sr.height - 6));
      if (sub.style) sub.style.top = Math.round(desiredTop - tr.top) + 'px';
    }
  }

  function syncSubmenuExpanded(menu) {
    if (!menu) return;
    for (const ch of menu.children || []) {
      if (!ch.classList?.contains?.('wf-ctx-subwrap')) continue;
      const trig = ch.children?.[0];
      trig?.setAttribute?.('aria-expanded', String(ch.classList.contains('open')));
    }
  }

  function openLaneContextMenu(clientX, clientY, row, hoverPoint) {
    const menu = node('chartContextMenu');
    if (!menu || !row) return;
    contextMenuOpener = typeof document !== 'undefined' ? document.activeElement : null;
    menu.replaceChildren();
    const key = row.key;
    const pref = settings.get(key) ?? {};
    const updatePref = values => {
      const nextPref = {...settings.get(key), ...values};
      if (nextPref.heightScale === undefined) delete nextPref.heightScale;
      if (nextPref.customHeight === undefined) delete nextPref.customHeight;
      settings.set(key, nextPref);
      saveProfile();
      renderLanes();
    };
    const addAction = (label, fn, active = false) => {
      const btn = make('button', label, {type: 'button', className: 'wf-ctx-item' + (active ? ' active' : '')});
      btn.setAttribute('role', 'menuitem');
      btn.addEventListener('click', () => { closeContextMenu(true); fn(); });
      menu.append(btn);
      return btn;
    };
    const addSep = () => {
      const sep = make('div', null, {className: 'wf-ctx-sep'});
      sep.setAttribute('role', 'separator');
      menu.append(sep);
    };
    const addSubmenu = (label, buildFn) => {
      const wrap = make('div', null, {className: 'wf-ctx-subwrap'});
      const trigger = make('button', label, {type: 'button', className: 'wf-ctx-item'});
      trigger.setAttribute('role', 'menuitem');
      trigger.setAttribute('aria-haspopup', 'menu');
      trigger.setAttribute('aria-expanded', 'false');
      const sub = make('div', null, {className: 'wf-ctx-submenu'});
      sub.setAttribute('role', 'menu');
      sub.setAttribute('aria-label', label);
      buildFn(sub, (lbl, fn, active = false, extraCls = '') => {
        const item = make('button', lbl, {type: 'button', className: 'wf-ctx-item' + (active ? ' active' : '') + (extraCls ? ' ' + extraCls : '')});
        item.setAttribute('role', 'menuitem');
        item.addEventListener('click', e => {
          e.stopPropagation();
          closeContextMenu(true);
          fn();
        });
        sub.append(item);
        return item;
      });
      wrap.addEventListener('pointerenter', () => positionSubmenu(menu, trigger, sub));
      trigger.addEventListener('click', e => {
        e.stopPropagation();
        const wasOpen = wrap.classList.contains('open');
        for (const other of menu.children) other.classList?.remove?.('open');
        wrap.classList.toggle('open', !wasOpen);
        syncSubmenuExpanded(menu);
        if (!wasOpen) positionSubmenu(menu, trigger, sub);
      });
      wrap.append(trigger, sub);
      menu.append(wrap);
      return wrap;
    };

    if (isGroupRow(row)) {
      const grp = row.group || row.divider;
      menu.append(make('div', 'Group: ' + row.label, {className: 'wf-ctx-label'}));
      addAction('Rename Group…', () => {
        let inp;
        openParamModal('Rename Group', bodyEl => {
          const r = make('label', null, {className: 'wf-param-row'});
          inp = make('input', null, {type: 'text', value: row.label, maxLength: 64, ariaLabel: 'Group name'});
          inp.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); paramModalApplyFn?.(); }
          });
          r.append(make('span', 'Group name'), inp);
          bodyEl.append(r);
          return inp;
        }, () => {
          const text = inp.value.trim();
          if (!text) return false;
          grp.label = text;
          saveProfile();
          renderLanes();
          return true;
        });
      });
      addSep();
      addAction(row.collapsed ? 'Expand Group' : 'Collapse Group', () => toggleGroupCollapse(grp));
      addAction('Move Up', () => moveTopItem(grp, -1));
      addAction('Move Down', () => moveTopItem(grp, 1));
      addAction('Ungroup (Keep Signals)', () => ungroupSignals(grp));
      addAction('Remove Group & Signals', () => removeRow(row));
      positionContextMenu(menu, clientX, clientY);
      return;
    }

    const lane = row.lane;
    menu.append(make('div', lane.label, {className: 'wf-ctx-label'}));

    // Reveal in Raw Log (explicit action replacing single-click log popup)
    let logPoint = hoverPoint;
    if (!logPoint && cursorA != null && data) {
      if (!waveGeometry.has(key)) waveGeometry.set(key, ChartMath.waveSegments(data, lane));
      logPoint = ChartMath.holdAt(waveGeometry.get(key), cursorA)?.point ?? null;
    }
    if (!logPoint && picked?.series_id === (lane.parent?.id ?? lane.id)) logPoint = picked;
    const revBtn = make('button', Number.isSafeInteger(logPoint?.log_seq)
      ? `Reveal in Raw Log (#${logPoint.log_seq})`
      : 'Reveal in Raw Log (none)', {type: 'button', className: 'wf-ctx-item'});
    revBtn.setAttribute('role', 'menuitem');
    revBtn.disabled = !Number.isSafeInteger(logPoint?.log_seq);
    revBtn.addEventListener('click', () => {
      closeContextMenu(true);
      picked = {...logPoint, axis, lane: laneKey(lane), view_id: view.id};
      setTarget(picked);
      evidence(picked);
    });
    menu.append(revBtn);

    if (cursorA != null && cursorB != null && cursorA !== cursorB) {
      addAction('Zoom Between A & B', zoomToCursors);
    }

    const openFxpModal = () => {
      const curFxpSigned = pref.fxpSigned !== undefined ? !!pref.fxpSigned : !!lane.signed;
      const defaultFrac = pref.frac !== undefined ? pref.frac
        : (lane.frac_bits || Math.max(1, Math.min(256, Math.floor((lane.bits || 8) / 2))));
      let signSel, fracInp;
      openParamModal('Fixed-Point (FXP) Format', bodyEl => {
        signSel = make('select', null, {ariaLabel: 'FXP signedness'});
        signSel.append(
          make('option', "Signed (2's comp)", {value: 'signed'}),
          make('option', 'Unsigned', {value: 'unsigned'}),
        );
        signSel.value = curFxpSigned ? 'signed' : 'unsigned';
        fracInp = make('input', null, {
          type: 'number', min: 0, max: 256, step: 1, value: defaultFrac, ariaLabel: 'Decimal point bits',
        });
        const previewEl = make('div', '', {className: 'wf-ctx-fxp-preview'});
        const refreshPreview = () => {
          const pt = Math.max(0, Math.min(256, Number(fracInp.value) || 0));
          const modeLabel = signSel.value === 'signed' ? 'Signed' : 'Unsigned';
          const divTxt = pt <= 20 ? `2^${pt} = ${Math.pow(2, pt)}` : `2^${pt}`;
          previewEl.textContent = `${modeLabel} · point = ${pt} (raw ÷ ${divTxt})`;
        };
        refreshPreview();
        signSel.addEventListener('change', refreshPreview);
        fracInp.addEventListener('input', refreshPreview);
        fracInp.addEventListener('keydown', e => {
          if (e.key === 'Enter') { e.preventDefault(); paramModalApplyFn?.(); }
        });
        const r1 = make('label', null, {className: 'wf-param-row'});
        r1.append(make('span', 'Type:'), signSel);
        const r2 = make('label', null, {className: 'wf-param-row'});
        r2.append(make('span', 'Decimal Point:'), fracInp);
        bodyEl.append(r1, r2, previewEl);
        return fracInp;
      }, () => {
        if (!fracInp.checkValidity() || fracInp.value === '') return false;
        updatePref({radix: 'fxp', frac: Number(fracInp.value), fxpSigned: signSel.value === 'signed'});
        return true;
      });
    };

    if (lane.bits) {
      addSep();
      const curRadix = RADICES.includes(pref.radix) ? pref.radix : lane.radix ?? 'hex';
      addSubmenu('Radix', (_sub, addSubAction) => {
        for (const [r, title] of [['hex', 'Hexadecimal'], ['bin', 'Binary'], ['unsigned', 'Unsigned Decimal'], ['signed', 'Signed Decimal']]) {
          addSubAction(title, () => updatePref({radix: r}), curRadix === r);
        }
        addSubAction('Fixed-Point (FXP)…', openFxpModal, curRadix === 'fxp');
      });
      addAction('Fixed-Point (FXP)…', openFxpModal, curRadix === 'fxp');
    }

    if (lane.bits || lane.type === 'number') {
      const curDisp = pref.display || 'digital';
      const digitalUpdates = curDisp.startsWith('analog')
        ? {display: 'digital', heightScale: undefined, customHeight: undefined}
        : {display: 'digital'};
      addSubmenu('Wave Style', (_sub, addSubAction) => {
        addSubAction('Digital Bus / Value', () => updatePref(digitalUpdates), curDisp === 'digital');
        addSubAction('Analog Step', () => updatePref({display: 'analog_step', heightScale: pref.heightScale || 2}), curDisp === 'analog_step');
        addSubAction('Analog Linear', () => updatePref({display: 'analog_linear', heightScale: pref.heightScale || 2}), curDisp === 'analog_linear');
      });
    }

    if (row.kind === 'signal' && lane.bits) {
      if (lane.bits <= 64) {
        addAction(pref.expanded ? 'Collapse Bits' : `Expand Bits [${lane.bits - 1}:0]`,
          () => updatePref({expanded: !pref.expanded}), !!pref.expanded);
      }
      addAction('Bit Select [msb:lsb]…', () => {
        let inp;
        openParamModal('Bit Select [msb:lsb]', bodyEl => {
          const r = make('label', null, {className: 'wf-param-row'});
          inp = make('input', null, {type: 'text', placeholder: `[${lane.bits - 1}:0] or [0]`, maxLength: 9, ariaLabel: 'Bit slice [msb:lsb]'});
          inp.addEventListener('input', () => inp.setCustomValidity?.(''));
          inp.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); paramModalApplyFn?.(); }
          });
          r.append(make('span', 'Range'), inp);
          bodyEl.append(r);
          return inp;
        }, () => {
          const parsed = ChartMath.parseSlice(inp.value, lane.bits);
          if (!parsed) {
            inp.setCustomValidity?.(`Enter [msb:lsb] or [bit] within 0..${lane.bits - 1}`);
            inp.reportValidity?.();
            return false;
          }
          const text = '[' + parsed[0] + (parsed[0] === parsed[1] ? '' : ':' + parsed[1]) + ']';
          const slices = Array.isArray(pref.slices) ? pref.slices : [];
          updatePref({slices: [...new Set([...slices, text])]});
          return true;
        });
      });
    }

    const curColor = rowColor(row, 0);
    addSubmenu('Color', (_sub, addSubAction) => {
      for (const c of WAVE_PALETTE) {
        const item = addSubAction(c, () => updatePref({color: c}), curColor === c, 'wf-ctx-color-item');
        const dot = make('span', null, {className: 'wf-ctx-color-dot'});
        dot.style.backgroundColor = c;
        item.replaceChildren(dot, make('span', c));
      }
    });

    addSep();
    if (row.kind === 'signal') {
      const tok = lane.instanceId ?? lane.id;
      addAction('Move Up', () => moveTopItem(tok, -1));
      addAction('Move Down', () => moveTopItem(tok, 1));
      if (row.groupId) {
        addAction('Remove from Group', () => moveSignalToTop(tok, row.groupId));
      }
    }
    addAction('Group into = G (Shift+G)', () => insertDividerAbove(row.key));
    addAction(row.kind === 'slice' ? 'Remove Slice' : row.kind === 'expanded_bit' ? 'Collapse Parent Bits' : 'Remove Signal', () => removeRow(row));
    positionContextMenu(menu, clientX, clientY);
  }

  function positionContextMenu(menu, x, y) {
    menu.hidden = false;
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    const vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
    const rect = menu.getBoundingClientRect();
    const clampedX = Math.max(6, Math.min(x, vw - (rect.width || 220) - 6));
    const clampedY = Math.max(6, Math.min(y, vh - (rect.height || 260) - 6));
    menu.style.left = clampedX + 'px';
    menu.style.top = clampedY + 'px';
    const firstItem = menu.querySelector?.('.wf-ctx-item:not(:disabled)') ||
      menu.children?.find?.(ch => ch.tagName === 'BUTTON' && !ch.disabled) ||
      menu.children?.find?.(ch => ch.classList?.contains?.('wf-ctx-subwrap'))?.children?.[0] ||
      null;
    firstItem?.focus?.({preventScroll: true});
  }

  function clearFind() {
    cancel('find');
    findCursor = findRequest = null;
    if (node('chartContinueFind')) {
      node('chartContinueFind').disabled = true;
      node('chartContinueFind').hidden = true;
    }
  }

  function pushViewHistory(prev) {
    if (!Array.isArray(prev) || prev.length !== 2 || !Number.isFinite(prev[0]) || !Number.isFinite(prev[1]) || prev[1] <= prev[0]) return;
    const last = viewHistory[viewHistory.length - 1];
    if (last && last[0] === prev[0] && last[1] === prev[1]) return;
    viewHistory.push([prev[0], prev[1]]);
    if (viewHistory.length > MAX_HISTORY) viewHistory.shift();
  }

  function syncLastViewButton() {
    const btn = node('chartLastView');
    if (btn) btn.disabled = !viewHistory.length;
  }

  function restoreLastView() {
    if (!viewHistory.length) {
      status('No previous view.');
      return;
    }
    lastWheelAt = 0;
    const prev = viewHistory.pop();
    changeRange(prev, true, false, {skipHistory: true});
  }

  function changeRange(next, immediate = false, keepFind = false, opts = {}) {
    if (!opts.skipHistory && Array.isArray(range) && Array.isArray(next) && domain) {
      const eps = Math.max((domain[1] - domain[0]) * 1e-12, 1e-12);
      if (Math.abs(next[0] - range[0]) > eps || Math.abs(next[1] - range[1]) > eps) {
        pushViewHistory(range);
      }
    }
    range = next;
    syncWindowControls();
    if (!keepFind) clearFind();
    cancel('data');
    clearTimeout(timer);
    cursor = null;
    if (node('chartContinue')) {
      node('chartContinue').disabled = true;
      node('chartContinue').hidden = true;
    }
    if (!data || !range || !chosen().length || !immediate) scheduleBasePaint();
    if (!range || !chosen().length) {
      if (view?.series_count && selected !== null) status('Select a signal to draw.', '');
      return;
    }
    if (immediate) loadWindow();
    else {
      status('Updating window…');
      timer = setTimeout(loadWindow, 120);
    }
  }

  function loadWindow(more = false) {
    if (!view || !range || !chosen().length) return;
    if (!more) {
      const fraction = (range[1] - range[0]) / (domain[1] - domain[0]);
      const buckets = clamp(Math.round((width || 600) * scale / 3), 32, 320);
      pageOptions = {
        ...params('chart_window'),
        axis,
        lo: range[0],
        hi: range[1],
        inclusive: true,
        series: JSON.stringify(chosen().map(item => item.id)),
        mode: view.id === '__auto__' || fraction < .05 ? 'raw' : 'overview',
        buckets,
      };
      status('Reading chart window…', '');
    } else status('Reading more samples…');
    if (node('chartContinue')) {
      node('chartContinue').disabled = true;
      node('chartContinue').hidden = true;
    }
    request('data', {...pageOptions, ...(more && cursor ? {cursor} : {})}, page => {
      if (!more && !page.complete && pageOptions.mode === 'raw' &&
          (view.kind === 'xy_line' || view.kind === 'waveform')) {
        pageOptions.mode = 'overview';
        request('data', pageOptions, overview => acceptPage(overview, false));
      } else acceptPage(page, more);
    }, error => {
      status(error.message, '');
      if (node('chartContinue')) {
        node('chartContinue').disabled = !cursor;
        node('chartContinue').hidden = !cursor;
      }
    });
  }

  function mergeBuckets(first, next, kind) {
    for (const [id, cells] of Object.entries(next)) {
      const target = first[id] ?? (first[id] = []);
      for (const cell of cells) {
        const old = target[target.length - 1];
        if (!old || old.index !== cell.index) {
          target.push(cell);
          continue;
        }
        old.last = cell.last;
        if (kind === 'waveform') {
          old.first_change ??= cell.first_change;
          old.transitions += cell.transitions;
          old.unknown ||= cell.unknown;
        } else {
          if (cell.min && (!old.min || cell.min.value < old.min.value)) old.min = cell.min;
          if (cell.max && (!old.max || cell.max.value > old.max.value)) old.max = cell.max;
          old.violation ??= cell.violation;
          old.has_gap ||= cell.has_gap;
        }
      }
    }
  }

  function acceptPage(page, more) {
    if (!more) data = {...page, range: [pageOptions.lo, pageOptions.hi]};
    else {
      if (page.buckets) mergeBuckets(data.buckets, page.buckets, page.kind);
      else if (page.kind === 'histogram') {
        if (data.phase !== page.phase || data.bins.length !== page.bins.length) data.bins = page.bins;
        else data.bins = page.bins.map((count, i) => data.bins[i] + count);
        data.stats = page.stats;
        data.edges = page.edges;
        data.phase = page.phase;
        data.underflow += page.underflow;
        data.overflow += page.overflow;
      } else if (page.kind === 'scatter') {
        data.points = [...data.points, ...page.points]
          .sort((a, b) => a.rank.length - b.rank.length || a.rank.localeCompare(b.rank) || a.flow_seq - b.flow_seq)
          .slice(0, page.sample_limit);
        data.valid += page.valid;
        data.sampled = data.valid > data.points.length;
      } else data.points.push(...page.points);
      data.complete = page.complete;
      data.coverage = page.coverage;
      data.scanned += page.scanned;
      data.health = page.health;
    }
    cursor = page.next_cursor;
    waveGeometry.clear();
    if (view?.kind === 'waveform') {
      if (cursorAAnchor && cursorAAnchor.axis !== axis) {
        const mappedA = resolveAnchorOnData(cursorAAnchor);
        if (Number.isFinite(mappedA)) {
          cursorA = mappedA;
          cursorAAnchor = {...cursorAAnchor, axis, x: mappedA, coords: {...(cursorAAnchor.coords || {}), [axis]: mappedA}};
        }
      }
      if (cursorBAnchor && cursorBAnchor.axis !== axis) {
        const mappedB = resolveAnchorOnData(cursorBAnchor);
        if (Number.isFinite(mappedB)) {
          cursorB = mappedB;
          cursorBAnchor = {...cursorBAnchor, axis, x: mappedB, coords: {...(cursorBAnchor.coords || {}), [axis]: mappedB}};
        }
      }
      updateCursorSummary();
    }
    if (node('chartContinue')) {
      node('chartContinue').disabled = !cursor;
      node('chartContinue').hidden = !cursor;
    }
    let text = data.complete ? data.scanned.toLocaleString() + ' rows scanned' :
      'Scanned through ' + fmt(data.coverage?.[0]) + ' · remaining range unknown';
    if (data.kind === 'scatter') text += ' · ' + data.points.length + '/' + data.valid + ' valid points' +
      (data.sampled ? ' sampled' : '');
    if (view.id === '__auto__') text += ' · latest ≤100 Parser records';
    if (view.diagnostics?.invalid || view.diagnostics?.dropped) {
      text += ' · ' + (view.diagnostics.invalid + view.diagnostics.dropped) + ' projection diagnostics';
    }
    if (data.health?.error) text += ' · Archive save failed';
    else if (!data.health?.closed_cleanly) text += ' · Committed prefix';
    status('', text);
    scheduleBasePaint();
  }

  function find(direction, more = false, override = null) {
    const targetLane = override?.lane ?? selectedActiveLane();
    if (!targetLane || !domain) return;
    if (!more) {
      const op = override?.op ?? node('chartFindOp').value;
      const rawText = override?.value ?? node('chartFindValue').value;
      const trimmed = String(rawText ?? '').trim();
      let value = trimmed;
      if (op === 'eq' && (targetLane.parent?.bits || targetLane.bits || targetLane.type === 'bit')) {
        if (/^[xXzZ]$/.test(trimmed)) {
          value = trimmed.toUpperCase();
        } else if (!/^-?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|[0-9]{1,78})$/.test(trimmed)) {
          return status('Enter decimal, 0x..., 0b..., X, or Z.');
        }
      } else if (op === 'eq' && targetLane.type === 'number') {
        value = Number(trimmed);
        if (!trimmed || !Number.isFinite(value)) return status('Enter a finite numeric value.');
      }
      const anchorX = cursorA ?? (picked?.axis === axis && picked?.lane === laneKey(targetLane) ? picked.x : hover?.x);
      const anchor = picked?.axis === axis && picked?.lane === laneKey(targetLane) && anchorX === picked.x
        ? [picked.x, picked.flow_seq]
        : anchorX != null ? [anchorX, direction === 'forward' ? Number.MAX_SAFE_INTEGER : 0] : null;
      findRequest = {
        ...params('chart_find'),
        series_id: targetLane.id,
        axis,
        direction,
        op,
        ...(op === 'eq' ? {value: JSON.stringify(value)} : {}),
        ...(targetLane.bitSlice ? {bit_slice: JSON.stringify(targetLane.bitSlice)} : {}),
        ...(anchor ? {anchor: JSON.stringify(anchor)} : {}),
      };
      findCursor = null;
    }
    if (!findRequest) return;
    if (node('chartContinueFind')) {
      node('chartContinueFind').disabled = true;
      node('chartContinueFind').hidden = true;
    }
    status('Searching for an event…');
    request('find', {...findRequest, ...(findCursor ? {cursor: findCursor} : {})}, resPage => {
      findCursor = resPage.next_cursor;
      if (node('chartContinueFind')) {
        node('chartContinueFind').disabled = !findCursor;
        node('chartContinueFind').hidden = !findCursor;
      }
      if (!resPage.hit) return status(resPage.complete ? 'No matching event.' : 'Search paused at the scan limit.');
      picked = {...resPage.hit, axis, lane: laneKey(targetLane), view_id: view.id};
      cursorA = picked.x;
      cursorAAnchor = captureCursorAnchor(cursorA, picked);
      setTarget(picked);
      updateCursorSummary();
      if (node('chartAnnouncement')) {
        node('chartAnnouncement').textContent = 'Event at ' + fmt(picked.x) +
          (picked.log_seq ? ', Log line ' + picked.log_seq : '');
      }
      status('Found event at ' + fmt(picked.x));
      if (picked.x < range[0] || picked.x > range[1]) {
        const span = Math.min(range[1] - range[0], (domain[1] - domain[0]) / 12);
        moveWindow(picked.x - span / 2, span, true);
      } else {
        updateSignalPaneValues();
        scheduleOverlayPaint();
      }
    });
  }

  function setTarget(point) {
    const buttonEl = node('chartTarget');
    if (buttonEl) {
      buttonEl.hidden = !Number.isSafeInteger(point?.log_seq);
      buttonEl.textContent = buttonEl.hidden ? '' : 'Log #' + point.log_seq;
    }
  }

  function evidence(point) {
    if (!Number.isSafeInteger(point?.log_seq)) {
      status('No original Log line for this sample.');
      return;
    }
    AnalysisWorkspace.openContext(detail, point.log_seq);
  }

  function openPickedLog() {
    if (!Number.isSafeInteger(picked?.log_seq)) {
      status('Select a sample with an original Log line first.');
      return;
    }
    if (typeof setWorkspaceLayout === 'function' && typeof jumpToLogEvidence === 'function') {
      setWorkspaceLayout('split');
      jumpToLogEvidence(picked.log_seq);
    } else {
      evidence(picked);
    }
  }

  function updateCursorSummary() {
    const el = node('chartCursorSummary');
    const inpA = node('chartCursorAInput');
    const inpB = node('chartCursorBInput');
    if (view?.kind !== 'waveform') {
      if (el) {
        el.hidden = true;
        el.disabled = true;
      }
      if (inpA) inpA.value = '';
      if (inpB) inpB.value = '';
      return;
    }
    const active = typeof document !== 'undefined' ? document.activeElement : null;
    if (inpA && active !== inpA) inpA.value = cursorA != null ? fmtAxisVal(cursorA) : '';
    if (inpB && active !== inpB) inpB.value = cursorB != null ? fmtAxisVal(cursorB) : '';
    if (!el) return;
    el.hidden = false;
    el.disabled = !(cursorA != null && cursorB != null && cursorA !== cursorB);
    const activeUnit = axis === 'time' ? activeTimeScaleLabel() : '';
    const unit = activeUnit ? ' ' + activeUnit : '';
    const partDt = cursorA != null && cursorB != null ? fmtAxisVal(cursorB - cursorA) + unit : '—';
    el.textContent = `Δt: ${partDt}`;
  }

  function jumpCursorInput(which, rawText) {
    if (view?.kind !== 'waveform' || !domain) return;
    const trimmed = String(rawText ?? '').trim();
    if (!trimmed) {
      if (which === 'B') {
        cursorB = null;
        cursorBAnchor = null;
        updateCursorSummary();
        scheduleOverlayPaint();
      } else {
        updateCursorSummary();
      }
      return;
    }
    const unitMatch = /\s*(fs|ps|ns|us|ms|s)$/i.exec(trimmed);
    const cleaned = trimmed.replace(/,/g, '').replace(/\s*(?:fs|ps|ns|us|ms|s)$/i, '');
    const parsed = Number(cleaned);
    if (!cleaned || !Number.isFinite(parsed)) {
      status('Enter a valid cursor time value.');
      updateCursorSummary();
      return;
    }
    let scaled = parsed;
    if (axis === 'time') {
      const nat = String(view?.axis?.unit || 'ns').toLowerCase();
      if (unitMatch) {
        const fTyped = UNIT_FACTORS[unitMatch[1].toLowerCase()], fNat = UNIT_FACTORS[nat];
        if (fTyped && fNat) scaled = parsed * (fTyped / fNat);
      } else {
        scaled = parsed / timeUnitScale();
      }
    }
    const xVal = clamp(scaled, domain[0], domain[1]);
    if (which === 'A') {
      cursorA = xVal;
      cursorAAnchor = captureCursorAnchor(xVal);
    } else {
      cursorB = xVal;
      cursorBAnchor = captureCursorAnchor(xVal);
    }
    if (range && (xVal < range[0] || xVal > range[1])) {
      const span = range[1] - range[0];
      moveWindow(xVal - span / 2, span);
    }
    updateCursorSummary();
    updateSignalPaneValues();
    scheduleOverlayPaint();
  }

  function zoomToCursors() {
    if (view?.kind !== 'waveform' || cursorA == null || cursorB == null || cursorA === cursorB) return;
    if (!ChartControls.windowActions(view?.kind, domain, range).zoom) return;
    const lo = Math.min(cursorA, cursorB), hi = Math.max(cursorA, cursorB);
    moveWindow(lo, hi - lo);
  }

  function cycleSelectedColor() {
    const row = activeRows.rows.find(r => r.key === selectedRowKey && !isGroupRow(r)) ||
      activeRows.rows.find(r => !isGroupRow(r));
    if (!row) return;
    const cur = rowColor(row, 0);
    const idx = WAVE_PALETTE.indexOf(cur);
    const nextColor = WAVE_PALETTE[(idx + 1) % WAVE_PALETTE.length];
    const pref = settings.get(row.key) ?? {};
    settings.set(row.key, {...pref, color: nextColor});
    saveProfile();
    renderLanes();
  }

  function keyboardZoomRatio() {
    if (view?.kind === 'waveform' && cursorA != null && range && range[1] > range[0] &&
        cursorA >= range[0] && cursorA <= range[1]) {
      return (cursorA - range[0]) / (range[1] - range[0]);
    }
    return 0.5;
  }

  function moveWindow(lower, span, keepFind = false, opts = {}) {
    if (!domain) return;
    const total = domain[1] - domain[0];
    const precision = Math.max(total * 1e-12,
      Math.max(Math.abs(domain[0]), Math.abs(domain[1])) * Number.EPSILON * 4);
    const bounded = clamp(span, Math.min(total, axis === 'seq' ? 1 : precision), total);
    const lo = clamp(lower, domain[0], domain[1] - bounded);
    const immediate = opts.immediate !== undefined ? !!opts.immediate : true;
    changeRange([lo, Math.min(domain[1], lo + bounded)], immediate, keepFind, opts);
  }

  function zoom(factor, ratio = .5, opts = {}) {
    if (!ChartControls.windowActions(view?.kind, domain, range).zoom) return;
    const span = range[1] - range[0], newSpan = span * factor;
    moveWindow(range[0] + ratio * (span - newSpan), newSpan, false, opts);
  }

  function pan(fraction, opts = {}) {
    if (!ChartControls.windowActions(view?.kind, domain, range).pan) return;
    const span = range[1] - range[0];
    moveWindow(range[0] + fraction * span, span, false, opts);
  }

  function syncHorizontalScrollbar() {
    const hs = node('chartHScroll'), thumb = node('chartHScrollThumb');
    const leftBtn = node('chartHScrollLeft'), rightBtn = node('chartHScrollRight');
    if (!hs) return;
    if (view?.kind !== 'waveform' || !domain || !range || !(domain[1] > domain[0])) {
      hs.hidden = true;
      return;
    }
    hs.hidden = false;
    const total = domain[1] - domain[0], span = range[1] - range[0];
    const widthPct = clamp((span / total) * 100, 3, 100);
    const maxLeft = Math.max(0, 100 - widthPct);
    const leftPct = total > span
      ? clamp(((range[0] - domain[0]) / (total - span)) * maxLeft, 0, maxLeft)
      : 0;
    if (thumb?.style) {
      thumb.style.left = leftPct.toFixed(3) + '%';
      thumb.style.width = widthPct.toFixed(3) + '%';
    }
    const scrollPct = maxLeft > 0 ? Math.round((leftPct / maxLeft) * 100) : 0;
    thumb?.setAttribute?.('aria-valuenow', String(scrollPct));
    const eps = total * 1e-12;
    if (leftBtn) leftBtn.disabled = range[0] <= domain[0] + eps;
    if (rightBtn) rightBtn.disabled = range[1] >= domain[1] - eps;
  }

  function syncWindowControls() {
    syncLastViewButton();
    syncHorizontalScrollbar();
    if (!node('chartZoomIn')) return null;
    return ChartControls.syncWindowButtons(view?.kind, domain, range, {
      zoomIn: node('chartZoomIn'),
      zoomOut: node('chartZoomOut'),
      panLeft: node('chartPanLeft'),
      panRight: node('chartPanRight'),
      reset: node('chartResetWindow'),
    });
  }

  function resize() {
    if (!box || !canvas) return;
    width = box.clientWidth;
    height = box.clientHeight;
    if (!width || !height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    scale = Math.min(dpr, Math.sqrt(4000000 / (width * height)));
    const w = Math.max(1, Math.floor(width * scale)), h = Math.max(1, Math.floor(height * scale));
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    if (overlayCanvas) {
      if (overlayCanvas.width !== w) overlayCanvas.width = w;
      if (overlayCanvas.height !== h) overlayCanvas.height = h;
    }
    scheduleBasePaint();
  }

  function scheduleBasePaint() {
    if (baseFrame !== null) return;
    if (typeof requestAnimationFrame !== 'function') {
      paintBase();
      return;
    }
    let ranSync = false;
    const handle = requestAnimationFrame(() => { ranSync = true; baseFrame = null; paintBase(); });
    if (!ranSync) baseFrame = handle;
  }

  function scheduleOverlayPaint() {
    if (overlayFrame !== null) return;
    if (typeof requestAnimationFrame !== 'function') {
      paintOverlay();
      return;
    }
    let ranSync = false;
    const handle = requestAnimationFrame(() => { ranSync = true; overlayFrame = null; paintOverlay(); });
    if (!ranSync) overlayFrame = handle;
  }

  function line(targetCtx, x1, y1, x2, y2) {
    targetCtx.beginPath();
    targetCtx.moveTo(x1, y1);
    targetCtx.lineTo(x2, y2);
    targetCtx.stroke();
  }

  function hatch(targetCtx, x, y, w, h, fill, stroke) {
    if (!(w > 0 && h > 0)) return;
    targetCtx.fillStyle = fill;
    targetCtx.fillRect(x, y, w, h);
    targetCtx.save();
    targetCtx.beginPath();
    targetCtx.rect(x, y, w, h);
    targetCtx.clip();
    targetCtx.strokeStyle = stroke;
    for (let i = -h; i < w; i += 8) line(targetCtx, x + i, y + h, x + i + h, y);
    targetCtx.restore();
  }

  function spanX(rect, value) {
    return rect.left + (rect.right - rect.left) * ((value - range[0]) / (range[1] - range[0]));
  }

  function pxToX(rect, px) {
    return range[0] + ((px - rect.left) / (rect.right - rect.left)) * (range[1] - range[0]);
  }

  function drawRuler(rect) {
    ctx.fillStyle = '#151f2c';
    ctx.fillRect(0, 0, width, RULER_HEIGHT);
    ctx.strokeStyle = '#293548';
    line(ctx, 0, RULER_HEIGHT - .5, width, RULER_HEIGHT - .5);
    const steps = rect.right - rect.left < 260 ? 3 : rect.right - rect.left < 560 ? 5 : 8;
    ctx.fillStyle = '#9aacc0';
    ctx.font = '11px monospace';
    ctx.textAlign = 'center';
    for (let i = 0; i <= steps; i++) {
      const px = rect.left + (rect.right - rect.left) * i / steps;
      const tickX = Math.floor(px) + .5;
      ctx.strokeStyle = '#384a5e';
      line(ctx, tickX, RULER_HEIGHT - 6, tickX, RULER_HEIGHT);
      ctx.strokeStyle = '#1b2838';
      line(ctx, tickX, rect.top, tickX, rect.bottom);
      const val = range[0] + (range[1] - range[0]) * i / steps;
      ctx.fillText(fmtAxisVal(val), clamp(px, rect.left + 26, rect.right - 26), 14, (rect.right - rect.left) / steps);
    }
    ctx.textAlign = 'right';
    const unit = axis === 'time' ? activeTimeScaleLabel() : (axisTitle(axis) + activeTimeScaleLabel());
    ctx.fillStyle = '#7b8fa6';
    ctx.fillText(clipped(unit, 16), rect.right - 4, 14, 100);
    ctx.textAlign = 'left';
  }

  function drawDigitalBusSpan(start, end, topY, botY, midY, color, label) {
    const w = end - start;
    const edgeW = Math.min(4, w / 3);
    ctx.fillStyle = color + '1e';
    ctx.strokeStyle = color;
    ctx.beginPath();
    if (w >= 8) {
      ctx.moveTo(start, midY);
      ctx.lineTo(start + edgeW, topY);
      ctx.lineTo(end - edgeW, topY);
      ctx.lineTo(end, midY);
      ctx.lineTo(end - edgeW, botY);
      ctx.lineTo(start + edgeW, botY);
      ctx.closePath();
    } else {
      ctx.rect(start, topY, Math.max(1, w), botY - topY);
    }
    ctx.fill();
    ctx.stroke();
    if (w > 18 && label) {
      ctx.fillStyle = '#e7edf5';
      ctx.font = '11px monospace';
      ctx.fillText(clipped(label, 64), start + edgeW + 4, midY + 4, Math.max(2, w - edgeW * 2 - 6));
    }
  }

  function drawClippedLine(x1, y1, x2, y2, left, right) {
    if (x1 > x2) {
      [x1, x2] = [x2, x1];
      [y1, y2] = [y2, y1];
    }
    if (x2 < left || x1 > right) return;
    if (x2 === x1) {
      if (x1 >= left && x1 <= right) line(ctx, x1, y1, x2, y2);
      return;
    }
    if (x1 < left) {
      y1 += ((left - x1) / (x2 - x1)) * (y2 - y1);
      x1 = left;
    }
    if (x2 > right) {
      y2 = y1 + ((right - x1) / (x2 - x1)) * (y2 - y1);
      x2 = right;
    }
    line(ctx, x1, y1, x2, y2);
  }

  function drawAnalogRow(row, spans, rect, y, color, mode) {
    const [loVal, hiVal] = ChartMath.analogRange(spans, row.lane, range, row.pref);
    const padTop = y + 3, padBot = y + row.height - 3;
    const toY = v => padBot - ((v - loVal) / (hiVal - loVal)) * (padBot - padTop);
    const xFn = v => spanX(rect, v);
    ctx.fillStyle = '#7388a0';
    ctx.font = '10px monospace';
    ctx.fillText(fmt(hiVal), rect.left + 3, padTop + 8, 60);
    ctx.fillText(fmt(loVal), rect.left + 3, padBot - 1, 60);

    let prevPy = null, prevSample = null, lastValidEnd = null;
    const startSpanIdx = ChartMath.firstSpanIndex(spans, range[0]);
    for (let sIdx = startSpanIdx; sIdx < spans.length; sIdx++) {
      const span = spans[sIdx];
      if (span.lo > range[1]) break;
      if (span.hi < range[0]) continue;
      const start = clamp(xFn(span.lo), rect.left, rect.right);
      const end = clamp(xFn(span.hi), rect.left, rect.right);
      if (span.dense) {
        if (prevSample && lastValidEnd !== null && lastValidEnd > prevSample.x) {
          ctx.strokeStyle = color;
          drawClippedLine(prevSample.x, prevSample.y, lastValidEnd, prevSample.y, rect.left, rect.right);
        }
        hatch(ctx, start, padTop, Math.max(1, end - start), padBot - padTop, 'rgba(85,192,206,.24)', '#66adb9');
        prevPy = null;
        prevSample = null;
        lastValidEnd = null;
        continue;
      }
      const num = ChartMath.analogNumeric(span.value, row.lane, row.pref);
      if (num === null) {
        if (prevSample && lastValidEnd !== null && lastValidEnd > prevSample.x) {
          ctx.strokeStyle = color;
          drawClippedLine(prevSample.x, prevSample.y, lastValidEnd, prevSample.y, rect.left, rect.right);
        }
        hatch(ctx, start, padTop, Math.max(1, end - start), padBot - padTop,
          span.isZ ? 'rgba(245,158,11,.12)' : 'rgba(239,68,68,.18)',
          span.isZ ? '#d97706' : '#ef4444');
        prevPy = null;
        prevSample = null;
        lastValidEnd = null;
        continue;
      }
      const py = clamp(toY(num), padTop, padBot);
      ctx.strokeStyle = color;
      if (mode === 'analog_step') {
        if (prevPy !== null) line(ctx, start, prevPy, start, py);
        line(ctx, start, py, end, py);
        prevPy = py;
      } else {
        const rawX = xFn(span.lo);
        if (prevSample !== null) {
          drawClippedLine(prevSample.x, prevSample.y, rawX, py, rect.left, rect.right);
        }
        prevSample = {x: rawX, y: py};
        lastValidEnd = end;
      }
    }
    if (mode !== 'analog_step' && prevSample !== null && lastValidEnd !== null && lastValidEnd > prevSample.x) {
      ctx.strokeStyle = color;
      drawClippedLine(prevSample.x, prevSample.y, lastValidEnd, prevSample.y, rect.left, rect.right);
    }
  }

  function drawWaveform(rect) {
    drawRuler(rect);
    const xFn = value => spanX(rect, value);
    const scrollTop = box ? box.scrollTop : 0;
    const topBase = rect.top - scrollTop;
    rendered.waves = [];
    ctx.save();
    ctx.beginPath();
    ctx.rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top);
    ctx.clip();

    const knownLo = clamp(xFn(data.range[0]), rect.left, rect.right);
    const knownHi = clamp(xFn(ChartMath.frontier(data)), rect.left, rect.right);

    activeRows.rows.forEach((row, idx) => {
      const y = topBase + row.y;
      if (y + row.height < rect.top || y >= rect.bottom) return;
      if (isGroupRow(row)) {
        ctx.fillStyle = '#142231';
        ctx.fillRect(rect.left, y, rect.right - rect.left, row.height);
        ctx.strokeStyle = '#293548';
        line(ctx, rect.left, y + row.height - .5, rect.right, y + row.height - .5);
        rendered.waves.push({row, lane: null, spans: [], visible: [y, y + row.height]});
        return;
      }
      const color = rowColor(row, idx);
      if (idx % 2 === 1) {
        ctx.fillStyle = 'rgba(24, 36, 50, 0.45)';
        ctx.fillRect(rect.left, y, rect.right - rect.left, row.height);
      }
      ctx.strokeStyle = '#1e2d3f';
      line(ctx, rect.left, y + row.height - .5, rect.right, y + row.height - .5);

      const key = row.key;
      if (!waveGeometry.has(key)) waveGeometry.set(key, ChartMath.waveSegments(data, row.lane));
      const spans = waveGeometry.get(key);
      rendered.waves.push({row, lane: row.lane, spans, visible: [Math.max(y, rect.top), Math.min(y + row.height, rect.bottom)]});

      hatch(ctx, rect.left, y + 3, knownLo - rect.left, row.height - 6, 'rgba(126,149,176,.12)', '#485665');
      hatch(ctx, knownHi, y + 3, rect.right - knownHi, row.height - 6, 'rgba(126,149,176,.12)', '#485665');

      const disp = row.pref?.display || 'digital';
      if (disp === 'analog_step' || disp === 'analog_linear') {
        drawAnalogRow(row, spans, rect, y, color, disp);
        return;
      }

      const topY = y + 4, botY = y + row.height - 4, midY = y + row.height / 2;
      let previousY = null;
      const startSpanIdx = ChartMath.firstSpanIndex(spans, range[0]);
      for (let sIdx = startSpanIdx; sIdx < spans.length; sIdx++) {
        const span = spans[sIdx];
        if (span.lo > range[1]) break;
        if (span.hi < range[0]) continue;
        const start = clamp(xFn(span.lo), rect.left, rect.right);
        const end = clamp(xFn(span.hi), rect.left, rect.right);
        const w = Math.max(1, end - start);
        if (span.dense) {
          hatch(ctx, start, topY, w, botY - topY,
            span.unknown ? 'rgba(239,68,68,.24)' : 'rgba(85,192,206,.25)',
            span.unknown ? '#ef4444' : '#66adb9');
          previousY = null;
        } else if (span.isX) {
          hatch(ctx, start, topY, w, botY - topY, 'rgba(239,68,68,.20)', '#ef4444');
          ctx.strokeStyle = '#ef4444';
          if (w > 2) ctx.strokeRect(start + .5, topY + .5, w - 1, botY - topY - 1);
          previousY = null;
        } else if (span.isZ) {
          ctx.strokeStyle = '#f59e0b';
          line(ctx, start, midY, end, midY);
          previousY = null;
        } else if (row.lane.type === 'bit') {
          const level = Number(span.value) ? topY : botY;
          ctx.strokeStyle = color;
          if (previousY !== null) line(ctx, start, previousY, start, level);
          line(ctx, start, level, end, level);
          if (Number(span.value) && w > 2) {
            ctx.fillStyle = color + '1a';
            ctx.fillRect(start, topY, w, botY - topY);
          }
          previousY = level;
        } else {
          drawDigitalBusSpan(start, end, topY, botY, midY, color, laneFormat(row.lane, span.value));
          previousY = null;
        }
      }
    });
    ctx.restore();
  }

  function grid(left, top, right, bottom, xRange, yRange, axisLabel, yLabel, xFormat = fmt) {
    ctx.strokeStyle = '#33475a';
    ctx.fillStyle = '#9aacc0';
    ctx.font = '11px monospace';
    ctx.textAlign = 'center';
    const xSteps = right - left < 220 ? 2 : 4;
    for (let i = 0; i <= xSteps; i++) {
      const x = left + (right - left) * i / xSteps;
      line(ctx, Math.floor(x) + .5, top, Math.floor(x) + .5, bottom);
      ctx.fillText(xFormat(xRange[0] + (xRange[1] - xRange[0]) * i / xSteps), x, bottom + 15, (right - left) / xSteps + 18);
    }
    ctx.fillText(axisLabel, (left + right) / 2, bottom + 31, right - left);
    if (yRange) {
      ctx.textAlign = 'right';
      const ySteps = bottom - top < 56 ? 1 : bottom - top < 96 ? 2 : 4;
      for (let i = 0; i <= ySteps; i++) {
        const y = top + (bottom - top) * i / ySteps;
        line(ctx, left, Math.floor(y) + .5, right, Math.floor(y) + .5);
        ctx.fillText(fmt(yRange[1] - (yRange[1] - yRange[0]) * i / ySteps), left - 5, y + 4, left - 7);
      }
      if (yLabel) {
        ctx.textAlign = 'left';
        ctx.fillText(yLabel, left, top - 6, right - left);
      }
    }
    ctx.textAlign = 'left';
  }

  function rangeOf(values) {
    if (!values.length) return [-1, 1];
    let min = values[0], max = min;
    for (const value of values) { min = Math.min(min, value); max = Math.max(max, value); }
    const pad = min === max ? Math.max(.5, Math.abs(min) * .05) : (max - min) * .05;
    return [min - pad, max + pad];
  }

  function localGrid(picks, x, y) {
    const cell = Math.floor(x / 18) + ',' + Math.floor(y / 18);
    if (!picks.has(cell)) picks.set(cell, []);
    return picks.get(cell);
  }

  function projection(rect, xRange, yRange, axisLabel, yLabel) {
    grid(rect.left, rect.top, rect.right, rect.bottom, xRange, yRange, axisLabel, yLabel);
    const x = value => rect.left + (rect.right - rect.left) * ((value - xRange[0]) / (xRange[1] - xRange[0]));
    const y = value => rect.bottom - (rect.bottom - rect.top) * ((value - yRange[0]) / (yRange[1] - yRange[0]));
    return {x, y};
  }

  function clippedPlot(rect, draw) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top);
    ctx.clip();
    draw();
    ctx.restore();
  }

  function marker(point, x, y, color, kind = 0) {
    ctx.strokeStyle = ctx.fillStyle = color;
    ctx.beginPath();
    if (kind === 1) {
      ctx.moveTo(x, y - 5);
      ctx.lineTo(x + 5, y + 4);
      ctx.lineTo(x - 5, y + 4);
      ctx.closePath();
      ctx.fill();
    } else if (kind === 2) ctx.strokeRect(x - 2, y - 2, 4, 4);
    else {
      ctx.arc(x, y, 2.5, 0, 2 * Math.PI);
      ctx.fill();
    }
    localGrid(rendered.picks, x, y).push({x, y, point});
  }

  function drawXY(rect, list) {
    const thresholds = (view.options.thresholds ?? []).filter(item => list.some(lane => lane.id === item.series_id));
    const series = list.map(lane => data.buckets ? (data.buckets[lane.id] ?? []).map(cell =>
      ChartMath.xyOverviewCell(cell, lane.id)) : [{points: (data.points ?? []).filter(point => point.series_id === lane.id), gap: false}]);
    const values = thresholds.map(item => item.value);
    series.forEach(lane => lane.forEach(cell => cell.points.forEach(point => {
      if (Number.isFinite(point.value) && point.x >= range[0] && point.x <= range[1]) values.push(point.value);
    })));
    const yRange = rangeOf(values), {x, y} = projection(rect, range, yRange, axisTitle(axis), list[0]?.unit);
    clippedPlot(rect, () => {
      for (const threshold of thresholds) {
        const level = y(threshold.value);
        ctx.strokeStyle = '#e2b36b';
        ctx.setLineDash([5, 4]);
        line(ctx, rect.left, level, rect.right, level);
        ctx.setLineDash([]);
        ctx.fillStyle = '#e2b36b';
        ctx.fillText(threshold.label || threshold.side + ' ' + threshold.series_id + ' ' + fmt(threshold.value),
          rect.left + 4, level - 4, rect.right - rect.left - 8);
      }
      const left = clamp(x(data.range[0]), rect.left, rect.right), right = clamp(x(ChartMath.frontier(data)), rect.left, rect.right);
      hatch(ctx, rect.left, rect.top, left - rect.left, rect.bottom - rect.top, 'rgba(126,149,176,.14)', '#485665');
      hatch(ctx, right, rect.top, rect.right - right, rect.bottom - rect.top, 'rgba(126,149,176,.14)', '#485665');
      series.forEach((cells, i) => {
        const color = SCI_COLORS[i % SCI_COLORS.length];
        const limits = thresholds.filter(t => t.series_id === list[i].id);
        let previous = null;
        for (const cell of cells) {
          if (cell.gap) previous = null;
          for (const point of cell.points) {
            if (!Number.isFinite(point.value)) { previous = null; continue; }
            const cx = x(point.x), cy = y(point.value);
            const outlier = limits.some(t => t.side === 'min' ? point.value < t.value : point.value > t.value);
            ctx.strokeStyle = color;
            if (previous && !cell.gap) line(ctx, previous[0], previous[1], cx, cy);
            if (cx >= rect.left && cx <= rect.right) {
              marker(point, cx, cy, color, outlier ? 1 : i % 2 ? 2 : 0);
              ChartControls.drawPicked(ctx, picked, point, view?.id, axis, cx, cy);
            }
            previous = cell.gap ? null : [cx, cy];
          }
          if (cell.violation) {
            const cx = x(cell.violation.x);
            if (cx >= rect.left && cx <= rect.right) {
              marker(cell.violation, cx, y(cell.violation.value), color, 1);
              ChartControls.drawPicked(ctx, picked, cell.violation, view?.id, axis, cx, y(cell.violation.value));
            }
          }
        }
      });
    });
  }

  function drawScatter(rect, list) {
    const points = data.points ?? [];
    let xs = rangeOf(points.map(point => point.x)), ys = rangeOf(points.map(point => point.y));
    if (view.options.equal_aspect) {
      const ratio = (rect.right - rect.left) / (rect.bottom - rect.top);
      const spanXVal = xs[1] - xs[0], spanY = ys[1] - ys[0];
      if (spanXVal / spanY < ratio) {
        const diff = (ratio * spanY - spanXVal) / 2;
        xs = [xs[0] - diff, xs[1] + diff];
      } else {
        const diff = (spanXVal / ratio - spanY) / 2;
        ys = [ys[0] - diff, ys[1] + diff];
      }
    }
    const {x, y} = projection(rect, xs, ys, axisTitle('time'), list[0]?.label);
    clippedPlot(rect, () => {
      for (const point of points) {
        const i = list.findIndex(item => item.id === point.series_id);
        if (i >= 0) {
          const px = x(point.x), py = y(point.y);
          marker(point, px, py, SCI_COLORS[i % SCI_COLORS.length], i % 2 ? 2 : 0);
          ChartControls.drawPicked(ctx, picked, point, view?.id, axis, px, py);
        }
      }
    });
  }

  function drawHistogram(rect, list) {
    const edges = data.edges ?? [], counts = data.bins ?? [];
    if (!counts.length) return;
    const only = edges[0] === edges.at(-1);
    const xs = only ? rangeOf([edges[0]]) : [edges[0], edges.at(-1)];
    const ys = [0, Math.max(1, ...counts)];
    const {x, y} = projection(rect, xs, ys, list[0]?.label || 'Value', 'Count');
    rendered.bins = counts.map((count, i) => {
      const start = x(only ? xs[0] : edges[i]), end = x(only ? xs[1] : edges[i + 1]);
      ctx.fillStyle = '#60b4c5';
      ctx.fillRect(start + 1, y(count), Math.max(1, end - start - 2), rect.bottom - y(count));
      return {start, end, count, lo: edges[i], hi: edges[i + 1], last: i === counts.length - 1};
    });
  }

  function putReadout(result) {
    const readout = node('chartReadout');
    if (!readout || view?.kind === 'waveform') return;
    const values = result?.values ?? [];
    const lines = [];
    if (Number.isFinite(result?.x)) lines.push([axisTitle(axis), fmt(result.x)]);
    lines.push(...values);
    if (result?.bin) {
      const b = result.bin;
      lines.push(['Bin', '[' + fmt(b.lo) + ', ' + fmt(b.hi) + (b.last ? ']' : ')') + ' · ' + b.count]);
    }
    if (data?.stats) {
      const s = data.stats;
      lines.push(['n', s.n], ['Min', fmt(s.min)], ['Max', fmt(s.max)], ['Mean', fmt(s.mean)],
        ['Std', fmt(s.std)], ['Underflow', data.underflow], ['Overflow', data.overflow], ['Missing', s.missing]);
    }
    if (!lines.length) lines.push(['', 'Point to a sample to inspect it.']);
    const key = JSON.stringify(lines);
    if (key === readout.dataset.values) return;
    readout.dataset.values = key;
    readout.replaceChildren(...lines.map(([name, value]) => {
      const row = make('div');
      row.append(make('dt', name), make('dd', String(value), {title: String(value)}));
      return row;
    }));
  }

  function paintBase() {
    if (!ctx || width < 60 || height < 60) return;
    ctx.setTransform(canvas.width / width, 0, 0, canvas.height / height, 0, 0);
    ctx.fillStyle = '#101924';
    ctx.fillRect(0, 0, width, height);
    const isWave = view?.kind === 'waveform';
    const list = view ? lanes() : [];
    const rect = isWave
      ? {left: 0, right: Math.max(20, width - 8), top: RULER_HEIGHT, bottom: height}
      : {left: 56, right: width - 18, top: 22, bottom: height - 39};
    rendered = {rect, picks: new Map(), waves: []};
    const spacer = node('chartPlotSpace');
    if (spacer) {
      const extra = isWave ? Math.max(0, activeRows.totalHeight - Math.max(0, rect.bottom - rect.top)) : 0;
      if (spacer.style.height !== extra + 'px') spacer.style.height = extra + 'px';
    }
    if (!data || !range || (!list.length && !activeRows.rows.length)) {
      putReadout(null);
      paintOverlay();
      return;
    }
    if (isWave) drawWaveform(rect);
    else if (view.kind === 'xy_line') drawXY(rect, list);
    else if (view.kind === 'scatter') drawScatter(rect, list);
    else drawHistogram(rect, list);
    paintOverlay();
  }

  function paintOverlay() {
    if (!rendered) return;
    const rect = rendered.rect;
    const isWave = view?.kind === 'waveform';
    if (!isWave) {
      const res = hover ? probeScientific(rect, hover.px, hover.py) : null;
      if (res && hover) hover.x = res.x;
      putReadout(res);
      return;
    }
    if (overlayCtx) {
      overlayCtx.setTransform(overlayCanvas.width / width, 0, 0, overlayCanvas.height / height, 0, 0);
      overlayCtx.clearRect(0, 0, width, height);
      if (data && range) {
        // Highlight selected lane row softly in waveform view.
        if (selectedRowKey) {
          const entry = (rendered.waves ?? []).find(w => w.row.key === selectedRowKey);
          if (entry) {
            overlayCtx.fillStyle = 'rgba(56, 189, 248, 0.08)';
            overlayCtx.fillRect(rect.left, entry.visible[0], rect.right - rect.left, entry.visible[1] - entry.visible[0]);
          }
        }
        // Shade A..B measurement band + dashed B cursor + delta badge.
        if (cursorA != null && cursorB != null) {
          const ax = spanX(rect, cursorA), bx = spanX(rect, cursorB);
          const xLo = clamp(Math.min(ax, bx), rect.left, rect.right);
          const xHi = clamp(Math.max(ax, bx), rect.left, rect.right);
          if (xHi > xLo) {
            overlayCtx.fillStyle = 'rgba(56, 189, 248, 0.09)';
            overlayCtx.fillRect(xLo, rect.top, xHi - xLo, rect.bottom - rect.top);
            if (xHi - xLo > 46) {
              const dtLabel = 'Δt=' + fmtAxisVal(cursorB - cursorA);
              overlayCtx.font = '11px monospace';
              const bw = Math.min(xHi - xLo - 4, overlayCtx.measureText(dtLabel).width + 10);
              const mx = (xLo + xHi) / 2;
              overlayCtx.fillStyle = 'rgba(13, 20, 29, 0.88)';
              overlayCtx.fillRect(mx - bw / 2, rect.top + 4, bw, 16);
              overlayCtx.strokeStyle = '#38bdf8';
              overlayCtx.strokeRect(mx - bw / 2 + .5, rect.top + 4.5, bw - 1, 15);
              overlayCtx.fillStyle = '#bae6fd';
              overlayCtx.textAlign = 'center';
              overlayCtx.fillText(dtLabel, mx, rect.top + 15, bw - 4);
              overlayCtx.textAlign = 'left';
            }
          }
        }
        const drawCursorLine = (xVal, col, dashed, tag) => {
          if (xVal == null || xVal < range[0] || xVal > range[1]) return;
          const cx = Math.floor(spanX(rect, xVal)) + .5;
          if (cx < rect.left || cx > rect.right) return;
          overlayCtx.strokeStyle = col;
          overlayCtx.lineWidth = 1.5;
          if (dashed) overlayCtx.setLineDash([5, 4]);
          line(overlayCtx, cx, rect.top, cx, rect.bottom);
          overlayCtx.setLineDash([]);
          overlayCtx.lineWidth = 1;
          overlayCtx.fillStyle = col;
          const label = tag + ' ' + fmtAxisVal(xVal);
          overlayCtx.font = '10px monospace';
          const tw = Math.min(90, overlayCtx.measureText(label).width + 8);
          const bx = clamp(cx - tw / 2, rect.left + 1, rect.right - tw - 1);
          overlayCtx.fillRect(bx, 3, tw, 16);
          overlayCtx.fillStyle = '#0d141d';
          overlayCtx.textAlign = 'center';
          overlayCtx.fillText(label, bx + tw / 2, 14, tw - 4);
          overlayCtx.textAlign = 'left';
        };
        drawCursorLine(cursorA, '#facc15', false, 'A');
        drawCursorLine(cursorB, '#38bdf8', true, 'B');

        // Hover hairline & snap target marker
        if (hover && hover.px >= rect.left && hover.px <= rect.right && hover.py >= rect.top && hover.py <= rect.bottom) {
          let hx = hover.px;
          let snappedPoint = false;
          if (snapEnabled && range[1] > range[0] && rect.right > rect.left) {
            const rawX = pxToX(rect, hover.px);
            const rowHit = (rendered.waves ?? []).find(w => w.lane && hover.py >= w.visible[0] && hover.py < w.visible[1]);
            if (rowHit) {
              const maxDx = (8 / (rect.right - rect.left)) * (range[1] - range[0]);
              const snapRes = ChartMath.snapToEdge(rowHit.spans, rawX, maxDx);
              if (snapRes.snapped) {
                hx = spanX(rect, snapRes.x);
                snappedPoint = true;
              }
            }
          }
          hover.x = pxToX(rect, hx);
          overlayCtx.strokeStyle = 'rgba(148, 163, 184, 0.7)';
          overlayCtx.setLineDash([3, 3]);
          line(overlayCtx, Math.floor(hx) + .5, rect.top, Math.floor(hx) + .5, rect.bottom);
          overlayCtx.setLineDash([]);
          if (snappedPoint) {
            overlayCtx.fillStyle = '#facc15';
            overlayCtx.beginPath();
            overlayCtx.arc(hx, hover.py, 3.5, 0, 2 * Math.PI);
            overlayCtx.fill();
          }
        }
        // Active Verdi 4-direction drag stroke / Zoom Box
        if (drag && drag.mode === 'zoombox' && drag.moved) {
          const stroke = ChartMath.classifyDragStroke(drag.curPx - drag.px, drag.curPy - drag.py);
          const strokeLabel = stroke === 'zoom_out' ? 'Zoom Out ↗' : stroke === 'zoom_fit' ? 'Fit All ↙' : 'Zoom Area';
          const badgeColor = stroke === 'zoom_out' ? '#fbbf24' : stroke === 'zoom_fit' ? '#22c55e' : '#38bdf8';
          if (stroke === 'zoom_in') {
            const bx1 = clamp(Math.min(drag.px, drag.curPx), rect.left, rect.right);
            const bx2 = clamp(Math.max(drag.px, drag.curPx), rect.left, rect.right);
            overlayCtx.fillStyle = 'rgba(56, 189, 248, 0.16)';
            overlayCtx.fillRect(bx1, rect.top, bx2 - bx1, rect.bottom - rect.top);
            overlayCtx.strokeStyle = '#38bdf8';
            overlayCtx.strokeRect(bx1 + .5, rect.top + .5, Math.max(1, bx2 - bx1 - 1), rect.bottom - rect.top - 1);
          } else {
            const sx1 = clamp(drag.px, rect.left, rect.right), sy1 = clamp(drag.py, rect.top, rect.bottom);
            const sx2 = clamp(drag.curPx, rect.left, rect.right), sy2 = clamp(drag.curPy, rect.top, rect.bottom);
            overlayCtx.strokeStyle = badgeColor;
            overlayCtx.lineWidth = 2;
            overlayCtx.setLineDash([5, 3]);
            line(overlayCtx, sx1, sy1, sx2, sy2);
            overlayCtx.setLineDash([]);
            overlayCtx.lineWidth = 1;
          }
          overlayCtx.font = '11px monospace';
          const bw = Math.min(rect.right - rect.left - 8, overlayCtx.measureText(strokeLabel).width + 12);
          const mx = clamp((drag.px + drag.curPx) / 2, rect.left + bw / 2 + 2, rect.right - bw / 2 - 2);
          const by = clamp(drag.py - 22, rect.top + 4, rect.bottom - 20);
          overlayCtx.fillStyle = 'rgba(13, 20, 29, 0.9)';
          overlayCtx.fillRect(mx - bw / 2, by, bw, 16);
          overlayCtx.strokeStyle = badgeColor;
          overlayCtx.strokeRect(mx - bw / 2 + .5, by + .5, bw - 1, 15);
          overlayCtx.fillStyle = badgeColor;
          overlayCtx.textAlign = 'center';
          overlayCtx.fillText(strokeLabel, mx, by + 12, bw - 6);
          overlayCtx.textAlign = 'left';
        }
      }
    }
    updateSignalPaneValues();
  }

  function probeScientific(rect, px, py) {
    if (!data || !rect || px < rect.left || px > rect.right || py < rect.top || py > rect.bottom) return null;
    const result = {px, py, values: []};
    if (view.kind === 'histogram') {
      result.bin = rendered.bins?.find(bin => px >= bin.start && (px < bin.end || bin.last && px <= bin.end));
    } else {
      let distance = 9;
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        for (const hit of localGrid(rendered.picks, px + 18 * dx, py + 18 * dy)) {
          const d = Math.hypot(hit.x - px, hit.y - py);
          if (d >= distance) continue;
          distance = d;
          result.point = hit.point;
        }
      }
      if (result.point) {
        const point = result.point, name = definitions.get(point.series_id)?.label || 'Sample';
        result.values.push([name, '(' + fmt(point.x) + ', ' + fmt(point.y ?? point.value) + ')']);
      }
    }
    return result;
  }

  function waveformHitAt(px, py) {
    if (!rendered || !range || !data) return null;
    const rect = rendered.rect;
    if (px < rect.left || px > rect.right || py < rect.top || py > rect.bottom) return null;
    let xVal = pxToX(rect, px);
    const waveHit = (rendered.waves ?? []).find(w => py >= w.visible[0] && py < w.visible[1]);
    let point = null;
    if (waveHit?.lane) {
      if (snapEnabled && rect.right > rect.left) {
        const maxDx = (8 / (rect.right - rect.left)) * (range[1] - range[0]);
        const snapRes = ChartMath.snapToEdge(waveHit.spans, xVal, maxDx);
        xVal = snapRes.x;
        point = snapRes.span?.point ?? null;
      } else {
        point = ChartMath.holdAt(waveHit.spans, xVal)?.point ?? null;
      }
    }
    return {x: xVal, row: waveHit?.row ?? null, lane: waveHit?.lane ?? null, point};
  }

  function handleWaveClick(px, py, shiftKey) {
    const hit = waveformHitAt(px, py);
    if (!hit) return;
    if (shiftKey) {
      cursorB = hit.x;
      cursorBAnchor = captureCursorAnchor(cursorB);
    } else {
      cursorA = hit.x;
      cursorAAnchor = captureCursorAnchor(cursorA, hit.point);
      if (hit.row) selectRow(hit.row);
      if (hit.point) {
        picked = {...hit.point, axis, lane: hit.lane ? laneKey(hit.lane) : null, view_id: view.id};
        setTarget(picked);
      }
    }
    updateCursorSummary();
    updateSignalPaneValues();
    scheduleOverlayPaint();
  }

  function handleScientificPick(px, py) {
    const res = probeScientific(rendered?.rect, px, py);
    if (!res?.point) return;
    picked = {...res.point, axis, lane: null, view_id: view.id};
    setTarget(picked);
    evidence(picked);
    scheduleBasePaint();
  }

  function position(event) {
    const rect = canvas.getBoundingClientRect();
    return {px: event.clientX - rect.left, py: event.clientY - rect.top};
  }

  function stepSelectedEdge(direction) {
    const currentRow = activeRows.rows.find(r => r.key === selectedRowKey && !isGroupRow(r)) ||
      activeRows.rows.find(r => !isGroupRow(r));
    if (!currentRow || !currentRow.lane || !data || !range) return;
    if (!waveGeometry.has(currentRow.key)) waveGeometry.set(currentRow.key, ChartMath.waveSegments(data, currentRow.lane));
    const spans = waveGeometry.get(currentRow.key);
    const cur = cursorA ?? (range[0] + range[1]) / 2;
    const eps = Math.max((range[1] - range[0]) * 1e-9, 1e-12);
    let hitSpan = null;
    if (direction > 0) {
      for (let i = 1; i < spans.length; i++) {
        if (spans[i].lo > cur + eps && (spans[i].value !== spans[i - 1].value || spans[i].dense)) {
          hitSpan = spans[i];
          break;
        }
      }
    } else {
      for (let i = spans.length - 1; i >= 1; i--) {
        if (spans[i].lo < cur - eps && (spans[i].value !== spans[i - 1].value || spans[i].dense)) {
          hitSpan = spans[i];
          break;
        }
      }
    }
    if (hitSpan) {
      cursorA = hitSpan.lo;
      cursorAAnchor = captureCursorAnchor(cursorA, hitSpan.point);
      if (hitSpan.point) {
        picked = {...hitSpan.point, axis, lane: laneKey(currentRow.lane), view_id: view.id};
        setTarget(picked);
      }
      if (cursorA < range[0] || cursorA > range[1]) {
        const span = range[1] - range[0];
        moveWindow(cursorA - span / 2, span, true);
      }
      updateCursorSummary();
      updateSignalPaneValues();
      scheduleOverlayPaint();
    } else {
      find(direction > 0 ? 'forward' : 'backward', false, {lane: currentRow.lane, op: 'any'});
    }
  }

  function handleWaveKeydown(event) {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
    if (typeof WORKSPACE_LAYOUT !== 'undefined' && WORKSPACE_LAYOUT !== 'charts') return;
    if (node('chartsView')?.hidden) return;
    if (typeof MODAL_STACK !== 'undefined' && MODAL_STACK.length) return;
    const key = event.key;
    if (key === 'Escape') {
      if (!node('chartFileModal')?.hidden) {
        closeFileModal(true);
        event.preventDefault?.();
        return;
      }
      if (!node('chartParamModal')?.hidden) {
        closeParamModal(true);
        event.preventDefault?.();
        return;
      }
      if (!node('chartContextMenu')?.hidden) {
        closeContextMenu(true);
        event.preventDefault?.();
        return;
      }
      if (!node('chartSignals')?.hidden) {
        setBrowserVisible(false, true);
        event.preventDefault?.();
        return;
      }
    }
    if (key === 'Tab' && node('chartContextMenu')?.hidden) {
      const modal = activeChartModal();
      if (modal && trapModalTab(modal, event)) return;
    }
    if (!node('chartFileModal')?.hidden || !node('chartParamModal')?.hidden || !node('chartContextMenu')?.hidden) return;
    const tag = event.target?.tagName;
    const inField = !!(event.target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])') ||
      tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT');
    const activeEl = typeof document !== 'undefined' ? document.activeElement : null;
    const focusEl = (activeEl && typeof document !== 'undefined' && activeEl !== document.body) ? activeEl : event.target;
    const inSignalRows = !!(event.target?.closest?.('#chartSignalRows') || focusEl?.closest?.('#chartSignalRows'));
    const inOutsideControl = !inSignalRows && !!(
      event.target?.closest?.('button,summary,a[href],[role="menuitem"],[role="treeitem"],[role="separator"],[role="scrollbar"],.chart-toolbar,.charts-host-strip,.wf-hscroll,.chart-footer,.chart-signals') ||
      tag === 'BUTTON' || tag === 'SUMMARY' || tag === 'A' ||
      focusEl?.closest?.('button,summary,a[href],[role="menuitem"],[role="treeitem"],[role="separator"],[role="scrollbar"],.chart-toolbar,.charts-host-strip,.wf-hscroll,.chart-footer,.chart-signals') ||
      focusEl?.tagName === 'BUTTON' || focusEl?.tagName === 'SUMMARY' || focusEl?.tagName === 'A'
    );
    if ((event.ctrlKey || event.metaKey) && !event.altKey) {
      if (inField || inOutsideControl || view?.kind !== 'waveform') return;
      const lk = String(event.key || '').toLowerCase();
      if (lk === 'x') {
        copySelectedRow(true);
        event.preventDefault?.();
      } else if (lk === 'c') {
        const selText = typeof window !== 'undefined' ? window.getSelection?.()?.toString?.() : '';
        if (!selText) {
          copySelectedRow(false);
          event.preventDefault?.();
        }
      } else if (lk === 'v' && signalClipboard) {
        pasteClipboardSignal();
        event.preventDefault?.();
      }
      return;
    }
    if (event.altKey) return;
    if (inField || inOutsideControl) return;
    if (key === 'Escape') {
      if (view?.kind === 'waveform' && cursorB != null) {
        cursorB = null;
        cursorBAnchor = null;
        updateCursorSummary();
        scheduleOverlayPaint();
        event.preventDefault?.();
      }
      return;
    }
    if (key === 'g' && view?.kind === 'waveform' && view?.series_count) {
      const aside = node('chartSignals');
      if (aside) {
        setBrowserVisible(aside.hidden, true);
        if (!aside.hidden) node('chartSignalSearch')?.focus?.();
        event.preventDefault?.();
      }
      return;
    }
    if (key === 'L' && detail) {
      status('Reloading waveform…', '');
      loadMeta(false, true);
      event.preventDefault?.();
      return;
    }
    if (!range || !rendered) return;
    const actions = ChartControls.windowActions(view?.kind, domain, range);
    if (event.shiftKey && (key === 'ArrowLeft' || key === 'ArrowRight') && actions.pan) {
      pan(key === 'ArrowLeft' ? -.25 : .25);
      event.preventDefault?.();
      return;
    }
    if (view?.kind !== 'waveform') {
      if ((key === '+' || key === '=') && actions.zoom) zoom(.55);
      else if (key === '-' && actions.zoom) zoom(1 / .55);
      else if (key === 'Home' && actions.reset) changeRange(domain.slice(), true);
      else if (key === 'l') restoreLastView();
      else return;
      event.preventDefault?.();
      return;
    }
    const inWaveSurface = focusEl === canvas || inSignalRows;
    if (key === 'f' || key === 'Home') {
      if (actions.reset) changeRange(domain.slice(), true);
    } else if (key === 'Z' || key === '+' || key === '=') {
      if (actions.zoom) zoom(.55, keyboardZoomRatio());
    } else if (key === 'z' || key === '-') {
      if (actions.zoom) zoom(1 / .55, keyboardZoomRatio());
    } else if (key === 'l') {
      restoreLastView();
    } else if (key === 'G') {
      insertDividerAbove(selectedRowKey);
    } else if (key === 'h') {
      toggleHierarchyNames();
    } else if (key === 'c') {
      cycleSelectedColor();
    } else if (key === 'n') {
      find('forward');
    } else if (key === 'N') {
      find('backward');
    } else if (key === 'ArrowRight') {
      stepSelectedEdge(1);
    } else if (key === 'ArrowLeft') {
      stepSelectedEdge(-1);
    } else if ((key === 'ArrowDown' || key === 'ArrowUp') && inWaveSurface) {
      const idx = activeRows.rows.findIndex(r => r.key === selectedRowKey);
      const nextIdx = idx < 0 ? 0 : clamp(idx + (key === 'ArrowDown' ? 1 : -1), 0, activeRows.rows.length - 1);
      const target = activeRows.rows[nextIdx];
      if (target) {
        selectRow(target);
        rowRefs.get(target.key)?.el.scrollIntoView?.({block: 'nearest'});
      }
    } else if ((key === 'Delete' || key === 'Backspace') && inWaveSurface) {
      const target = activeRows.rows.find(r => r.key === selectedRowKey);
      if (target) removeRow(target);
    } else return;
    event.preventDefault?.();
  }

  function setupResizer(resizerId, getStart, onDrag) {
    const handle = node(resizerId);
    if (!handle || typeof handle.addEventListener !== 'function') return;
    let active = null;
    handle.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      e.preventDefault();
      active = {id: e.pointerId, startX: e.clientX, val: getStart()};
      handle.classList?.add?.('dragging');
      try { handle.setPointerCapture?.(e.pointerId); } catch (_) {}
    });
    handle.addEventListener('pointermove', e => {
      if (!active || active.id !== e.pointerId) return;
      onDrag(active.val + (e.clientX - active.startX));
    });
    const end = e => {
      if (!active || (e && active.id !== e.pointerId)) return;
      const pid = active.id;
      active = null;
      handle.classList?.remove?.('dragging');
      try { handle.releasePointerCapture?.(pid); } catch (_) {}
    };
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }

  function setupSignalsDialogDragResize() {
    const dialog = node('chartSignalsDialog'), header = node('chartSignalsHeader');
    if (!dialog || !dialog.style) return;
    let dragOp = null;
    const startRect = () => {
      const r = dialog.getBoundingClientRect?.() || {left: 80, top: 60, width: 720, height: 470};
      const vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
      const w = clamp(Math.round(r.width || 720), 420, vw - 16);
      const h = clamp(Math.round(r.height || 470), 260, vh - 16);
      const left = clamp(Math.round(r.left || (vw - w) / 2), 8, Math.max(8, vw - w - 8));
      const top = clamp(Math.round(r.top || (vh - h) / 2), 8, Math.max(8, vh - h - 8));
      return {left, top, width: w, height: h, vw, vh};
    };
    const begin = (e, kind, dir = '') => {
      if (e.button !== 0) return;
      e.preventDefault();
      dragOp = {kind, dir, id: e.pointerId, startX: e.clientX, startY: e.clientY, rect: startRect()};
      try { (e.currentTarget || dialog).setPointerCapture?.(e.pointerId); } catch (_) {}
    };
    if (header && typeof header.addEventListener === 'function') {
      header.addEventListener('pointerdown', e => {
        if (e.target?.closest?.('button')) return;
        begin(e, 'move');
      });
      header.addEventListener('dblclick', e => {
        if (e.target?.closest?.('button')) return;
        dialog.style.left = dialog.style.top = dialog.style.width = dialog.style.height = '';
      });
    }
    const handles = dialog.querySelectorAll?.('[data-gs-resize]') || [];
    handles.forEach?.(h => {
      h.addEventListener?.('pointerdown', e => begin(e, 'resize', h.dataset?.gsResize || ''));
    });
    const onMove = e => {
      if (!dragOp || dragOp.id !== e.pointerId) return;
      const dx = e.clientX - dragOp.startX, dy = e.clientY - dragOp.startY, b = dragOp.rect;
      if (dragOp.kind === 'move') {
        dialog.style.left = clamp(b.left + dx, 6, Math.max(6, b.vw - b.width - 6)) + 'px';
        dialog.style.top = clamp(b.top + dy, 6, Math.max(6, b.vh - b.height - 6)) + 'px';
        return;
      }
      let l = b.left, t = b.top, w = b.width, h = b.height;
      const dir = dragOp.dir;
      if (dir.includes('e')) w = clamp(b.width + dx, 420, b.vw - l - 6);
      if (dir.includes('s')) h = clamp(b.height + dy, 260, b.vh - t - 6);
      if (dir.includes('w')) {
        const right = b.left + b.width;
        w = clamp(b.width - dx, 420, right - 6);
        l = right - w;
      }
      if (dir.includes('n')) {
        const bot = b.top + b.height;
        h = clamp(b.height - dy, 260, bot - 6);
        t = bot - h;
      }
      dialog.style.left = Math.round(l) + 'px';
      dialog.style.top = Math.round(t) + 'px';
      dialog.style.width = Math.round(w) + 'px';
      dialog.style.height = Math.round(h) + 'px';
    };
    const onEnd = e => {
      if (!dragOp || (e && dragOp.id !== e.pointerId)) return;
      dragOp = null;
    };
    document?.addEventListener?.('pointermove', onMove);
    document?.addEventListener?.('pointerup', onEnd);
    document?.addEventListener?.('pointercancel', onEnd);
  }

  function initialize() {
    if (ready) return;
    ready = true;
    box = node('chartPlot');
    canvas = node('chartCanvas');
    overlayCanvas = node('chartOverlayCanvas');
    ctx = canvas?.getContext?.('2d') || null;
    overlayCtx = overlayCanvas?.getContext?.('2d') || null;
    if (box && typeof ResizeObserver === 'function') new ResizeObserver(resize).observe(box);
    if (typeof WaveIcons !== 'undefined' && node('chartsView')) {
      WaveIcons.mount?.(node('chartsView'));
      if (node('chartColRatioBtn') && !node('chartColRatioBtn').querySelector?.('svg')) {
        mountWaveIcon(node('chartColRatioBtn'), 'arrows-left-right', 12);
      }
      if (node('chartHScrollLeft') && !node('chartHScrollLeft').querySelector?.('svg')) {
        mountWaveIcon(node('chartHScrollLeft'), 'chevron-left', 9);
      }
      if (node('chartHScrollRight') && !node('chartHScrollRight').querySelector?.('svg')) {
        mountWaveIcon(node('chartHScrollRight'), 'chevron-right', 9);
      }
    }
    setupSignalsDialogDragResize();

    node('chartViewSelect')?.addEventListener('change', event => {
      AnalysisWorkspace.context('hidden');
      chooseView(event.target.value);
    });
    node('chartAxis')?.addEventListener('change', event => setAxis(event.target.value, true));
    node('chartTimeUnit')?.addEventListener('change', event => {
      const pickedVal = event.target.value;
      if (pickedVal === 'custom') {
        openParamModal('Time / Axis Scale', bodyEl => {
          const inp = make('input', null, {
            type: 'text',
            className: 'mono',
            value: displayTimeUnit || '1ns',
            placeholder: 'e.g. 1ns, 10ps, 2.5ns, x4',
          });
          const rowEl = make('label', null, {className: 'wf-param-row'});
          rowEl.append(make('span', 'Scale (fs..s or xN)'), inp);
          const hintEl = make('div', 'Examples: 100ps, 2.5ns, x4', {className: 'wf-param-hint help-text'});
          const errEl = make('div', '', {className: 'wf-param-err error-text'});
          errEl.setAttribute('role', 'alert');
          inp.addEventListener('input', () => {
            inp.removeAttribute?.('aria-invalid');
            errEl.textContent = '';
          });
          bodyEl.append(rowEl, hintEl, errEl);
          return inp;
        }, () => {
          const bodyEl = node('chartParamBody');
          const inp = bodyEl?.querySelector?.('input');
          const errEl = bodyEl?.querySelector?.('.wf-param-err');
          const rawSpec = String(inp?.value || '').trim();
          if (!parseTimeScaleSpec(rawSpec)) {
            inp?.setAttribute?.('aria-invalid', 'true');
            if (errEl) errEl.textContent = 'Enter a valid scale (e.g. 1ns, 2.5ns, 100ps, x4).';
            return false;
          }
          displayTimeUnit = rawSpec;
          syncTimeUnitSelect(displayTimeUnit);
          updateCursorSummary();
          scheduleBasePaint();
          scheduleOverlayPaint();
          return true;
        });
        return;
      }
      displayTimeUnit = pickedVal;
      updateCursorSummary();
      scheduleBasePaint();
      scheduleOverlayPaint();
    });
    node('chartCutSignal')?.addEventListener('click', () => copySelectedRow(true));
    node('chartCopySignal')?.addEventListener('click', () => copySelectedRow(false));
    node('chartPasteSignal')?.addEventListener('click', () => pasteClipboardSignal());
    node('chartSaveRcBtn')?.addEventListener('click', event => saveRcDirectOrPrompt(!!event?.shiftKey));
    node('chartOpenRcBtn')?.addEventListener('click', () => openFileModal('open_rc'));
    node('chartExportVcdBtn')?.addEventListener('click', () => openFileModal('export_vcd'));
    node('chartRcFileInput')?.addEventListener('change', async event => {
      const file = event.target?.files?.[0];
      event.target.value = '';
      if (!file) return;
      const text = typeof file.text === 'function' ? await file.text() : '';
      if (text) applyRcText(text, '');
    });
    node('chartParamClose')?.addEventListener('click', () => closeParamModal(true));
    node('chartParamCancel')?.addEventListener('click', () => closeParamModal(true));
    node('chartParamApply')?.addEventListener('click', () => paramModalApplyFn?.());
    node('chartParamModal')?.addEventListener('click', event => {
      if (event.target === node('chartParamModal')) closeParamModal(true);
    });
    node('chartParamBody')?.addEventListener('keydown', event => {
      if (event.key === 'Enter' && event.target?.tagName === 'INPUT') {
        event.preventDefault?.();
        paramModalApplyFn?.();
      }
    });
    node('chartFileClose')?.addEventListener('click', () => closeFileModal(true));
    node('chartFileCancel')?.addEventListener('click', () => closeFileModal(true));
    node('chartFileConfirm')?.addEventListener('click', () => confirmFileModal());
    node('chartFileBack')?.addEventListener('click', () => {
      if (fileHistoryIndex <= 0) return;
      --fileHistoryIndex;
      loadFileBrowserDir(fileHistory[fileHistoryIndex], {historyNav: true});
    });
    node('chartFileForward')?.addEventListener('click', () => {
      if (fileHistoryIndex < 0 || fileHistoryIndex >= fileHistory.length - 1) return;
      ++fileHistoryIndex;
      loadFileBrowserDir(fileHistory[fileHistoryIndex], {historyNav: true});
    });
    node('chartFileUp')?.addEventListener('click', () => loadFileBrowserDir(node('chartFileUp')?.dataset?.parent || ''));
    node('chartFileRefresh')?.addEventListener('click', () => loadFileBrowserDir(lastRcDir, {historyNav: true}));
    node('chartFileGo')?.addEventListener('click', () => loadFileBrowserDir(node('chartFileDir')?.value?.trim() || ''));
    node('chartFileDir')?.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault?.();
        loadFileBrowserDir(event.target.value?.trim() || '');
      }
    });
    node('chartFileName')?.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault?.();
        confirmFileModal();
      }
    });
    node('chartFileLocalBtn')?.addEventListener('click', () => {
      closeFileModal(false);
      node('chartRcFileInput')?.click?.();
    });
    node('chartFileModal')?.addEventListener('click', event => {
      if (event.target === node('chartFileModal')) closeFileModal(true);
    });
    node('chartNewData')?.addEventListener('click', () => loadMeta(false, true));
    node('chartContinue')?.addEventListener('click', () => loadWindow(true));
    node('chartTarget')?.addEventListener('click', () => openPickedLog());
    node('chartSignalsToggle')?.addEventListener('click', () => {
      const aside = node('chartSignals');
      if (!aside) return;
      setBrowserVisible(aside.hidden, true);
      if (!aside.hidden) node('chartSignalSearch')?.focus();
    });
    node('chartCloseSignals')?.addEventListener('click', () => setBrowserVisible(false, true));
    node('chartSignals')?.addEventListener('click', event => {
      if (event.target === node('chartSignals')) setBrowserVisible(false, true);
    });
    node('chartScopeAll')?.addEventListener('click', () => {
      activePrefix = [];
      loadDirectory(false, false, {autoPage: useClientSignalFilter() ? 50 : 0});
    });
    node('chartScopeRecursive')?.addEventListener('click', () => {
      scopeRecursive = !scopeRecursive;
      const btn = node('chartScopeRecursive');
      if (btn) {
        btn.setAttribute('aria-pressed', String(scopeRecursive));
        btn.textContent = scopeRecursive ? 'Scope: Sub-tree' : 'Scope: Level Only';
      }
      renderDirectory();
    });
    node('chartSelectAllSignals')?.addEventListener('click', () => {
      if (view?.kind === 'histogram') return;
      const {items: listed} = filterSignals(directory);
      const inWave = new Set(selectedSignalIds());
      const addable = listed.filter(it => !inWave.has(it.id));
      const targets = addable.length ? addable : listed;
      const allSelected = targets.length && targets.every(it => pendingSignals.has(it.id));
      if (allSelected) {
        for (const it of targets) pendingSignals.delete(it.id);
      } else {
        for (const it of targets) pendingSignals.add(it.id);
      }
      renderDirectory();
    });
    node('chartAddSignals')?.addEventListener('click', () => addSignalsToWave([...pendingSignals]));
    const triggerSignalFilter = (debounce = true) => {
      cancel('catalog');
      clearTimeout(searchTimer);
      const rawQuery = node('chartSignalSearch')?.value || '';
      if (useClientSignalFilter()) {
        if (catalogBackendQuery === '' && !directoryCursor) {
          renderDirectory();
        } else if (catalogBackendQuery === '' && directoryCursor) {
          renderDirectory();
          if (rawQuery) {
            if (debounce) searchTimer = setTimeout(() => loadDirectory(true, false, {autoPage: 50}), 180);
            else loadDirectory(true, false, {autoPage: 50});
          }
        } else {
          const runReload = () => loadDirectory(false, false, {autoPage: rawQuery ? 50 : 0});
          if (debounce) searchTimer = setTimeout(runReload, 180);
          else runReload();
        }
      } else if (debounce) {
        searchTimer = setTimeout(() => loadDirectory(false), 180);
      } else {
        loadDirectory(false);
      }
    };
    node('chartSignalSearch')?.addEventListener('input', () => triggerSignalFilter(true));
    for (const optId of ['chartSignalCase', 'chartSignalWord', 'chartSignalRegex']) {
      node(optId)?.addEventListener('change', () => triggerSignalFilter(false));
    }
    node('chartMoreSignals')?.addEventListener('click', () => loadDirectory(true));
    node('chartResetSignals')?.addEventListener('click', () => {
      selected = view.series.defaults.length ? view.series.defaults.slice() :
        view.series.mode === 'long' ? [...definitions.keys()].slice(0, 8) : [];
      if (view.kind === 'histogram') selected = selected.slice(0, 1);
      settings = new Map();
      selectionChanged();
    });
    node('chartResetWindow')?.addEventListener('click', () => {
      if (!ChartControls.windowActions(view?.kind, domain, range).reset) return;
      changeRange(domain?.slice() ?? null, true);
    });
    node('chartLastView')?.addEventListener('click', () => restoreLastView());
    node('chartHierToggle')?.addEventListener('click', () => toggleHierarchyNames());
    node('chartCursorSummary')?.addEventListener('click', () => zoomToCursors());
    for (const [inputId, which] of [['chartCursorAInput', 'A'], ['chartCursorBInput', 'B']]) {
      const inp = node(inputId);
      inp?.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          e.preventDefault();
          jumpCursorInput(which, inp.value);
          inp.blur?.();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          updateCursorSummary();
          inp.blur?.();
        }
      });
      inp?.addEventListener('change', () => jumpCursorInput(which, inp.value));
    }
    node('chartZoomIn')?.addEventListener('click', () => zoom(.55));
    node('chartZoomOut')?.addEventListener('click', () => zoom(1 / .55));
    node('chartPanLeft')?.addEventListener('click', () => pan(-.25));
    node('chartPanRight')?.addEventListener('click', () => pan(.25));
    node('chartSnapToggle')?.addEventListener('click', () => {
      snapEnabled = !snapEnabled;
      node('chartSnapToggle').setAttribute('aria-pressed', String(snapEnabled));
      scheduleOverlayPaint();
    });
    node('chartAddDivider')?.addEventListener('click', () => insertDividerAbove(selectedRowKey));
    node('chartFindOp')?.addEventListener('change', event => {
      clearFind();
      if (node('chartFindValue')) node('chartFindValue').hidden = event.target.value !== 'eq';
    });
    node('chartFindValue')?.addEventListener('input', clearFind);
    node('chartFindValue')?.addEventListener('keydown', event => { if (event.key === 'Enter') find('forward'); });
    node('chartFindPrev')?.addEventListener('click', () => find('backward'));
    node('chartFindNext')?.addEventListener('click', () => find('forward'));
    node('chartContinueFind')?.addEventListener('click', () => find(null, true));

    // Keyboard navigation for Hierarchy Tree, Signal Rows, Context Menu, and File Browser List
    node('chartHierarchyTree')?.addEventListener('keydown', event => {
      const tree = node('chartHierarchyTree');
      const items = Array.from(tree?.querySelectorAll?.('[role="treeitem"]') || []);
      if (!items.length) return;
      const activeEl = typeof document !== 'undefined' ? document.activeElement : null;
      let idx = items.indexOf(activeEl);
      if (idx < 0) idx = items.indexOf(event.target?.closest?.('[role="treeitem"]'));
      if (idx < 0) return;
      const item = items[idx];
      const key = event.key;
      const focusItem = targetBtn => {
        if (!targetBtn) return;
        for (const b of items) b.tabIndex = -1;
        targetBtn.tabIndex = 0;
        targetBtn.focus?.({preventScroll: true});
        targetBtn.scrollIntoView?.({block: 'nearest'});
      };
      const refocusByScopeKey = scopeKey => {
        const rebuilt = Array.from(tree.querySelectorAll?.('[role="treeitem"]') || []);
        const match = rebuilt.find(b => b.dataset?.scopeKey === scopeKey) || rebuilt[0];
        if (match) {
          for (const b of rebuilt) b.tabIndex = -1;
          match.tabIndex = 0;
          match.focus?.({preventScroll: true});
        }
      };
      if (key === 'ArrowDown') {
        event.preventDefault?.();
        focusItem(items[Math.min(items.length - 1, idx + 1)]);
      } else if (key === 'ArrowUp') {
        event.preventDefault?.();
        focusItem(items[Math.max(0, idx - 1)]);
      } else if (key === 'Home') {
        event.preventDefault?.();
        focusItem(items[0]);
      } else if (key === 'End') {
        event.preventDefault?.();
        focusItem(items[items.length - 1]);
      } else if (key === 'ArrowRight') {
        event.preventDefault?.();
        const expanded = item.getAttribute?.('aria-expanded');
        const scopeKey = item.dataset?.scopeKey;
        if (expanded === 'false' && scopeKey) {
          collapsedScopes.delete(scopeKey);
          renderHierarchyTree();
          refocusByScopeKey(scopeKey);
        } else if (expanded === 'true' && items[idx + 1]) {
          focusItem(items[idx + 1]);
        }
      } else if (key === 'ArrowLeft') {
        event.preventDefault?.();
        const expanded = item.getAttribute?.('aria-expanded');
        const scopeKey = item.dataset?.scopeKey;
        if (expanded === 'true' && scopeKey) {
          collapsedScopes.add(scopeKey);
          renderHierarchyTree();
          refocusByScopeKey(scopeKey);
        } else if (scopeKey) {
          const parts = JSON.parse(scopeKey);
          if (parts.length > 1) {
            const parentKey = JSON.stringify(parts.slice(0, -1));
            focusItem(items.find(b => b.dataset?.scopeKey === parentKey));
          }
        }
      } else if (key === 'Enter' || key === ' ') {
        event.preventDefault?.();
        const scopeKey = item.dataset?.scopeKey;
        if (scopeKey) {
          activePrefix = JSON.parse(scopeKey);
          loadDirectory(false, false, {autoPage: useClientSignalFilter() ? 50 : 0});
        }
      }
    });

    node('chartSignalRows')?.addEventListener('keydown', event => {
      if (view?.kind !== 'waveform') return;
      if (!node('chartContextMenu')?.hidden || !node('chartParamModal')?.hidden || !node('chartFileModal')?.hidden) return;
      const row = activeRows.rows.find(r => r.key === selectedRowKey) || activeRows.rows[0];
      if (!row) return;
      if (event.altKey && !isGroupRow(row) && (event.key === 'ArrowUp' || event.key === 'ArrowDown' || event.key === 'Home')) {
        event.preventDefault?.();
        event.stopPropagation?.();
        if (event.key === 'Home') {
          const cur = {...settings.get(row.key)};
          delete cur.customHeight;
          delete cur.heightScale;
          if (Object.keys(cur).length) settings.set(row.key, cur);
          else settings.delete(row.key);
        } else {
          const delta = event.key === 'ArrowDown' ? 5 : -5;
          const nextH = clamp(Math.round((row.height || baseRowH()) + delta), 18, 180);
          settings.set(row.key, {...settings.get(row.key), customHeight: nextH});
        }
        saveProfile();
        renderLanes();
        return;
      }
      if ((event.key === 'Enter' || event.key === ' ') && isGroupRow(row)) {
        if (event.target?.classList?.contains?.('wf-group-caret')) return;
        event.preventDefault?.();
        event.stopPropagation?.();
        toggleGroupCollapse(row.group || row.divider);
        return;
      }
      if ((event.key === 'F10' && event.shiftKey) || event.key === 'ContextMenu') {
        event.preventDefault?.();
        event.stopPropagation?.();
        selectRow(row);
        const r = rowRefs.get(row.key)?.el?.getBoundingClientRect?.();
        openLaneContextMenu(r ? r.left + 28 : 40, r ? r.bottom - 2 : 40, row, null);
      }
    });

    node('chartContextMenu')?.addEventListener('keydown', event => {
      const menu = node('chartContextMenu');
      if (!menu || menu.hidden) return;
      const key = event.key;
      if (key === 'Escape') {
        event.preventDefault?.();
        event.stopPropagation?.();
        closeContextMenu(true);
        return;
      }
      if (key === 'Tab') {
        closeContextMenu(false);
        return;
      }
      const activeEl = typeof document !== 'undefined' ? document.activeElement : null;
      const targetEl = activeEl && menu.contains?.(activeEl) ? activeEl : event.target;
      const subEl = targetEl?.closest?.('.wf-ctx-submenu');
      const topLevelItems = () => {
        const out = [];
        for (const ch of menu.children || []) {
          if (ch.classList?.contains?.('wf-ctx-item') && !ch.disabled) out.push(ch);
          else if (ch.classList?.contains?.('wf-ctx-subwrap')) {
            const trig = ch.children?.[0];
            if (trig && !trig.disabled) out.push(trig);
          }
        }
        return out;
      };
      const items = subEl
        ? Array.from(subEl.children || []).filter(ch => ch.classList?.contains?.('wf-ctx-item') && !ch.disabled)
        : topLevelItems();
      if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Home' || key === 'End') {
        if (!items.length) return;
        event.preventDefault?.();
        event.stopPropagation?.();
        const idx = items.indexOf(targetEl);
        let next = 0;
        if (key === 'End') next = items.length - 1;
        else if (key === 'ArrowDown') next = idx < 0 ? 0 : (idx + 1) % items.length;
        else if (key === 'ArrowUp') next = idx <= 0 ? items.length - 1 : idx - 1;
        items[next]?.focus?.({preventScroll: true});
      } else if (key === 'ArrowRight' && !subEl) {
        const wrap = targetEl?.closest?.('.wf-ctx-subwrap');
        if (!wrap) return;
        event.preventDefault?.();
        event.stopPropagation?.();
        for (const other of menu.children || []) other.classList?.remove?.('open');
        wrap.classList?.add?.('open');
        syncSubmenuExpanded(menu);
        const sub = wrap.children?.[1];
        positionSubmenu(menu, targetEl, sub);
        const firstSub = Array.from(sub?.children || []).find(ch => ch.classList?.contains?.('wf-ctx-item') && !ch.disabled);
        firstSub?.focus?.({preventScroll: true});
      } else if (key === 'ArrowLeft' && subEl) {
        const wrap = subEl.closest?.('.wf-ctx-subwrap') || subEl.parentElement;
        if (!wrap) return;
        event.preventDefault?.();
        event.stopPropagation?.();
        wrap.classList?.remove?.('open');
        syncSubmenuExpanded(menu);
        wrap.children?.[0]?.focus?.({preventScroll: true});
      }
    });

    node('chartFileList')?.addEventListener('keydown', event => {
      const key = event.key;
      if (key === 'Backspace') {
        const parent = node('chartFileUp')?.dataset?.parent || '';
        if (parent && parent !== lastRcDir) {
          event.preventDefault?.();
          loadFileBrowserDir(parent);
        }
        return;
      }
      if (!fileEntries.length) return;
      if (key === 'ArrowDown') {
        event.preventDefault?.();
        selectChartFileRow(fileSelectedIndex < 0 ? 0 : fileSelectedIndex + 1, true);
      } else if (key === 'ArrowUp') {
        event.preventDefault?.();
        selectChartFileRow(fileSelectedIndex <= 0 ? 0 : fileSelectedIndex - 1, true);
      } else if (key === 'Home') {
        event.preventDefault?.();
        selectChartFileRow(0, true);
      } else if (key === 'End') {
        event.preventDefault?.();
        selectChartFileRow(fileEntries.length - 1, true);
      } else if (key === 'Enter') {
        const idxAttr = event.target?.dataset?.index;
        const idx = idxAttr !== undefined ? Number(idxAttr) : fileSelectedIndex;
        const ent = fileEntries[idx];
        if (!ent) return;
        event.preventDefault?.();
        if (ent.directory) loadFileBrowserDir(ent.path);
        else if (fileModalMode === 'open_rc') confirmFileModal();
        else {
          if (node('chartFileName')) node('chartFileName').value = ent.name;
          node('chartFileName')?.focus?.();
        }
      }
    });

    // Horizontal timeline scrollbar (scroll arrows + track click + draggable thumb + keyboard)
    node('chartHScrollLeft')?.addEventListener('click', () => pan(-.25));
    node('chartHScrollRight')?.addEventListener('click', () => pan(.25));
    const hTrack = node('chartHScrollTrack'), hThumb = node('chartHScrollThumb');
    if (hTrack && hThumb && typeof hThumb.addEventListener === 'function') {
      hThumb.setAttribute?.('role', 'scrollbar');
      hThumb.setAttribute?.('aria-controls', 'chartCanvas');
      hThumb.setAttribute?.('aria-label', 'Waveform horizontal scrollbar');
      hThumb.setAttribute?.('aria-valuemin', '0');
      hThumb.setAttribute?.('aria-valuemax', '100');
      hThumb.setAttribute?.('aria-valuenow', '0');
      hThumb.tabIndex = 0;
      hThumb.addEventListener('keydown', event => {
        if (!domain || !range || !ChartControls.windowActions(view?.kind, domain, range).pan) return;
        const span = range[1] - range[0];
        if (event.key === 'ArrowLeft') {
          event.preventDefault?.();
          pan(event.shiftKey ? -.25 : -.1);
        } else if (event.key === 'ArrowRight') {
          event.preventDefault?.();
          pan(event.shiftKey ? .25 : .1);
        } else if (event.key === 'Home') {
          event.preventDefault?.();
          moveWindow(domain[0], span);
        } else if (event.key === 'End') {
          event.preventDefault?.();
          moveWindow(domain[1] - span, span);
        }
      });
      let thumbDrag = null;
      hThumb.addEventListener('pointerdown', e => {
        if (e.button !== 0 || !domain || !range || !(domain[1] > domain[0])) return;
        e.preventDefault();
        e.stopPropagation();
        thumbDrag = {
          id: e.pointerId,
          startX: e.clientX,
          base: range.slice(),
          pushed: false,
          trackW: Math.max(20, (hTrack.getBoundingClientRect?.().width || 400) - (hThumb.getBoundingClientRect?.().width || 40)),
        };
        hThumb.classList?.add?.('dragging');
        try { hThumb.setPointerCapture?.(e.pointerId); } catch (_) {}
      });
      hThumb.addEventListener('pointermove', e => {
        if (!thumbDrag || thumbDrag.id !== e.pointerId || !domain) return;
        const total = domain[1] - domain[0];
        const span = thumbDrag.base[1] - thumbDrag.base[0];
        if (total <= span) return;
        const deltaFrac = (e.clientX - thumbDrag.startX) / thumbDrag.trackW;
        if (!thumbDrag.pushed) {
          pushViewHistory(thumbDrag.base);
          thumbDrag.pushed = true;
        }
        moveWindow(thumbDrag.base[0] + deltaFrac * (total - span), span, false, {skipHistory: true, immediate: false});
      });
      const stopThumb = e => {
        if (!thumbDrag || (e && thumbDrag.id !== e.pointerId)) return;
        const pid = thumbDrag.id;
        thumbDrag = null;
        hThumb.classList?.remove?.('dragging');
        try { hThumb.releasePointerCapture?.(pid); } catch (_) {}
        if (timer) {
          clearTimeout(timer);
          timer = 0;
          loadWindow();
        }
      };
      hThumb.addEventListener('pointerup', stopThumb);
      hThumb.addEventListener('pointercancel', stopThumb);
      hTrack.addEventListener('pointerdown', e => {
        if (e.target !== hTrack || !domain || !range) return;
        const tr = hTrack.getBoundingClientRect?.(), tb = hThumb.getBoundingClientRect?.();
        if (!tr || !tb) return;
        if (e.clientX < tb.left) pan(-.8);
        else if (e.clientX > tb.right) pan(.8);
      });
    }

    // Synchronize vertical scroll 1:1 between #chartPlot and #chartSignalRows
    const signalRowsEl = node('chartSignalRows');
    box?.addEventListener('scroll', () => {
      if (syncScrollLock) return;
      syncScrollLock = true;
      if (signalRowsEl) signalRowsEl.scrollTop = box.scrollTop;
      syncScrollLock = false;
      scheduleBasePaint();
    }, {passive: true});
    signalRowsEl?.addEventListener('scroll', () => {
      if (syncScrollLock) return;
      syncScrollLock = true;
      if (box) box.scrollTop = signalRowsEl.scrollTop;
      syncScrollLock = false;
      scheduleBasePaint();
    }, {passive: true});

    // Get Signals Hierarchy/Signals splitter + Signal Pane width resizer
    const browserSplit = node('chartBrowserSplit');
    const browserSplitter = node('chartBrowserSplitter');
    const applyBrowserSplitW = next => {
      if (!browserSplit) return;
      const w = clamp(Math.round(next), 130, 520);
      browserSplit.style.setProperty('--wf-hier-w', w + 'px');
      browserSplitter?.setAttribute?.('aria-valuenow', String(w));
    };
    if (browserSplitter) {
      browserSplitter.setAttribute?.('role', 'separator');
      browserSplitter.setAttribute?.('aria-orientation', 'vertical');
      browserSplitter.setAttribute?.('aria-valuemin', '130');
      browserSplitter.setAttribute?.('aria-valuemax', '520');
      browserSplitter.setAttribute?.('aria-valuenow', '250');
      browserSplitter.tabIndex = 0;
      browserSplitter.addEventListener?.('keydown', e => {
        if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(e.key)) return;
        e.preventDefault?.();
        const cur = parseFloat(browserSplit?.style?.getPropertyValue?.('--wf-hier-w') || '') ||
          node('chartHierarchyPane')?.getBoundingClientRect?.().width || 250;
        const step = e.shiftKey ? 32 : 16;
        applyBrowserSplitW(e.key === 'Home' ? 250 : cur + (e.key === 'ArrowRight' ? step : -step));
      });
    }
    setupResizer('chartBrowserSplitter',
      () => node('chartHierarchyPane')?.getBoundingClientRect().width || 250,
      applyBrowserSplitW);

    const viewRoot = node('chartsView');
    const paneResizer = node('chartSignalPaneResizer');
    const applyPaneW = next => {
      if (!viewRoot) return;
      const w = clamp(Math.round(next), 140, 520);
      viewRoot.style.setProperty('--wf-pane-w', w + 'px');
      paneResizer?.setAttribute?.('aria-valuenow', String(w));
      resize();
    };
    if (paneResizer) {
      paneResizer.setAttribute?.('role', 'separator');
      paneResizer.setAttribute?.('aria-orientation', 'vertical');
      paneResizer.setAttribute?.('aria-valuemin', '140');
      paneResizer.setAttribute?.('aria-valuemax', '520');
      paneResizer.setAttribute?.('aria-valuenow', '274');
      paneResizer.tabIndex = 0;
      paneResizer.addEventListener?.('keydown', e => {
        if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(e.key)) return;
        e.preventDefault?.();
        const cur = parseFloat(viewRoot?.style?.getPropertyValue?.('--wf-pane-w') || '') ||
          node('chartSignalPane')?.getBoundingClientRect?.().width || 274;
        const step = e.shiftKey ? 32 : 16;
        applyPaneW(e.key === 'Home' ? 274 : cur + (e.key === 'ArrowRight' ? step : -step));
      });
    }
    setupResizer('chartSignalPaneResizer',
      () => node('chartSignalPane')?.getBoundingClientRect().width || 274,
      applyPaneW);
    node('chartSignalPaneResizer')?.addEventListener('dblclick', autoFitSignalColumns);
    // Dedicated inverse-delta binding for name/val split inside Signal Pane
    const colHandle = node('chartNameColResizer');
    if (colHandle && typeof colHandle.addEventListener === 'function') {
      colHandle.setAttribute?.('role', 'separator');
      colHandle.setAttribute?.('aria-orientation', 'vertical');
      if (!colHandle.hasAttribute?.('aria-label')) colHandle.setAttribute?.('aria-label', 'Resize signal name and value columns');
      colHandle.setAttribute?.('aria-valuemin', '56');
      colHandle.setAttribute?.('aria-valuemax', '450');
      colHandle.setAttribute?.('aria-valuenow', '104');
      colHandle.tabIndex = 0;
      const applyColValW = nextVal => {
        if (!viewRoot) return;
        const paneW = node('chartSignalPane')?.getBoundingClientRect?.().width || 274;
        const maxVal = Math.max(70, paneW - 70);
        const clamped = clamp(Math.round(nextVal), 56, maxVal);
        viewRoot.style.setProperty('--wf-val-w', clamped + 'px');
        colHandle.setAttribute?.('aria-valuemax', String(Math.round(maxVal)));
        colHandle.setAttribute?.('aria-valuenow', String(clamped));
      };
      colHandle.addEventListener('keydown', e => {
        if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(e.key)) return;
        e.preventDefault?.();
        const valEl = viewRoot?.querySelector?.('.wf-col-val');
        const cur = parseFloat(viewRoot?.style?.getPropertyValue?.('--wf-val-w') || '') ||
          valEl?.getBoundingClientRect?.().width || 104;
        const step = e.shiftKey ? 32 : 16;
        applyColValW(e.key === 'Home' ? 104 : cur + (e.key === 'ArrowRight' ? -step : step));
      });
      let colDrag = null;
      colHandle.addEventListener('pointerdown', e => {
        if (e.button !== 0 || !viewRoot) return;
        e.preventDefault();
        const valEl = viewRoot.querySelector?.('.wf-col-val');
        colDrag = {id: e.pointerId, startX: e.clientX, valW: valEl?.getBoundingClientRect().width || 104};
        colHandle.classList?.add?.('dragging');
        try { colHandle.setPointerCapture?.(e.pointerId); } catch (_) {}
      });
      colHandle.addEventListener('pointermove', e => {
        if (!colDrag || colDrag.id !== e.pointerId || !viewRoot) return;
        applyColValW(colDrag.valW - (e.clientX - colDrag.startX));
      });
      const stopCol = e => {
        if (!colDrag || (e && colDrag.id !== e.pointerId)) return;
        const pid = colDrag.id;
        colDrag = null;
        colHandle.classList?.remove?.('dragging');
        try { colHandle.releasePointerCapture?.(pid); } catch (_) {}
      };
      colHandle.addEventListener('pointerup', stopCol);
      colHandle.addEventListener('pointercancel', stopCol);
      colHandle.addEventListener('dblclick', autoFitSignalColumns);
    }
    node('chartColRatioBtn')?.addEventListener('click', cycleColumnRatio);

    canvas?.addEventListener('wheel', event => {
      if (!event.cancelable) return;
      const rect = rendered?.rect, px = position(event).px;
      const units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1;
      const delta = clamp(event.deltaY * units, -1000, 1000);
      if (event.ctrlKey || (view?.kind !== 'waveform' && event.shiftKey)) {
        if (!ChartControls.windowActions(view?.kind, domain, range).zoom || !rect || px < rect.left || px > rect.right) return;
        event.preventDefault();
        const now = Date.now();
        const pushHistory = !lastWheelAt || now - lastWheelAt > 500;
        lastWheelAt = now;
        zoom(Math.exp(delta * .002), (px - rect.left) / (rect.right - rect.left), {immediate: false, skipHistory: !pushHistory});
      } else if (view?.kind === 'waveform' && event.shiftKey) {
        if (!ChartControls.windowActions(view?.kind, domain, range).pan || !rect || px < rect.left || px > rect.right) return;
        event.preventDefault();
        const now = Date.now();
        const pushHistory = !lastWheelAt || now - lastWheelAt > 500;
        lastWheelAt = now;
        pan((delta / Math.max(200, rect.right - rect.left)) * 0.65, {immediate: false, skipHistory: !pushHistory});
      }
    }, {passive: false});

    canvas?.addEventListener('mousedown', event => {
      if (event.button === 1) event.preventDefault();
    });
    canvas?.addEventListener('auxclick', event => {
      if (event.button === 1) event.preventDefault();
    });

    canvas?.addEventListener('pointerdown', event => {
      closeContextMenu();
      if (!range || !rendered) return;
      const p = position(event), rect = rendered.rect;
      if (p.px < rect.left || p.px > rect.right || p.py < rect.top || p.py > rect.bottom) return;
      let mode = null;
      if (view?.kind !== 'waveform') {
        if (event.button !== 0 && event.button !== 1) return;
        mode = 'pan';
      } else if (event.button === 1 || (event.button === 0 && event.altKey)) {
        mode = 'pan';
      } else if (event.button === 0 && event.shiftKey) {
        mode = 'cursorB';
      } else if (event.button === 0) {
        mode = 'zoombox';
      } else {
        return;
      }
      event.preventDefault();
      try { canvas.focus?.({preventScroll: true}); } catch (_) {}
      try { canvas.setPointerCapture?.(event.pointerId); } catch (_) {}
      drag = {
        id: event.pointerId,
        mode,
        button: event.button,
        px: p.px,
        py: p.py,
        curPx: p.px,
        curPy: p.py,
        base: range.slice(),
        moved: false,
        shiftKey: event.shiftKey,
        altKey: event.altKey,
        historyPushed: false,
      };
    });

    canvas?.addEventListener('pointermove', event => {
      const p = position(event);
      hover = p;
      if (drag && drag.id === event.pointerId) {
        drag.curPx = p.px;
        drag.curPy = p.py;
        if (Math.hypot(p.px - drag.px, p.py - drag.py) >= 6) drag.moved = true;
        if (drag.mode === 'cursorB' && view?.kind === 'waveform') {
          const hit = waveformHitAt(p.px, p.py);
          if (hit) {
            cursorB = hit.x;
            cursorBAnchor = captureCursorAnchor(cursorB);
            updateCursorSummary();
          }
        } else if (drag.moved && drag.mode === 'pan' && ChartControls.windowActions(view?.kind, domain, range).pan) {
          const span = drag.base[1] - drag.base[0], rect = rendered.rect;
          if (rect && rect.right > rect.left) {
            if (!drag.historyPushed) {
              pushViewHistory(drag.base);
              drag.historyPushed = true;
            }
            moveWindow(drag.base[0] - span * (p.px - drag.px) / (rect.right - rect.left), span, false, {skipHistory: true, immediate: false});
          }
        }
      }
      scheduleOverlayPaint();
    });

    canvas?.addEventListener('pointerup', event => {
      if (!drag || drag.id !== event.pointerId) return;
      const p = position(event), d = drag;
      drag = null;
      const rect = rendered?.rect;
      if (!d.moved) {
        if (view?.kind === 'waveform') {
          if (d.button === 1) handleWaveClick(p.px, p.py, true);
          else if (d.button === 0 && !d.altKey) handleWaveClick(p.px, p.py, event.shiftKey || d.shiftKey);
        } else if (d.button === 0 && !d.altKey) {
          handleScientificPick(p.px, p.py);
        }
      } else if (d.mode === 'pan') {
        if (timer) {
          clearTimeout(timer);
          timer = 0;
          loadWindow();
        }
      } else if (d.mode === 'cursorB' && view?.kind === 'waveform') {
        const hit = waveformHitAt(p.px, p.py);
        if (hit) {
          cursorB = hit.x;
          cursorBAnchor = captureCursorAnchor(cursorB);
          updateCursorSummary();
        }
      } else if (d.mode === 'zoombox' && rect && rect.right > rect.left) {
        const stroke = ChartMath.classifyDragStroke(p.px - d.px, p.py - d.py);
        const acts = ChartControls.windowActions(view?.kind, domain, range);
        if (stroke === 'zoom_out') {
          if (acts.zoom) zoom(1 / .55, (clamp(d.px, rect.left, rect.right) - rect.left) / (rect.right - rect.left));
        } else if (stroke === 'zoom_fit') {
          if (acts.reset) changeRange(domain.slice(), true);
        } else if (acts.zoom) {
          const x1 = pxToX(rect, clamp(d.px, rect.left, rect.right));
          const x2 = pxToX(rect, clamp(p.px, rect.left, rect.right));
          const lo = Math.min(x1, x2), hi = Math.max(x1, x2);
          if (hi > lo) moveWindow(lo, hi - lo);
        }
      }
      scheduleOverlayPaint();
    });

    canvas?.addEventListener('pointercancel', () => { drag = null; scheduleOverlayPaint(); });
    canvas?.addEventListener('lostpointercapture', () => { drag = null; });
    canvas?.addEventListener('pointerleave', () => { hover = null; scheduleOverlayPaint(); });
    canvas?.addEventListener('contextmenu', event => {
      if (view?.kind !== 'waveform' || !rendered) return;
      const p = position(event), hit = waveformHitAt(p.px, p.py);
      if (!hit?.row) return;
      event.preventDefault();
      selectRow(hit.row);
      openLaneContextMenu(event.clientX, event.clientY, hit.row, hit.point);
    });

    document?.addEventListener?.('keydown', handleWaveKeydown);
    document?.addEventListener?.('pointerdown', event => {
      const menu = node('chartContextMenu');
      if (menu && !menu.hidden && !menu.contains?.(event.target)) closeContextMenu();
    });
  }

  return {refresh, openPickedLog, hasEscapeTarget};
})();
