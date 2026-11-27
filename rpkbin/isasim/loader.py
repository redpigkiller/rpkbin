"""Transactional program and symbol loading."""

from __future__ import annotations

import json as _json
import os
import re as _re
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .errors import DebugError, LoadError, TargetError, _is_int

if _TYPE_CHECKING:
    from .assembler import Assembly


class _Loaders:
    def _load_space(self, space):
        space = self._mcu._program if space is None else space
        if space not in self._mcu._spaces:
            raise LoadError(f"space={space!r}: unknown space")
        return space

    def _cell_bytes(self, space, order):
        bits = self._mcu._spaces[space]["cell_bits"]
        if bits % 8:
            raise LoadError(
                f"space={space!r}, cell_bits={bits}: expected multiple of 8"
            )
        count = bits // 8
        if count > 1 and order not in ("big", "little"):
            raise LoadError(
                f"order={order!r}: big/little required for cell_bits={bits}"
            )
        return count

    def _read_file(self, path, binary=False):
        try:
            if binary:
                with open(path, "rb") as stream:
                    return stream.read()
            with open(path, encoding="utf-8-sig") as stream:
                return stream.read()
        except (OSError, UnicodeError) as error:
            raise LoadError(f"path={path!r}: {error}") from error

    def load_hex(
        self,
        path: str | os.PathLike[str],
        *,
        space: str | None = None,
        order: str | None = None,
    ) -> None:
        space = self._load_space(space)
        count = self._cell_bytes(space, order)
        content = self._read_file(path)
        data = {}
        base = 0
        ended = False
        for number, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            if ended:
                raise LoadError(f"path={path!r}, line={number}: data after EOF")
            if _re.fullmatch(r":[0-9A-Fa-f]+", line) is None:
                raise LoadError(
                    f"path={path!r}, line={number}: invalid record {line!r}"
                )
            try:
                record = bytes.fromhex(line[1:])
            except ValueError as error:
                raise LoadError(f"path={path!r}, line={number}: {error}") from error
            if (
                len(record) < 5
                or len(record) != record[0] + 5
                or sum(record) % (1 << 8)
            ):
                raise LoadError(
                    f"path={path!r}, line={number}: invalid length/checksum {line!r}"
                )
            length = record[0]
            addr = (record[1] << 8) | record[2]
            kind = record[3]
            payload = record[4:-1]
            if kind == 0:
                for offset, value in enumerate(payload):
                    absolute = base + addr + offset
                    if absolute in data:
                        raise LoadError(f"byte address={absolute}: duplicate")
                    data[absolute] = value
            elif kind == 1:
                if length or addr:
                    raise LoadError(f"line={number}: invalid EOF")
                ended = True
            elif kind in (2, 4):
                if length != 2 or addr:
                    raise LoadError(f"line={number}: invalid extended address")
                base = int.from_bytes(payload, "big") << (4 if kind == 2 else 16)
            elif kind not in (3, 5):
                raise LoadError(f"line={number}, record type={kind}: unsupported")
        if not ended:
            raise LoadError(f"path={path!r}: missing EOF")
        groups = {}
        for addr, value in data.items():
            groups.setdefault(addr // count, {})[addr % count] = value
        cells = {}
        for addr, parts in groups.items():
            if len(parts) != count:
                raise LoadError(f"cell address={addr}: incomplete cell")
            cells[addr] = int.from_bytes(
                bytes(parts[i] for i in range(count)), order if count > 1 else "big"
            )
        self.load_cells(space, cells)

    def load_bin(
        self,
        path: str | os.PathLike[str],
        *,
        base: int,
        space: str | None = None,
        order: str | None = None,
    ) -> None:
        space = self._load_space(space)
        count = self._cell_bytes(space, order)
        if not _is_int(base) or base < 0:
            raise LoadError(f"base={base!r}: expected nonnegative cell address")
        data = self._read_file(path, True)
        if len(data) % count:
            raise LoadError(f"path={path!r}, length={len(data)}: incomplete cell")
        cells = {
            base + i // count: int.from_bytes(
                data[i : i + count], order if count > 1 else "big"
            )
            for i in range(0, len(data), count)
        }
        self.load_cells(space, cells)

    def load_symbols(self, path: str | os.PathLike[str]) -> None:
        text = self._read_file(path)
        try:
            data = _json.loads(text)
        except ValueError as error:
            raise LoadError(f"path={path!r}: {error}") from error
        if not isinstance(data, dict) or any(
            key not in ("symbols", "lines") for key in data
        ):
            raise LoadError(f"path={path!r}: invalid top-level keys")
        symbols = data.get("symbols", {})
        lines = data.get("lines", [])
        size = self._mcu._spaces[self._mcu._program]["size"]
        if not isinstance(symbols, dict) or not isinstance(lines, list):
            raise LoadError(f"path={path!r}: symbols/lines types")
        for name, addr in symbols.items():
            if (
                not isinstance(name, str)
                or not name
                or type(addr) is not int
                or not 0 <= addr < size
            ):
                raise LoadError(f"symbol={name!r}, addr={addr!r}: invalid")
        for entry in lines:
            if (
                not isinstance(entry, dict)
                or len(entry) != 3
                or any(key not in entry for key in ("addr", "file", "line"))
            ):
                raise LoadError(f"line entry={entry!r}: invalid keys")
            if (
                type(entry["addr"]) is not int
                or not 0 <= entry["addr"] < size
                or not isinstance(entry["file"], str)
                or not entry["file"]
                or type(entry["line"]) is not int
                or entry["line"] < 1
            ):
                raise LoadError(f"line entry={entry!r}: invalid values")
        self._symbols = dict(symbols)
        self._lines = [dict(entry) for entry in lines]
        self._assembly = None

    def load_assembly(self, image: Assembly) -> None:
        """Replace the program image and attach verified assembly source."""
        from .assembler import Assembly, _canonical, _isa_hash

        self._require_idle()

        if not isinstance(image, Assembly):
            raise LoadError(f"image={image!r}: expected Assembly")
        self._mcu._validate()
        if image._isa_hash != _isa_hash(self._mcu):
            raise LoadError("assembly ISA mismatch")
        verified = Assembly._rebuild(image, self._mcu)
        if _canonical(verified._payload()) != _canonical(image._payload()):
            raise LoadError("assembly image/source mismatch")
        space = self._mcu._program
        meta = self._mcu._spaces[space]
        memory = [meta["init"]] * meta["size"]
        initialized = [meta["reset_state"] == "initialized"] * meta["size"]
        for addr, value in verified._cells.items():
            memory[addr], initialized[addr] = value, True
        self._memory[space] = memory
        self._initialized[space] = initialized
        self._images = [(name, cells) for name, cells in self._images if name != space]
        self._images.append((space, verified.cells))
        self._symbols = verified.symbols
        self._lines = [
            {"addr": addr + offset, "file": file, "line": line}
            for addr, cells, line, _, file in verified._located_entries()
            for offset in range(len(cells))
        ]
        self._assembly = verified

    def _source_image(self):
        image = self._assembly
        if image is None:
            raise DebugError("no assembled source loaded")
        space = self._mcu._program
        if any(
            not self._initialized[space][addr] or self._memory[space][addr] != cell
            for addr, cell in image._cells.items()
        ):
            raise DebugError("program changed since assembly; source mapping is stale")
        return image

    def _source_addresses(self, where):
        filename, separator, number = where.rpartition(":")
        if not separator or not number.isdecimal():
            return None
        try:
            image = self._source_image()
        except DebugError as error:
            raise TargetError(f"source={where!r}: {error}") from error
        if not filename:
            filename = image._filename
        elif filename not in image._sources:
            normalized = filename.replace("\\", "/")
            matches = [
                key
                for key in image._sources
                if key.replace("\\", "/") == normalized
            ]
            if not matches:
                if "/" not in normalized:
                    matches = [
                        key
                        for key in image._sources
                        if key.replace("\\", "/").rsplit("/", 1)[-1] == normalized
                    ]
                else:
                    matches = [
                        key
                        for key in image._sources
                        if key.replace("\\", "/").endswith("/" + normalized)
                    ]
            if not matches:
                raise TargetError(f"source={where!r}: unknown source file")
            if len(matches) != 1:
                raise TargetError(
                    f"source={where!r}: ambiguous source file {matches!r}"
                )
            filename = matches[0]
        try:
            number = int(number)
        except ValueError as error:
            raise TargetError(f"source={where!r}: invalid line number") from error
        addresses = tuple(
            addr
            for addr, _, line, instruction, key in image._located_entries()
            if filename == key and line == number and instruction
        )
        if not addresses:
            raise TargetError(f"source={where!r}: no instruction on this line")
        return addresses

    def _source_address(self, where):
        addresses = self._source_addresses(where)
        if addresses is None:
            return None
        if len(addresses) > 1:
            raise TargetError(f"source={where!r}: multiple addresses {addresses!r}")
        return addresses[0]

    def source_context(
        self, where: int | str | None = None, *, context: int = 2
    ) -> list[tuple[int, str, bool]]:
        """Return captured source lines near an instruction, with a current marker."""
        image = self._source_image()
        if not _is_int(context) or context < 0:
            raise DebugError(f"context={context!r}: expected nonnegative integer")
        addr = self.state.pc if where is None else self._where(where)
        source = self.source_line(addr)
        if source is None:
            return []
        file, current = source
        lines = image._sources[file].splitlines()
        return [
            (line, lines[line - 1], line == current)
            for line in range(
                max(1, current - context), min(len(lines), current + context) + 1
            )
        ]

    def label_at(self, addr: int) -> tuple[str, int] | None:
        best = None
        for name, value in self._symbols.items():
            if value <= addr and (best is None or value > best[1]):
                best = (name, value)
        return None if best is None else (best[0], addr - best[1])

    def source_line(self, addr: int) -> tuple[str, int] | None:
        for entry in self._lines:
            if entry["addr"] == addr:
                return entry["file"], entry["line"]
        return None
