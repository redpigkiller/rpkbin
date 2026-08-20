# Debug & DAP reference

`rpkbin.debug` provides a small, experimental integration boundary between an
instruction-oriented debugger and clients that speak the Debug Adapter Protocol
(DAP). It does not provide an emulator or impose a shared machine-state model.

The core package has no additional dependency: DAP framing, dispatch, cooperative
cancellation, and stdio transport use the Python standard library.

## Ownership and boundaries

`rpkbin.debug` owns:

- immutable, target-neutral records for contexts, locations, scopes, variables,
  breakpoints, capabilities, and stop results;
- structural protocols implemented by a target adapter;
- `Content-Length` framed JSON transport;
- DAP request/response/event ordering and continue/pause worker lifecycle.

The target package owns:

- instruction execution, scheduling, clocks, and machine state;
- register and variable schemas;
- source maps and instruction rendering;
- breakpoint-condition parsing and evaluation;
- checkpoint/history implementation;
- memory serialization and target-specific address interpretation.

There is intentionally no `BaseEmulator` or `BaseDebugger`. A single-context
target and a multi-context target implement the same small adapter without
sharing their internal state.

## Minimal adapter

```python
from threading import Event

from rpkbin.debug import (
    AddressSpace,
    BreakpointResult,
    DebugCapabilities,
    DebugLocation,
    DebugScope,
    DebugVariable,
    ExecutionContext,
    StopReason,
    StopResult,
)


class TargetAdapter:
    capabilities = DebugCapabilities(supports_reverse_step=True)

    def address_spaces(self):
        # Addresses in this space are 16-bit words, not bytes.
        return (AddressSpace("program", unit_bits=16),)

    def contexts(self):
        return (ExecutionContext(1, "main"),)

    def location(self, context_id):
        return DebugLocation("program", self.debugger.pc, instruction="nop")

    def scopes(self, context_id):
        return (DebugScope("registers", "Registers"),)

    def variables(self, context_id, scope_id):
        return (DebugVariable("pc", hex(self.debugger.pc), "address"),)

    def set_instruction_breakpoints(self, breakpoints):
        # condition_text is opaque: interpret it here or reject it.
        return tuple(
            BreakpointResult(True, bp.address_space, bp.address)
            for bp in breakpoints
        )

    def step_instruction(self, context_id):
        self.debugger.step()
        return StopResult(StopReason.STEP, context_id, self.location(context_id))

    def continue_execution(self, context_id, cancel: Event):
        while not cancel.is_set():
            stop = self.debugger.run_one()
            if stop:
                return StopResult(
                    StopReason.BREAKPOINT, context_id or 1, self.location(1)
                )
        return StopResult(StopReason.PAUSE, context_id or 1, self.location(1))

    def reverse_step(self, context_id):
        self.debugger.reverse_step()
        return StopResult(StopReason.STEP, context_id, self.location(context_id))
```

A `DebugSessionFactory` supplies `capabilities`, `supports_attach`, and
`launch(arguments)`. Implement the optional `SupportsAttach` protocol only when
attaching has real target semantics. Start a stdio adapter with
`serve_stdio(factory)`.

The factory and target must advertise identical target capabilities. This keeps
the `initialize` response honest before a concrete launch target is installed.

## DAP lifecycle

The server supports:

| Request | Behavior |
| --- | --- |
| `initialize` | Advertises configuration, instruction-breakpoint, and reverse-step capabilities |
| `launch` | Creates and validates a target; emits `initialized` after the response |
| `attach` | Optional; rejected unless the factory advertises and implements it |
| `configurationDone` | Emits an entry stop by default, or starts continue when launch uses `stopOnEntry: false` |
| `threads` | Maps execution contexts to DAP threads |
| `stackTrace` | Returns one synthetic frame per selected context; it does not invent a call stack |
| `scopes`, `variables` | Delegates target-owned presentation through short-lived handles |
| `setInstructionBreakpoints` | Uses `<address-space>:<address>` references in target address units |
| `setBreakpoints` | Optional source mapping; unsupported targets return unverified breakpoints |
| `continue`, `pause` | Runs continue on a worker and uses cooperative cancellation; continue is global unless the target advertises single-context execution |
| `stepIn` | Executes exactly one target instruction |
| `stepBack` | Optional reverse instruction step |
| `disconnect` | Cancels, joins the worker, then closes the target |

Execution invalidates frame and variable handles. Clients must request a fresh
stack and scopes after each stop. Target exceptions during continue emit an
`output` event followed by an exception `stopped` event.

## Known limitations

- `next` is deliberately unsupported. Mapping it to `stepIn` would claim
  step-over semantics that an instruction-only target does not have.
- `readMemory`, `disassemble`, and `evaluate` are deliberately unsupported.
  Memory serialization and expression languages require additional explicit
  target capabilities.
- Instruction-reference offsets use the selected address space's units. The DAP
  layer never silently converts word addresses to bytes.
- Pause is cooperative. `continue_execution` must observe the supplied
  `threading.Event`; a non-cooperative target cannot be safely closed while its
  worker remains active.
- `supports_single_thread_execution` defaults to false. In that mode a DAP
  `continue` request resumes the complete target even when the client supplies a
  thread ID; only targets that advertise the capability receive that context ID.
  The DAP-required thread ID is still validated in both modes.
- Transport is stdio only. TCP hosting, VS Code extension packaging, UI, and
  target adapters belong to downstream integrations.

Focused tests:

```bash
python -m pytest tests/debug -q
```
