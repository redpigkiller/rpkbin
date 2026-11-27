"""Reproducible source/reference/simulator differential execution."""

import ast
from collections.abc import Mapping
from dataclasses import dataclass
import random

from .assemble import _assemble, _encode, _placement
from .compiler import compile_source
from .errors import ConfigError, SourceError, TargetError, UnsupportedError
from .expand import MInstr
from .frontend import _parse
from .ir import Imm
from .lang import run_reference
from .machine import _in_pool
from .target import MemLoc, T


@dataclass(frozen=True)
class DiffReport:
    checked: int
    failures: tuple
    unsupported: tuple
    timeouts: int
    _unverified: tuple=()


class _PoolAccessError(ValueError):
    pass


class _ReferenceCells(dict):
    """Freeze cells on first reference read, before machine execution begins."""
    def __init__(self, bits, reader, cells, space=None, pool=None):
        super().__init__(cells)
        self._limit, self._reader = 1 << bits, reader
        self._space, self._pool = space, pool or {}

    def _check_source_address(self, address, *, direct):
        if _in_pool(self._pool, self._space, address):
            raise _PoolAccessError(f"accesses spill pool at {self._space}:{address}")

    def __len__(self):
        return self._limit

    def __missing__(self,address):
        value=self._reader(address)
        self[address]=value
        return value

    def __deepcopy__(self,memo):
        result=type(self).__new__(type(self))
        dict.__init__(result,self)
        result._limit,result._reader=self._limit,self._reader
        result._space, result._pool = self._space, self._pool
        return result


class _WindowAccessError(ValueError):
    pass


class _WindowCells(_ReferenceCells):
    """Keep an access limit even when the source writes a new cell."""

    def __init__(self, bits, space, cells, *, unlisted, pool=None):
        super().__init__(bits, None, cells, space, pool)
        self._allowed = frozenset(cells)
        self._unlisted = unlisted

    def _check(self, address):
        if not self._unlisted and address not in self._allowed:
            raise _WindowAccessError(f"{self._space}[{address}] outside sampling window and spill pool")

    def _check_source_address(self, address, *, direct):
        super()._check_source_address(address, direct=direct)
        if not 0 <= address < self._limit or (
                address not in self._allowed and not (direct and self._unlisted)):
            raise _WindowAccessError(f"{self._space}[{address}] outside sampling window and spill pool")

    def __missing__(self, address):
        self._check(address)
        return 0

    def __setitem__(self, address, value):
        self._check(address)
        super().__setitem__(address, value)

    def __deepcopy__(self, memo):
        result = super().__deepcopy__(memo)
        result._space = self._space
        result._allowed = self._allowed
        result._unlisted = self._unlisted
        return result


def _validate_window(window, addr_bits):
    if type(window) is int:
        return
    if not isinstance(window, Mapping):
        raise ConfigError(f"window {window!r}: expected an integer or a mapping")
    for space, bounds in window.items():
        if (space not in addr_bits or not isinstance(bounds, tuple) or len(bounds) != 2
                or any(type(value) is not int for value in bounds)):
            raise ConfigError(f"window {space!r}={bounds!r}: expected a data space and integer bounds")
        low, high = bounds
        width = high - low + 1
        if low < 0 or high >= 1 << addr_bits[space] or width < 512:
            raise ConfigError(f"window {space!r}={bounds!r}: width {width}; "
                              "expected an in-range inclusive interval of width at least 512")


