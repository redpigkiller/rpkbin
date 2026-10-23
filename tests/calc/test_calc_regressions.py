"""Regressions for Python semantics and lossless calculator views/edits."""

import numpy as np
import pytest

from rpkbin.calc.core import CalcConfig, WorkspaceSession, make_fxp, make_fxpi
from rpkbin.calc.core.fxp_model import PlainValueSnapshot, extract_spec


def test_appearance_preferences_validate_and_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = CalcConfig(theme="light", ui_font_family="Arial", code_font_family="Consolas",
                     ui_font_size=14, code_font_size=16, ui_scale=120, inspector_collapsed=True)
    cfg.save(path)
    assert CalcConfig.load(path) == cfg
    path.write_text('{"theme":"invalid","ui_font_size":999,"code_font_size":true,"ui_scale":-1,"ui_font_family":""}')
    loaded = CalcConfig.load(path)
    assert (loaded.theme, loaded.ui_font_size, loaded.code_font_size, loaded.ui_scale,
            loaded.ui_font_family) == ("dark", 24, 10, 80, "Segoe UI")


@pytest.fixture
def session():
    return WorkspaceSession(CalcConfig(startup_script=""), seed_demo_vars=False)


@pytest.mark.parametrize("assignment, expected", [
    ("x = 1.0", 1.0), ("x = [1, 2]", [1, 2]),
    ("x: int = 5", 5), ("x, y = (1.0, 2)", 1.0),
    ("for x in [1.0]:\n    pass", 1.0),
])
def test_python_rebinding_never_inherits_fxp_format(session, assignment, expected):
    session.execute("x = APyFixed.from_float(0.1, bits=10, frac_bits=12)")
    assert not session.execute(assignment).error_text
    assert type(session.user_ns["x"]) is type(expected)
    assert session.user_ns["x"] == expected


def test_output_before_exception_is_not_lost(session):
    result = session.execute('print("diagnostic"); 1/0')
    assert "diagnostic" in result.output_text
    assert "ZeroDivisionError" in result.error_text


def test_unpacking_and_shadowed_helpers_are_visible(session):
    result = session.execute("a, b = (1, 2)\nsin = 3")
    assert result.assigned_vars == ("a", "b", "sin")
    assert "sin" in session.user_variable_names()


def test_fft_views_keep_imaginary_components(session):
    session.execute("v = fft([1,2,3,4])")
    matrix = session.get_matrix_snapshot("v")
    assert complex(matrix.cells_real[0][1]) == -2 + 2j
    assert complex(matrix.cells_real[0][3]) == -2 - 2j
    assert matrix.format_str.startswith("complex")
    snapshot = session.get_inspector_snapshot("v", (0, 1))
    assert isinstance(snapshot, PlainValueSnapshot)
    assert complex(snapshot.value_str) == -2 + 2j
    assert next(r for r in session.list_variables() if r.name == "v").minmax_str == "—"


@pytest.mark.parametrize("code", [
    "x = np.array([])", "x = np.empty((0, 3))", "x = np.array(3)",
    "x = 'hello'", "x = float('nan')", "x = float('inf')", "x = {'a': 1}",
])
def test_python_values_can_be_selected_without_crashing(session, code):
    assert not session.execute(code).error_text
    assert session.get_inspector_snapshot("x") is not None
    session.list_variables()
    session.get_matrix_snapshot("x")


def test_native_array_edits_do_not_quantize_other_elements(session):
    session.execute("A = np.array([[1.23456789, 100000.125]])")
    original = session.user_ns["A"]
    session.update_target_field("A", (0, 0), "real", "2.5")
    assert session.user_ns["A"] is original
    assert original.dtype == np.float64
    assert original[0, 1] == 100000.125
    assert original[0, 0] == 2.5


def test_complex_edit_and_integer_dtype_guard(session):
    session.execute("A = np.array([1+2j, 3+4j])\nI = np.array([1, 2], dtype=np.int16)")
    session.update_target_field("A", (0, 0), "real", "5-6j")
    assert session.user_ns["A"].tolist() == [5-6j, 3+4j]
    with pytest.raises(ValueError, match="without loss"):
        session.update_target_field("I", (0, 0), "real", "1.5")
    assert session.user_ns["I"].tolist() == [1, 2]
    with pytest.raises(TypeError, match="FXP"):
        session.toggle_target_bit("I", (0, 0), 0)
    assert session.user_ns["I"].dtype == np.int16
    assert session.user_ns["I"].tolist() == [1, 2]


