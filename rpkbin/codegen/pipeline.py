"""End-to-end codegen pipeline.

Three entry points are provided:

``run_codegen(func, target, patterns)``
    Original API: accepts a hand-built ``lir.Function`` and runs
    rewrite → isel.  Unchanged from the initial implementation.

``run_codegen_from_hir(hfunc, target, patterns, register_model)``
    Accepts a ``HFunction`` and runs the full pipeline:
    validate → lower → validate LIR → rewrite → isel.

``run_codegen_from_fragment(fragment, target, patterns, register_model)``
    Accepts an ``HFragment`` and runs the fragment pipeline:
    validate → lower → validate LIR → rewrite → validate → isel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .asm import AsmFunction
from .hir import HFragment, HFunction
from .hir_validate import validate_hfragment, validate_hfunction
from .lower import lower_fragment, lower_function
from .lir import Fragment, Function, validate_fragment, validate_function
from .isel import select_fragment_instructions, select_instructions
from .patterns import RewritePattern
from .rewrite import rewrite_fragment, rewrite_function
from .target import RegisterModel, Target


@dataclass(frozen=True)
class CodegenResult:
    """Result of a codegen pipeline run.

    Fields
    ------
    input_hir : HFunction | None
        The original HIR input.  ``None`` when the pipeline was entered via
        ``run_codegen`` (i.e. directly from LIR, without an HIR front-end).
        This field is always present to keep the dataclass layout stable.
    input_lir : Function
        The LIR function after lowering (or the hand-built LIR if HIR was
        not used).  Named ``input_lir`` to distinguish from the rewritten
        version.
    rewritten_lir : Function
        The LIR function after the pattern-rewrite pass.
    asm : AsmFunction
        The pseudo-ASM output.
    applied_patterns : Sequence[str]
        Names of rewrite patterns that were applied, in application order.
    register_assignment : dict[str, str] | None
        Maps var/vreg names to physical register names, populated when a
        ``RegisterModel`` is supplied.  ``None`` when no allocation was
        performed (backward-compatible default).

    Backward-compatibility note
    ---------------------------
    The original ``CodegenResult`` had ``input_ir`` (not ``input_lir``).
    A property alias ``input_ir`` is provided so existing code that reads
    ``result.input_ir`` continues to work.
    """

    input_hir: HFunction | None
    input_lir: Function
    rewritten_lir: Function
    asm: AsmFunction
    applied_patterns: Sequence[str]
    register_assignment: dict | None = None

    # ------------------------------------------------------------------
    # Backward-compatibility helpers
    # ------------------------------------------------------------------

    @property
    def input_ir(self) -> Function:
        """Alias for ``input_lir`` — kept for backward compatibility."""
        return self.input_lir

    @property
    def asm_text(self) -> str:
        return self.asm.format()


# ---------------------------------------------------------------------------
# Original LIR-first entry point (unchanged behaviour)
# ---------------------------------------------------------------------------

def run_codegen(
    func: Function,
    target: Target,
    patterns: Iterable[RewritePattern] = (),
) -> CodegenResult:
    """Run the codegen pipeline starting from a hand-built LIR function.

    Steps: validate LIR → rewrite → validate rewritten LIR → isel.

    This is the original API and its behaviour is unchanged.  Existing tests
    that use this function will continue to pass.
    """
    validate_function(func)
    rewrite_result = rewrite_function(func, patterns)
    validate_function(rewrite_result.function)
    asm = select_instructions(rewrite_result.function, target)
    return CodegenResult(
        input_hir=None,
        input_lir=func,
        rewritten_lir=rewrite_result.function,
        asm=asm,
        applied_patterns=rewrite_result.applied,
    )


# ---------------------------------------------------------------------------
# HIR-first entry point
# ---------------------------------------------------------------------------

def run_codegen_from_hir(
    hfunc: HFunction,
    target: Target,
    patterns: Iterable[RewritePattern] = (),
    register_model: RegisterModel | None = None,
) -> CodegenResult:
    """Run the full HIR → pseudo ASM pipeline.

    Steps
    -----
    1. ``hir_validate(hfunc, register_model)`` — source-level type checks.
    2. ``lower_function(hfunc)`` → ``lir_func``.
    3. ``validate_function(lir_func)`` — structural LIR checks.
    4. ``rewrite_function(lir_func, patterns)`` — pattern simplification.
    5. ``validate_function(rewritten)`` — post-rewrite integrity check.
    6. ``allocate_registers(rewritten, register_model)`` — physical reg
       assignment (skipped when ``register_model`` is ``None``).
    7. ``select_instructions(allocated, target)`` → pseudo ASM.

    Parameters
    ----------
    hfunc:
        The HIR function to compile.
    target:
        A ``Target`` Protocol implementation (e.g. ``ToyTarget()``).
    patterns:
        Optional rewrite patterns to apply between lowering and isel.
    register_model:
        Optional ``RegisterModel`` Protocol implementation.  When provided,
        the HIR validator checks physical-register hints and aliases, and the
        register allocator assigns physical registers.
        Pass ``None`` to skip those checks and allocation.

    Returns
    -------
    CodegenResult
        Contains all intermediate representations and the pseudo-ASM output.
        ``result.register_assignment`` contains the var→reg map when a
        ``register_model`` was supplied, otherwise ``None``.

    Raises
    ------
    HIRValidationError
        If the HIR fails validation.
    NotImplementedError
        If the HIR contains constructs not yet supported.
    ValueError
        If the produced LIR fails structural validation.
    RegisterAllocationError
        If allocation fails because the available registers cannot satisfy it.
    """
    # Step 1: HIR validation
    validate_hfunction(hfunc, register_model)

    # Step 2: Lower HIR → LIR
    lir_func = lower_function(hfunc)

    # Step 3: Structural LIR validation
    validate_function(lir_func)

    # Step 4 + 5: Rewrite + post-rewrite validation
    rewrite_result = rewrite_function(lir_func, patterns)
    validate_function(rewrite_result.function)

    # Step 6: Register allocation (only when register_model is provided)
    reg_assignment: dict | None = None
    if register_model is not None:
        from .register_alloc import allocate_registers
        allocated, reg_assignment = allocate_registers(
            rewrite_result.function,
            register_model,
            var_hints=_extract_hints(hfunc),
        )
    else:
        allocated = rewrite_result.function

    # Step 7: Instruction selection
    asm = select_instructions(allocated, target)

    return CodegenResult(
        input_hir=hfunc,
        input_lir=lir_func,
        rewritten_lir=rewrite_result.function,
        asm=asm,
        applied_patterns=rewrite_result.applied,
        register_assignment=reg_assignment,
    )


# ---------------------------------------------------------------------------
# Helper: extract @hint annotations from HIR
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fragment pipeline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FragmentCodegenResult:
    """Result of a fragment codegen pipeline run.

    This is a separate result type — fragment output is NOT embedded in
    the existing ``CodegenResult``.

    Fields
    ------
    input_hir : HFragment
        The original HFragment input.
    input_lir : Fragment
        The LIR Fragment after lowering.
    rewritten_lir : Fragment
        The LIR Fragment after the pattern-rewrite pass.
    asm : AsmFunction
        The pseudo-ASM output.
    applied_patterns : Sequence[str]
        Names of rewrite patterns that were applied, in application order.
    """

    input_hir: HFragment
    input_lir: Fragment
    rewritten_lir: Fragment
    asm: AsmFunction
    applied_patterns: Sequence[str]
    register_assignment: dict | None = None


def run_codegen_from_fragment(
    fragment: HFragment,
    target,
    patterns: Iterable[RewritePattern] = (),
    register_model=None,
) -> FragmentCodegenResult:
    """Run the full HFragment → pseudo-ASM pipeline.

    Steps
    -----
    1. ``validate_hfragment(fragment, register_model)`` — HIR validation.
    2. ``lower_fragment(fragment)`` → ``lir_fragment``.
    3. ``validate_fragment(lir_fragment)`` — structural LIR checks.
    4. ``rewrite_fragment(lir_fragment, patterns)`` — pattern rewrite.
    5. ``validate_fragment(rewritten)`` — post-rewrite integrity check.
    6. Allocate locals from ``scratch_regs`` without spilling.
    7. ``select_fragment_instructions(allocated, target)`` → pseudo ASM.

    Parameters
    ----------
    fragment:
        The HFragment to compile.
    target:
        A target object.  Must implement ``select_fragment_instructions``
        (see ``FragmentTarget`` protocol in ``target.py``).
    patterns:
        Optional rewrite patterns to apply between lowering and isel.
    register_model:
        Optional ``RegisterModel`` protocol instance.  When provided, the
        HIR validator performs additional checks and Fragment locals are
        allocated only from ``scratch_regs``. Fragment allocation never spills.
        Fragments that still contain locals after rewrite require a
        ``register_model``; the selector no longer accepts unresolved
        symbolic locals.

    Returns
    -------
    FragmentCodegenResult
        Contains all intermediate representations and the pseudo-ASM output.

    Raises
    ------
    HIRValidationError
        If the HIR fails validation.
    TypeError
        If *target* does not support ``select_fragment_instructions``.
    ValueError
        If the produced LIR fails structural validation.
    """
    # Step 1: HIR validation (register_model used only for validation)
    validate_hfragment(fragment, register_model)

    # Step 2: Lower HIR → LIR
    lir_fragment = lower_fragment(fragment)

    # Step 3: Structural LIR validation
    validate_fragment(lir_fragment)

    # Step 4: Rewrite
    rewritten, applied = rewrite_fragment(lir_fragment, patterns)

    # Step 5: Post-rewrite validation
    validate_fragment(rewritten)

    # Step 6: scratch-only, no-spill Fragment register allocation
    reg_assignment: dict | None = None
    allocated = rewritten
    if register_model is not None:
        from .register_alloc import allocate_fragment_registers
        allocated, reg_assignment = allocate_fragment_registers(
            rewritten, register_model
        )
    else:
        from .register_alloc import _fragment_unresolved_local_names
        unresolved = _fragment_unresolved_local_names(rewritten)
        if unresolved:
            raise ValueError(
                "fragment locals require register_model for scratch allocation: "
                + ", ".join(repr(name) for name in sorted(unresolved))
            )

    # Step 7: Fragment instruction selection
    asm = select_fragment_instructions(allocated, target)

    return FragmentCodegenResult(
        input_hir=fragment,
        input_lir=lir_fragment,
        rewritten_lir=rewritten,
        asm=asm,
        applied_patterns=applied,
        register_assignment=reg_assignment,
    )


def _extract_hints(hfunc: HFunction) -> dict:
    """Extract all @hint annotations from HIR function params.

    Returns a dict mapping param name → hint string for params that
    carry a ``reg_hint``.
    """
    hints = {}
    for p in hfunc.params:
        if p.reg_hint:
            hints[p.name] = p.reg_hint
    return hints
