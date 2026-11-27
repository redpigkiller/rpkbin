"""Preprocessing and deterministic include replay for assembly source."""

import ast as _ast
import os as _os
import re as _re
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from .mcu import _IDENTIFIER


def _label(text):
    return _re.match(rf"^({_IDENTIFIER}):", text)


def _constant(text):
    return (
        _re.fullmatch(rf"({_IDENTIFIER})\s*=\s*(.+)", text)
        or _re.fullmatch(rf"({_IDENTIFIER})\s+EQU\s+(.+)", text, _re.IGNORECASE)
        or _re.fullmatch(rf"\.equ\s+({_IDENTIFIER})\s*,\s*(.+)", text, _re.IGNORECASE)
    )


def _integer_atom(text, *, signed=False):
    node = _ast.parse(text, mode="eval").body
    sign = 1
    if signed and isinstance(node, _ast.UnaryOp) and isinstance(node.op, _ast.USub):
        sign, node = -1, node.operand
    if not isinstance(node, _ast.Constant) or type(node.value) is not int:
        raise ValueError(f"{text!r} is not an integer literal")
    return sign * node.value


def _define_argument(text):
    name, separator, value = text.partition("=")
    if _re.fullmatch(_IDENTIFIER, name) is None:
        raise ValueError(f"{name!r} is not an identifier")
    if not separator:
        return name, 1
    if _re.fullmatch(r"-?[0-9][A-Za-z_0-9]*", value) is None:
        raise ValueError(f"{value!r} is not an integer literal")
    return name, _integer_atom(value, signed=True)


# The lexer handles token boundaries only. The operator table mirrors the
# assembler's arithmetic precedence and adds the conditional levels.
_TOKEN = _re.compile(
    r"\s*(==|!=|<=|>=|//|<<|>>|[!<>+\-~*/%&^|()]|[0-9][A-Za-z_0-9]*|[A-Za-z_][A-Za-z_0-9]*)"
)
_BINARY = {
    "or": (1, _ast.Or),
    "and": (2, _ast.And),
    "==": (3, _ast.Eq),
    "!=": (3, _ast.NotEq),
    "<": (3, _ast.Lt),
    "<=": (3, _ast.LtE),
    ">": (3, _ast.Gt),
    ">=": (3, _ast.GtE),
    "|": (4, _ast.BitOr),
    "^": (5, _ast.BitXor),
    "&": (6, _ast.BitAnd),
    "<<": (7, _ast.LShift),
    ">>": (7, _ast.RShift),
    "+": (8, _ast.Add),
    "-": (8, _ast.Sub),
    "*": (9, _ast.Mult),
    "//": (9, _ast.FloorDiv),
    "%": (9, _ast.Mod),
}
_PREFIX = {
    "+": _ast.UAdd,
    "-": _ast.USub,
    "~": _ast.Invert,
    "not": _ast.Not,
    "!": _ast.Not,
}


class _Condition:
    def __init__(self, text, context):
        self.context = context
        self.tokens = []
        self.index = 0
        text = text.strip()
        if len(text) > 4096:
            context.fail("conditional expression too long")
        offset = 0
        while offset < len(text):
            token = _TOKEN.match(text, offset)
            if token is None:
                context.fail(f"invalid conditional expression near {text[offset:]!r}")
            self.tokens.append(token[1])
            offset = token.end()
            if len(self.tokens) > 128:
                context.fail("conditional expression too complex")
        self.tokens.append("")

    def _take(self):
        token = self.tokens[self.index]
        if token:
            self.index += 1
        return token

    def parse(self):
        result = self._expression(1)
        if self.tokens[self.index]:
            self.context.fail(
                f"unexpected conditional token {self.tokens[self.index]!r}"
            )
        return result

    def _expression(self, precedence):
        token = self._take()
        if token in _PREFIX:
            left = _ast.UnaryOp(op=_PREFIX[token](), operand=self._expression(10))
        elif token == "(":
            left = self._expression(1)
            if self._take() != ")":
                self.context.fail("expected closing parenthesis in condition")
        elif token[:1].isdigit():
            try:
                left = _ast.Constant(value=_integer_atom(token))
            except (SyntaxError, ValueError) as error:
                self.context.fail(f"invalid conditional integer {token!r}: {error}")
        elif _re.fullmatch(_IDENTIFIER, token) and token not in ("and", "or"):
            left = _ast.Name(id=token)
        else:
            self.context.fail(f"expected conditional operand, got {token!r}")
        while (entry := _BINARY.get(self.tokens[self.index])) and entry[
            0
        ] >= precedence:
            self._take()
            level, operation = entry
            right = self._expression(level + 1)
            operator = operation()
            if isinstance(operator, _ast.boolop):
                left = _ast.BoolOp(op=operator, values=[left, right])
            elif isinstance(operator, _ast.cmpop):
                left = _ast.Compare(left=left, ops=[operator], comparators=[right])
            else:
                left = _ast.BinOp(left=left, op=operator, right=right)
        return left


@_dataclass
class _Branch:
    line: int
    active: bool
    taken: bool
    otherwise: bool = False


@_dataclass
class _Expansion:
    sources: dict[str, str]
    lines: list[tuple[str, int, str, tuple[str, ...]]]
    includes: list[dict]
    used: bool = False


def _absolute(path):
    return _Path(_os.path.abspath(path))


def _source_key(path, root):
    if root is not None and path.is_relative_to(root):
        return path.relative_to(root).as_posix()
    return path.as_posix()


