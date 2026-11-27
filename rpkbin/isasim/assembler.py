"""Native single-source assembly and self-contained program bundles."""

from __future__ import annotations

import ast as _ast
import hashlib as _hashlib
import json as _json
import operator as _operator
import os
import re as _re
import string as _string
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from .errors import AssemblyError, LoadError
from .mcu import MCU, Operand, _IDENTIFIER, _operand_bounds
from ._preprocess import _Preprocessor, _constant, _label


def _canonical(value):
    return _json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value):
    return _hashlib.sha256(value.encode("utf-8")).hexdigest()


def _operand_payload(op):
    data = {"signed": op.signed, "relative": op.relative}
    if op.names is not None:
        data["names"] = list(op.names.items())
    if op.range is not None:
        data["range"] = op.range
    return data


def _instruction_payload(ins):
    data = {
        "pattern": ins["compiled"],
        "syntax": ins["syntax"],
        "name": ins["name"],
        "cycles": ins["cycles"],
        "operands": {
            key: _operand_payload(op) for key, op in ins.get("operands", {}).items()
        },
    }
    if ins["overrides"]:
        data["overrides"] = ins["overrides"]
    if ins["kind"] is not None:
        data["kind"] = ins["kind"]
    return data


def _isa_hash(mcu):
    return _digest(
        _canonical(
            {
                "name": mcu._name,
                "spaces": list(mcu._spaces.values()),
                "registers": list(mcu._registers.values()),
                "irqs": mcu._irqs,
                "policies": mcu._policies,
                "description": getattr(mcu, "_description_sha256", None),
                "instructions": [
                    _instruction_payload(ins) for ins in mcu._instructions
                ],
            }
        )
    )


def _name_key(case_sensitive_names):
    return str if case_sensitive_names else str.casefold


