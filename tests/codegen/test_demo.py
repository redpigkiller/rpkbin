import pytest
from rpkbin.codegen.demo import list_cases, run_case

@pytest.mark.parametrize("name", list_cases())
def test_demo_cases(name):
    res = run_case(name)
    assert res is not None
    if name == "pattern_mul2":
        assert res is not None
        assert "mul2_to_shl1" in res["applied_patterns"], (
            f"pattern_mul2 should apply mul2_to_shl1, got {res['applied_patterns']}"
        )