def _module(source, name):
    tree = ast.parse(source)
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    needed, pending = {name}, [name]
    while pending:
        current = pending.pop()
        for node in ast.walk(functions[current]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions and node.func.id not in needed:
                needed.add(node.func.id)
                pending.append(node.func.id)
    tree.body = [n for n in tree.body if not isinstance(n, ast.FunctionDef) or n.name in needed]
    return ast.unparse(tree)


def _function_names(source):
    try:
        tree=ast.parse(source)
    except SyntaxError as exc:
        raise SourceError(f"<source>:{exc.lineno}: {exc.msg}") from exc
    return [n.name for n in tree.body if isinstance(n,ast.FunctionDef)
            and not any(isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=="extern" for d in n.decorator_list)]


def _pointer_range(space, window, pool):
    if space not in window:
        raise ConfigError(f"Ptr({space!r}) argument has no sampling window")
    low, high = window[space]
    stop = low + (high - low + 1) // 2
    if pool is not None and low <= pool[1] and pool[0] < stop:
        raise ConfigError(f"window {space!r}: pointer range [{low}, {stop}) overlaps spill pool {pool!r}")
    return range(low, stop)


def _input_sets(function, config, rng, count, window=None):
    choices = []
    for _, ty in function.params:
        limit = 1 << ty.width
        if ty.space:
            pool = config.spill_pool.get(ty.space)
            if isinstance(window, Mapping):
                values = _pointer_range(ty.space, window, pool)
                boundary = [values.start, values.start + 0x3F]
            else:
                values = [i for i in range(0x80, 1 << 8) if pool is None or not pool[0] <= i <= pool[1]]
                if not values:
                    raise UnsupportedError("differential pointer input window is occupied by pool")
                boundary = [i for i in (0x80, 0xbf) if i in values]
            choices.append(boundary + [rng.choice(values) for _ in range(24)])
        else:
            boundary = (0, 1, 2, 0x7f, 0x80, limit - 2, limit - 1) if ty.width == 8 else (0, 1, (1 << 8) - 1, 1 << 8, limit // 2 - 1, limit // 2, limit - 1)
            choices.append(list(boundary) + [rng.randrange(limit) for _ in range(24)])
    if not choices:
        return [()] * count
    output = []
    for index, values in enumerate(choices):
        for value in values:
            row = [rng.choice(c) for c in choices]
            row[index] = value
            output.append(tuple(row))
    minimum = max(count, 120 if len(choices) > 1 else len(choices[0]))
    output.extend(tuple(rng.choice(c) for c in choices) for _ in range(max(40, minimum - len(output))))
    return output


def _native(binding, name):
    return (binding.names or {}).get(name, name)


def _read(sim, binding, target, location):
    if isinstance(location, str):
        return sim.read(_native(binding, location))
    return sum(sim.read(f"{_native(binding, location.space)}[{location.addr + i}]") << (i * target.cell_bits) for i in range(location.n))


def _write(sim, binding, target, location, value):
    if isinstance(location, str):
        sim.poke(_native(binding, location), value)
    else:
        for i in range(location.n):
            sim.poke(f"{_native(binding, location.space)}[{location.addr + i}]", (value >> (i * target.cell_bits)) & ((1 << target.cell_bits) - 1))


def run_differential(source, target, sim, config, *, stubs=None, seeds, inputs_per_function, window, post_check=None):
    checked, failures, unsupported, timeouts = 0, [], [], 0
    unverified=[]
    names = _function_names(source)
    addr_bits = {s.name: s.addr_bits for s in target.spaces if s.kind == "data"}
    _validate_window(window, addr_bits)
    shifted = isinstance(window, Mapping)
    for name in names:
        subset = _module(source, name)
        try:
            if shifted:
                typed = _parse(subset, target, "<source>").functions[name]
                for _, ty in typed.params:
                    if ty.space:
                        _pointer_range(ty.space, window, config.spill_pool.get(ty.space))
            result = compile_source(subset, target, config)
        except UnsupportedError as exc:
            unsupported.append((name, str(exc)))
            continue
        function = result.functions[name]
        if any(fn.verified=="unverified: asm" for fn in result.functions.values()):
            unverified.append((name,"unverified: asm"))
            continue
        if not shifted:
            typed = _parse(subset, target, "<differential>").functions[name]
        call = min((f for f in target.forms if f.op == "call" and any(isinstance(o, T) and o.abs_bits is not None for o in f.operands)),
                   key=lambda f: f.bytes, default=None)
        if call is None:
            raise UnsupportedError(f"{name}: verify driver requires an absolute call form")
        origin = call.bytes + len(sim.halt_cells)
        _, _, _, end = _placement(result.functions, origin)
        external_symbols, external_cells = {}, {}
        for external, cells in (stubs or {}).items():
            external_symbols[external] = end
            external_cells.update((end + i, cell) for i, cell in enumerate(cells))
            end += len(cells)
        program = _assemble(result, target, origin, external_symbols)
        program_space = next(s for s in target.spaces if s.kind == "program")
        if end > 1 << program_space.addr_bits:
            raise UnsupportedError("program does not fit program address space")
        operand = next(o for o in call.operands if isinstance(o, T))
        driver_call = MInstr(call, {operand.name: name})
        driver = (*_encode(driver_call, 0, program.symbols, target), *sim.halt_cells)
        program_cells = dict(program.cells)
        program_cells.update(external_cells)
        program_cells.update(enumerate(driver))
        direct_addresses = {}
        if shifted:
            for compiled in result.functions.values():
                for block in compiled._ir.blocks:
                    for instr in block.instrs:
                        if instr.op in ("load", "store") and isinstance(instr.args[0], Imm):
                            space = instr.meta["space"]
                            if space not in window:
                                direct_addresses.setdefault(space, set()).add(instr.args[0].value)
        fn_attempts = 0
        fn_timeouts = 0
        for seed in seeds:
            rng = random.Random(seed)
            for arguments in _input_sets(typed, config, rng, inputs_per_function, window):
                fn_attempts += 1
                try:
                    instance = sim.make_sim()
                    if sim.init is not None:
                        sim.init(instance)
                except Exception as exc:
                    raise TargetError(f"simulation binding make_sim/init failed for {name!r}") from exc
                instance.load_cells(sim.program_space, program_cells)
                for reg in target.regs:
                    instance.poke(_native(sim, reg.name), rng.getrandbits(reg.bits))
                memory = {}
                for space in target.spaces:
                    if space.kind != "data":
                        continue
                    if shifted:
                        low, high = window.get(space.name, (0, -1))
                        addresses = set(range(low, high + 1))
                    else:
                        addresses = set(range(min(window, 1 << space.addr_bits)))
                    if space.name in config.spill_pool:
                        low, high = config.spill_pool[space.name]
                        addresses.update(range(low, high + 1))
                    cells = {a: rng.getrandbits(target.cell_bits) for a in sorted(addresses)}
                    initial = cells
                    if shifted and space.name in direct_addresses:
                        initial = {**dict.fromkeys(sorted(direct_addresses[space.name]), 0), **cells}
                    instance.load_cells(_native(sim, space.name), initial)
                    def reader(address,native=_native(sim,space.name),current=instance):
                        return current.read(f"{native}[{address}]")
                    if shifted:
                        memory[space.name] = _WindowCells(space.addr_bits, space.name, cells,
                                                         unlisted=space.name not in window, pool=config.spill_pool)
                    else:
                        memory[space.name] = _ReferenceCells(space.addr_bits, reader, cells, space.name, config.spill_pool)
                for location, value in zip(function._ir.abi["args"], arguments):
                    _write(instance, sim, target, location, value)
                    if isinstance(location, MemLoc):
                        for i in range(location.n):
                            cells = memory[location.space]
                            address = location.addr + i
                            if not shifted or address in cells:
                                cells[address] = (value >> (i * target.cell_bits)) & ((1 << target.cell_bits) - 1)
                before = {r.name: instance.read(_native(sim, r.name)) for r in target.regs}
                try:
                    expected, expected_memory = run_reference(subset, name, arguments, memory, addr_bits=addr_bits)
                except TimeoutError:
                    timeouts += 1
                    fn_timeouts += 1
                    continue
                except SourceError as exc:
                    if not (isinstance(exc.__cause__, _PoolAccessError) or
                            shifted and isinstance(exc.__cause__, _WindowAccessError)):
                        raise
                    failures.append((name, seed, arguments, str(exc), result.asm))
                    checked += 1
                    continue
                try:
                    if post_check is not None:
                        post_check("before", instance)
                    outcome = instance.run(max_steps=200000)
                    assert outcome.stop_reason == "halted", f"{outcome.stop_reason}, address={instance.read(sim.pc)}"
                    if post_check is not None:
                        post_check("after", instance)
                    if function._ir.abi["ret"] is not None:
                        actual = _read(instance, sim, target, function._ir.abi["ret"])
                        assert actual == int(expected), f"return {actual} != {expected}"
                    for space, cells in expected_memory.items():
                        if shifted and space not in window:
                            continue
                        pool = config.spill_pool.get(space)
                        for address, value in cells.items():
                            if pool is None or not pool[0] <= address <= pool[1]:
                                actual = instance.read(f"{_native(sim, space)}[{address}]")
                                assert actual == value, f"{space}[{address}]: {actual} != {value}"
                    for reg in target.regs:
                        if reg.name not in function.clobbers:
                            assert instance.read(_native(sim, reg.name)) == before[reg.name], f"unreported clobber {reg.name}"
                except AssertionError as exc:
                    failures.append((name, seed, arguments, str(exc), result.asm))
                checked += 1
        if fn_attempts and fn_timeouts * 10 > fn_attempts:
            failures.append((name, "reference timeout ratio", fn_timeouts, fn_attempts))
    return DiffReport(checked, tuple(failures), tuple(unsupported), timeouts,tuple(unverified))