def test_large_int_bits_and_matrix_text_are_exact(session):
    value = (1 << 100) + 1
    x = make_fxpi(value, "Q128.0")
    assert x.to_bits() == value
    assert make_fxp(value, "Q128.0").to_bits() == value
    session.user_ns.update(x=x, A=np.array([value], dtype=object), n=value)
    assert session.get_inspector_snapshot("n").value_str == str(value)
    assert session.get_inspector_snapshot("x").real_str == str(value)
    assert session.get_matrix_snapshot("A").cells_real == [[str(value)]]
    session.reformat_variable("x", "Q129.0", pin_mode="real")
    assert session.user_ns["x"].to_bits() == value


def test_array_cell_saturation_and_quantization_provenance(session):
    session.user_ns["A"] = make_fxp([[0.25, 0.1]], "Q-2.12")
    assert session.get_inspector_snapshot("A", (0, 0)).status_kind == "saturated"
    assert session.get_inspector_snapshot("A", (0, 1)).status_kind == "quant_err"
    assert session.list_variables()[0].is_saturated
    session.update_target_field("A", (0, 0), "real", "0.0625")
    assert session.get_inspector_snapshot("A", (0, 0)).status_kind == "exact"
    assert not session.list_variables()[0].is_saturated
    assert session.get_inspector_snapshot("A", (0, 1)).status_kind == "quant_err"


def test_large_array_saturation_summary_never_reuses_cached_flag(session, monkeypatch):
    from rpkbin.calc.core.fxp_model import RpkFixedArray
    session.user_ns["A"] = make_fxp([99.0]+[0.0]*4096,"Q4.4")
    assert session.get_inspector_snapshot("A").status_kind == "saturated"
    assert session.list_variables()[0].is_saturated is None
    assert not session.execute("A[0] = APyFixed.from_float(0, bits=8, frac_bits=4)").error_text
    assert session.get_inspector_snapshot("A").status_kind == "untracked"
    # A metadata refresh must not materialize the whole array, even after edits.
    def reject_full_read(_self):
        pytest.fail("Workspace saturation summary read a whole large array")
    monkeypatch.setattr(RpkFixedArray, "to_bits", reject_full_read)
    for _ in range(3):
        assert session.list_variables()[0].is_saturated is None


@pytest.mark.parametrize("size, expected", [(4096, True), (4097, None)])
def test_saturation_scan_budget_boundary(session, size, expected):
    session.user_ns["A"] = make_fxp([99] + [0] * (size - 1), "Q4.4")
    assert session.list_variables()[0].is_saturated is expected
    assert session.get_inspector_snapshot("A").is_saturated


def test_large_array_single_cell_edit_does_not_reduce_whole_mask(session, monkeypatch):
    session.user_ns["A"] = make_fxp([0] * 4097, "Q4.4")
    def reject_full_reduction(*args, **kwargs):
        pytest.fail("A single-cell edit reduced the full saturation mask")
    with monkeypatch.context() as patch:
        patch.setattr(np, "any", reject_full_reduction)
        session.update_target_field("A", (0, 0), "real", "99")
    assert session.get_inspector_snapshot("A").is_saturated
    assert session.list_variables()[0].is_saturated is None


def test_dense_array_saturation_summary_requires_current_conversion_sources(session):
    session.user_ns["A"] = make_fxp([99,0.125],"Q4.4")
    assert session.list_variables()[0].is_saturated is True
    assert not session.execute('A[0]=A[1]').error_text
    assert session.list_variables()[0].is_saturated is None
    session.update_target_field("A", (0, 0), "real", "0")
    assert session.list_variables()[0].is_saturated is False
    session.update_target_field("A", (0, 1), "hex", "0")
    assert session.list_variables()[0].is_saturated is None
    session.update_target_field("A", (0, 0), "real", "99")
    assert session.list_variables()[0].is_saturated is True