class _Source:
    def __init__(self, filename, case_sensitive_names):
        self.filename = filename
        self.line = 1
        self.chain = ()
        self.labels = {}
        self.label_names = set()
        self.constants = {}
        self.values = {}
        self.layout_values = {}
        self.name_key = _name_key(case_sensitive_names)

    def fail(self, reason):
        chain = f" (included from {' -> '.join(self.chain)})" if self.chain else ""
        raise AssemblyError(f"{self.filename}:{self.line}: {reason}{chain}")

    def locate(self, location):
        self.filename, self.line, _, self.chain = location

    def expression(self, text, stack=(), *, without_labels=False):
        if len(text) > 4096:
            self.fail("expression too long")
        try:
            tree = _ast.parse(text.strip(), mode="eval")
        except (SyntaxError, ValueError, RecursionError) as error:
            self.fail(f"invalid expression {text!r}: {error}")
        if sum(1 for _ in _ast.walk(tree)) > 128:
            self.fail("expression too complex")
        return self._evaluate(tree.body, stack, without_labels)

    def _evaluate(self, node, stack, without_labels, *, conditional=False):
        if isinstance(node, _ast.Constant) and type(node.value) is int:
            result = node.value
        elif isinstance(node, _ast.Name):
            name = self.name_key(node.id)
            if without_labels and name in self.label_names:
                self.fail(f"layout expression may not depend on label {node.id!r}")
            if name in self.labels:
                return self.labels[name]
            values = self.layout_values if without_labels else self.values
            if name in values:
                return values[name]
            if name not in self.constants:
                self.fail(f"unknown name {node.id!r}")
            if name in stack or len(stack) > 64:
                self.fail(f"cyclic constant {node.id!r}")
            expression = self.constants[name]
            result = (
                self.expression(
                    expression, (*stack, name), without_labels=without_labels
                )
                if isinstance(expression, str)
                else expression
            )
            values[name] = result
            return result
        elif (
            conditional
            and isinstance(node, _ast.UnaryOp)
            and isinstance(node.op, _ast.Not)
        ):
            result = int(
                not self._evaluate(
                    node.operand, stack, without_labels, conditional=True
                )
            )
        elif conditional and isinstance(node, _ast.BoolOp):
            left, right = (
                self._evaluate(value, stack, without_labels, conditional=True)
                for value in node.values
            )
            result = int(
                bool(left) and bool(right)
                if isinstance(node.op, _ast.And)
                else bool(left) or bool(right)
            )
        elif conditional and isinstance(node, _ast.Compare):
            operations = {
                _ast.Eq: _operator.eq,
                _ast.NotEq: _operator.ne,
                _ast.Lt: _operator.lt,
                _ast.LtE: _operator.le,
                _ast.Gt: _operator.gt,
                _ast.GtE: _operator.ge,
            }
            left = self._evaluate(node.left, stack, without_labels, conditional=True)
            right = self._evaluate(
                node.comparators[0], stack, without_labels, conditional=True
            )
            result = int(operations[type(node.ops[0])](left, right))
        elif isinstance(node, _ast.UnaryOp) and type(node.op) in (
            _ast.UAdd,
            _ast.USub,
            _ast.Invert,
        ):
            operations = {
                _ast.UAdd: _operator.pos,
                _ast.USub: _operator.neg,
                _ast.Invert: _operator.invert,
            }
            result = operations[type(node.op)](
                self._evaluate(
                    node.operand, stack, without_labels, conditional=conditional
                )
            )
        elif isinstance(node, _ast.BinOp):
            operations = {
                _ast.Add: _operator.add,
                _ast.Sub: _operator.sub,
                _ast.Mult: _operator.mul,
                _ast.FloorDiv: _operator.floordiv,
                _ast.Mod: _operator.mod,
                _ast.LShift: _operator.lshift,
                _ast.RShift: _operator.rshift,
                _ast.BitAnd: _operator.and_,
                _ast.BitOr: _operator.or_,
                _ast.BitXor: _operator.xor,
            }
            if type(node.op) not in operations:
                self.fail("unsupported expression operator")
            left, right = (
                self._evaluate(
                    node.left, stack, without_labels, conditional=conditional
                ),
                self._evaluate(
                    node.right, stack, without_labels, conditional=conditional
                ),
            )
            if (
                isinstance(node.op, (_ast.LShift, _ast.RShift))
                and not 0 <= right <= 4096
            ):
                self.fail(f"shift count {right} outside range 0..4096")
            if isinstance(node.op, (_ast.FloorDiv, _ast.Mod)) and right == 0:
                self.fail("division by zero in expression")
            result = operations[type(node.op)](left, right)
        else:
            self.fail("unsupported expression; integers and named constants required")
        if result.bit_length() > 4096:
            self.fail("expression integer exceeds 4096 bits")
        return result


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _template(ins, source):
    tokens, names = [], []
    for literal, field, _, conversion in _string.Formatter().parse(ins["syntax"]):
        if field is not None and names and not literal.strip():
            source.fail(
                f"instruction syntax {ins['syntax']!r}: explicit operand delimiters required"
            )
        for piece in _re.split(r"(\s+)", literal):
            if piece:
                tokens.append(("ws" if piece.isspace() else "lit", piece))
        if field is not None:
            if conversion is not None or field in names:
                source.fail(
                    f"instruction syntax {ins['syntax']!r}: unique unconverted placeholders required"
                )
            names.append(field)
            tokens.append(("field", field))
    if sorted(names) != sorted(ins["letters"]):
        source.fail(
            f"instruction syntax {ins['syntax']!r}: every pattern field must appear once"
        )
    chunks = []
    for idx, (kind, text) in enumerate(tokens):
        if kind == "lit":
            chunks.append(_re.escape(text))
        elif kind == "field":
            chunks.append(f"(?P<{text}>.+?)")
        else:
            prev_tok = tokens[idx - 1] if idx > 0 else None
            next_tok = tokens[idx + 1] if idx + 1 < len(tokens) else None
            prev_word = prev_tok is not None and (
                prev_tok[0] == "field"
                or (prev_tok[0] == "lit" and _is_word_char(prev_tok[1][-1]))
            )
            next_word = next_tok is not None and (
                next_tok[0] == "field"
                or (next_tok[0] == "lit" and _is_word_char(next_tok[1][0]))
            )
            chunks.append(r"\s+" if prev_word and next_word else r"\s*")
    return _re.compile("".join(chunks), _re.IGNORECASE)


