"""Local, offline Wave monitor backed by the shared JobManager core."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from rpkbin.job_manager import JobManager

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wave — Job Monitor</title>
<style>
:root {
  --bg0: #090d14;
  --bg1: #111827;
  --bg2: #1e293b;
  --bg3: #334155;
  --border: #293548;
  --text: #f1f5f9;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --accent-dim: rgba(56, 189, 248, 0.15);
  --green: #22c55e;
  --green-dim: rgba(34, 197, 94, 0.16);
  --amber: #f59e0b;
  --amber-dim: rgba(245, 158, 11, 0.18);
  --red: #ef4444;
  --red-dim: rgba(239, 68, 68, 0.18);
  --purple: #a855f7;
  --purple-dim: rgba(168, 85, 247, 0.18);
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-mono: "JetBrains Mono", "Cascadia Code", "Consolas", "DejaVu Sans Mono", monospace;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg0);
  color: var(--text);
  font-family: var(--font-sans);
  font-size: 12px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
[hidden] { display: none !important; }

/* Buttons & Inputs */
button, .btn {
  background: var(--bg2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 3px 7px;
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.3;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.1s, border-color 0.1s;
}
button:disabled { opacity: 0.38; cursor: not-allowed; }
button:not(:disabled):hover { background: var(--bg3); border-color: #475569; }
button.primary {
  background: rgba(56, 189, 248, 0.2);
  border-color: var(--accent);
  color: #e0f2fe;
}
button.primary:not(:disabled):hover { background: rgba(56, 189, 248, 0.32); }
button.success {
  background: var(--green-dim);
  border-color: var(--green);
  color: #dcfce7;
}
button.warn {
  background: var(--amber-dim);
  border-color: var(--amber);
  color: #fef3c7;
}
button.danger {
  background: var(--red-dim);
  border-color: var(--red);
  color: #fee2e2;
}
button.ghost {
  background: transparent;
  border-color: transparent;
  color: var(--muted);
}
button.ghost:not(:disabled):hover {
  background: var(--bg2);
  border-color: var(--border);
  color: var(--text);
}
input[type="text"], input[type="number"], select, textarea {
  background: var(--bg0);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 3px 6px;
  font-size: 11.5px;
  line-height: 1.3;
  font-family: inherit;
}
input.mono, textarea.mono { font-family: var(--font-mono); }
input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--accent);
}
label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  color: var(--muted);
  font-size: 11px;
}

/* Top Header (Global / Manager Scope) */
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  background: var(--bg1);
  border-bottom: 1px solid var(--border);
  gap: 8px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.brand-group {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.brand {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 12.5px;
  color: var(--accent);
  letter-spacing: 0.4px;
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  flex-shrink: 0;
}
.brand-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 7px var(--green);
  flex-shrink: 0;
}
.brand-dot.paused {
  background: var(--amber);
  box-shadow: 0 0 7px var(--amber);
}
.summary-pills {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: nowrap;
  overflow-x: auto;
  scrollbar-width: none;
  min-width: 0;
}
.summary-pills::-webkit-scrollbar { display: none; }
.pill {
  padding: 1px 6px;
  border-radius: 999px;
  font-family: var(--font-mono);
  font-size: 11px;
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  white-space: nowrap;
  flex-shrink: 0;
}
.pill.interactive { cursor: pointer; }
.pill.interactive:hover { border-color: var(--accent); }
.pill.run { border-color: rgba(56,189,248,0.5); color: var(--accent); }
.pill.wait { border-color: rgba(168,85,247,0.6); color: #d8b4fe; background: var(--purple-dim); }
.pill.done { border-color: rgba(34,197,94,0.5); color: #86efac; }
.pill.fail { border-color: rgba(239,68,68,0.5); color: #fca5a5; }
.pill.alert { border-color: rgba(245,158,11,0.6); color: #fcd34d; background: var(--amber-dim); cursor: pointer; }
.scope-tag {
  font-size: 10px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--muted);
  padding: 1px 5px;
  border-radius: 3px;
  background: rgba(148, 163, 184, 0.1);
  white-space: nowrap;
}

/* Status Strip: Workspace Info + Persistent Last Action Feedback */
.status-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 10px;
  background: #0d131f;
  border-bottom: 1px solid var(--border);
  font-size: 11px;
  min-height: 20px;
  flex-shrink: 0;
}
.page-status {
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  flex: 1;
}
.action-feedback {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 3px;
  background: rgba(56, 189, 248, 0.1);
  border: 1px solid rgba(56, 189, 248, 0.3);
  color: #bae6fd;
  max-width: 58%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.action-feedback.warn {
  background: var(--amber-dim);
  border-color: rgba(245, 158, 11, 0.45);
  color: #fde68a;
}
.action-feedback.error {
  background: var(--red-dim);
  border-color: rgba(239, 68, 68, 0.45);
  color: #fca5a5;
}
.action-feedback span {
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Main Views */
.view {
  display: none;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.view.active {
  display: flex;
  flex-direction: column;
}

/* Monitor Toolbar (Filters + Checked Batch Scope) */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 3px 10px;
  background: rgba(17, 24, 39, 0.85);
  border-bottom: 1px solid var(--border);
  gap: 4px 10px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.toolbar-group {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  min-width: 0;
}
.batch-group {
  background: rgba(30, 41, 59, 0.55);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 2px 5px;
}
#checkedCount {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text);
  padding: 0 3px;
}

/* Split Pane */
.split-pane {
  --list-width: 42%;
  --list-height: 36%;
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.split-pane.stacked {
  flex-direction: column;
}
.pane-left {
  flex: 0 0 var(--list-width);
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg0);
}
.split-pane.stacked .pane-left {
  flex: 0 0 var(--list-height);
  height: var(--list-height);
  max-height: var(--list-height);
  width: 100%;
}
.pane-right {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg0);
}
.pane-divider {
  flex: 0 0 7px;
  cursor: col-resize;
  touch-action: none;
  background: var(--bg1);
  border-inline: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}
.pane-divider::after {
  content: "⋮";
  color: var(--muted);
  font-size: 11px;
  line-height: 1;
  pointer-events: none;
}
.split-pane.stacked .pane-divider {
  flex: 0 0 7px;
  cursor: row-resize;
  border-inline: 0;
  border-block: 1px solid var(--border);
}
.split-pane.stacked .pane-divider::after {
  content: "⋯";
}
.pane-divider:hover, .pane-divider:focus-visible {
  background: var(--accent-dim);
  outline: 1px solid var(--accent);
}
body.resizing { user-select: none; cursor: col-resize; }
body.resizing-row { user-select: none; cursor: row-resize; }

/* Left Job Table */
.table-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
}
th, td {
  padding: 5px 7px;
  text-align: left;
  border-bottom: 1px solid rgba(41, 53, 72, 0.65);
  white-space: nowrap;
  vertical-align: middle;
}
th {
  position: sticky;
  top: 0;
  background: var(--bg1);
  color: var(--muted);
  font-weight: 600;
  z-index: 2;
  border-bottom: 1px solid var(--border);
}
tr.job-row { cursor: pointer; }
tr.job-row:hover { background: rgba(56, 189, 248, 0.06); }
tr.job-row.selected {
  background: rgba(56, 189, 248, 0.14);
}
tr.job-row.selected td:first-child {
  box-shadow: inset 3px 0 0 var(--accent);
}
.job-name {
  display: block;
  max-width: 170px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending-reason {
  display: block;
  max-width: 165px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.compact-state { display: none; }
.pane-left.narrow-table #jobTableBody td.col-state,
.pane-left.narrow-table #jobTableHead th.col-state {
  display: none;
}
.pane-left.narrow-table .compact-state {
  display: inline;
}
.pane-left.narrow-table .job-name {
  max-width: 118px;
}
.table-footer {
  padding: 2px 8px;
  background: var(--bg1);
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-shrink: 0;
  white-space: nowrap;
  overflow: hidden;
}
#tableInfo {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.table-hint {
  white-space: nowrap;
  flex-shrink: 0;
}
.pane-left.narrow-table .table-hint {
  display: none;
}

/* Status & Verdict Badges */
.status-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1.35;
}
.st-PENDING { background: rgba(148,163,184,0.16); color: #cbd5e1; }
.st-RUNNING { background: var(--accent-dim); color: var(--accent); border: 1px solid rgba(56,189,248,0.4); }
.st-WAITING_INPUT {
  background: var(--purple-dim);
  color: #e9d5ff;
  border: 1px solid var(--purple);
  animation: pulse-border 1.4s infinite;
}
.st-STOPPING { background: var(--amber-dim); color: #fde68a; }
.st-DONE { background: var(--green-dim); color: #86efac; }
.st-FAILED { background: var(--red-dim); color: #fca5a5; }
.st-CANCELLED { background: rgba(107,114,128,0.2); color: #9ca3af; }
@keyframes pulse-border {
  0%, 100% { box-shadow: 0 0 0 0 rgba(168, 85, 247, 0.45); }
  50% { box-shadow: 0 0 0 4px rgba(168, 85, 247, 0.0); }
}
.result-FAIL, .result-ERROR, .check-FAIL, .check-ERROR {
  color: #fca5a5;
  background: var(--red-dim);
  border: 1px solid rgba(239, 68, 68, 0.35);
}
.result-PASS, .check-OK {
  color: #86efac;
  background: var(--green-dim);
  border: 1px solid rgba(34, 197, 94, 0.35);
}
.result-INCOMPLETE, .check-INCOMPLETE {
  color: #fde68a;
  background: var(--amber-dim);
  border: 1px solid rgba(245, 158, 11, 0.35);
}
.result-NA, .result--, .check-- {
  color: var(--muted);
  background: rgba(148, 163, 184, 0.12);
}
.error-badge-btn {
  padding: 1px 5px;
  font-size: 10.5px;
  font-family: var(--font-mono);
  background: var(--red-dim);
  border: 1px solid var(--red);
  color: #fecaca;
  border-radius: 4px;
  margin-left: 3px;
}
.warning-count {
  padding: 1px 6px;
  font-family: var(--font-mono);
  font-size: 10.5px;
}

/* Right Pane Header & Tabs */
.job-detail-header {
  padding: 3px 8px;
  flex-shrink: 0;
  background: var(--bg1);
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.job-detail-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  flex-wrap: nowrap;
  min-width: 0;
}
.job-heading {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  flex: 1;
  overflow: hidden;
}
.job-heading strong {
  font-size: 12px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 0 1 auto;
  min-width: 75px;
}
#detailMeta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 auto;
  min-width: 0;
}
.detail-toolbar {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
}
.detail-toolbar::-webkit-scrollbar { display: none; }
.detail-tabs {
  display: inline-flex;
  gap: 2px;
  background: var(--bg0);
  padding: 1px;
  border-radius: 5px;
  border: 1px solid var(--border);
  flex-shrink: 0;
}
.detail-tabs button {
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  padding: 2px 7px;
  font-weight: 600;
}
.detail-tabs button[aria-selected="true"] {
  color: var(--accent);
  border-color: rgba(56, 189, 248, 0.45);
  background: var(--accent-dim);
}
.single-actions {
  display: flex;
  align-items: center;
  gap: 3px;
  flex-wrap: nowrap;
  flex-shrink: 0;
}
#attemptSelect {
  max-width: 150px;
  min-width: 90px;
  font-family: var(--font-mono);
  font-size: 11px;
  flex-shrink: 0;
}

/* Log View & Details View */
.log-view {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.details-view {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.detail-card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 7px 9px;
}
.detail-card-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--muted);
  margin-bottom: 4px;
}
.job-cmd-line {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: #e2e8f0;
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 5px 7px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin-bottom: 5px;
}
.job-facts {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
#priorityEditor {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.diagnostics {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.diagnostics details {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 5px 9px;
}
.diagnostics details.danger-box {
  border-color: rgba(239, 68, 68, 0.5);
  background: rgba(239, 68, 68, 0.06);
}
.diagnostics details.warn-box {
  border-color: rgba(245, 158, 11, 0.45);
}
.diagnostics summary {
  cursor: pointer;
  padding: 2px 0;
  font-weight: 600;
  font-size: 11.5px;
  color: var(--text);
}
.evidence {
  display: block;
  width: 100%;
  text-align: left;
  padding: 3px 6px;
  margin: 3px 0;
  font-family: var(--font-mono);
  font-size: 11px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--bg0);
}
.check-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  padding: 3px 0;
  border-bottom: 1px solid rgba(41, 53, 72, 0.4);
}
.check-row:last-child { border-bottom: 0; }
.check-row strong { flex: 1; min-width: 90px; overflow-wrap: anywhere; }

/* Log Toolbar & Scrollable Log Box */
.log-toolbar {
  padding: 2px 8px;
  flex-shrink: 0;
  flex-wrap: nowrap;
  background: #0d131f;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  min-width: 0;
}
.log-toolbar .toolbar-group {
  flex-wrap: nowrap;
  min-width: 0;
}
#logCountInfo {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.log-box {
  overflow-anchor: none; /* renderLogs preserves the reader's visible row. */
  flex: 1;
  min-height: 80px;
  overflow: auto;
  padding: 6px 8px;
  font-family: var(--font-mono);
  font-size: var(--log-font-size, 11.5px);
  line-height: 1.42;
  background: #06090e;
}
.log-empty {
  color: var(--muted);
  padding: 12px 6px;
  font-family: var(--font-sans);
  font-size: 11.5px;
}
.log-line {
  display: flex;
  gap: 8px;
  padding: 1px 4px;
  border-radius: 3px;
  white-space: var(--log-white-space, pre-wrap);
  word-break: var(--log-word-break, break-all);
}
.log-line:hover { background: rgba(255,255,255,0.04); }
.log-line.highlight {
  background: rgba(245, 158, 11, 0.28);
  border-left: 3px solid var(--amber);
}
.log-line.wave {
  color: #bae6fd;
  background: rgba(56, 189, 248, 0.06);
  border-left: 2px solid var(--accent);
}
.log-lineno {
  color: #475569;
  user-select: none;
  min-width: 36px;
  text-align: right;
  flex-shrink: 0;
}
.log-line.wave .log-lineno {
  color: var(--accent);
  font-weight: 600;
}

/* Interactive Command Bar (Selected Job Scope) */
.console-footer {
  padding: 3px 8px;
  flex-shrink: 0;
  background: var(--bg1);
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stdin-row {
  display: flex;
  align-items: center;
  gap: 5px;
}
.stdin-row input { flex: 1; min-width: 0; }
#stdinTarget {
  max-width: 135px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  color: var(--accent);
}
.stdin-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 10.5px;
  color: var(--muted);
}
#stdinFeedback {
  font-family: var(--font-mono);
  color: #93c5fd;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Notices & Overlays */
.connection-notice {
  padding: 4px 10px;
  color: #fcd34d;
  background: var(--amber-dim);
  border-bottom: 1px solid var(--amber);
  font-weight: 600;
  flex-shrink: 0;
}
.history-notice, .batch-input {
  padding: 4px 10px;
  background: var(--amber-dim);
  border-bottom: 1px solid rgba(245, 158, 11, 0.45);
  color: #fde68a;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.batch-input {
  flex-direction: column;
  align-items: stretch;
  gap: 4px;
}
.help-text { color: var(--muted); font-size: 11px; line-height: 1.35; }
.error-text { color: #fca5a5; white-space: pre-wrap; }
.sort-button { border: 0; padding: 0; font: inherit; color: inherit; background: transparent; cursor: pointer; }

/* Panels & Modals */
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
}
.card-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--accent);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
}
.form-field label {
  font-size: 11px;
  color: var(--muted);
  font-weight: 600;
}
.overlay {
  position: fixed;
  inset: 0;
  background: #0009;
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal {
  width: min(680px, 94vw);
  max-height: 88vh;
  overflow: auto;
  margin: 0;
}
.settings-modal {
  width: min(760px, 95vw);
  max-height: 90vh;
}
.settings-nav {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.settings-nav button.active {
  background: var(--accent-dim);
  border-color: var(--accent);
  color: #e0f2fe;
}
.settings-section {
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 9px 11px;
  margin-bottom: 10px;
}
.settings-section.focused {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.3);
}
.settings-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.settings-section-title {
  font-weight: 600;
  font-size: 12px;
  color: var(--text);
}
.settings-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.settings-subbox {
  background: rgba(17, 24, 39, 0.7);
  border: 1px solid rgba(41, 53, 72, 0.75);
  border-radius: 4px;
  padding: 7px 9px;
  margin-bottom: 7px;
}
.columns-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(125px, 1fr));
  gap: 4px 10px;
  margin: 6px 0;
}
.path-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.path-row input { min-width: 160px; flex: 1; }
.file-list { max-height: 42vh; overflow: auto; margin: 8px 0; }
.file-list button { display: block; width: 100%; text-align: left; margin-bottom: 3px; }
.toast {
  position: fixed;
  bottom: 54px;
  right: 12px;
  background: var(--bg2);
  border: 1px solid var(--accent);
  color: var(--text);
  padding: 5px 10px;
  border-radius: 5px;
  font-size: 11px;
  z-index: 100;
  display: none;
  pointer-events: none;
  box-shadow: 0 6px 20px #0009;
}

/* Responsive: Medium / Side-by-Side Window (e.g. 736x540) */
@media (max-width: 960px) {
  .topbar { padding: 3px 8px; gap: 3px 8px; }
  .brand-group { flex: 1 1 100%; }
  .toolbar { padding: 3px 8px; gap: 3px 6px; }
  .scope-tag { display: none; }
  #jobTableBody td, #jobTableHead th { padding: 4px 5px; }
  .job-name { max-width: 115px; }
  #jobTableBody td.col-state, #jobTableHead th.col-state { display: none; }
  .compact-state { display: inline; }
  .job-attempt { max-width: 125px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #jobSearch { width: 130px !important; }
  .stdin-meta .help-text { display: none; }
  .meta-pid { display: none; }
}

/* Responsive: Compact / Low-Height Window (e.g. 600x450) */
@media (max-width: 660px), (max-height: 490px) {
  body { font-size: 11px; }
  button, .btn, input[type="text"], input[type="number"], select {
    padding: 2px 5px;
    font-size: 11px;
  }
  .topbar { padding: 2px 6px; gap: 2px 6px; }
  .brand { font-size: 11.5px; }
  .status-strip { padding: 1px 6px; min-height: 18px; font-size: 10px; }
  .toolbar { padding: 2px 6px; gap: 2px 4px; }
  .toolbar-group { gap: 3px; }
  .batch-group { padding: 1px 4px; }
  #jobSearch { width: 96px !important; }
  .job-detail-header { padding: 2px 6px; gap: 2px; }
  .job-heading strong { font-size: 11px; min-width: 65px; }
  #attemptSelect { max-width: 110px; min-width: 78px; }
  .detail-tabs button { padding: 1px 5px; }
  .log-toolbar { padding: 2px 6px; }
  #logFilterInput { width: 100px !important; }
  .log-box { padding: 4px 6px; font-size: var(--log-font-size, 11px); line-height: 1.34; }
  .console-footer { padding: 2px 6px; gap: 1px; }
  #stdinTarget { max-width: 85px; }
  .table-footer { padding: 2px 6px; }
}
</style></head><body>
<div class="topbar">
  <div class="brand-group">
    <div class="brand"><span id="statusDot" class="brand-dot"></span><span>WAVE</span></div>
    <div class="summary-pills" id="summaryPills"></div>
  </div>
  <div class="toolbar-group">
    <span class="scope-tag" title="Controls for the entire JobManager queue">Queue / All</span>
    <button onclick="openWorkFile()" title="Load a Python work file, preview its jobs, then press Start to dispatch">Load Work File…</button>
    <button id="btnStart" class="success" onclick="managerAction('start')">Start</button>
    <button id="btnPauseResume" onclick="managerAction(STATE.paused?'resume':'pause')" title="Pause dispatch only; running jobs continue">Pause Queue</button>
    <button id="btnStopAll" class="warn" onclick="allAction('stop')" title="Cancel pending jobs and stop active jobs using their configured policy">Stop All</button>
    <button id="btnForceAll" class="danger" onclick="allAction('force_stop')" title="Immediately force-stop all active jobs">Force Stop All</button>
    <button id="btnSettings" onclick="openSettings('all')" title="Open Settings: Execution limits, memory retention policies, and display preferences" disabled>⚙ Settings</button>
  </div>
</div>
<div class="connection-notice" id="connectionNotice" role="status">Connecting to Wave…</div>
<div class="status-strip">
  <div class="page-status" id="workspaceInfo"></div>
  <div class="action-feedback" id="lastActionBar" hidden>
    <span id="lastActionText"></span>
    <button id="btnLastReport" class="ghost" style="padding:0 4px;font-size:10.5px" onclick="el('reportOverlay').hidden=false" hidden>Report</button>
  </div>
</div>
<div class="view active" id="view-monitor">
  <div class="toolbar">
    <div class="toolbar-group" aria-label="Job filters">
      <label>State <select id="stateFilter" onchange="renderMonitor()"><option value="">All</option><option>PENDING</option><option>RUNNING</option><option value="WAITING_INPUT">PROMPT DETECTED</option><option>STOPPING</option><option value="DONE">FINISHED</option><option>FAILED</option><option>CANCELLED</option></select></label>
      <label>Result <select id="resultFilter" onchange="renderMonitor()"><option value="">All</option><option value="-">Undecided</option><option>PASS</option><option>FAIL</option><option>ERROR</option><option>INCOMPLETE</option><option>N/A</option></select></label>
      <label><input type="checkbox" id="warningFilter" onchange="renderMonitor()"> Warn</label>
      <input type="text" id="jobSearch" aria-label="Search jobs, checks and causes" placeholder="Search jobs, checks, causes…" oninput="renderMonitor()" style="width:155px">
      <button onclick="clearFilters()" title="Clear all filters">Clear</button>
    </div>
    <div class="toolbar-group batch-group" aria-label="Checked jobs batch controls">
      <span id="checkedCount" class="help-text" title="Batch actions strictly target checked jobs and never fall back to the selected row">0 checked</span>
      <button class="warn" data-batch-action="stop" onclick="batchJobAction('stop')">Stop</button>
      <button class="danger" data-batch-action="force_stop" onclick="batchJobAction('force_stop')">Force Stop</button>
      <button data-batch-action="rerun" onclick="batchJobAction('rerun')">Rerun</button>
      <button id="btnBroadcast" onclick="toggleBroadcast()">Send to Checked…</button>
      <button id="btnLayoutMode" onclick="toggleSplitOrientation()" title="Switch between Left/Right split and Top/Bottom stacked layout">◫ Split</button>
    </div>
  </div>
  <div class="batch-input" id="broadcastPanel" hidden>
    <div id="broadcastTargets" class="help-text" style="color:#fde68a"></div>
    <div class="stdin-row">
      <input id="broadcastText" type="text" class="mono" aria-label="Broadcast text" placeholder="Text for checked running jobs…" onkeydown="if(event.key==='Enter')broadcastSend()">
      <button id="btnBroadcastSend" class="primary" onclick="broadcastSend()">Send</button>
      <button onclick="toggleBroadcast(false)">Close</button>
    </div>
    <span class="help-text">Transport only. Rejected or skipped targets are reported separately and input is retained on rejection.</span>
  </div>
  <div class="split-pane" id="monitorSplit">
    <div class="pane-left" id="paneLeft">
      <div class="table-wrap">
        <table aria-label="Jobs">
          <thead id="jobTableHead" title="Click column to sort · Right-click header for Settings"></thead>
          <tbody id="jobTableBody"></tbody>
        </table>
      </div>
      <div class="table-footer">
        <div id="tableInfo" class="help-text"></div>
        <span class="help-text table-hint" title="Single-click a job for Log; double-click or right-click for Details">Click: Log · Dbl/Right-click: Details</span>
      </div>
    </div>
    <div id="paneDivider" class="pane-divider" role="separator" aria-label="Resize job list and log (drag, arrow keys, or double-click to reset)" aria-orientation="vertical" aria-valuemin="20" aria-valuemax="80" aria-valuenow="42" tabindex="0"></div>
    <div class="pane-right">
      <div class="job-detail-header">
        <div class="job-detail-top">
          <div class="job-heading">
            <strong id="detailTitle">Select a job</strong>
            <span id="detailMeta" class="help-text"></span>
          </div>
          <select id="attemptSelect" aria-label="Attempt to inspect" onchange="selectAttempt(this.value)"></select>
        </div>
        <div class="detail-toolbar">
          <div class="detail-tabs" role="tablist" aria-label="Job view">
            <button id="btnLogTab" role="tab" aria-selected="true" aria-controls="logView" onclick="setDetailView('log')">Log</button>
            <button id="btnDetailsTab" role="tab" aria-selected="false" aria-controls="detailsView" onclick="setDetailView('details')">Details</button>
          </div>
          <div class="single-actions" aria-label="Selected job controls">
            <span class="scope-tag" title="Applies only to the currently displayed attempt of the selected job">Selected</span>
            <button class="warn" data-single-action="stop" onclick="singleJobAction('stop')">Stop</button>
            <button class="danger" data-single-action="force_stop" onclick="singleJobAction('force_stop')">Force Stop</button>
            <button data-single-action="rerun" onclick="singleJobAction('rerun')">Rerun</button>
          </div>
        </div>
      </div>
      <div class="history-notice" id="historyNotice" hidden>
        <span id="historyNoticeText">Viewing historical attempt — read-only. Controls are disabled so actions cannot affect the current run.</span>
        <button class="primary" onclick="selectAttempt('')">Back to Current</button>
      </div>
      <div id="detailsView" class="details-view" role="tabpanel" aria-labelledby="btnDetailsTab" hidden>
        <div class="detail-card">
          <div class="detail-card-title">Command &amp; Dispatch Policy</div>
          <div id="detailCmd" class="job-cmd-line"></div>
          <div id="detailFacts" class="job-facts"></div>
          <div id="priorityEditor" hidden>
            <label for="priorityValue" style="color:var(--text);font-weight:600">Pending priority</label>
            <input type="number" id="priorityValue" step="1" style="width:72px" onkeydown="if(event.key==='Enter')savePriority()">
            <button id="btnApplyPriority" class="primary" onclick="savePriority()">Apply</button>
            <span class="help-text">Higher priority dispatches first among eligible pending jobs.</span>
          </div>
        </div>
        <div class="diagnostics" id="diagnostics"></div>
      </div>
      <div id="logView" class="log-view" role="tabpanel" aria-labelledby="btnLogTab">
        <div class="log-toolbar">
          <div class="toolbar-group">
            <input type="text" id="logFilterInput" aria-label="Filter log lines" placeholder="Filter log lines…" oninput="renderLogs()" style="width:150px">
            <span id="logCountInfo" class="help-text">0 lines</span>
          </div>
          <div class="toolbar-group">
            <button id="btnClearHighlight" class="ghost" onclick="clearHighlight()" hidden>Clear Highlight</button>
            <label><input type="checkbox" id="chkAutoScroll" checked> Auto-scroll</label>
          </div>
        </div>
        <div class="log-box" id="logBox"></div>
        <div class="console-footer" title="Transport only; sending does not confirm tool execution. Closing this page leaves jobs running.">
          <div class="stdin-row">
            <span id="stdinTarget" class="help-text">Current job</span>
            <input type="text" class="mono" id="stdinInput" aria-label="Text to selected job" placeholder="Text to process stdin…" onkeydown="if(event.key==='Enter')sendStdinCommand()">
            <button id="btnSend" class="primary" onclick="sendStdinCommand()">Send ↵</button>
            <button id="btnCtrlC" class="warn" onclick="singleJobAction('interrupt')">Ctrl+C</button>
          </div>
          <div class="stdin-meta">
            <span id="stdinFeedback">Ready for input delivery (when process is active)</span>
            <span class="help-text">Input delivery only · closing page leaves jobs running</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Unified Settings Dialog (integrates Limits, Retention Policy, and Columns/Display Preferences) -->
<div class="overlay" id="settingsOverlay" hidden>
  <div class="modal settings-modal card" role="dialog" aria-labelledby="settingsTitle">
    <div class="card-title">
      <span id="settingsTitle">Wave Settings</span>
      <button onclick="closeSettings()">Close</button>
    </div>
    <div class="settings-nav" role="tablist" aria-label="Settings sections">
      <button type="button" id="btnSecAll" onclick="focusSettingsSection('all')">All Sections</button>
      <button type="button" id="btnSecExecution" onclick="focusSettingsSection('execution')">A. Execution Limits</button>
      <button type="button" id="btnSecRetention" onclick="focusSettingsSection('retention')">B. Memory Retention</button>
      <button type="button" id="btnSecDisplay" onclick="focusSettingsSection('display')">C. Display &amp; Columns</button>
    </div>

    <!-- Section A: Execution Settings -->
    <div class="settings-section" id="settingsSectionExecution">
      <div class="settings-section-header">
        <span class="settings-section-title">A. Execution Limits (Concurrency &amp; Fixed Quotas)</span>
        <span class="scope-tag">Core Session</span>
      </div>
      <div class="settings-row">
        <label for="maxWorkers" style="color:var(--text);font-weight:600">Concurrent jobs (max_workers)</label>
        <input id="maxWorkers" type="number" min="1" step="1" style="width:90px">
      </div>
      <div id="resourceFields" class="settings-row"></div>
      <div class="settings-row" style="justify-content:space-between;margin-top:6px">
        <span class="help-text">Lowering limits does not preempt active jobs; updating settings does not unpause dispatch. Python files are not rewritten.</span>
        <button id="btnApplyLimits" class="primary" onclick="saveLimits()">Apply Execution Limits</button>
      </div>
    </div>

    <!-- Section B: Memory Retention Settings -->
    <div class="settings-section" id="settingsSectionRetention">
      <div class="settings-section-header">
        <span class="settings-section-title">B. Memory Retention Policy (Logs &amp; Completed History)</span>
        <span class="scope-tag">Core Session</span>
      </div>
      <div class="settings-subbox">
        <div style="font-weight:600;margin-bottom:4px;color:var(--text)">1. Per-Job / Attempt Log Retention (<code>max_log_lines</code>, Python default: 10000)</div>
        <div id="retSelectedJobInfo" class="help-text" style="margin-bottom:2px"></div>
        <div id="retBatchJobInfo" class="help-text" style="margin-bottom:6px"></div>
        <div class="settings-row">
          <label for="logRetentionInput" style="color:var(--text)">Retained log lines</label>
          <input id="logRetentionInput" type="number" min="1" step="1" style="width:120px" placeholder="Positive integer">
          <button id="btnApplyLogSelected" class="primary" onclick="applyLogRetention('selected')">Apply to Selected Job</button>
          <button id="btnApplyLogAll" onclick="applyLogRetention('all')">Apply to All Jobs</button>
        </div>
        <div class="help-text">Applies to the current attempt and future attempts of the target job(s); completed historical attempts are untouched. Shrinking evicts older lines; enlarging does not restore evicted lines. Line numbers, monitoring, saved evidence, and Result are unaffected.</div>
      </div>
      <div class="settings-subbox" style="margin-bottom:0">
        <div style="font-weight:600;margin-bottom:4px;color:var(--text)">2. Per-Job Historical Attempts Retention (<code>max_history</code>, default: 20)</div>
        <div id="retHistoryInfo" class="help-text" style="margin-bottom:6px"></div>
        <div class="settings-row">
          <label for="maxHistoryInput" style="color:var(--text)">Retained history per job</label>
          <input id="maxHistoryInput" type="number" min="1" step="1" style="width:100px">
          <button id="btnApplyHistory" class="primary" onclick="saveHistoryLimit()">Apply History Limit</button>
        </div>
        <div class="help-text">Excludes the current attempt. Shrinking evicts oldest completed attempts and discloses truncation without recalculating existing Results.</div>
      </div>
    </div>

    <!-- Section C: Display Preferences -->
    <div class="settings-section" id="settingsSectionDisplay" style="margin-bottom:0">
      <div class="settings-section-header">
        <span class="settings-section-title">C. Display Preferences &amp; Table Columns</span>
        <span class="scope-tag">Browser localStorage + Columns</span>
      </div>
      <div class="settings-subbox">
        <div style="font-weight:600;margin-bottom:4px;color:var(--text)">Log &amp; Layout Preferences (saved in browser <code>localStorage</code>)</div>
        <div class="settings-row">
          <label for="prefLogFontSize" style="color:var(--text)">Log font size</label>
          <select id="prefLogFontSize" onchange="updateDisplayPrefsFromUI()">
            <option value="10">10 px</option>
            <option value="11">11 px</option>
            <option value="11.5">11.5 px (Default)</option>
            <option value="12">12 px</option>
            <option value="13">13 px</option>
            <option value="14">14 px</option>
            <option value="15">15 px</option>
          </select>
          <label style="color:var(--text)"><input type="checkbox" id="prefLogWrap" onchange="updateDisplayPrefsFromUI()"> Wrap long log lines</label>
        </div>
        <div class="settings-row">
          <label for="prefLayoutMode" style="color:var(--text)">Monitor layout</label>
          <select id="prefLayoutMode" onchange="updateDisplayPrefsFromUI()">
            <option value="split">◫ Split (Left / Right)</option>
            <option value="stack">⊟ Stack (Top / Bottom)</option>
          </select>
          <label for="prefSplitWidth" style="color:var(--text)">Split left %</label>
          <input type="number" id="prefSplitWidth" min="20" max="80" step="1" style="width:62px" onchange="updateDisplayPrefsFromUI()">
          <label for="prefStackHeight" style="color:var(--text)">Stack top %</label>
          <input type="number" id="prefStackHeight" min="20" max="75" step="1" style="width:62px" onchange="updateDisplayPrefsFromUI()">
          <button type="button" id="btnResetDisplayPrefs" onclick="resetDisplayPreferences()">Reset Display Preferences</button>
        </div>
      </div>
      <div class="settings-subbox" style="margin-bottom:0">
        <div style="font-weight:600;margin-bottom:4px;color:var(--text)">Visible Table Columns (State, Result &amp; Warnings are always visible)</div>
        <div id="columnFields" class="columns-grid"></div>
        <div class="settings-row" style="justify-content:space-between;margin-top:4px">
          <span class="help-text">Display preferences never overwrite core execution or retention settings.</span>
          <button id="btnApplyColumns" class="primary" onclick="saveColumns()">Apply Columns</button>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="overlay" id="workOverlay" hidden>
  <div class="modal card">
    <div class="card-title">Load Python Work File <button onclick="closeWorkFile()">Close</button></div>
    <p class="help-text">Load a Python file that defines your jobs. Preview the list, then press Start to dispatch. Loading executes trusted Python code with your permissions.</p>
    <div class="path-row">
      <input type="text" class="mono" id="workPath" aria-label="Python work file path" placeholder="Python work file path" onkeydown="if(event.key==='Enter')loadWorkFile()">
      <button onclick="browseFiles()">Browse…</button>
      <button id="btnLoadFile" class="primary" onclick="loadWorkFile()">Load &amp; Preview</button>
    </div>
    <div id="fileBrowser" hidden>
      <div class="path-row" style="margin-top:10px">
        <button onclick="browseParent()">Parent</button>
        <input type="text" class="mono" id="browsePath" aria-label="Browse directory" onkeydown="if(event.key==='Enter')browseFiles(this.value)">
        <button onclick="browseFiles(el('browsePath').value)">Go</button>
      </div>
      <div id="fileList" class="file-list"></div>
    </div>
    <p class="help-text">Relative paths use Wave's launch directory. Loading does not change cwd. Stop queued/running jobs before replacing an active batch.</p>
    <div id="loadError" class="error-text" role="alert"></div>
  </div>
</div>
<div class="overlay" id="reportOverlay" hidden>
  <div class="modal card">
    <div class="card-title">Action Report <button onclick="el('reportOverlay').hidden=true">Close</button></div>
    <div id="actionReport"></div>
  </div>
</div>
<div id="toastBox" class="toast" role="status"></div>
<script>
const PREFS_STORAGE_KEY = 'wave_display_prefs_v1';
const DEFAULT_DISPLAY_PREFS = {
  logFontSize: 11.5,
  logWrap: true,
  optional: { elapsed: false, priority: false },
  stackedMode: false,
  splitWidth: 42,
  stackHeight: 38
};
let DISPLAY_PREFS = JSON.parse(JSON.stringify(DEFAULT_DISPLAY_PREFS));
let STATE = {
    jobs: [],
    detail: null,
    columns: [],
    resources: {},
    resources_in_use: {},
    max_history: 20
  },
  SELECTED_JOB_ID = null,
  SELECTED_ATTEMPT_ID = null,
  CHECKED_JOBS = new Set();
let CONNECTED = false,
  REQUEST_ID = 0,
  HIGHLIGHT_LINE_NO = null,
  LAST_LOG_KEY = '',
  SORT_KEY = '',
  SORT_ASC = true,
  BROWSE_PARENT = null,
  BUSY = false,
  STACKED_MODE = false,
  PROGRAMMATIC_SCROLL_UNTIL = 0,
  SETTINGS_SECTION = 'all';
let KNOWN_COLUMNS = new Set(),
  OPTIONAL = {
    elapsed: false,
    priority: false
  },
  DETAIL_KEY = '',
  DETAIL_DISCLOSURES = {},
  DETAIL_VIEW = 'log',
  LAST_LOG_RENDER_KEY = '',
  LAST_LOG_DOM_KEY = '',
  LOG_CACHE = null,
  SCROLL_TO_EVIDENCE = false,
  STDIN_STATUS_BY_ATTEMPT = {};
const ACTIVE = new Set(['RUNNING', 'WAITING_INPUT', 'STOPPING']),
  FINISHED = new Set(['DONE', 'FAILED', 'CANCELLED']);
const LABELS = {
  stop: 'Stop',
  force_stop: 'Force Stop',
  rerun: 'Rerun',
  send: 'Send',
  interrupt: 'Ctrl+C',
  max_log_lines: 'Log Retention'
};
const el = id => document.getElementById(id),
  currentJob = () => STATE.jobs.find(j => j.id === SELECTED_JOB_ID),
  isHistory = () => !!SELECTED_ATTEMPT_ID;
const currentDetailMatches = () => !!STATE.detail && STATE.detail.id === SELECTED_JOB_ID && STATE.detail
  .attempt_id === currentJob()?.attempt_id;

function escHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function displayState(s) {
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

function loadDisplayPreferences() {
  try {
    const raw = window.localStorage.getItem(PREFS_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      const fs = Number(parsed.logFontSize);
      if (Number.isFinite(fs) && fs >= 9 && fs <= 20) DISPLAY_PREFS.logFontSize = fs;
      if (typeof parsed.logWrap === 'boolean') DISPLAY_PREFS.logWrap = parsed.logWrap;
      if (parsed.optional && typeof parsed.optional === 'object') {
        if (typeof parsed.optional.elapsed === 'boolean') DISPLAY_PREFS.optional.elapsed = parsed.optional.elapsed;
        if (typeof parsed.optional.priority === 'boolean') DISPLAY_PREFS.optional.priority = parsed.optional.priority;
      }
      if (typeof parsed.stackedMode === 'boolean') DISPLAY_PREFS.stackedMode = parsed.stackedMode;
      const sw = Number(parsed.splitWidth);
      if (Number.isFinite(sw) && sw >= 20 && sw <= 80) DISPLAY_PREFS.splitWidth = Math.round(sw);
      const sh = Number(parsed.stackHeight);
      if (Number.isFinite(sh) && sh >= 20 && sh <= 75) DISPLAY_PREFS.stackHeight = Math.round(sh);
    }
  } catch (_) {}
  OPTIONAL.elapsed = DISPLAY_PREFS.optional.elapsed;
  OPTIONAL.priority = DISPLAY_PREFS.optional.priority;
  STACKED_MODE = DISPLAY_PREFS.stackedMode;
}

function saveDisplayPreferences() {
  DISPLAY_PREFS.optional = { elapsed: !!OPTIONAL.elapsed, priority: !!OPTIONAL.priority };
  DISPLAY_PREFS.stackedMode = !!STACKED_MODE;
  try {
    window.localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(DISPLAY_PREFS));
  } catch (_) {}
}

function applyDisplayPreferences() {
  document.documentElement.style.setProperty('--log-font-size', DISPLAY_PREFS.logFontSize + 'px');
  document.documentElement.style.setProperty('--log-white-space', DISPLAY_PREFS.logWrap ? 'pre-wrap' : 'pre');
  document.documentElement.style.setProperty('--log-word-break', DISPLAY_PREFS.logWrap ? 'break-all' : 'normal');
  OPTIONAL.elapsed = !!DISPLAY_PREFS.optional.elapsed;
  OPTIONAL.priority = !!DISPLAY_PREFS.optional.priority;
  const split = el('monitorSplit'),
    divider = el('paneDivider'),
    btn = el('btnLayoutMode');
  STACKED_MODE = !!DISPLAY_PREFS.stackedMode;
  if (split) {
    split.classList.toggle('stacked', STACKED_MODE);
    split.style.setProperty('--list-width', DISPLAY_PREFS.splitWidth + '%');
    split.style.setProperty('--list-height', DISPLAY_PREFS.stackHeight + '%');
  }
  if (divider) {
    divider.setAttribute('aria-orientation', STACKED_MODE ? 'horizontal' : 'vertical');
    divider.setAttribute('aria-valuenow', String(STACKED_MODE ? DISPLAY_PREFS.stackHeight : DISPLAY_PREFS.splitWidth));
  }
  if (btn) btn.textContent = STACKED_MODE ? '⊟ Stack' : '◫ Split';
  syncNarrowTableClass();
  const box = el('logBox');
  if (box && el('chkAutoScroll')?.checked && DETAIL_VIEW === 'log') {
    markProgrammaticScroll();
    box.scrollTop = box.scrollHeight;
  }
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
  const fs = Number(el('prefLogFontSize').value);
  if (Number.isFinite(fs) && fs >= 9 && fs <= 20) DISPLAY_PREFS.logFontSize = fs;
  DISPLAY_PREFS.logWrap = !!el('prefLogWrap').checked;
  DISPLAY_PREFS.stackedMode = el('prefLayoutMode').value === 'stack';
  const sw = Number(el('prefSplitWidth').value);
  if (Number.isFinite(sw)) DISPLAY_PREFS.splitWidth = Math.max(20, Math.min(80, Math.round(sw)));
  const sh = Number(el('prefStackHeight').value);
  if (Number.isFinite(sh)) DISPLAY_PREFS.stackHeight = Math.max(20, Math.min(75, Math.round(sh)));
  applyDisplayPreferences();
  saveDisplayPreferences();
  syncDisplayPrefsControls();
}

function resetDisplayPreferences() {
  DISPLAY_PREFS = JSON.parse(JSON.stringify(DEFAULT_DISPLAY_PREFS));
  try {
    window.localStorage.removeItem(PREFS_STORAGE_KEY);
  } catch (_) {}
  applyDisplayPreferences();
  syncDisplayPrefsControls();
  if (!el('settingsOverlay').hidden) populateSettingsDialog();
  renderMonitor();
  recordFeedback('Display preferences reset to defaults.');
}

function recordFeedback(message, tone = 'info', hasReport = false) {
  const bar = el('lastActionBar'),
    txt = el('lastActionText'),
    btn = el('btnLastReport');
  bar.hidden = false;
  bar.className = 'action-feedback' + (tone === 'warn' ? ' warn' : tone === 'error' ? ' error' : '');
  const full = '[' + nowStamp() + '] ' + message;
  txt.textContent = full;
  bar.title = full;
  btn.hidden = !hasReport;
  showToast(message);
}

function setDetailView(view, openSectionId) {
  DETAIL_VIEW = view;
  markProgrammaticScroll();
  el('logView').hidden = view !== 'log';
  el('detailsView').hidden = view !== 'details';
  el('btnLogTab').setAttribute('aria-selected', String(view === 'log'));
  el('btnDetailsTab').setAttribute('aria-selected', String(view === 'details'));
  if (view === 'details' && openSectionId) {
    DETAIL_DISCLOSURES[openSectionId] = true;
    const sec = el(openSectionId);
    if (sec) {
      sec.open = true;
      sec.scrollIntoView({ block: 'nearest' });
    }
  }
  if (view === 'log') {
    LAST_LOG_RENDER_KEY = '';
    renderLogs();
  }
}

function clearHighlight() {
  HIGHLIGHT_LINE_NO = null;
  SCROLL_TO_EVIDENCE = false;
  el('btnClearHighlight').hidden = true;
  renderLogs();
}

function toggleSplitOrientation(forceStacked) {
  markProgrammaticScroll();
  DISPLAY_PREFS.stackedMode = typeof forceStacked === 'boolean' ? forceStacked : !STACKED_MODE;
  applyDisplayPreferences();
  saveDisplayPreferences();
  syncDisplayPrefsControls();
}

function syncNarrowTableClass() {
  const pane = el('paneLeft');
  if (!pane) return;
  const w = pane.getBoundingClientRect().width;
  pane.classList.toggle('narrow-table', w > 0 && w < 430);
}

function setupMonitorLayout() {
  loadDisplayPreferences();
  applyDisplayPreferences();
  const split = el('monitorSplit'),
    divider = el('paneDivider'),
    box = el('logBox');
  function resize(percent) {
    markProgrammaticScroll();
    if (STACKED_MODE) {
      const height = split.getBoundingClientRect().height;
      const low = height ? Math.min(110 / height * 100, 25) : 20,
        high = height ? 100 - Math.min(140 / height * 100, 35) : 75;
      const value = Math.round(Math.max(low, Math.min(high, percent)));
      DISPLAY_PREFS.stackHeight = value;
      split.style.setProperty('--list-height', value + '%');
      divider.setAttribute('aria-valuenow', String(value));
      divider.setAttribute('aria-valuemin', String(Math.round(low)));
      divider.setAttribute('aria-valuemax', String(Math.round(high)));
    } else {
      const width = split.getBoundingClientRect().width;
      const low = width ? Math.min(175 / width * 100, 24) : 20,
        high = width ? 100 - Math.min(220 / width * 100, 36) : 80;
      const value = Math.round(Math.max(low, Math.min(high, percent)));
      DISPLAY_PREFS.splitWidth = value;
      split.style.setProperty('--list-width', value + '%');
      divider.setAttribute('aria-valuenow', String(value));
      divider.setAttribute('aria-valuemin', String(Math.round(low)));
      divider.setAttribute('aria-valuemax', String(Math.round(high)));
    }
    saveDisplayPreferences();
    syncDisplayPrefsControls();
    syncNarrowTableClass();
    if (el('chkAutoScroll').checked && DETAIL_VIEW === 'log') {
      box.scrollTop = box.scrollHeight;
    }
  }
  divider.onpointerdown = e => {
    if (e.button !== 0) return;
    divider.setPointerCapture(e.pointerId);
    document.body.classList.add(STACKED_MODE ? 'resizing-row' : 'resizing');
    e.preventDefault();
  };
  divider.onpointermove = e => {
    if (!divider.hasPointerCapture(e.pointerId)) return;
    const bounds = split.getBoundingClientRect();
    if (STACKED_MODE) {
      resize((e.clientY - bounds.top) / bounds.height * 100);
    } else {
      resize((e.clientX - bounds.left) / bounds.width * 100);
    }
  };
  divider.onpointerup = divider.onpointercancel = e => {
    if (divider.hasPointerCapture(e.pointerId)) divider.releasePointerCapture(e.pointerId);
    document.body.classList.remove('resizing', 'resizing-row');
  };
  divider.ondblclick = () => resize(STACKED_MODE ? DEFAULT_DISPLAY_PREFS.stackHeight : DEFAULT_DISPLAY_PREFS.splitWidth);
  divider.onkeydown = e => {
    const current = Number(divider.getAttribute('aria-valuenow'));
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const delta = ['ArrowLeft', 'ArrowUp'].includes(e.key) ? -3 : 3;
    resize(e.key === 'Home' ? 0 : e.key === 'End' ? 100 : current + delta);
  };
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => {
      markProgrammaticScroll(60);
      syncNarrowTableClass();
    }).observe(el('paneLeft'));
  }
  window.addEventListener('resize', () => {
    markProgrammaticScroll(80);
    syncNarrowTableClass();
    if (el('chkAutoScroll').checked && DETAIL_VIEW === 'log') {
      box.scrollTop = box.scrollHeight;
    }
  });
  const pauseScroll = () => {
    el('chkAutoScroll').checked = false;
    SCROLL_TO_EVIDENCE = false;
  };
  box.onwheel = e => {
    if (e.deltaY < 0) {
      pauseScroll();
    } else if (e.deltaY > 0) {
      requestAnimationFrame(() => {
        if (isLogAtBottom(box) && HIGHLIGHT_LINE_NO == null) el('chkAutoScroll').checked = true;
      });
    }
  };
  box.onscroll = () => {
    if (performance.now() < PROGRAMMATIC_SCROLL_UNTIL || DETAIL_VIEW !== 'log' || !STATE.detail) return;
    if (isLogAtBottom(box)) {
      if (!el('chkAutoScroll').checked && HIGHLIGHT_LINE_NO == null) {
        el('chkAutoScroll').checked = true;
      }
    } else if (el('chkAutoScroll').checked) {
      pauseScroll();
    }
  };
  box.tabIndex = 0;
  box.onkeydown = e => {
    if (['PageUp', 'Home', 'ArrowUp'].includes(e.key)) {
      pauseScroll();
    } else if (['End', 'PageDown', 'ArrowDown'].includes(e.key)) {
      requestAnimationFrame(() => {
        if (isLogAtBottom(box) && HIGHLIGHT_LINE_NO == null) el('chkAutoScroll').checked = true;
      });
    }
  };
  el('chkAutoScroll').onchange = e => {
    if (e.target.checked) {
      HIGHLIGHT_LINE_NO = null;
      SCROLL_TO_EVIDENCE = false;
      el('btnClearHighlight').hidden = true;
      markProgrammaticScroll();
      box.scrollTop = box.scrollHeight;
    }
  };
  el('jobTableHead').oncontextmenu = e => {
    e.preventDefault();
    openSettings('display');
  };
  ['btnLogTab', 'btnDetailsTab'].forEach(id => el(id).onkeydown = e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const view = e.key === 'Home' ? 'log' : e.key === 'End' ? 'details' : DETAIL_VIEW === 'log' ? 'details' : 'log';
    setDetailView(view);
    el(view === 'log' ? 'btnLogTab' : 'btnDetailsTab').focus();
  });
}

function showToast(message) {
  el('toastBox').textContent = message;
  el('toastBox').style.display = 'block';
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => el('toastBox').style.display = 'none', 6500);
}

function setConnection(yes) {
  CONNECTED = yes;
  el('connectionNotice').hidden = yes;
  el('connectionNotice').textContent =
    'Connection lost — last snapshot shown. Controls disabled until reconnection.';
  updateControls();
}
async function fetchJson(path, options) {
  const c = new AbortController(),
    timer = setTimeout(() => c.abort(), 15000);
  try {
    const r = await fetch(path, Object.assign({}, options, {
        signal: c.signal
      })),
      d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || 'HTTP ' + r.status);
    return d;
  } finally {
    clearTimeout(timer);
  }
}
async function apiPost(path, payload) {
  if (!CONNECTED) return null;
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
    recordFeedback('Could not confirm action: ' + e.message + '. Check state before retrying.', 'error');
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
      SELECTED_JOB_ID = null;
      SELECTED_ATTEMPT_ID = null;
      CHECKED_JOBS.clear();
      KNOWN_COLUMNS.clear();
      LOG_CACHE = null;
      closePanels();
      STATE = s;
      STATE.detail = null;
      CONNECTED = false;
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
    (s.available_columns || s.columns || []).forEach(c => KNOWN_COLUMNS.add(c));
    s.jobs.forEach(j => Object.keys(j.data || {}).forEach(c => KNOWN_COLUMNS.add(c)));
    CHECKED_JOBS = new Set([...CHECKED_JOBS].filter(id => s.jobs.some(j => j.id === id)));
    if (!currentJob()) {
      SELECTED_JOB_ID = s.jobs[0]?.id || null;
      SELECTED_ATTEMPT_ID = null;
      if (SELECTED_JOB_ID) return fetchState();
    }
    setConnection(true);
    renderTopBar();
    renderMonitor();
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
  ['settingsOverlay', 'broadcastPanel'].forEach(id => {
    if (el(id)) el(id).hidden = true;
  });
}

function quickFilterState(st) {
  el('stateFilter').value = el('stateFilter').value === st ? '' : st;
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

function renderTopBar() {
  el('statusDot').classList.toggle('paused', STATE.paused || !STATE.started);
  el('btnPauseResume').textContent = STATE.paused ? 'Resume Queue' : 'Pause Queue';
  el('btnStart').hidden = STATE.started || !STATE.jobs.length;
  el('btnStart').textContent = 'Start ' + STATE.jobs.length + ' Jobs';
  const pendingCount = STATE.jobs.filter(j => j.status === 'PENDING').length,
    promptCount = STATE.jobs.filter(j => j.status === 'WAITING_INPUT').length;
  let h =
    `<span class="pill run" title="Active / Max worker slots">Workers: ${STATE.active_workers}/${STATE.max_workers}</span>` +
    `<span class="pill interactive" onclick="quickFilterState('PENDING')" title="Click to filter PENDING jobs">Pending: ${pendingCount}</span>`;
  if (promptCount) {
    h += `<span class="pill wait interactive" onclick="quickFilterState('WAITING_INPUT')" title="Click to filter jobs with detected prompt">Prompt: ${promptCount}</span>`;
  }
  ['PASS', 'FAIL', 'ERROR', 'INCOMPLETE'].forEach(r => {
    const n = STATE.jobs.filter(j => j.result === r).length;
    if (n) h += `<span class="pill interactive ${r==='PASS'?'done':'fail'}" onclick="quickFilterResult('${r}')" title="Click to filter ${r} result">${r}: ${n}</span>`;
  });
  Object.entries(STATE.resources).forEach(([k, v]) => h +=
    `<span class="pill" title="Resource ${escHtml(k)} in use / capacity">${escHtml(k)}: ${STATE.resources_in_use[k]||0}/${v}</span>`);
  const n = STATE.jobs.reduce((acc, j) => acc + j.warning_count, 0);
  if (n) h += `<span class="pill alert" onclick="quickFilterWarnings()" title="Click to toggle warning filter">Warnings: ${n}</span>`;
  el('summaryPills').innerHTML = h;
  const fullStatus = (STATE.source_path || 'No work file loaded.') + ' · ' + (!STATE.started ?
    (STATE.jobs.length ? 'Preview — review, then Start.' : 'Load a work file to begin.') : (STATE.paused ?
      'Dispatch paused; running jobs continue.' : 'Dispatch active.')) + ' · cwd: ' + STATE.launch_cwd;
  el('workspaceInfo').textContent = fullStatus;
  el('workspaceInfo').title = fullStatus;
}

function filteredJobs() {
  const s = el('stateFilter').value,
    r = el('resultFilter').value,
    q = el('jobSearch').value.toLowerCase().trim();
  let jobs = STATE.jobs.filter(j => (!s || j.status === s) && (!r || (j.result || '-') === r) && (!el(
    'warningFilter').checked || j.warning_count > 0) && (!q || JSON.stringify([j.name, j.cmd, j.data, j
    .checks, j.errors, j.termination
  ]).toLowerCase().includes(q)));
  if (SORT_KEY) jobs = jobs.slice().sort((a, b) => {
    const av = SORT_KEY.startsWith('data:') ? a.data[SORT_KEY.slice(5)] : a[SORT_KEY],
      bv = SORT_KEY.startsWith('data:') ? b.data[SORT_KEY.slice(5)] : b[SORT_KEY];
    const n = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av ?? '').localeCompare(
      String(bv ?? ''), undefined, {
        numeric: true
      });
    return SORT_ASC ? n : -n;
  });
  return jobs;
}

function clearFilters() {
  el('stateFilter').value = '';
  el('resultFilter').value = '';
  el('warningFilter').checked = false;
  el('jobSearch').value = '';
  renderMonitor();
}

function sortJobs(key) {
  SORT_ASC = SORT_KEY === key ? !SORT_ASC : true;
  SORT_KEY = key;
  renderMonitor();
}

function heading(label, key, cls = '') {
  return `<th${cls ? ` class="${cls}"` : ''}><button class="sort-button" data-sort="${escHtml(key)}">${label}${SORT_KEY===key?(SORT_ASC?' ↑':' ↓'):''}</button></th>`;
}

function badge(r) {
  const cls = r ? r.replace(/[^A-Z]/g, '') : '-';
  return `<span class="status-badge result-${escHtml(cls)}">${escHtml(r||'—')}</span>`;
}

function renderMonitor() {
  const jobs = filteredJobs(),
    cols = STATE.columns || [];
  el('jobTableHead').innerHTML = '<tr><th style="width:24px"><input type="checkbox" aria-label="Check visible jobs"></th>' +
    heading('Job', 'name') + heading('State', 'status', 'col-state') + heading('Result', 'result') + heading('Warn',
      'warning_count') + (OPTIONAL.elapsed ? heading('Elapsed', 'elapsed') : '') + (OPTIONAL.priority ?
      heading('Prio', 'priority') : '') + cols.map(c => heading(escHtml(c), 'data:' + c)).join('') +
    '</tr>';
  const master = el('jobTableHead').querySelector('input'),
    n = jobs.filter(j => CHECKED_JOBS.has(j.id)).length;
  master.checked = jobs.length > 0 && n === jobs.length;
  master.indeterminate = n > 0 && n < jobs.length;
  master.disabled = !jobs.length;
  master.onchange = () => {
    jobs.forEach(j => master.checked ? CHECKED_JOBS.add(j.id) : CHECKED_JOBS.delete(j.id));
    renderMonitor();
  };
  el('jobTableHead').querySelectorAll('[data-sort]').forEach(b => b.onclick = () => sortJobs(b.dataset.sort));
  el('jobTableBody').innerHTML = jobs.map(j => {
    const reasonText = j.pending_reason || (j.status === 'WAITING_INPUT' ? 'Configured prompt detected; input may be expected.' : '');
    return `<tr class="job-row ${j.id===SELECTED_JOB_ID?'selected':''}" data-job-id="${escHtml(j.id)}">
 <td><input type="checkbox" aria-label="Check ${escHtml(j.name)}" ${CHECKED_JOBS.has(j.id)?'checked':''}></td>
 <td><strong class="job-name" title="${escHtml(j.name)}">${escHtml(j.name)}</strong><div class="help-text job-attempt" title="${escHtml('Attempt ' + j.attempt_number + ' · ' + displayState(j.status) + (reasonText ? ': ' + reasonText : ''))}">#${j.attempt_number}<span class="compact-state"> · <span class="status-badge st-${escHtml(j.status)}" style="padding:0 4px;font-size:9.5px">${escHtml(j.status==='WAITING_INPUT'?'PROMPT':displayState(j.status))}</span></span></div></td>
 <td class="col-state"><span class="status-badge st-${escHtml(j.status)}">${escHtml(displayState(j.status))}</span>${reasonText ? `<div class="help-text pending-reason" title="${escHtml(reasonText)}">${escHtml(reasonText)}</div>` : ''}</td>
 <td>${badge(j.result)}${j.error_count?`<button class="error-badge-btn" title="Monitoring/control errors occurred (${j.error_count}) — click to inspect in Details">⚠ ${j.error_count} err</button>`:''}</td>
 <td>${j.warning_count?`<button class="warn warning-count" title="View ${j.warning_count} warning(s) in Details">${j.warning_count}</button>`:'<span class="help-text">0</span>'}</td>
 ${OPTIONAL.elapsed?`<td>${escHtml(j.elapsed_str)}</td>`:''}${OPTIONAL.priority?`<td>${j.priority}</td>`:''}${cols.map(c=>`<td>${escHtml(j.data[c]??'—')}</td>`).join('')}</tr>`;
  }).join('') || '<tr><td colspan="20" class="help-text" style="padding:16px">' + (STATE.jobs.length ?
    'No jobs match these filters.' : 'No jobs loaded. Use Load Work File…') + '</td></tr>';
  el('jobTableBody').querySelectorAll('.job-row').forEach(row => {
    row.onclick = () => selectJob(row.dataset.jobId);
    row.ondblclick = async () => {
      await selectJob(row.dataset.jobId, 'details');
    };
    row.oncontextmenu = async e => {
      e.preventDefault();
      await selectJob(row.dataset.jobId, 'details');
    };
    const c = row.querySelector('input');
    c.parentElement.onclick = e => e.stopPropagation();
    c.onchange = () => {
      c.checked ? CHECKED_JOBS.add(row.dataset.jobId) : CHECKED_JOBS.delete(row.dataset.jobId);
      renderMonitor();
    };
    const w = row.querySelector('.warning-count');
    if (w) w.onclick = async e => {
      e.stopPropagation();
      await selectJob(row.dataset.jobId, 'details', 'warningDetails');
    };
    const errBtn = row.querySelector('.error-badge-btn');
    if (errBtn) errBtn.onclick = async e => {
      e.stopPropagation();
      await selectJob(row.dataset.jobId, 'details', 'errorDetails');
    };
  });
  el('tableInfo').textContent = jobs.length + '/' + STATE.jobs.length +
    ' shown · Sort does not alter priority/FIFO/resource dispatch.';
  el('tableInfo').title = el('tableInfo').textContent;
  syncNarrowTableClass();
  renderDetail();
  updateControls();
}
async function selectJob(id, targetView = 'log', sectionId = null) {
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
  if (sectionId) DETAIL_DISCLOSURES[sectionId] = true;
  setDetailView(targetView, sectionId);
  renderDetail();
  updateControls();
  await fetchState();
  if (targetView === 'details' && sectionId && el(sectionId)) {
    el(sectionId).open = true;
    el(sectionId).scrollIntoView({ block: 'nearest' });
  }
}
async function selectAttempt(id) {
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

function renderDetail() {
  const j = currentJob(),
    d = STATE.detail,
    historyEvicted = isHistory() && !d && !!STATE.history_error;
  if (d && DETAIL_KEY !== d.id + ':' + d.attempt_id) {
    HIGHLIGHT_LINE_NO = null;
    SCROLL_TO_EVIDENCE = false;
    LAST_LOG_KEY = '';
    LAST_LOG_RENDER_KEY = '';
    el('btnClearHighlight').hidden = true;
  }
  el('detailTitle').textContent = j ? j.name : 'Select a job';
  if (d) {
    const errChip = d.error_count ? ` <button type="button" class="error-badge-btn" onclick="setDetailView('details','errorDetails')" title="Monitoring/control errors occurred — click to inspect in Details">⚠ ${d.error_count} err</button>` : '';
    const warnChip = d.warning_count ? ` <button type="button" class="warning-count" onclick="setDetailView('details','warningDetails')" title="Warnings recorded — click to inspect in Details">${d.warning_count} warn</button>` : '';
    el('detailMeta').innerHTML =
      `<span class="help-text">#${d.attempt_number}</span> ` +
      `<span class="status-badge st-${escHtml(d.status)}">${escHtml(displayState(d.status))}</span> ` +
      `${badge(d.result)}${errChip}${warnChip}` +
      `<span class="help-text meta-pid">${d.pid ? ' · PID ' + d.pid : ''}${d.returncode != null ? ' · Exit ' + d.returncode : ''}</span>`;
  } else if (historyEvicted) {
    el('detailMeta').innerHTML =
      `<span class="status-badge result-INCOMPLETE">History Unavailable</span> ` +
      `<span class="help-text">${escHtml(STATE.history_error || 'Attempt history is no longer retained')}</span>`;
  } else {
    el('detailMeta').textContent = isHistory() ? 'Loading historical attempt…' : '';
  }
  el('detailTitle').parentElement.title = el('detailTitle').textContent + (d ? ' · Attempt ' + d.attempt_number + ' · ' + displayState(d.status) + ' · ' + (d.result || '—') : '');
  const detailsBadges = [];
  if (d?.checks?.length) {
    const failChecks = d.checks.filter(c => c.state === 'FAIL').length;
    const errChecks = d.checks.filter(c => c.state === 'ERROR').length;
    const chkParts = [];
    if (failChecks) chkParts.push(`${failChecks}/${d.checks.length} FAIL`);
    if (errChecks) chkParts.push(`${errChecks}/${d.checks.length} ERROR`);
    detailsBadges.push(chkParts.length ? chkParts.join(' · ') : `${d.checks.length} chk`);
  }
  if (d?.warning_count) detailsBadges.push(`${d.warning_count}w`);
  if (d?.error_count) detailsBadges.push(`⚠${d.error_count}e`);
  el('btnLogTab').textContent = 'Log' + (d ? ` (${d.log_records?.length || 0})` : '');
  el('btnDetailsTab').textContent = 'Details' + (detailsBadges.length ? ' · ' + detailsBadges.join(' · ') : '');
  el('detailCmd').textContent = d ? '$ ' + (Array.isArray(d.cmd) ? d.cmd.join(' ') : d.cmd) : (historyEvicted ? 'Historical attempt is no longer retained.' : '');
  el('detailCmd').title = el('detailCmd').textContent;
  el('detailFacts').textContent = d ? 'cwd: ' + d.cwd + ' | resources: ' + JSON.stringify(d.resources) +
    ' | stop: ' + d.stop_policy + (d.stop_cmd ? ' → stdin ' + d.stop_cmd : '') + (d.stop_ctrl_c ?
      ' → Ctrl+C' : '') + (d.stop_policy === 'FORCE' ? '' : ' → wait ' + d.stop_timeout +
      's → force if needed') + ' | log retention: ' + (d.attempt_max_log_lines ?? d.max_log_lines ?? 10000) + ' lines | elapsed: ' + d.elapsed_str : '';
  const select = el('attemptSelect'),
    history = j?.history || [],
    missingSelectedHistory = SELECTED_ATTEMPT_ID && !history.some(a => a.attempt_id === SELECTED_ATTEMPT_ID),
    options = `<option value="">Current (#${j ? j.attempt_number : 1} · ${escHtml(j ? (j.result || displayState(j.status)) : '')})</option>` + history.map(a =>
      `<option value="${escHtml(a.attempt_id)}">History #${a.attempt_number} · ${escHtml(a.result||displayState(a.status))}</option>`
      ).join('') + (missingSelectedHistory ?
      '<option value="' + escHtml(SELECTED_ATTEMPT_ID) + '">History unavailable</option>' : '');
  if (select.dataset.options !== options) {
    select.innerHTML = options;
    select.dataset.options = options;
  }
  select.value = SELECTED_ATTEMPT_ID || '';
  select.disabled = !history.length && !missingSelectedHistory;
  el('historyNotice').hidden = !isHistory();
  if (isHistory()) {
    el('historyNoticeText').textContent = historyEvicted ?
      'Selected historical attempt is no longer retained (evicted by history limit). Controls remain disabled; click Back to Current to inspect the active attempt.' :
      'Viewing historical attempt — read-only. Controls are disabled so actions cannot affect the current run.';
  }
  el('priorityEditor').hidden = !j || j.status !== 'PENDING' || isHistory();
  if (j && document.activeElement !== el('priorityValue')) el('priorityValue').value = j.priority;
  el('stdinTarget').textContent = j ? 'To: ' + j.name + ' (#' + (d?.attempt_number ?? (isHistory() ? 'history' : j.attempt_number)) + ')' : 'No target';
  el('stdinTarget').title = el('stdinTarget').textContent;
  const attemptStatus = d ? STDIN_STATUS_BY_ATTEMPT[d.attempt_id] : null;
  el('stdinFeedback').textContent = isHistory() ?
    (historyEvicted ? 'Historical attempt no longer retained — return to Current to operate' : 'Read-only historical attempt — return to Current to send input') :
    (attemptStatus || (j && ['RUNNING', 'WAITING_INPUT'].includes(j.status) ?
      'Active process (#' + j.attempt_number + ') — ready for stdin / Ctrl+C' :
      'Process not active — stdin / Ctrl+C disabled'));
  renderDiagnostics(d, j);
  renderLogs();
}

function evidence(e, retainedLines) {
  if (!e) return '';
  const hasLine = e.line_no != null;
  const isRetained = hasLine && (!retainedLines || retainedLines.has(e.line_no));
  const hint = hasLine ? (isRetained ? ' ↗ Jump to Log' : ' (Log line evicted; saved evidence)') : '';
  return `<button class="evidence" ${hasLine?`data-line="${e.line_no}"`:'disabled'} title="${hasLine ? (isRetained ? 'Click to highlight line #' + e.line_no + ' in Log' : 'Original log line is no longer in the ring buffer; saved evidence is shown here') : 'Event has no associated log line'}">${hasLine?'#'+e.line_no+' ':''}${escHtml(e.line||'')}<span class="help-text">${hint}</span></button>`;
}

function renderDiagnostics(d, j) {
  const box = el('diagnostics');
  if (!d) {
    box.textContent = isHistory() ? (STATE.history_error ?
      'Historical attempt is no longer retained (evicted by history limit). Click Back to Current to inspect the active attempt.' :
      'Loading historical attempt…') : '';
    return;
  }
  let h = '';
  const key = d.id + ':' + d.attempt_id;
  if (DETAIL_KEY !== key) {
    DETAIL_KEY = key;
    DETAIL_DISCLOSURES = {};
  }
  const retainedSet = new Set((d.log_records || []).map(r => r.line_no));
  if (d.termination?.length) h +=
    '<details id="terminationDetails" open><summary>Termination / stop sources (' + d.termination.length + ')</summary>' + d.termination.map(
      t =>
      `<div class="check-row"><strong>${escHtml(t.source)}: ${escHtml(t.reason)}</strong><span class="status-badge st-STOPPING">${escHtml(t.requested || '—')} → ${escHtml(t.actual||'requested')}</span></div>`
      ).join('') + '</details>';
  if (d.checks?.length) h += '<details id="checkDetails" open><summary>Checks (' + d.checks.length + ')</summary>' + d.checks.map(c =>
    `<div class="check-row"><strong>${escHtml(c.name)}</strong><span class="status-badge check-${escHtml(c.state)}">${escHtml(c.state)}</span><span class="help-text">expect ${escHtml(c.expect)} · hits ${c.hits}</span></div>${evidence(c.evidence, retainedSet)}`
    ).join('') + '</details>';
  else h += '<div class="detail-card help-text">No checks declared for this job (Result is N/A on normal completion).</div>';
  if (d.errors?.length) h +=
    '<details id="errorDetails" class="danger-box" open><summary class="error-text">Monitoring / control errors (' + d
    .error_count + ')</summary>' + d.errors.map(e =>
      `<div class="check-row"><strong class="error-text">${escHtml(e.source)}: ${escHtml(e.message)}</strong></div>${evidence(e.evidence, retainedSet)}`).join('') +
    '</details>';
  if (d.warning_count) h += '<details id="warningDetails" class="warn-box" open><summary>Warnings (' + d.warning_count +
    ')</summary>' + d.warnings.map(w => `<div class="check-row"><strong>${escHtml(w.name)}</strong><span class="status-badge result-INCOMPLETE">${w.count} match(es)</span></div>${evidence(w.evidence, retainedSet)}`)
    .join('') + '</details>';
  h += '<details id="executionDetails"><summary>Execution settings &amp; monitoring declarations</summary><div class="job-facts">' +
    escHtml('PTY: ' + d.pty + ' | prompts: ' + JSON.stringify(d.prompts) + ' | timeout: ' + (d.timeout == null ?
      'none' : d.timeout + 's') + ' | extra retries: ' + d.max_retries + ' | max_log_lines: ' + (d.max_log_lines ?? 10000) + ' (attempt limit: ' + (d.attempt_max_log_lines ?? d.max_log_lines ?? 10000) + ') | env overrides: ' + JSON.stringify(d.env)) +
    '</div>' + (d.monitor_definitions?.length ? '<div class="job-facts" style="margin-top:4px;font-weight:600">Monitoring definitions (callable names only; inspect Python for bodies):</div>' +
      d.monitor_definitions.map(x => '<div class="job-facts" style="font-family:var(--font-mono)">• ' + escHtml(x) + '</div>').join('') : '') + '</details>';
  if (d.diagnostics?.length) h += '<details id="diagnosticDetails"><summary>Runner diagnostics (' + d.diagnostics.length + ')</summary>' + d
    .diagnostics.map(x => '<div class="job-facts">' + escHtml(x.source) + ': ' + escHtml(x.message) + '</div>').join('') +
    '</details>';
  if (j?.history_truncated) h += '<p class="help-text">Earlier attempts discarded by the history limit (' + j.history_truncated + ' evicted).</p>';
  box.innerHTML = h;
  box.querySelectorAll('details').forEach(item => {
    if (Object.prototype.hasOwnProperty.call(DETAIL_DISCLOSURES, item.id)) item.open = DETAIL_DISCLOSURES[
      item.id];
    item.ontoggle = () => {
      if (item.isConnected) DETAIL_DISCLOSURES[item.id] = item.open;
    };
  });
  box.querySelectorAll('[data-line]').forEach(b => b.onclick = () => {
    HIGHLIGHT_LINE_NO = Number(b.dataset.line);
    if (!d.log_records.some(r => r.line_no === HIGHLIGHT_LINE_NO)) {
      recordFeedback('Original log line #' + HIGHLIGHT_LINE_NO + ' is no longer retained. Saved evidence is shown in Details.', 'warn');
      return;
    }
    SCROLL_TO_EVIDENCE = true;
    el('chkAutoScroll').checked = false;
    el('logFilterInput').value = '';
    el('btnClearHighlight').hidden = false;
    setDetailView('log');
  });
}

function renderLogs() {
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
    LAST_LOG_RENDER_KEY = '';
    return;
  }
  const records = d.log_records || [],
    q = el('logFilterInput').value.toLowerCase().trim(),
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
  const renderKey = key + ':' + q + ':' + HIGHLIGHT_LINE_NO + ':' + d.status;
  if (DETAIL_VIEW !== 'log' || (renderKey === LAST_LOG_RENDER_KEY && !SCROLL_TO_EVIDENCE)) return;
  const scroll = box.scrollTop;
  let removedHeight = 0;
  const matching = records.filter(r => !q || r.text.toLowerCase().includes(q));
  const domKey = d.id + ':' + d.attempt_id + ':' + q + ':' + HIGHLIGHT_LINE_NO;
  let anchor = null, anchorTop = 0;
  if (!el('chkAutoScroll').checked && LAST_LOG_DOM_KEY === domKey &&
      box.firstElementChild?.classList.contains('log-line')) {
    // Preserve the visible row itself. Pixel-height subtraction alone drifts
    // under fractional line heights and the browser's native scroll anchoring.
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
  const rowHtml = rows => rows.map(r =>
    `<div class="log-line ${r.source==='wave'?'wave':''} ${r.line_no===HIGHLIGHT_LINE_NO?'highlight':''}" data-line-no="${r.line_no}" id="log-line-${r.line_no}"><span class="log-lineno">${r.source==='wave'?'Wave':r.line_no??'—'}</span><span>${escHtml(r.text)}</span></div>`
  ).join('');
  if (!matching.length) {
    box.innerHTML = '<div class="log-empty">' + (records.length ? 'No log lines match filter "' + escHtml(q) + '".' :
      (d.status === 'PENDING' ? 'Job is PENDING (' + escHtml(d.pending_reason || 'waiting to start') + '). No output yet.' : 'No output lines recorded for this attempt.')) + '</div>';
  } else if (LAST_LOG_DOM_KEY === domKey && box.firstElementChild?.classList.contains('log-line')) {
    // Keep retained DOM rows while appending output. Rebuilding 10,000 rows on
    // every poll both stalls input and moves the reader away from older lines.
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
  if (h && SCROLL_TO_EVIDENCE) box.scrollTop += h.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 2;
  else if (el('chkAutoScroll').checked && key !== LAST_LOG_KEY) box.scrollTop = box.scrollHeight;
  else if (anchor?.isConnected) box.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
  else box.scrollTop = Math.max(0, scroll - removedHeight);
  LAST_LOG_KEY = key;
  LAST_LOG_RENDER_KEY = renderKey;
  LAST_LOG_DOM_KEY = domKey;
  SCROLL_TO_EVIDENCE = false;
}

function allowed(j, a) {
  if (!j) return false;
  if (a === 'rerun') return FINISHED.has(j.status) && j.can_rerun === true;
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

function checkedJobs() {
  return STATE.jobs.filter(j => CHECKED_JOBS.has(j.id));
}

function updateControls() {
  const j = currentJob(),
    enabled = CONNECTED && !BUSY;
  document.querySelectorAll('[data-single-action]').forEach(b => {
    b.disabled = !enabled || isHistory() || !currentDetailMatches() || !allowed(j, b.dataset.singleAction);
    if (b.dataset.singleAction === 'stop') b.textContent = j?.status === 'PENDING' ? 'Cancel Pending' : 'Stop';
    b.title = isHistory() ? 'History is read-only; return to Current.' : (!j ? 'Select a job.' :
      (b.dataset.singleAction === 'rerun' && !allowed(j, 'rerun') ? 'Rerun is available after this attempt finishes and cleanup completes.' : 'Target: ' + j.name + ' (Attempt #' + j.attempt_number + ')'));
  });
  const checked = checkedJobs(),
    visible = filteredJobs(),
    hidden = checked.filter(j => !visible.some(v => v.id === j.id)).length;
  el('checkedCount').textContent = checked.length + ' checked' + (hidden ? ' (' + hidden + ' hidden)' : '');
  document.querySelectorAll('[data-batch-action]').forEach(b => {
    const a = b.dataset.batchAction,
      n = checked.filter(j => allowed(j, a)).length;
    b.textContent = (a === 'stop' ? 'Stop/Cancel' : LABELS[a]) + ' ' + n + '/' + checked.length;
    b.disabled = !enabled || !n;
    const skipped = checked.filter(j => !allowed(j, a)).map(j =>
      j.name + ': skipped (' + (a === 'rerun' && FINISHED.has(j.status) && !j.can_rerun ?
        'cleanup in progress' : displayState(j.status)) + ')');
    const scope = a === 'stop' ?
      `Cancels ${checked.filter(j => j.status === 'PENDING').length} pending; stops ${checked.filter(j => ACTIVE.has(j.status)).length} active checked job(s).` :
      `Applies ${LABELS[a]} to ${n}/${checked.length} checked job(s).`;
    b.title = checked.length ? [scope, ...skipped].join('\n') :
      'Check one or more jobs in the table to use batch controls.';
  });
  const sendable = checked.filter(j => allowed(j, 'send'));
  el('btnBroadcast').textContent = 'Send (' + sendable.length + '/' + checked.length + ')…';
  el('btnBroadcast').disabled = !enabled || !sendable.length;
  el('btnBroadcastSend').disabled = !enabled || !sendable.length;
  el('broadcastTargets').textContent = 'Send to ' + sendable.length + '/' + checked.length + ' checked: ' +
    sendable.map(j => j.name).join(', ') + (checked.length > sendable.length ?
      ' · other checked jobs skipped' : '');
  el('btnBroadcastSend').textContent = 'Send to ' + sendable.length + ' Job(s)';
  const canSend = enabled && !isHistory() && currentDetailMatches() && allowed(j, 'send');
  ['btnSend', 'btnCtrlC', 'stdinInput'].forEach(id => el(id).disabled = !canSend);
  el('stdinInput').placeholder = isHistory() ? 'Historical attempt is read-only' : (canSend ? 'Text to process stdin… (Enter to send)' : 'Process not running — stdin unavailable');
  el('btnStart').disabled = !enabled || !STATE.jobs.length || !STATE.ready;
  el('btnPauseResume').disabled = !enabled || !STATE.started;
  el('btnStopAll').disabled = !enabled || !STATE.jobs.some(j => allowed(j, 'stop'));
  el('btnForceAll').disabled = !enabled || !STATE.jobs.some(j => allowed(j, 'force_stop'));
  el('btnLoadFile').disabled = !enabled;
  el('btnSettings').disabled = !CONNECTED;
  el('btnApplyLimits').disabled = !enabled;
  el('btnApplyColumns').disabled = !enabled;
  el('btnApplyHistory').disabled = !enabled;
  el('btnApplyLogSelected').disabled = !enabled || !j || isHistory() || !currentDetailMatches();
  el('btnApplyLogAll').disabled = !enabled || !STATE.jobs.length;
  el('btnApplyPriority').disabled = !enabled || isHistory() || !currentDetailMatches() || j?.status !== 'PENDING';
}

function report(res) {
  const items = res.results || [],
    skipped = items.filter(x => !x.ok);
  const msg = (LABELS[res.action] || res.action) + ': ' + res.affected + '/' + items.length + ' target(s) accepted.' +
    (['send', 'interrupt'].includes(res.action) ? ' Transport only; tool execution is not verified.' : '');
  if (skipped.length) {
    el('actionReport').innerHTML = items.map(x =>
      `<div class="check-row"><strong>${escHtml(x.name||x.job_id)}</strong><span class="status-badge ${x.ok?'check-OK':'result-INCOMPLETE'}">${x.ok?'Accepted':escHtml(x.message||'Not applicable')}</span></div>`
      ).join('');
    el('reportOverlay').hidden = false;
  }
  recordFeedback(msg, skipped.length ? 'warn' : 'info', skipped.length > 0);
}
async function jobAction(action, jobs, extra = {}) {
  if (!CONNECTED || BUSY || !jobs.length) return null;
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
      report(r);
    }
    await fetchState();
    return r;
  } finally {
    BUSY = false;
    updateControls();
  }
}
async function singleJobAction(a) {
  if (isHistory() || !currentDetailMatches()) return;
  const j = currentJob();
  if (allowed(j, a)) {
    const target = captureTargets([j])[0];
    const result = await jobAction(a, [target]);
    if (a === 'rerun' && result?.affected === 1) {
      const item = (result.results || []).find(x => x.job_id === target.job_id);
      const nextNo = item?.new_attempt_number ?? STATE.jobs.find(x => x.id === target.job_id)?.attempt_number ?? 'next';
      recordFeedback('Rerun accepted: ' + target.name + ' (from #' + target.attempt_number + ') → Attempt #' + nextNo);
    } else if (a === 'interrupt' && result?.affected === 1) {
      STDIN_STATUS_BY_ATTEMPT[target.attempt_id] = '✓ Sent Ctrl+C at ' + nowStamp() + ' (signal delivery only)';
      renderDetail();
    }
  }
}
async function batchJobAction(a) {
  const reqTargets = captureTargets(checkedJobs());
  await jobAction(a, reqTargets);
}
async function allAction(a) {
  const reqTargets = captureTargets(STATE.jobs);
  await jobAction(a, reqTargets);
}
async function managerAction(a) {
  const r = await apiPost('/api/manager/action', {
    action: a
  });
  if (r) {
    recordFeedback(r.message);
    await fetchState();
  }
}
async function sendStdinCommand() {
  if (isHistory() || !currentDetailMatches() || el('btnSend').disabled || !el('stdinInput').value) return;
  const j = currentJob();
  if (!j) return;
  const target = captureTargets([j])[0],
    sentText = el('stdinInput').value;
  const r = await jobAction('send', [target], {
    text: sentText
  });
  if (r?.affected === 1) {
    if (el('stdinInput').value === sentText) el('stdinInput').value = '';
    STDIN_STATUS_BY_ATTEMPT[target.attempt_id] = '✓ Sent "' + sentText + '" at ' + nowStamp() + ' (transport only)';
    renderDetail();
  } else if (r) {
    STDIN_STATUS_BY_ATTEMPT[target.attempt_id] = '⚠ Delivery rejected at ' + nowStamp() + ' (input retained)';
    renderDetail();
  }
}

function toggleBroadcast(open) {
  el('broadcastPanel').hidden = open === false ? true : !el('broadcastPanel').hidden;
  updateControls();
  if (!el('broadcastPanel').hidden) el('broadcastText').focus();
}
async function broadcastSend() {
  if (!el('broadcastText').value) return;
  const text = el('broadcastText').value,
    reqTargets = captureTargets(checkedJobs()),
    targetMap = new Map(reqTargets.map(t => [t.job_id, t]));
  const r = await jobAction('send', reqTargets, {
    text
  });
  if (r) {
    r.results.forEach(item => {
      const orig = targetMap.get(item.job_id);
      const boundAttemptId = item.attempt_id || orig?.attempt_id;
      if (boundAttemptId && item.ok) {
        STDIN_STATUS_BY_ATTEMPT[boundAttemptId] = '✓ Broadcast "' + text + '" at ' + nowStamp() + ' (transport only)';
      } else if (boundAttemptId && !item.ok) {
        STDIN_STATUS_BY_ATTEMPT[boundAttemptId] = '⚠ Broadcast rejected at ' + nowStamp() + ': ' + (item.message || 'Not applicable');
      }
    });
    if (r.results.every(x => x.ok) && el('broadcastText').value === text) el('broadcastText').value = '';
    renderDetail();
  }
}
async function savePriority() {
  if (isHistory() || !currentDetailMatches()) return;
  const j = currentJob(),
    n = Number(el('priorityValue').value);
  if (!j || !Number.isSafeInteger(n)) {
    recordFeedback('Priority must be an integer.', 'warn');
    return;
  }
  const target = captureTargets([j])[0];
  if (await apiPost('/api/job/priority', {
      target: targets([target])[0],
      priority: n
    })) {
    await fetchState();
    recordFeedback('Updated pending priority for ' + target.name + ' to ' + n + '.');
  }
}

function focusSettingsSection(section) {
  SETTINGS_SECTION = section || 'all';
  ['all', 'execution', 'retention', 'display'].forEach(s => {
    const btnId = 'btnSec' + s[0].toUpperCase() + s.slice(1);
    if (el(btnId)) el(btnId).classList.toggle('active', SETTINGS_SECTION === s);
  });
  const map = {
    execution: 'settingsSectionExecution',
    retention: 'settingsSectionRetention',
    display: 'settingsSectionDisplay'
  };
  Object.entries(map).forEach(([k, id]) => {
    const node = el(id);
    if (!node) return;
    node.hidden = SETTINGS_SECTION !== 'all' && SETTINGS_SECTION !== k;
    node.classList.toggle('focused', SETTINGS_SECTION === k);
  });
}

function refreshSettingsLiveStatus() {
  const j = currentJob();
  if (j) {
    el('retSelectedJobInfo').innerHTML = `Selected job (<strong>${escHtml(j.name)}</strong>, #${j.attempt_number}): <strong>${j.max_log_lines ?? 10000}</strong> lines · currently retained ${j.log_count}/${j.total_lines}`;
    el('btnApplyLogSelected').textContent = 'Apply to ' + j.name;
  } else {
    el('retSelectedJobInfo').textContent = 'Selected job: None selected';
    el('btnApplyLogSelected').textContent = 'Apply to Selected Job';
  }
  const distinctLimits = [...new Set(STATE.jobs.map(x => x.max_log_lines ?? 10000))].sort((a, b) => a - b);
  if (!STATE.jobs.length) {
    el('retBatchJobInfo').textContent = 'Batch: No jobs loaded.';
  } else if (distinctLimits.length === 1) {
    el('retBatchJobInfo').innerHTML = `Batch (all ${STATE.jobs.length} job(s)): <strong>${distinctLimits[0]}</strong> lines (uniform)`;
  } else {
    el('retBatchJobInfo').innerHTML = `Batch (${STATE.jobs.length} jobs): <span class="status-badge result-INCOMPLETE">mixed</span> distinct values (${distinctLimits.join(', ')} lines) — preserved unless explicitly applied`;
  }
  el('btnApplyLogAll').textContent = 'Apply to All Jobs (' + STATE.jobs.length + ')';
  const totalTruncated = STATE.jobs.reduce((acc, x) => acc + (x.history_truncated || 0), 0);
  el('retHistoryInfo').innerHTML = `Current manager <code>max_history</code>: <strong>${STATE.max_history ?? 20}</strong> completed attempts per job` +
    (totalTruncated ? ` · <span style="color:#fde68a">${totalTruncated} older attempt(s) evicted so far</span>` : '');
}

function populateSettingsDialog() {
  el('maxWorkers').value = STATE.max_workers;
  el('resourceFields').innerHTML = Object.entries(STATE.resources).map(([k, n]) =>
    `<div class="form-field" style="margin-bottom:0"><label>${escHtml(k)}</label><input type="number" min="0" step="1" value="${n}" data-resource="${escHtml(k)}" aria-label="Capacity ${escHtml(k)}" style="width:90px"></div>`
    ).join('') || '<span class="help-text">No fixed resource quotas declared in this manager.</span>';
  const j = currentJob(),
    distinctLimits = [...new Set(STATE.jobs.map(x => x.max_log_lines ?? 10000))];
  if (distinctLimits.length > 1) {
    el('logRetentionInput').value = j ? String(j.max_log_lines ?? 10000) : '';
    el('logRetentionInput').placeholder = 'Mixed (' + distinctLimits.join(', ') + ')';
  } else {
    el('logRetentionInput').value = String(j?.max_log_lines ?? distinctLimits[0] ?? 10000);
    el('logRetentionInput').placeholder = 'Positive integer (default 10000)';
  }
  el('maxHistoryInput').value = String(STATE.max_history ?? 20);
  refreshSettingsLiveStatus();
  syncDisplayPrefsControls();
  el('columnFields').innerHTML = ['elapsed', 'priority'].map(c =>
    `<label style="display:flex;color:var(--text)"><input type="checkbox" data-optional="${c}" ${OPTIONAL[c]?'checked':''}> ${c} (built-in)</label>`
    ).join('') + [...KNOWN_COLUMNS].map(c =>
    `<label style="display:flex;color:var(--text)"><input type="checkbox" data-column="${escHtml(c)}" ${(STATE.columns||[]).includes(c)?'checked':''}> ${escHtml(c)} (track)</label>`
    ).join('');
  updateControls();
}

function openSettings(section = 'all') {
  // Populate inputs only from a confirmed core snapshot, including on first load.
  if (!CONNECTED) return;
  el('settingsOverlay').hidden = false;
  populateSettingsDialog();
  focusSettingsSection(section);
}

function closeSettings() {
  el('settingsOverlay').hidden = true;
}

async function saveLimits() {
  const max = Number(el('maxWorkers').value),
    resources = {};
  if (!Number.isSafeInteger(max) || max < 1) {
    recordFeedback('Concurrent jobs must be a positive integer.', 'warn');
    return;
  }
  for (const x of el('resourceFields').querySelectorAll('input')) {
    const n = Number(x.value);
    if (!Number.isSafeInteger(n) || n < 0) {
      recordFeedback('Capacities must be nonnegative integers.', 'warn');
      return;
    }
    Object.defineProperty(resources, x.dataset.resource, {
      value: n,
      enumerable: true
    });
  }
  if (await apiPost('/api/settings/save', {
      max_workers: max,
      resources
    })) {
    await fetchState();
    recordFeedback('Updated execution limits: max_workers=' + max + '.');
  }
}

async function applyLogRetention(scope) {
  const raw = el('logRetentionInput').value.trim(),
    lines = Number(raw);
  if (!raw || !Number.isSafeInteger(lines) || lines < 1) {
    recordFeedback('Log retention must be a positive integer (>= 1).', 'warn');
    return;
  }
  let targetJobs = [];
  if (scope === 'selected') {
    if (isHistory() || !currentDetailMatches() || !currentJob()) {
      recordFeedback('Return to Current attempt of a selected job before applying per-job retention.', 'warn');
      return;
    }
    targetJobs = [currentJob()];
  } else {
    targetJobs = STATE.jobs.slice();
  }
  if (!targetJobs.length) {
    recordFeedback('No target jobs available.', 'warn');
    return;
  }
  const reqTargets = captureTargets(targetJobs);
  BUSY = true;
  updateControls();
  try {
    const r = await apiPost('/api/job/retention', {
      max_log_lines: lines,
      targets: targets(reqTargets)
    });
    if (r) {
      const items = r.results || [],
        skipped = items.filter(x => !x.ok),
        evicted = r.evicted_lines || 0;
      if (skipped.length) {
        el('actionReport').innerHTML = items.map(x =>
          `<div class="check-row"><strong>${escHtml(x.name||x.job_id)}</strong><span class="status-badge ${x.ok?'check-OK':'result-INCOMPLETE'}">${escHtml(x.message||(x.ok?'Accepted':'Rejected'))}</span></div>`
        ).join('');
        el('reportOverlay').hidden = false;
      }
      const scopeLabel = scope === 'selected' ? reqTargets[0].name : `all ${items.length} job(s)`;
      const evictNote = evicted ? ` (${evicted} older line(s) evicted)` : '';
      recordFeedback(`Log retention set to ${lines} lines for ${scopeLabel}: ${r.affected}/${items.length} accepted${evictNote}.`, skipped.length ? 'warn' : 'info', skipped.length > 0);
      await fetchState();
      if (!el('settingsOverlay').hidden) populateSettingsDialog();
    }
  } finally {
    BUSY = false;
    updateControls();
  }
}

async function saveHistoryLimit() {
  const raw = el('maxHistoryInput').value.trim(),
    limit = Number(raw);
  if (!raw || !Number.isSafeInteger(limit) || limit < 1) {
    recordFeedback('History retention limit must be a positive integer (>= 1).', 'warn');
    return;
  }
  const r = await apiPost('/api/settings/save', {
    max_history: limit
  });
  if (r) {
    const evicted = r.evicted_history || 0;
    await fetchState();
    if (!el('settingsOverlay').hidden) populateSettingsDialog();
    recordFeedback(
      evicted ?
      `Updated max_history=${limit} (${evicted} oldest historical attempt(s) evicted).` :
      `Updated max_history=${limit}.`,
      evicted ? 'warn' : 'info'
    );
  }
}

async function saveColumns() {
  const cols = [...el('columnFields').querySelectorAll('[data-column]:checked')].map(x => x.dataset.column);
  if (await apiPost('/api/settings/save', {
      columns: cols
    })) {
    el('columnFields').querySelectorAll('[data-optional]').forEach(x => {
      OPTIONAL[x.dataset.optional] = x.checked;
    });
    saveDisplayPreferences();
    await fetchState();
    recordFeedback('Updated visible table columns.');
  }
}

function openWorkFile() {
  el('workOverlay').hidden = false;
  el('loadError').textContent = '';
  el('workPath').focus();
}

function closeWorkFile() {
  el('workOverlay').hidden = true;
}
async function browseFiles(path) {
  try {
    const r = await fetchJson('/api/files?path=' + encodeURIComponent(path || ''));
    el('fileBrowser').hidden = false;
    el('browsePath').value = r.path;
    BROWSE_PARENT = r.parent;
    el('fileList').innerHTML = r.entries.map((e, i) =>
        `<button data-entry="${i}">${e.directory?'📁 ':'🐍 '}${escHtml(e.name)}</button>`).join('') ||
      '<span class="help-text">No directories or Python files.</span>';
    el('fileList').querySelectorAll('button').forEach(b => b.onclick = () => {
      const e = r.entries[Number(b.dataset.entry)];
      if (e.directory) browseFiles(e.path);
      else el('workPath').value = e.path;
    });
    el('loadError').textContent = '';
  } catch (e) {
    el('loadError').textContent = e.message;
  }
}

function browseParent() {
  if (BROWSE_PARENT) browseFiles(BROWSE_PARENT);
}
async function loadWorkFile() {
  const path = el('workPath').value.trim();
  if (!path) return;
  el('btnLoadFile').disabled = true;
  el('loadError').textContent = 'Loading and validating…';
  try {
    const r = await fetchJson('/api/work/load', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        path,
        workspace_id: STATE.workspace_id
      })
    });
    SELECTED_JOB_ID = null;
    SELECTED_ATTEMPT_ID = null;
    CHECKED_JOBS.clear();
    KNOWN_COLUMNS.clear();
    STDIN_STATUS_BY_ATTEMPT = {};
    LOG_CACHE = null;
    closeWorkFile();
    await fetchState();
    recordFeedback(r.message);
  } catch (e) {
    el('loadError').textContent = e.message;
  } finally {
    updateControls();
  }
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closePanels();
    closeWorkFile();
    el('reportOverlay').hidden = true;
  }
});
setupMonitorLayout();
pollState();
</script></body></html>
"""