def test_sparse_array_saturation_summary_validates_repl_writes_and_copies(session):
    session.user_ns["A"] = make_fxp([0.125,0.25],"Q4.4")+1
    assert session.list_variables()[0].is_saturated is None
    session.update_target_field("A", (0, 0), "real", "999")
    assert session.list_variables()[0].is_saturated is True
    assert not session.execute('A[0]=A[1]').error_text
    assert session.list_variables()[0].is_saturated is None
    session.update_target_field("A", (0, 0), "real", "0")
    assert session.list_variables()[0].is_saturated is None  # Second cell remains untracked.
    session.update_target_field("A", (0, 1), "real", "0")
    assert session.list_variables()[0].is_saturated is False
    name = session.duplicate_variable("A")
    session.update_target_field(name, (0, 0), "real", "999")
    states = {row.name: row.is_saturated for row in session.list_variables()}
    assert states == {"A": False, name: True}


@pytest.mark.parametrize("value", [True, False, None])
def test_workspace_saturation_status_is_json_tristate(session, value):
    import json
    from dataclasses import asdict, replace
    from rpkbin.calc.protocol import rows_from_dict
    session.user_ns["A"] = make_fxp([0], "Q4.4")
    row = replace(session.list_variables()[0], is_saturated=value)
    encoded = json.loads(json.dumps([asdict(row)], allow_nan=False))
    assert encoded[0]["is_saturated"] is value
    assert rows_from_dict(encoded) == [row]


@pytest.mark.parametrize("expression", [
    'APyFixed(0x20, bits=8, frac_bits=4)',
    'APyFixed.from_float(0.1, bits=8, frac_bits=4) + 1',
    '-APyFixed.from_float(0.1, bits=8, frac_bits=4)',
    '(APyFixedArray.from_float([0.1], bits=8, frac_bits=4) + 1)',
])
def test_untracked_bits_and_arithmetic_do_not_claim_exactness(session, expression):
    assert not session.execute(f"x = {expression}").error_text
    snapshot = session.get_inspector_snapshot("x")
    assert snapshot.status_kind == "untracked"
    assert snapshot.quant_err is None
    assert "unknown" in snapshot.status_text


@pytest.mark.parametrize("operation", ["hex", "uint", "bin", "toggle", "invert", "zero", "hex_pin"])
def test_raw_manipulations_drop_numeric_conversion_reference(session, operation):
    session.user_ns["x"] = make_fxp(0.125, "Q4.4")
    if operation in ("hex", "uint", "bin"):
        session.update_target_field("x", None, operation, "0")
    elif operation == "toggle":
        session.toggle_target_bit("x", None, 0)
    elif operation == "invert":
        session.invert_target_bits("x", None)
    elif operation == "zero":
        session.clear_target_bits("x", None)
    else:
        session.reformat_variable("x", "Q6.4", pin_mode="hex")
    assert session.get_inspector_snapshot("x").status_kind == "untracked"


@pytest.mark.parametrize("array", [False, True])
def test_sub_femtoscale_error_is_not_hidden(session, array):
    value = 0.1
    session.user_ns["x"] = make_fxp([value] if array else value, "Q4.60")
    snapshot = session.get_inspector_snapshot("x")
    # Binary64's actual 0.1 is representable at FWL=60; a coarser format is not.
    assert snapshot.status_kind == "exact"
    session.user_ns["x"] = make_fxp([value] if array else value, "Q4.50")
    snapshot = session.get_inspector_snapshot("x")
    assert snapshot.status_kind == "quant_err"
    assert 0 < abs(snapshot.quant_err) < 1e-15


@pytest.mark.parametrize("array", [False, True])
def test_large_integer_error_compares_exact_source(session, array):
    value = (1 << 100) + 1
    session.user_ns["x"] = make_fxp([value] if array else value, "Q130.-2")
    snapshot = session.get_inspector_snapshot("x")
    assert snapshot.status_kind == "quant_err"
    assert snapshot.quant_err == -1
    session.reformat_variable("x", "Q129.0", pin_mode="real")
    snapshot = session.get_inspector_snapshot("x")
    assert snapshot.status_kind == "exact"  # Only the most recent conversion is measured.
    assert "conversion" in snapshot.status_text


@pytest.mark.parametrize("array", [False, True])
def test_fxpi_wrap_and_saturation_use_original_scaled_integer(session, array):
    for overflow, kind, error in [("WRAP", "quant_err", -16), ("SAT", "saturated", -8)]:
        session.user_ns["x"] = make_fxpi([255] if array else 255, "Q4.4", overflow=overflow)
        snapshot = session.get_inspector_snapshot("x")
        assert snapshot.status_kind == kind
        assert snapshot.quant_err == error


