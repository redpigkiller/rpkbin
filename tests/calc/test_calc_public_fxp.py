from decimal import Decimal

import numpy as np
import pytest

from rpkbin.app import calc


def test_public_fxp_quantizes_exact_numeric_input():
    assert calc.fxp(Decimal("0.50000000000000000000001"), "Q8.0", quant="RND_EVEN").to_bits() == 1
    with pytest.raises(ValueError):
        calc.fxp("0x15", "Q4.4")


def test_public_fxpi_masks_and_preserves_shape():
    assert float(calc.fxpi(0xFF, "Q4.4")) == -0.0625
    assert calc.fxpi(300, "Q4.4").to_bits() == 0x2C
    assert calc.fxpi(1 << 70, "Q80.0").to_bits() == 1 << 70
    assert calc.fxpi(np.empty((0, 2)), "Q4.4").shape == (0, 2)
    assert calc.fxpi(np.array(1), "Q4.4").shape == ()
    with pytest.raises(TypeError):
        calc.fxpi(1, "Q4.4", overflow="SAT")