class _Preprocessor:
    def __init__(self, filename, source, include_paths, defines, context, replay):
        self.context = context
        self.context.constants = {
            context.name_key(name): value for name, value in defines.items()
        }
        self.expansion = _Expansion({filename: source}, [], [])
        self.replay = replay
        virtual = filename.startswith("<") and filename.endswith(">")
        main = None if virtual or replay is not None else _absolute(filename)
        self.root = None if main is None else main.parent
        self.search = (
            ()
            if replay is not None
            else tuple(_absolute(path) for path in include_paths)
        )
        self.cache = (
            {} if main is None else {_os.path.normcase(str(main)): (filename, source)}
        )
        self._walk(filename, source, main, (filename,), ())
        if replay is not None:
            sources, includes = replay
            if len(self.expansion.includes) != len(includes):
                self.context.fail("unconsumed include bindings")
            if self.expansion.sources.keys() != sources.keys():
                self.context.fail("unreferenced source keys in bundle")

    def _locate(self, including, text, path):
        if self.replay is not None:
            sources, includes = self.replay
            index = len(self.expansion.includes)
            if index >= len(includes):
                self.context.fail(f"missing include binding for {text!r}")
            record = includes[index]
            site = (including, self.context.line, text)
            if (
                not isinstance(record, dict)
                or (record.get("from"), record.get("line"), record.get("text")) != site
            ):
                self.context.fail(f"include binding {index} does not match {site!r}")
            key = record.get("to")
            if not isinstance(key, str) or key not in sources:
                self.context.fail(f"unknown include source key {key!r}")
            return key, sources[key], None
        dirs = self.search if path is None else (path.parent, *self.search)
        for directory in dirs:
            candidate = _absolute(directory / text)
            identity = _os.path.normcase(str(candidate))
            if identity not in self.cache:
                try:
                    source = candidate.read_text(encoding="utf-8-sig")
                except (FileNotFoundError, NotADirectoryError):
                    continue
                except (OSError, UnicodeError) as error:
                    self.context.fail(
                        f"cannot read include {str(candidate)!r}: {error}"
                    )
                self.cache[identity] = (_source_key(candidate, self.root), source)
            key, source = self.cache[identity]
            return key, source, candidate
        self.context.fail(
            f"include {text!r} not found; searched {[str(item) for item in dirs]!r}"
        )

    def _condition(self, directive, rest):
        if directive in (".ifdef", ".ifndef"):
            if not _re.fullmatch(_IDENTIFIER, rest):
                self.context.fail(f"{directive} expects an identifier, got {rest!r}")
            present = self.context.name_key(rest) in self.context.constants
            return present if directive == ".ifdef" else not present
        node = _Condition(rest, self.context).parse()
        return bool(self.context._evaluate(node, (), True, conditional=True))

    def _branch(self, directive, rest, stack, opening):
        active = not stack or stack[-1].active
        if directive in (".if", ".ifdef", ".ifndef"):
            taken = active and self._condition(directive, rest)
            stack.append(_Branch(self.context.line, taken, taken))
            return
        if not stack:
            origin = f" (condition opened at {opening})" if opening is not None else ""
            self.context.fail(
                f"unmatched {directive}; conditions must open and close in the same file{origin}"
            )
        if directive in (".else", ".endif") and rest:
            self.context.fail(f"unexpected text {rest!r} after {directive}")
        if directive == ".endif":
            stack.pop()
            return
        branch = stack[-1]
        if branch.otherwise:
            self.context.fail(
                f"{directive} after .else for condition opened at line {branch.line}"
            )
        parent = len(stack) < 2 or stack[-2].active
        branch.active = (
            parent
            and not branch.taken
            and (directive == ".else" or self._condition(directive, rest))
        )
        branch.taken |= branch.active
        branch.otherwise = directive == ".else"

    def _walk(self, key, source, path, keys, chain, opening=None):
        stack = []
        for number, original in enumerate(source.splitlines(), 1):
            self.context.filename, self.context.line, self.context.chain = (
                key,
                number,
                chain,
            )
            text = original.partition(";")[0].strip()
            parts = text.split(None, 1)
            directive = parts[0].lower() if parts else ""
            rest = parts[1].strip() if len(parts) > 1 else ""
            if directive in (".if", ".elseif", ".else", ".endif", ".ifdef", ".ifndef"):
                self.expansion.used = True
                self._branch(directive, rest, stack, opening)
                continue
            if directive == ".include":
                self.expansion.used = True
            if stack and not stack[-1].active:
                continue
            if directive == ".include":
                include = _re.fullmatch(
                    r'\s*\.include\s+"([^"]+)"\s*(?:;.*)?', original, _re.IGNORECASE
                )
                if not include:
                    self.context.fail(f"invalid include syntax: {original!r}")
                if len(keys) > 16:
                    self.context.fail("include depth exceeds 16")
                target, content, target_path = self._locate(key, include[1], path)
                if target in keys:
                    self.context.fail(f"include cycle: {' -> '.join((*keys, target))}")
                self.expansion.sources[target] = content
                self.expansion.includes.append(
                    {"from": key, "line": number, "text": include[1], "to": target}
                )
                self._walk(
                    target,
                    content,
                    target_path,
                    (*keys, target),
                    (*chain, f"{key}:{number}"),
                    f"{key}:{stack[-1].line}" if stack else opening,
                )
                continue
            if label := _label(text):
                self.context.label_names.add(self.context.name_key(label[1]))
                text = text[label.end() :].strip()
            if constant := _constant(text):
                name, expression = constant.groups()
                self.context.constants.setdefault(
                    self.context.name_key(name), expression
                )
            self.expansion.lines.append((key, number, original, chain))
        if stack:
            self.context.filename, self.context.line, self.context.chain = (
                key,
                stack[-1].line,
                chain,
            )
            self.context.fail(
                "unterminated condition; expected .endif in the same file"
            )
