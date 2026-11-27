"""State accessors; all mutation passes through one transaction owner."""

from __future__ import annotations

from .errors import StateAccessError, _Stop, _is_int


def _int(value, name):
    if not _is_int(value):
        raise StateAccessError(f"{name}={value!r}: expected int")
    return value


class RegAccess:
    def __init__(self, state):
        self._state = state

    def __getitem__(self, name):
        return self._state._sim._reg_read(name, semantic=not self._state._readonly)

    def __setitem__(self, name, value):
        self._state._write_check()
        self._state._sim._reg_write(name, value)


class IrqAccess:
    def __init__(self, state):
        self._state = state

    def __getitem__(self, line):
        sim = self._state._sim
        if line not in sim._irq:
            raise StateAccessError(f"irq={line!r}: unknown line")
        value = sim._irq[line]
        if not self._state._readonly:
            sim._observe("irq", f"irq.{line}", None, "r", None, value)
        return value

    def __setitem__(self, line, value):
        self._state._write_check()
        self._state._sim._irq_write(line, value)


class MemAccess:
    def __init__(self, state):
        self._state = state

    def __getitem__(self, space):
        if space not in self._state._sim._mcu._spaces:
            raise StateAccessError(f"space={space!r}: unknown space")
        return SpaceAccess(self._state, space)


class SpaceAccess:
    def __init__(self, state, space):
        self._state = state
        self._space = space

    def __getitem__(self, addr: int) -> int:
        return self._state._sim._mem_read(self._space, addr, peek=self._state._readonly)

    def __setitem__(self, addr: int, value: int) -> None:
        self._state._write_check()
        self._state._sim._mem_write(self._space, addr, value)

    def read_word(self, addr: int, n: int, order: str) -> int:
        self._word_check(addr, n, order)
        values = [self[addr + i] for i in range(n)]
        if order == "little":
            values.reverse()
        bits = self._state._sim._mcu._spaces[self._space]["cell_bits"]
        value = 0
        for cell in values:
            value = (value << bits) | cell
        return value

    def write_word(self, addr: int, n: int, value: int, order: str) -> None:
        self._word_check(addr, n, order)
        self._state._write_check()
        _int(value, "value")
        bits = self._state._sim._mcu._spaces[self._space]["cell_bits"]
        mask = (1 << bits) - 1
        for i in range(n):
            position = n - i - 1 if order == "big" else i
            self[addr + i] = (value >> (position * bits)) & mask

    def _word_check(self, addr, n, order):
        if not _is_int(addr):
            raise StateAccessError(f"addr={addr!r}: address must be int, not bool")
        if not _is_int(n) or n < 1:
            raise StateAccessError(f"n={n!r}: expected >= 1")
        if order not in ("big", "little"):
            raise StateAccessError(f"order={order!r}: expected big/little")


class State:
    reg: RegAccess
    mem: MemAccess
    irq: IrqAccess
    pc: int
    next_pc: int
    cycle: int

    def __init__(self, sim, readonly=False):
        self._sim = sim
        self._readonly = readonly
        object.__setattr__(self, "reg", RegAccess(self))
        object.__setattr__(self, "mem", MemAccess(self))
        object.__setattr__(self, "irq", IrqAccess(self))

    def __setattr__(self, name, value):
        if getattr(self, "_readonly", False) and not name.startswith("_"):
            raise StateAccessError("use Simulator.poke")
        object.__setattr__(self, name, value)

    def _write_check(self):
        if self._readonly or self._sim._phase != "B":
            raise StateAccessError("use Simulator.poke")

    @property
    def pc(self) -> int:
        return self._sim._registers[self._sim._mcu._pc]

    @property
    def cycle(self) -> int:
        return self._sim._cycle

    @property
    def next_pc(self) -> int:
        return self._sim._next_pc

    @next_pc.setter
    def next_pc(self, value: int) -> None:
        self._write_check()
        self._sim._next_pc = _int(value, "next_pc")

    def add_cycles(self, n: int) -> None:
        self._write_check()
        _int(n, "n")
        if n < 0:
            raise StateAccessError(f"n={n!r}: expected >= 0")
        self._sim._extra += n

    def halt(self, reason: str) -> None:
        self._write_check()
        if not isinstance(reason, str):
            raise StateAccessError(f"reason={reason!r}: expected str")
        self._sim._halted = reason

    def fault(self, code: str, message: str = "") -> None:
        self._write_check()
        if not isinstance(code, str) or not isinstance(message, str):
            raise StateAccessError(f"fault={code!r}, message={message!r}")
        raise _Stop("fault", {"code": code, "message": message})