class WaveServer:
    """Serve one local manager; page lifetime never owns process lifetime."""

    def __init__(
        self,
        manager: JobManager | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        source_path: str | Path | None = None,
    ) -> None:
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("Wave GUI binds only to loopback (127.0.0.1 or localhost)")
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 0 <= port <= 65535
        ):
            raise ValueError("port must be an integer from 0 to 65535")
        self.manager = manager if manager is not None else JobManager()
        self.host, self._requested_port = host, port
        self.source_path = str(Path(source_path).resolve()) if source_path else None
        self.launch_cwd = Path.cwd()
        self._workspace_id = uuid.uuid4().hex
        self._action_lock = threading.RLock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._httpd.server_port) if self._httpd else self._requested_port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def _state(
        self, job_id=None, attempt_id=None, *, log_after=None, log_attempt=None
    ) -> dict[str, Any]:
        with self._action_lock, self.manager._events_lock:
            try:
                result = self.manager.snapshot(
                    selected_job_id=job_id, attempt_id=attempt_id, include_logs=False
                )
            except ValueError:
                if not attempt_id:
                    raise
                result = self.manager.snapshot(
                    selected_job_id=job_id, include_logs=False
                )
                result["detail"] = None
                result["history_error"] = "Requested attempt is no longer retained"
            if result["detail"] is not None:
                chunk = self.manager.get_job(job_id)._log_snapshot(
                    attempt_id=attempt_id,
                    after_line=log_after,
                    cursor_attempt=log_attempt,
                )
                # Python callers can also append management log messages. Use
                # the interval's counters if one arrived after the metadata read.
                result["detail"].update(
                    log_count=chunk["retained_count"],
                    total_lines=chunk["next_line"],
                    attempt_max_log_lines=chunk["capacity"],
                )
                if log_after is None:
                    # Legacy HTTP readers still get a full log, once in detail.
                    result["detail"]["log_records"] = chunk["records"]
                else:
                    result["detail"]["log_chunk"] = chunk
            result.update(
                workspace_id=self._workspace_id,
                source_path=self.source_path,
                launch_cwd=str(self.launch_cwd),
            )
            return result

    @staticmethod
    def _target(raw: Any) -> tuple[str, str, str]:
        if not isinstance(raw, dict):
            raise ValueError(
                "Each target must include job_id, attempt_id and submission_id"
            )
        values = tuple(raw.get(k) for k in ("job_id", "attempt_id", "submission_id"))
        if not all(isinstance(v, str) and v for v in values):
            raise ValueError(
                "Explicit job, attempt and submission identities are required"
            )
        return values

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        # Serialize load/start/control against this server's workspace replacement.
        with self._action_lock:
            if body.get("workspace_id") != self._workspace_id:
                raise ValueError("Workspace changed; refresh before issuing an action")
            manager = self.manager
            if path == "/api/work/load":
                if manager.started and any(
                    not j.status.is_finished for j in manager.jobs
                ):
                    raise ValueError(
                        "Stop queued and running jobs before replacing this batch"
                    )
                raw = body.get("path")
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError("Python work file path is required")
                target = Path(raw).expanduser()
                if not target.is_absolute():
                    target = self.launch_cwd / target
                target = target.resolve()
                # Late import avoids the CLI -> web launch import cycle.
                from .cli import load_work_file

                replacement = load_work_file(target)
                manager.close()
                self.manager = replacement
                self.source_path = str(target)
                self._workspace_id = uuid.uuid4().hex
                return {
                    "ok": True,
                    "message": "Validated. Review the preview, then Start.",
                }
            if path == "/api/manager/action":
                action = body.get("action")
                actions = {
                    "start": manager.start,
                    "pause": manager.pause,
                    "resume": manager.resume,
                }
                if action not in actions:
                    raise ValueError("Unknown manager action")
                if action != "start" and not manager.started:
                    raise ValueError("Preview has not started; use Start")
                actions[action]()
                return {
                    "ok": True,
                    "message": {
                        "start": "Dispatch started",
                        "pause": "Dispatch paused; running jobs continue",
                        "resume": "Dispatch resumed",
                    }[action],
                }
            if path == "/api/settings/save":
                keys = set(body) - {"workspace_id"}
                if not keys or keys - {
                    "max_workers",
                    "resources",
                    "columns",
                    "max_history",
                }:
                    raise ValueError(
                        "Settings accepts max_workers, resources, columns and max_history only"
                    )
                if any(body[k] is None for k in keys):
                    raise ValueError("Settings values cannot be null")
                info = manager.update_settings(**{k: body[k] for k in keys})
                return {
                    "ok": True,
                    "evicted_history": (
                        info.get("evicted_history", 0) if isinstance(info, dict) else 0
                    ),
                }
            if path == "/api/job/retention":
                raw_lines = body.get("max_log_lines")
                if (
                    isinstance(raw_lines, bool)
                    or not isinstance(raw_lines, int)
                    or raw_lines < 1
                ):
                    raise ValueError("max_log_lines must be an integer >= 1")
                targets = body.get("targets")
                if not isinstance(targets, list) or not targets or len(targets) > 10000:
                    raise ValueError("Provide between 1 and 10000 explicit targets")
                parsed = [self._target(t) for t in targets]
                if len({t[0] for t in parsed}) != len(parsed):
                    raise ValueError("Duplicate job target")
                results = []
                for jid, attempt, submission in parsed:
                    job = manager.get_job(jid)
                    item = {
                        "job_id": jid,
                        "attempt_id": attempt,
                        "submission_id": submission,
                        "name": job.name if job else jid,
                        "ok": False,
                        "evicted_lines": 0,
                    }
                    try:
                        if job is None or job.id != jid:
                            raise ValueError("Unknown job ID")
                        evicted = manager.set_max_log_lines(
                            raw_lines,
                            jid,
                            expected_attempt=attempt,
                            expected_submission=submission,
                        )
                        item["ok"] = True
                        item["evicted_lines"] = evicted
                        item["max_log_lines"] = job.max_log_lines
                        item["message"] = (
                            f"Accepted ({evicted} older line(s) evicted)"
                            if evicted
                            else "Accepted"
                        )
                    except (ValueError, TypeError, RuntimeError) as exc:
                        item["message"] = str(exc)
                    results.append(item)
                return {
                    "ok": True,
                    "action": "max_log_lines",
                    "max_log_lines": raw_lines,
                    "results": results,
                    "affected": sum(item["ok"] for item in results),
                    "evicted_lines": sum(
                        item.get("evicted_lines", 0) for item in results
                    ),
                }
            if path == "/api/job/priority":
                jid, attempt, submission = self._target(body.get("target"))
                manager.set_priority(
                    jid,
                    body.get("priority"),
                    expected_attempt=attempt,
                    expected_submission=submission,
                )
                return {"ok": True}
            if path == "/api/job/action":
                action = body.get("action")
                if action not in ("stop", "force_stop", "rerun", "send", "interrupt"):
                    raise ValueError("Unknown job action")
                targets = body.get("targets")
                if not isinstance(targets, list) or not targets or len(targets) > 10000:
                    raise ValueError("Provide between 1 and 10000 explicit targets")
                parsed = [self._target(t) for t in targets]
                if len({t[0] for t in parsed}) != len(parsed):
                    raise ValueError("Duplicate job target")
                if action == "send" and not isinstance(body.get("text"), str):
                    raise ValueError("send requires text")
                # Cancel queued targets first before running jobs release their slots.
                if action == "stop":
                    parsed.sort(
                        key=lambda t: (
                            manager.get_job(t[0]) is None
                            or manager.get_job(t[0]).status.value != "PENDING"
                        )
                    )
                results = []
                for jid, attempt, submission in parsed:
                    job = manager.get_job(jid)
                    item = {
                        "job_id": jid,
                        "attempt_id": attempt,
                        "submission_id": submission,
                        "name": job.name if job else jid,
                        "ok": False,
                    }
                    try:
                        if job is None or job.id != jid:
                            raise ValueError("Unknown job ID")
                        if action == "rerun":
                            manager.rerun(
                                jid,
                                expected_attempt=attempt,
                                expected_submission=submission,
                            )
                            item["ok"] = True
                            item["new_attempt_id"] = job.attempt_id
                            item["new_attempt_number"] = job.attempt_number
                        else:
                            item["ok"] = bool(
                                manager.control(
                                    jid,
                                    action,
                                    expected_attempt=attempt,
                                    expected_submission=submission,
                                    text=body.get("text"),
                                )
                            )
                        item["message"] = (
                            "Accepted"
                            if item["ok"]
                            else "Not applicable in current state"
                        )
                    except (ValueError, TypeError, RuntimeError) as exc:
                        item["message"] = str(exc)
                    results.append(item)
                return {
                    "ok": True,
                    "action": action,
                    "results": results,
                    "affected": sum(item["ok"] for item in results),
                }
            raise LookupError("Not found")

    def start(self, *, open_browser: bool = False) -> str:
        """Start HTTP only. Neither serving nor reconnecting dispatches jobs."""
        if self._httpd is not None:
            return self.url
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def _headers(self, status: int, content_type: str, size: int) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; object-src 'none'; "
                    "frame-ancestors 'none'; base-uri 'none'",
                )
                self.end_headers()

            def _json(self, payload: Any, status: int = 200) -> None:
                raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(
                    "utf-8"
                )
                self._headers(status, "application/json; charset=utf-8", len(raw))
                self.wfile.write(raw)

            def _local_request(self) -> bool:
                host = self.headers.get("Host")
                if host not in (f"127.0.0.1:{owner.port}", f"localhost:{owner.port}"):
                    self._json({"error": "Invalid local Host"}, 403)
                    return False
                origin = self.headers.get("Origin")
                if (origin and origin != "http://" + host) or self.headers.get(
                    "Sec-Fetch-Site"
                ) == "cross-site":
                    self._json({"error": "Cross-origin requests are not allowed"}, 403)
                    return False
                return True

            def do_GET(self) -> None:
                if not self._local_request():
                    return
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    if parsed.path == "/":
                        raw = HTML_PAGE.encode("utf-8")
                        self._headers(200, "text/html; charset=utf-8", len(raw))
                        self.wfile.write(raw)
                    elif parsed.path == "/api/state":
                        raw_after = query.get("log_after", [None])[0]
                        if raw_after is not None and (
                            not raw_after.isascii()
                            or not raw_after.isdecimal()
                            or len(raw_after) > 18
                        ):
                            raise ValueError("log_after must be a nonnegative integer")
                        self._json(
                            owner._state(
                                query.get("job_id", [None])[0],
                                query.get("attempt_id", [None])[0],
                                log_after=int(raw_after)
                                if raw_after is not None
                                else None,
                                log_attempt=query.get("log_attempt", [None])[0],
                            )
                        )
                    elif parsed.path == "/api/logs":
                        state = owner._state(
                            query.get("job_id", [None])[0],
                            query.get("attempt_id", [None])[0],
                        )
                        if state["detail"] is None:
                            self._json({"error": "Job or attempt not found"}, 404)
                        else:
                            self._json(
                                {
                                    "job_id": state["detail"]["id"],
                                    "attempt_id": state["detail"]["attempt_id"],
                                    "records": state["detail"]["log_records"],
                                }
                            )
                    elif parsed.path == "/api/files":
                        raw = query.get("path", [""])[0]
                        path = Path(raw).expanduser() if raw else owner.launch_cwd
                        if not path.is_absolute():
                            path = owner.launch_cwd / path
                        path = path.resolve()
                        if not path.is_dir():
                            raise ValueError("Choose a directory")
                        entries = []
                        with os.scandir(path) as scan:
                            for entry in scan:
                                try:
                                    directory = entry.is_dir()
                                    if directory or (
                                        entry.is_file()
                                        and entry.name.lower().endswith(".py")
                                    ):
                                        entries.append(
                                            {
                                                "name": entry.name,
                                                "path": str(path / entry.name),
                                                "directory": directory,
                                            }
                                        )
                                except OSError:
                                    continue
                        entries.sort(
                            key=lambda e: (not e["directory"], e["name"].casefold())
                        )
                        self._json(
                            {
                                "path": str(path),
                                "parent": str(path.parent),
                                "entries": entries,
                            }
                        )
                    else:
                        self._json({"error": "Not found"}, 404)
                except (ValueError, TypeError, OSError) as exc:
                    self._json({"error": str(exc)}, 400)

            def do_POST(self) -> None:
                if not self._local_request():
                    return
                if (
                    self.headers.get("Content-Type", "").split(";")[0].strip()
                    != "application/json"
                ):
                    self._json({"error": "Content-Type must be application/json"}, 415)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 2 * 1024 * 1024 or self.headers.get(
                        "Transfer-Encoding"
                    ):
                        raise ValueError("Invalid request body size")
                    body = json.loads(self.rfile.read(size).decode("utf-8"))
                    if not isinstance(body, dict):
                        raise ValueError("JSON body must be an object")
                    response = owner._post(urllib.parse.urlsplit(self.path).path, body)
                    self._json(response)
                except LookupError as exc:
                    self._json({"error": str(exc)}, 404)
                except (ValueError, TypeError, RuntimeError, OSError) as exc:
                    self._json({"error": str(exc)}, 400)

        self._httpd = ThreadingHTTPServer((self.host, self._requested_port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="WaveServer-HTTP", daemon=True
        )
        self._thread.start()
        if open_browser:
            webbrowser.open(self.url)
        return self.url

    def stop(self) -> None:
        """Stop the HTTP service only; the caller owns manager cleanup."""
        httpd, self._httpd = self._httpd, None
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None


def launch(
    manager: JobManager | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    block: bool = True,
    source_path: str | Path | None = None,
) -> WaveServer:
    """Open a preview; explicit Start dispatches. Ctrl+C closes the core."""
    server = WaveServer(manager, host=host, port=port, source_path=source_path)
    url = server.start(open_browser=open_browser)
    print(f"[Wave] GUI: {url} (Ctrl+C shuts down the core; closing the page does not)")
    if block:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[Wave] Stopping jobs and closing the core…")
        finally:
            server.manager.close()
            server.stop()
    return server