def test_array_edits_track_reference_and_drop_it_for_raw_bits(session):
    session.user_ns["A"] = make_fxp([0.1, 0.125], "Q4.4")
    session.update_target_field("A", (0, 0), "hex", "20")
    assert session.get_inspector_snapshot("A").status_kind == "untracked"
    assert session.get_inspector_snapshot("A", (0, 1)).status_kind == "exact"
    session.update_target_field("A", (0, 0), "real", "0.1")
    assert session.get_inspector_snapshot("A").status_kind == "quant_err"
    # A REPL write invalidates the old conversion's diagnostics for this cell.
    assert not session.execute("A[0] = APyFixed.from_float(0.25, bits=8, frac_bits=4)").error_text
    assert session.get_inspector_snapshot("A").status_kind == "untracked"


def test_gui_edits_on_arithmetic_array_use_sparse_independent_provenance(session):
    session.user_ns["A"] = make_fxp([0.125, 0.25], "Q4.4") + 1
    assert session.get_inspector_snapshot("A").status_kind == "untracked"
    session.update_target_field("A", (0, 0), "real", "0.1")
    assert session.get_inspector_snapshot("A").status_kind == "quant_err"
    assert session.get_inspector_snapshot("A", (0, 1)).status_kind == "untracked"
    name = session.duplicate_variable("A")
    session.update_target_field(name, (0, 0), "real", "0.125")
    assert session.get_inspector_snapshot(name).status_kind == "exact"
    assert session.get_inspector_snapshot("A").status_kind == "quant_err"
    assert not session.execute('A[0] = A[1]').error_text
    assert session.get_inspector_snapshot("A").status_kind == "untracked"


def test_untracked_inspector_is_strict_json_serializable(session):
    import json
    from rpkbin.calc.protocol import inspector_from_dict, inspector_to_dict
    session.user_ns["x"] = make_fxp("0xFF", "Q8.0")
    snapshot = session.get_inspector_snapshot("x")
    payload = json.loads(json.dumps(inspector_to_dict(snapshot), allow_nan=False))
    assert payload["value"]["quant_err"] is None
    assert inspector_from_dict(payload) == snapshot


def test_exact_fxp_source_survives_copy_and_real_edit_without_float_loss(session):
    value = (1 << 100) + 1
    source = make_fxpi(value, "Q128.0")
    session.user_ns["x"] = make_fxp(source, "Q130.-2")
    assert session.get_inspector_snapshot("x").quant_err == -1
    name = session.duplicate_variable("x")
    assert session.get_inspector_snapshot(name).quant_err == -1
    session.update_target_field(name, None, "real", str(value + 4))
    assert session.get_inspector_snapshot(name).quant_err == -1
    assert session.get_inspector_snapshot("x").quant_err == -1


def test_saturating_arbitrary_integer_keeps_json_diagnostics_finite(session):
    import json
    from rpkbin.calc.protocol import inspector_to_dict
    session.user_ns["x"] = make_fxp(1 << 2048, "Q8.0")
    snapshot = session.get_inspector_snapshot("x")
    assert snapshot.status_kind == "saturated"
    assert snapshot.quant_err is None  # The error cannot be represented as binary64.
    json.dumps(inspector_to_dict(snapshot), allow_nan=False)


def test_duplicate_keeps_unsigned_format_and_independent_metadata(session):
    session.user_ns["A"] = make_fxp([0.3, 0.1], "UQ-2.12")
    name = session.duplicate_variable("A")
    assert not extract_spec(session.user_ns[name]).signed
    assert session.get_inspector_snapshot(name).status_kind == "saturated"
    session.update_target_field(name, (0, 0), "real", "0.125")
    assert session.get_inspector_snapshot("A").status_kind == "saturated"
    assert session.get_inspector_snapshot(name).status_kind == "exact"


@pytest.mark.parametrize("mode, expected", [
    ("RND", [2, -1]), ("RND_EVEN", [2, -2]),
    ("TRN", [1, -2]), ("TRN_ZERO", [1, -1]),
])
def test_quantization_matches_declared_rounding(mode, expected):
    values = [make_fxp(v, "Q8.0", quant=mode) for v in (1.5, -1.5)]
    assert [int(float(v)) for v in values] == expected


def test_rename_rejects_python_keyword(session):
    session.execute("x = 1")
    with pytest.raises(ValueError):
        session.rename_variable("x", "for")
    assert session.user_ns["x"] == 1