def _operand_names(ins, source):
    tables = {}
    for letter, operand in ins.get("operands", {}).items():
        if operand.names is None:
            continue
        table = {}
        for name, value in operand.names.items():
            key = source.name_key(name)
            if key in table:
                source.fail(f"operand {letter}: duplicate case-folded name {name!r}")
            table[key] = value
        tables[letter] = table
    return tables


def _select_instruction(text, templates, source):
    matches, rejections = [], []
    for ins, pattern, names in templates:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        arguments = match.groupdict()
        for letter, operand in ins.get("operands", {}).items():
            argument = arguments[letter]
            if letter in names:
                key = source.name_key(argument.strip())
                if key not in names[letter]:
                    rejections.append(
                        f"{ins['name']}: operand {letter}={argument!r} is not a declared name"
                    )
                    break
                value = names[letter][key]
            elif operand.range is not None:
                try:
                    value = source.expression(argument, without_labels=True)
                except AssemblyError as error:
                    rejections.append(f"{ins['name']}: {error}")
                    break
            else:
                continue
            if operand.range is not None:
                lo, hi = operand.range
                if not lo <= value <= hi:
                    rejections.append(
                        f"{ins['name']}: operand {letter}={value} outside range {lo}..{hi}"
                    )
                    break
            arguments[letter] = value
        else:
            matches.append((ins, arguments))
    if len(matches) != 1:
        reasons = f"; {'; '.join(rejections)}" if not matches and rejections else ""
        source.fail(
            f"instruction {text!r}: expected one syntax match, found {len(matches)}{reasons}"
        )
    return matches[0]


def _encode(ins, arguments, addr, size, source):
    values = {}
    for letter in ins["letters"]:
        width = ins["compiled"].count(letter)
        op = ins.get("operands", {}).get(letter, Operand())
        argument = arguments[letter]
        value = source.expression(argument) if isinstance(argument, str) else argument
        if op.relative is not None:
            if not 0 <= value < size:
                source.fail(f"target {value} outside program range 0..{size - 1}")
            value -= addr + (ins["length"] if op.relative == "next_pc" else 0)
        low, high = _operand_bounds(width, op.signed)
        if not low <= value <= high:
            source.fail(f"operand {letter}={value} outside range {low}..{high}")
        values[letter] = value & ((1 << width) - 1)
    remaining = {key: ins["compiled"].count(key) for key in values}
    word = 0
    for char in ins["compiled"]:
        word <<= 1
        if char == "1":
            word |= 1
        elif char in values:
            remaining[char] -= 1
            word |= (values[char] >> remaining[char]) & 1
    cell_bits = len(ins["compiled"]) // ins["length"]
    mask = (1 << cell_bits) - 1
    return tuple(
        (word >> (cell_bits * index)) & mask
        for index in range(ins["length"] - 1, -1, -1)
    )


def _data_layout(directive, rest, cell_bits, endian, source):
    arguments = rest.split(",")
    if directive in (".space", ".ds"):
        if len(arguments) not in (1, 2):
            source.fail(f"{directive} expects a count and optional fill")
        count = source.expression(arguments[0], without_labels=True)
        if count < 0:
            source.fail(f"{directive} count {count} outside range 0 or more")
        return (".space", arguments[1:] or ["0"], count)
    if directive == ".db" and cell_bits != 8:
        source.fail(f"{directive} requires cell_bits=8, got {cell_bits}; use .cell")
    if directive == ".dw" and cell_bits not in (8, 16):
        source.fail(f"{directive} requires cell_bits=8 or 16, got {cell_bits}")
    factor = 1
    if directive == ".dw" and cell_bits == 8:
        if endian is None:
            source.fail(
                f"{directive} with cell_bits=8 requires endian='little' or 'big'"
            )
        factor = 2
    if directive not in (".cell", ".db", ".dw"):
        source.fail(f"unknown directive {directive!r}")
    return directive, arguments, len(arguments) * factor


def _emit_data(directive, arguments, length, cell_bits, endian, source):
    values = tuple(source.expression(arg) for arg in arguments)
    width = {".db": 8, ".dw": 16}.get(directive, cell_bits)
    low = -(1 << (width - 1)) if directive in (".db", ".dw") else 0
    high = (1 << width) - 1
    for value in values:
        if not low <= value <= high:
            source.fail(f"{directive} value {value} outside range {low}..{high}")
    if directive == ".space":
        return values * length
    if directive == ".dw" and cell_bits == 8:
        return tuple(
            byte for value in values for byte in (value & high).to_bytes(2, endian)
        )
    return tuple(value & high for value in values)


