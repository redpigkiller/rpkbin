"""Import-only preferences: validate without loading packages in the GUI process."""
from __future__ import annotations

import ast
import traceback


def parse_imports(source: str) -> ast.Module:
    """Accept absolute imports and comments, with explicit public bindings only."""
    if not isinstance(source, str) or len(source) > 100_000:
        raise ValueError("Custom imports must be text of at most 100,000 characters")
    try:
        tree = ast.parse(source, filename="<calc-custom-imports>")
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"Invalid custom imports: {exc}") from exc
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError(f"Line {node.lineno}: only import statements are allowed; use startup.py for other code")
        if isinstance(node, ast.ImportFrom) and node.level:
            raise ValueError(f"Line {node.lineno}: use absolute imports")
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            if alias.name == "*" or name.startswith("_"):
                raise ValueError(f"Line {node.lineno}: use explicit names without a leading underscore")
    return tree


def apply_imports(source: str, symbols: dict) -> None:
    """Load independently per statement; report failures and keep a usable console.

    A failed statement publishes no partial bindings. Python module side effects
    are ordinary imports and cannot be rolled back.
    """
    errors = []
    try:
        tree = parse_imports(source)
    except ValueError as exc:
        symbols["_calc_import_error"] = str(exc)
        return
    for node in tree.body:
        imported = {}
        try:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<calc-custom-imports>", "exec"), imported)
        except (Exception, SystemExit, KeyboardInterrupt):
            errors.append(f"Line {node.lineno}: {ast.get_source_segment(source, node)}\n{traceback.format_exc().strip()}")
        else:
            symbols.update({key: value for key, value in imported.items() if key != "__builtins__"})
    symbols["_calc_import_error"] = "\n\n".join(errors)


BUILTIN_BINDINGS = {
    "fxp_core": "apy APyFixed APyFixedArray APyFloat APyFloatArray fxp fxpi",
    "math": "math sin cos tan asin acos atan atan2 exp log log2 log10 sqrt deg2rad rad2deg pi e",
    "numpy": "np svd eig inv pinv norm det eye zeros ones diag arange linspace "
             "polyfit polyval linfit qfunc erfc erf fft ifft fftshift ifftshift",
}


def extract_import_bindings(source: str) -> list[str]:
    return [alias.asname or alias.name.split(".")[0]
            for node in parse_imports(source).body for alias in node.names]


def validate_custom_groups(groups: object) -> list[dict]:
    if not isinstance(groups, list) or len(groups) > 100:
        raise ValueError("Expected a list of up to 100 custom import groups")
    ids = set()
    for item in groups:
        if not isinstance(item, dict) or set(item) != {"id", "name", "enabled", "code"}:
            raise ValueError("Invalid custom import group")
        for key in ("id", "name"):
            if not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > 128:
                raise ValueError(f"Invalid custom import group {key}")
        if item["id"] in ids or type(item["enabled"]) is not bool:
            raise ValueError("Invalid or duplicate custom import group")
        ids.add(item["id"])
        parse_imports(item["code"])
    return groups


def import_collisions(builtins, custom_groups) -> list[str]:
    groups = [(key, names.split()) for key, names in BUILTIN_BINDINGS.items() if builtins.get(key)]
    groups.extend((item["name"], extract_import_bindings(item["code"]))
                  for item in custom_groups if item["enabled"])
    owners = {}
    warnings = []
    for group, names in groups:
        for name in dict.fromkeys(names):
            if name in owners:
                warnings.append(f"{name}: {owners[name]} → {group}")
            owners[name] = group
    return warnings