def test_fxp_matrix_real_display_preserves_large_integer_bits(session):
    value = (1 << 100) + 1
    session.user_ns["A"] = make_fxp([[value]], "Q128.0")
    assert session.get_matrix_snapshot("A").cells_real == [[str(value)]]


def test_large_fxp_conversion_and_editor_never_round_trip_through_float(session):
    value = (1 << 100) + 1
    session.user_ns["A"] = make_fxp([[value]], "Q128.0")
    session.reformat_variable("A", "Q129.0", pin_mode="real")
    assert session.get_matrix_snapshot("A").cells_real == [[str(value)]]
    session.update_target_field("A", (0, 0), "real", str(value+2))
    assert session.get_matrix_snapshot("A").cells_real == [[str(value+2)]]
    session.user_ns["x"] = value
    with pytest.raises(TypeError, match="FXP"):
        session.toggle_target_bit("x", None, 0)
    assert session.user_ns["x"] == value


def test_arbitrary_python_integer_is_visible_without_fxp_limit(session):
    value = 1 << 2048
    session.user_ns["x"] = value
    assert session.list_variables()[0].value_str == str(value)[:512]
    assert session.get_inspector_snapshot("x").value_str == str(value)


@pytest.mark.parametrize("value", [2, -32768, (1 << 100) + 1, -(1 << 2048)])
def test_python_int_is_plain_and_has_no_invented_width(session, value):
    session.user_ns["x"] = value
    snapshot = session.get_inspector_snapshot("x")
    assert isinstance(snapshot, PlainValueSnapshot)
    assert snapshot.type_name == "int" and snapshot.value_str == str(value)
    row = session.list_variables()[0]
    assert row.format_str == "int"
    assert (row.hex_str, row.s2_str, row.uint_str, row.bin_str, row.wl_info_str) == ("—",) * 5
    assert type(session.user_ns["x"]) is int


@pytest.mark.parametrize("field", ["hex", "s2", "uint", "bin"])
def test_plain_integer_rejects_fxp_field_edits_without_changing_value(session, field):
    session.user_ns["x"] = 2
    with pytest.raises(TypeError, match="FXP"):
        session.update_target_field("x", None, field, "1")
    assert session.user_ns["x"] == 2


@pytest.mark.parametrize("action", ["toggle_target_bit", "invert_target_bits", "clear_target_bits"])
def test_native_integer_bit_controls_are_rejected_but_python_bits_work(session, action):
    session.user_ns["x"] = 2
    args = ("x", None, 15) if action == "toggle_target_bit" else ("x", None)
    with pytest.raises(TypeError, match="FXP"):
        getattr(session, action)(*args)
    assert session.user_ns["x"] == 2
    result = session.execute("x ^= 1 << 15")
    assert not result.error_text and session.user_ns["x"] == 32770


@pytest.mark.parametrize("dtype, original, edited", [
    (np.uint8, 255, 128), (np.uint64, (1 << 63) + 1, (1 << 63) + 3),
    (np.int8, -128, -127), (np.int32, -2, 17), (np.float32, 1.25, 2.5),
    (np.complex64, 1+2j, 3-4j),
])
def test_numpy_scalar_and_array_keep_native_type_when_inspected_and_edited(session, dtype, original, edited):
    session.user_ns.update(x=dtype(original), A=np.array([original], dtype=dtype))
    for name, coord in (("x", None), ("A", (0, 0))):
        snapshot = session.get_inspector_snapshot(name, coord)
        assert isinstance(snapshot, PlainValueSnapshot)
        assert snapshot.type_name == np.dtype(dtype).name
        session.update_target_field(name, coord, "real", repr(edited))
    assert type(session.user_ns["x"]) is dtype
    assert session.user_ns["x"] == edited
    assert session.user_ns["A"].dtype == dtype
    assert session.user_ns["A"][0] == edited


@pytest.mark.parametrize("value", [256, -1, 1.5])
def test_numpy_integer_scalar_rejects_out_of_range_or_fractional_edits(session, value):
    session.user_ns["x"] = np.uint8(255)
    with pytest.raises((ValueError, OverflowError)):
        session.update_target_field("x", None, "real", repr(value))
    assert type(session.user_ns["x"]) is np.uint8 and session.user_ns["x"] == 255


def test_read_only_array_is_read_only_in_inspector_and_core(session):
    array = np.array([1], dtype=np.int16)
    array.flags.writeable = False
    session.user_ns["A"] = array
    assert not session.get_inspector_snapshot("A").editable
    with pytest.raises(ValueError, match="read-only"):
        session.update_target_field("A", (0, 0), "real", "2")
    assert array[0] == 1


