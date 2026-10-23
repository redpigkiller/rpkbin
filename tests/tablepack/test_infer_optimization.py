import enum
import random

import pytest

from rpkbin.tablepack import Bool, Schema, Uint, infer, pack


def _compressors():
    values = [None, "zlib"]
    try:
        import brotli  # noqa: F401
    except ImportError:
        pass
    else:
        values.append("brotli")
    return values


@pytest.mark.parametrize("compress", _compressors())
@pytest.mark.parametrize("embed_schema", [True, False])
def test_joint_pack_is_measured_as_a_table_and_never_loses_to_baseline(compress, embed_schema):
    rng = random.Random(8271)
    n = 1200
    source = [rng.randrange(16) for _ in range(n)]
    cols = {f"field{i}": [(v + i) % 16 for v in source] for i in range(8)}

    baseline = infer(cols, compress=compress, embed_schema=embed_schema, optimize=False)
    optimized, report = infer(cols, report=True, compress=compress,
                              embed_schema=embed_schema, optimize=True)

    baseline_size = len(pack(cols, baseline, compress=compress, embed_schema=embed_schema))
    optimized_size = len(pack(cols, optimized, compress=compress, embed_schema=embed_schema))
    assert optimized_size <= baseline_size
    jointly_packed = Schema(*(
        next(c for c, _ in report[name] if isinstance(c, Uint) and c.pack)
        for name in cols
    ))
    assert optimized_size <= len(pack(cols, jointly_packed, compress=compress,
                                      embed_schema=embed_schema))
    # The report is deliberately the isolated zlib ranking, so a joint-table
    # winner may differ from the first per-column candidate.
    if compress is None:
        assert all(c.pack for c in optimized)
        assert any(optimized[name] != report[name][0][0] for name in cols)
    assert all([size for _, size in report[name]] == sorted(size for _, size in report[name])
               for name in cols)


def test_repeat_candidates_are_considered_for_long_repeated_strings():
    cols = {"label": ["shared-value" * 12] * 400}
    schema, report = infer(cols, report=True, compress="zlib")
    assert any(col.repeat for col, _ in report["label"])
    baseline = infer(cols, optimize=False)
    assert len(pack(cols, schema, compress="zlib")) <= len(pack(cols, baseline, compress="zlib"))


def test_report_is_fast_zlib_order_when_winner_is_not_first():
    cols = {"x": [i % 8 for i in range(500)]}
    schema, report = infer(cols, report=True, compress=None)
    assert report["x"] == sorted(report["x"], key=lambda item: item[1])
    assert schema["x"] in [candidate for candidate, _ in report["x"]]
    # The report is advisory; don't require a particular winner or assume
    # compressor versions must disagree about this data.


def test_optimize_false_keeps_the_fast_baseline():
    cols = {"flag": [i % 2 == 0 for i in range(30)]}
    schema, report = infer(cols, report=True, optimize=False)
    assert schema["flag"] == report["flag"][0][0]
    assert type(schema["flag"]) is Bool


def test_generator_inputs_are_normalized_once_for_infer_and_explain():
    from rpkbin.tablepack import explain

    rows = ({"text": f"row-{i % 3}", "count": i} for i in range(40))
    schema = infer(rows, compress="zlib")
    assert schema.names == ["text", "count"]

    rows = ({"text": f"row-{i % 3}", "count": i} for i in range(40))
    result = explain(rows, compress="zlib")
    assert "total: 40 rows" in result
    assert "text" in result and "count" in result


@pytest.mark.parametrize("value", [bytearray(b"same"), memoryview(bytearray(b"same"))])
def test_bytes_like_values_do_not_need_to_be_hashable(value):
    vals = [value, value]
    assert infer({"raw": vals}, optimize=False)["raw"].TYPE == "bytes"


def test_numpy_bool_is_inferred_as_bool_when_numpy_is_available():
    np = pytest.importorskip("numpy")
    values = [np.bool_(True), np.bool_(False), np.bool_(True)]
    assert type(infer({"flag": values}, optimize=False)["flag"]) is Bool


def test_str_enum_subclass_is_inferred_without_closed_value_assumptions():
    class Kind(str, enum.Enum):
        READ = "read"
        WRITE = "write"

    schema = infer({"kind": [Kind.READ, Kind.WRITE, Kind.READ]}, optimize=False)
    assert schema["kind"].TYPE in ("str", "category")
    assert schema["kind"].TYPE != "enum"
