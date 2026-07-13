import pytest
from rpkbin.codegen.demo import list_cases, run_case

cases = []
for name in list_cases():
    if "experimental" in name:
        cases.append(pytest.param(name, marks=pytest.mark.experimental))
    else:
        cases.append(name)

@pytest.mark.parametrize("name", cases)
def test_demo_cases(name):
    # Just ensure it doesn't crash
    # NotImplementedError is caught and handled inside run_case,
    # so it will return None in that case, which is fine.
    res = run_case(name)
    if "experimental" not in name and "for_break" not in name:
        assert res is not None
    if name == "pattern_mul2":
        assert res is not None
        assert "mul2_to_shl1" in res["applied_patterns"], (
            f"pattern_mul2 should apply mul2_to_shl1, got {res['applied_patterns']}"
        )
