import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT
ENABLE_BENCH = os.getenv("RUN_NUMBV_BENCHMARK") == "1"


def _run_backend_benchmark(backend: str, workload: str) -> dict:
    script = f"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(r"{PACKAGE_ROOT}")))

import numpy as np
import rpkbin.numbv as nbv

fmt = nbv.Format(16, 12, rounding="round_half_even")
acc_fmt = nbv.Format(32, 22, rounding="round_half_even")
out_fmt = nbv.Format(16, 12, rounding="round_half_even", overflow="saturate")

nbv.set_backend({backend!r})

if {backend!r} == "jax":
    import jax

    if {workload!r} == "mul":
        n = 50000
        a = nbv.array(np.linspace(-0.9, 0.9, n), fmt=fmt)
        b = nbv.array(np.linspace(0.1, 0.9, n), fmt=fmt)
        fn = jax.jit(lambda x, y: nbv.mul(x, y, out_fmt=out_fmt))
    else:
        n = 256
        a = nbv.array(np.linspace(-0.9, 0.9, n), fmt=fmt)
        b = nbv.array(np.linspace(0.2, 0.8, n), fmt=fmt)
        fn = jax.jit(lambda x, y: nbv.dot(x, y, acc_fmt=acc_fmt, out_fmt=out_fmt))

    t0 = time.perf_counter()
    y = fn(a, b)
    bits = np.asarray(y.bits, dtype=np.int64)
    first_elapsed = time.perf_counter() - t0

    t1 = time.perf_counter()
    y2 = fn(a, b)
    bits2 = np.asarray(y2.bits, dtype=np.int64)
    steady_elapsed = time.perf_counter() - t1
else:
    if {workload!r} == "mul":
        n = 50000
        a = nbv.array(np.linspace(-0.9, 0.9, n), fmt=fmt)
        b = nbv.array(np.linspace(0.1, 0.9, n), fmt=fmt)
        fn = lambda x, y: nbv.mul(x, y, out_fmt=out_fmt)
    else:
        n = 256
        a = nbv.array(np.linspace(-0.9, 0.9, n), fmt=fmt)
        b = nbv.array(np.linspace(0.2, 0.8, n), fmt=fmt)
        fn = lambda x, y: nbv.dot(x, y, acc_fmt=acc_fmt, out_fmt=out_fmt)

    t0 = time.perf_counter()
    y = fn(a, b)
    bits = np.asarray(y.bits, dtype=np.int64)
    first_elapsed = time.perf_counter() - t0
    bits2 = bits
    steady_elapsed = first_elapsed

print(json.dumps({{
    "backend": {backend!r},
    "workload": {workload!r},
    "first_elapsed_sec": first_elapsed,
    "steady_elapsed_sec": steady_elapsed,
    "checksum": int(bits.sum() % 1000000007),
    "checksum_second": int(bits2.sum() % 1000000007),
    "size": int(bits.size),
}}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not ENABLE_BENCH, reason="Set RUN_NUMBV_BENCHMARK=1 to run benchmark tests")
def test_numbv_numpy_vs_jax_mul_benchmark():
    pytest.importorskip("jax")

    numpy_result = _run_backend_benchmark("numpy", "mul")
    jax_result = _run_backend_benchmark("jax", "mul")

    assert numpy_result["checksum"] == jax_result["checksum"] == jax_result["checksum_second"]
    assert numpy_result["size"] == jax_result["size"]

    print(
        "\n".join(
            [
                f"mul numpy elapsed: {numpy_result['first_elapsed_sec']:.6f}s",
                f"mul jax first elapsed: {jax_result['first_elapsed_sec']:.6f}s",
                f"mul jax steady elapsed: {jax_result['steady_elapsed_sec']:.6f}s",
            ]
        )
    )


@pytest.mark.skipif(not ENABLE_BENCH, reason="Set RUN_NUMBV_BENCHMARK=1 to run benchmark tests")
def test_numbv_numpy_vs_jax_dot_benchmark():
    pytest.importorskip("jax")

    numpy_result = _run_backend_benchmark("numpy", "dot")
    jax_result = _run_backend_benchmark("jax", "dot")

    assert numpy_result["checksum"] == jax_result["checksum"] == jax_result["checksum_second"]
    assert numpy_result["size"] == jax_result["size"]

    print(
        "\n".join(
            [
                f"dot numpy elapsed: {numpy_result['first_elapsed_sec']:.6f}s",
                f"dot jax first elapsed: {jax_result['first_elapsed_sec']:.6f}s",
                f"dot jax steady elapsed: {jax_result['steady_elapsed_sec']:.6f}s",
            ]
        )
    )