@_dataclass(frozen=True, slots=True)
class SourceRecord:
    addr: int
    file: str
    line: int
    cells: tuple[int, ...]
    is_instruction: bool


class Assembly:
    """An assembled program and the captured sources that produced it."""

    def __init__(
        self,
        mcu: MCU,
        source: str,
        *,
        filename: str = "<string>",
        include_paths=(),
        defines: dict[str, int] | None = None,
        endian: str | None = None,
        case_sensitive_names: bool = True,
    ):
        self._assemble(
            mcu,
            source,
            filename,
            include_paths,
            defines,
            endian,
            case_sensitive_names,
            None,
        )

    def _assemble(
        self,
        mcu,
        source,
        filename,
        include_paths,
        defines,
        endian,
        case_sensitive_names,
        replay,
    ):
        if not isinstance(mcu, MCU):
            raise AssemblyError(f"mcu={mcu!r}: expected MCU")
        if not isinstance(source, str) or not isinstance(filename, str) or not filename:
            raise AssemblyError(
                f"filename={filename!r}: source and filename must be strings"
            )
        try:
            source.encode("utf-8")
            filename.encode("utf-8")
        except UnicodeError as error:
            raise AssemblyError(
                f"{filename!r}:1: expected valid UTF-8 source and filename"
            ) from error
        source = source.removeprefix("\ufeff")
        mcu._validate()
        context = _Source(filename, case_sensitive_names)
        if endian not in (None, "little", "big"):
            context.fail(f"endian={endian!r}: expected None, 'little' or 'big'")
        if type(case_sensitive_names) is not bool:
            context.fail(
                f"case_sensitive_names={case_sensitive_names!r}: expected bool"
            )
        defines = {} if defines is None else defines
        if not isinstance(defines, dict):
            context.fail(f"defines={defines!r}: expected dict[str, int]")
        self._defines = {}
        define_keys = {}
        for name, value in defines.items():
            if (
                not isinstance(name, str)
                or _re.fullmatch(_IDENTIFIER, name) is None
                or type(value) is not int
            ):
                context.fail(
                    f"defines entry {name!r}={value!r}: expected an identifier and an integer"
                )
            key = context.name_key(name)
            if key in define_keys:
                context.fail(f"duplicate name {name!r}")
            define_keys[key] = (name, None)
            self._defines[name] = value
            context.constants[key] = value
        expanded = _Preprocessor(
            filename,
            source,
            include_paths,
            self._defines,
            _Source(filename, case_sensitive_names),
            replay,
        ).expansion
        self._sources = expanded.sources
        self._includes = expanded.includes
        self._format = (
            "isasim-assembly-2"
            if (
                expanded.used
                or self._defines
                or endian is not None
                or not case_sensitive_names
            )
            else "isasim-assembly-1"
        )
        templates = [
            (ins, _template(ins, context), _operand_names(ins, context))
            for ins in mcu._instructions
        ]
        self._source, self._filename = source, filename
        self._endian, self._case_sensitive_names = endian, case_sensitive_names
        self._isa_hash = _isa_hash(mcu)
        self._cell_bits = mcu._spaces[mcu._program]["cell_bits"]
        size = mcu._spaces[mcu._program]["size"]
        statements, definitions = [], define_keys
        for line, location in enumerate(expanded.lines):
            context.locate(location)
            text = location[2].partition(";")[0].strip()
            label = _label(text)
            if label:
                name = label[1]
                key = context.name_key(name)
                if key in definitions:
                    context.fail(f"duplicate name {name!r}")
                definitions[key] = (name, line)
                context.label_names.add(key)
                statements.append((line, "label", name))
                text = text[label.end() :].strip()
            constant = _constant(text)
            if constant:
                name, expression = constant.groups()
                key = context.name_key(name)
                if key in definitions:
                    context.fail(f"duplicate name {name!r}")
                definitions[key] = (name, line)
                context.constants[key] = expression
            elif text:
                statements.append((line, "text", text))
        addr, output = 0, []
        for line, kind, text in statements:
            context.locate(expanded.lines[line])
            if kind == "label":
                if not 0 <= addr < size:
                    context.fail(f"label {text!r} outside program range")
                context.labels[context.name_key(text)] = addr
                continue
            if text.startswith("."):
                pieces = text.split(None, 1)
                directive, rest = pieces[0], pieces[1] if len(pieces) == 2 else ""
                directive = directive.lower()
                if directive == ".org":
                    try:
                        addr = context.expression(rest)
                    except AssemblyError as error:
                        context.fail(f"layout .org must be resolvable here: {error}")
                    if not 0 <= addr < size:
                        context.fail(f"origin {addr} outside program range")
                    continue
                statement, arguments, length = _data_layout(
                    directive, rest, self._cell_bits, endian, context
                )
            else:
                statement, arguments = _select_instruction(text, templates, context)
                length = statement["length"]
            if not 0 <= addr < size or addr + length > size:
                context.fail(f"output at {addr} length {length} outside program range")
            output.append((line, addr, statement, arguments, length))
            addr += length
        # Validate even unused constants, using their declaration's diagnostics.
        self._constants = {}
        for key in context.constants:
            name, declaration = definitions[key]
            if declaration is None:
                self._constants[name] = self._defines[name]
            else:
                context.locate(expanded.lines[declaration])
                self._constants[name] = context.expression(name)
        self._cells, self._entries, self._entry_files = {}, [], []
        for line, addr, statement, arguments, length in output:
            context.locate(expanded.lines[line])
            instruction = not isinstance(statement, str)
            if instruction:
                cells = _encode(statement, arguments, addr, size, context)
            else:
                cells = _emit_data(
                    statement, arguments, length, self._cell_bits, endian, context
                )
            for offset, cell in enumerate(cells):
                if addr + offset in self._cells:
                    context.fail(f"output overlap at cell {addr + offset}")
                self._cells[addr + offset] = cell
            if cells:
                self._entries.append((addr, cells, context.line, instruction))
                self._entry_files.append(context.filename)
        if not self._cells:
            context.fail("no emitted cells")
        self._entries = tuple(self._entries)
        self._symbols = {
            definitions[key][0]: addr for key, addr in context.labels.items()
        }

    def __repr__(self) -> str:
        return (
            f"Assembly({self._filename!r}, cells={len(self._cells)}, "
            f"symbols={len(self._symbols)})"
        )

    @property
    def cells(self) -> dict[int, int]:
        return dict(self._cells)

    @property
    def symbols(self) -> dict[str, int]:
        return dict(self._symbols)

    @property
    def constants(self) -> dict[str, int]:
        return dict(self._constants)

    @property
    def sources(self) -> dict[str, str]:
        return dict(self._sources)

    @property
    def main_key(self) -> str:
        """Return the source key of the main file."""
        return self._filename

    def records(self) -> tuple[SourceRecord, ...]:
        """Return emitted source records in listing order."""
        return tuple(
            SourceRecord(addr, file, line, cells, instruction)
            for addr, cells, line, instruction, file in self._located_entries()
        )

    def _located_entries(self):
        for entry, file in zip(self._entries, self._entry_files):
            yield (*entry, file)

    def _rebuild(self, mcu):
        image = Assembly.__new__(Assembly)
        image._assemble(
            mcu,
            self._source,
            self._filename,
            (),
            self._defines,
            self._endian,
            self._case_sensitive_names,
            (self._sources, self._includes),
        )
        return image

    def _payload(self):
        payload = {
            "format": self._format,
            "isa_hash": self._isa_hash,
            "source": self._source,
            "filename": self._filename,
            "source_sha256": _digest(self._source),
            "cell_bits": self._cell_bits,
            "cells": [list(pair) for pair in sorted(self._cells.items())],
            "symbols": self._symbols,
            "lines": [
                {
                    "addr": addr,
                    "cells": list(cells),
                    "line": line,
                    "instruction": instruction,
                }
                for addr, cells, line, instruction in self._entries
            ],
        }

        if self._format == "isasim-assembly-2":
            payload.update(
                sources=self._sources,
                main=self._filename,
                sources_sha256={
                    key: _digest(value) for key, value in self._sources.items()
                },
                options={
                    "endian": self._endian,
                    "case_sensitive_names": self._case_sensitive_names,
                    "defines": self._defines,
                },
                includes=self._includes,
                constants=self._constants,
            )
            for entry, file in zip(payload["lines"], self._entry_files):
                entry["file"] = file
        return payload

    def save(self, path: str | os.PathLike[str]) -> None:
        try:
            _Path(path).write_text(
                _json.dumps(self._payload(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        except (OSError, UnicodeError) as error:
            raise LoadError(f"path={str(path)!r}: {error}") from error

    @classmethod
    def load(cls, mcu: MCU, path: str | os.PathLike[str]) -> Assembly:
        try:
            data = _json.loads(_Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            raise LoadError(f"path={str(path)!r}: {error}") from error
        if not isinstance(data, dict):
            raise LoadError(f"path={str(path)!r}: invalid assembly bundle")
        format_name = data.get("format")
        formats = ("isasim-assembly-1", "isasim-assembly-2")
        if isinstance(format_name, str) and format_name not in formats:
            raise LoadError(
                f"path={str(path)!r}: unknown assembly format {format_name!r}; supported: {', '.join(formats)}"
            )
        source, filename = data.get("source"), data.get("filename")
        if (
            format_name not in formats
            or not isinstance(source, str)
            or not isinstance(filename, str)
        ):
            raise LoadError(f"path={str(path)!r}: invalid assembly bundle")
        try:
            if format_name == "isasim-assembly-1":
                image = cls(mcu, source, filename=filename)
            else:
                image = cls._load_expanded(mcu, data, source, filename)
            rebuilt = image._payload()
            for key in dict.fromkeys((*data, *rebuilt)):
                if (
                    key not in data
                    or key not in rebuilt
                    or _canonical(data[key]) != _canonical(rebuilt[key])
                ):
                    raise LoadError(f"path={str(path)!r}: bundle mismatch in {key!r}")
        except (AssemblyError, UnicodeError, RecursionError) as error:
            raise LoadError(
                f"path={str(path)!r}: invalid assembly bundle: {error}"
            ) from error
        return image

    @classmethod
    def _load_expanded(cls, mcu, data, source, filename):
        sources, options, includes = (
            data.get(key) for key in ("sources", "options", "includes")
        )
        if (
            not isinstance(sources, dict)
            or any(not isinstance(text, str) for text in sources.values())
            or not isinstance(options, dict)
            or options.keys() != {"endian", "case_sensitive_names", "defines"}
            or not isinstance(includes, list)
        ):
            raise AssemblyError("invalid sources, options or include bindings")
        if data.get("main") != filename or sources.get(filename) != source:
            raise AssemblyError("main, filename and source disagree")
        expected_hashes = {key: _digest(text) for key, text in sources.items()}
        if data.get("sources_sha256") != expected_hashes:
            raise AssemblyError("sources_sha256 does not match captured sources")
        image = cls.__new__(cls)
        image._assemble(
            mcu,
            source,
            filename,
            (),
            options["defines"],
            options["endian"],
            options["case_sensitive_names"],
            (sources, includes),
        )
        return image

    def listing(self) -> str:
        width = (self._cell_bits + 3) // 4
        lines = {key: source.splitlines() for key, source in self._sources.items()}
        return (
            "\n".join(
                f"{addr:04x}  {' '.join(f'{cell:0{width}x}' for cell in cells)}  {file}:{line}  {lines[file][line - 1]}"
                for addr, cells, line, _, file in self._located_entries()
            )
            + "\n"
        )


def assemble(
    mcu: MCU,
    source: str,
    *,
    filename: str = "<string>",
    include_paths=(),
    defines: dict[str, int] | None = None,
    endian: str | None = None,
    case_sensitive_names: bool = True,
) -> Assembly:
    """Assemble source without executing instruction semantics."""
    return Assembly(
        mcu,
        source,
        filename=filename,
        include_paths=include_paths,
        defines=defines,
        endian=endian,
        case_sensitive_names=case_sensitive_names,
    )


def _assemble_file(mcu, path, **options):
    try:
        source = _Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise LoadError(f"path={str(path)!r}: {error}") from error
    return assemble(mcu, source, filename=str(_Path(path).resolve()), **options)
