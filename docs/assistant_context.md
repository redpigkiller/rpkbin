# Assistant Collaboration Context

This file records working agreements and architectural context for future AI
assistant sessions on this repository. It is intended to be pasted into a new
conversation or referenced at the start of a thread.

## Communication Style

- Use Traditional Chinese with the user unless code, API names, or English docs
  are clearer in English.
- Be direct, pragmatic, and technically explicit. Avoid vague reassurance.
- Explain assumptions, risks, and tradeoffs before making architectural changes.
- If the request is exploratory, discuss and plan first. If the request is an
  implementation request and scope is clear, proceed without asking for extra
  confirmation.
- If permission or environment issues appear, do not repeatedly retry the same
  failing command. Report the blocker and the useful partial result.
- Keep the user updated during longer work, but keep updates short and concrete.

## Coding Rules

- Preserve user changes. Do not revert unrelated files or use destructive git
  commands unless explicitly requested.
- Prefer minimal, local changes that follow the existing style.
- Use existing abstractions before adding new ones. Add abstractions only when
  they remove real complexity or match an existing pattern.
- Use `rg` / `rg --files` for search when available.
- Use `apply_patch` for manual edits.
- Run focused tests for the touched area. If full tests fail due known
  environment/permission problems, report that separately from code failures.
- For review requests, lead with findings and concrete file/line references.

## Wave Mental Model

Wave is a general-purpose job orchestration and monitoring tool. It should not
be designed specifically for VCS, UCLI, or any single domain workflow.

Core boundaries:

- `CmdJob` is the default batch/log-oriented job type. It uses PIPE-based I/O
  and should remain efficient for large stdout streams.
- `PtyCmdJob` / `PtyJob` is the interactive job type. It uses a PTY so child
  processes can see a terminal-like environment. Terminal keys such as Ctrl-C
  should be sent with `send-key <job> ctrl-c`, not `send-signal <job> SIGINT`.
- `send-signal` means OS signal/control channel.
- `send-line` means stdin/data channel plus a trailing newline.
- `send-key` means terminal key byte through PTY.
- The TUI should remain a monitoring/control surface, not a full terminal
  emulator unless that is intentionally designed as a separate larger feature.

Important UX principles:

- Dashboard should prioritize smooth job focus switching and bounded preview.
- Job Detail should prioritize smooth switching between events, parsed data,
  system info, terminal output, and raw logs.
- Expensive panes should refresh lazily when possible.
- Prefer profile-style CLI presets for performance tuning over many user-facing
  knobs.
- Copy/paste ergonomics matter, especially on Linux remote desktop setups, but
  disabling Textual mouse behavior should be evaluated carefully.

## Wave Architecture Decisions Already Discussed

- Rerun should create a new job instance, such as `sim#rerun1`, instead of
  mutating or rewinding the old job. This preserves logs, events, parser state,
  and TUI row caches.
- Hooks should support job-level actions, but session-level control from hooks
  must be opt-in/guarded to avoid accidental global side effects.
- User-defined actions are useful, but action/session action boundaries should
  remain explicit:
  - job actions operate on a selected job
  - session actions operate on global session state
  - hook-triggered access should require explicit allow flags
- PTY support is useful if the target program behaves differently when attached
  to a real terminal. However, it does not guarantee every tool will enter an
  interactive mode; program-specific behavior may still matter.
- Do not build a true terminal emulator into the existing TUI casually. If the
  project later needs one, evaluate it as a separate feature with clear scope.

## Performance Context

The user runs on Linux/RHEL via Windows + NoMachine remote connection, sometimes
with heavy log output and no GPU. Lag may come from:

- large stdout volume
- parser/hook work
- TUI render/append cost
- remote desktop rendering
- terminal widget behavior

Known optimization directions:

- Keep dashboard preview bounded and tail-first.
- Avoid rebuilding heavy panes when inactive.
- Prefer lighter text widgets for plain logs when possible.
- Avoid per-character stdout reads unless `flush_tokens` requires partial-line
  detection.
- Add perf/debug counters as opt-in diagnostics, not normal UI noise.

## Documentation Preference

Docs should be beginner-friendly:

- Start with "what do I want to do?" tables.
- Provide minimal runnable examples before advanced options.
- Explain `CmdJob` vs `PtyJob`, `input` vs `key` vs `signal`, and TUI vs
  headless usage clearly.
- Keep advanced architecture notes available, but not in the first path for new
  users.

## Suggested First Message For A New Session

Paste this at the start of a new assistant session:

```text
Please first read docs/assistant_context.md in this repo and follow it for
communication style, coding rules, and Wave architecture context. Then inspect
the current git diff before making changes, because the worktree may contain
ongoing user/assistant edits that must not be reverted.
```
