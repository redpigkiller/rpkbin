from dataclasses import replace
import random

import pytest

from rpkbin.mcucc.errors import TargetError
from rpkbin.mcucc.opsem import flag_value
from test_formcheck import toy


@pytest.mark.parametrize("width", (8, 16))
@pytest.mark.parametrize(("word", "polarity"), (("borrow_msb0", 1), ("nborrow_msb0", 0)))
def test_conditional_flags(word, polarity, width):
    high = 1 << (width - 1)
    assert flag_value(word, "cmp", width, 0, 1, high * 2 - 1) == polarity
    assert flag_value(word, "sub", width, high - 1, high - 1, 0) == 1 - polarity
    for a, b in ((high, 0), (0, high), (high, high)):
        assert flag_value(word, "cmp", width, a, b, 0) is None
    with pytest.raises(ValueError, match=word):
        flag_value(word, "add", width, 0, 0, 0)


@pytest.mark.parametrize("word", ("borrow_msb0", "nborrow_msb0"))
def test_tl5_limits_conditional_flags_to_sub_and_cmp(word):
    target, _, _ = toy("t2", 8)
    index = next(i for i, form in enumerate(target.forms) if form.op == "cmp")
    forms = list(target.forms)
    forms[index] = replace(forms[index], flags_write={"C": word})
    replace(target, forms=tuple(forms))
    index = next(i for i, form in enumerate(forms) if form.op == "add")
    forms[index] = replace(forms[index], flags_write={"C": word})
    with pytest.raises(TargetError, match=word):
        replace(target, forms=tuple(forms))


@pytest.mark.parametrize("form_id", ("cmp_rr", "cmp_ri", "cmpw"))
def test_c4_adds_low_half_boundaries_and_seeded_pairs(form_id):
    from rpkbin.mcucc.formcheck import _msb0_samples

    target, _, _ = toy("t3", 16)
    form = next(form for form in target.forms if form.id == form_id)
    samples = list(_msb0_samples(target, form, random.Random(71)))
    assert samples == list(_msb0_samples(target, form, random.Random(71)))
    pairs = [(a, b) for _, a, b in samples]
    limit = 1 << (form.width - 1)
    assert len(pairs) >= 12
    assert all(0 <= a < limit and 0 <= b < limit for a, b in pairs)
    assert {(0, 1), (1, 0), (limit - 1, limit // 2), (limit // 2, limit - 1)} <= set(pairs)
    assert any(a not in (0, 1, limit // 2, limit - 1) for a, _ in pairs)
