"""One user-owned startup file; atomic writes and conflict-aware migration."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tempfile
import tokenize

STARTUP_TEMPLATE = '''# rpk-calc startup.py — imports and helpers for your console.
# Runs at kernel startup/restart, or with Reload and run in Settings.
# Saving this file does not execute it. np, apy and APyTypes classes are preloaded.

# Optional packages must be installed in the Python environment used by Calc.
# import scipy as sp
# from scipy import signal
# from scipy.fft import fft, ifft  # Override the default FFT helpers explicitly.

# def db20(x):
#     return 20.0 * np.log10(np.abs(x))

# def fxp(value, *, bits=16, frac_bits=12):
#     return APyFixed.from_float(value, bits=bits, frac_bits=frac_bits)
'''


def _encoded_source(source):
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source.encode("utf-8")).readline)
    except SyntaxError as exc:
        raise ValueError(str(exc)) from exc
    return source.removeprefix("\ufeff").encode(encoding)


def _read_source(path):
    try:
        with tokenize.open(path) as stream:
            return stream.read()
    except SyntaxError as exc:
        raise ValueError(str(exc)) from exc


class StartupFile:
    def __init__(self, config_path):
        self.path = Path(config_path).expanduser().resolve().with_name("startup.py")

    def read(self):
        # Respect Python encoding cookies/BOMs when an external editor writes.
        return _read_source(self.path)

    def revision(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def initialize(self, legacy=""):
        """Never overwrite an existing user file; preserve conflicting legacy code."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                data = _encoded_source(legacy if legacy else STARTUP_TEMPLATE)
                with self.path.open("xb") as stream:
                    stream.write(data)
                return ""
            except FileExistsError:
                pass
        if legacy and self.read() != legacy:
            digest = hashlib.sha256(legacy.encode("utf-8")).hexdigest()[:12]
            backup = self.path.with_name(f"startup.legacy-{digest}.py")
            try:
                data = _encoded_source(legacy)
                with backup.open("xb") as stream:
                    stream.write(data)
            except FileExistsError:
                previous = _read_source(backup)
                if previous != legacy:
                    raise OSError(f"Legacy backup conflicts: {backup}")
            return f"Existing startup.py kept. Previous settings code preserved in {backup}"
        return ""

    def write(self, source, *, expected_revision):
        """Commit an explicitly imported draft, refusing concurrent editor changes."""
        if self.revision() != expected_revision:
            raise ValueError("startup.py changed in another editor. Reopen Settings before importing code.")
        encoded = _encoded_source(source)
        previous = self.path.read_bytes()
        backup = self.path.with_name(f"startup.before-import-{expected_revision[:12]}.py")
        try:
            with backup.open("xb") as stream:
                stream.write(previous)
        except FileExistsError:
            if backup.read_bytes() != previous:
                raise OSError(f"Startup backup conflicts: {backup}")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.path.parent,
                                             prefix="startup-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
            # The check protects ordinary external editing; no file watcher or
            # background execution, and no claim of a cross-process file lock.
            if self.revision() != expected_revision:
                raise ValueError("startup.py changed in another editor. Reopen Settings before importing code.")
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def prepare_startup(config, config_path):
    """Resolve/migrate file ownership in the frontend; only the kernel runs code."""
    store = StartupFile(config_path)
    notice = store.initialize(config.startup_script)
    config.startup_file = str(store.path)
    if config.startup_script:
        config.startup_script = ""
        config.save(config_path)
    return store, notice