@pytest.mark.parametrize("value", [1, np.uint8(1), [1], np.array([1]), np.array([[1]])])
def test_native_conversion_rejects_hex_pin_and_real_conversion_is_consistent(value):
    from rpkbin.calc.core.fxp_model import reformat_value
    with pytest.raises(TypeError, match="Hex pin"):
        reformat_value(value, "Q4.4", pin_mode="hex")
    converted = reformat_value(value, "Q4.4", pin_mode="real")
    bits = converted.to_bits()
    assert np.asarray(bits).item() == 16


@pytest.mark.parametrize("value", [1.6, 2.0, np.float32(1.6), np.array(1.6),
                                  [1.6], [[2.0]], True, np.bool_(False), [True], [1, False], "1"])
def test_fxpi_rejects_non_integer_inputs_consistently(value):
    with pytest.raises(TypeError, match="raw integers"):
        make_fxpi(value, "Q4.4")


def test_fxpi_preserves_large_raw_integers_in_mixed_sequences():
    large = (1 << 100) + 1
    values = make_fxpi([large, np.int64(1)], "Q128.0")
    assert values.to_bits() == [large, 1]
    assert make_fxpi(np.array(np.uint64((1 << 63) + 1)), "UQ64.0").to_bits() == (1 << 63) + 1
    with pytest.raises(ValueError, match="Unsupported overflow"):
        make_fxpi(1, "Q8.0", overflow="typo")


def test_metadata_only_and_paged_matrix_do_bounded_formatting(session, monkeypatch):
    session.user_ns["A"] = np.arange(4_000_000).reshape(1_000_000, 4)
    calls = []
    original = session.format_python_scalar
    monkeypatch.setattr(session, "format_python_scalar", lambda value: calls.append(value) or original(value))
    metadata = session.get_matrix_snapshot("A", include_cells=False)
    assert metadata.shape == (1_000_000, 4)
    assert not calls
    page = session.get_matrix_snapshot("A", window=(999990, 0, 32, 16))
    assert page.shape == metadata.shape and page.row_offset == 999990
    assert len(calls) == 40
    assert page.cells_real[-1][-1] == "3999999"


def test_reload_imports_keeps_user_shadowed_helpers(session):
    session.execute("sin=3")
    session.reload_auto_imports()
    assert session.user_ns["sin"] == 3
    assert "sin" in session.user_variable_names()


def test_rename_collision_preserves_both_values(session):
    session.execute("a=1\nb=2")
    with pytest.raises(ValueError, match="already exists"):
        session.rename_variable("a", "b")
    assert session.user_ns["a"] == 1 and session.user_ns["b"] == 2


def test_complex_math_helpers_keep_complex_inputs(session):
    assert not session.execute("x=fft([1+2j,3+4j])\nd=det([[1j,0],[0,1]])").error_text
    assert session.user_ns["x"].tolist() == [4+6j, -2-2j]
    assert session.user_ns["d"] == 1j


def test_config_loads_malformed_json_root_and_saves_atomically(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[]", encoding="utf-8")
    assert CalcConfig.load(path).theme == "dark"
    config = CalcConfig(default_quant="TRN")
    config.save(path)
    assert CalcConfig.load(path).default_quant == "TRN"
    assert not list(tmp_path.glob("*.tmp"))


def test_snapshot_protocol_round_trip_and_build_mismatch(session):
    import json
    from dataclasses import asdict
    from rpkbin.calc.protocol import inspector_from_dict, inspector_to_dict, matrix_from_dict, validate_envelope
    session.user_ns["x"] = make_fxp(-0.03, "Q-2.12")
    snapshot = session.get_inspector_snapshot("x")
    decoded = inspector_from_dict(json.loads(json.dumps(inspector_to_dict(snapshot))))
    assert decoded == snapshot
    session.user_ns["A"] = np.array([[1+2j]])
    matrix = session.get_matrix_snapshot("A")
    assert matrix.spec is None
    assert matrix_from_dict(json.loads(json.dumps(asdict(matrix)))) == matrix
    with pytest.raises(ValueError, match="protocol mismatch"):
        validate_envelope({"protocol": 99, "build": "a"}, "a")
    with pytest.raises(ValueError, match="builds differ"):
        validate_envelope({"protocol": 1, "build": "old"}, "new")
