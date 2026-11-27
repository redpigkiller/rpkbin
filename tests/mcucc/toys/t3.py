"""T2(16) with conditional borrow on six sub/cmp forms and extra registers.

Adds r6/r7 to b and x2 (r7:r6) to w and ptr. ABI and form widths stay the same.
One-byte codes: mov 64, ldind 24, add_rr 32, movw/addw/subw/cmpw 36,
push/pop 6, ret 1, halt 1. Total 164 of 256.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from t2 import make_verified as _make_verified


def make_verified():
    return _make_verified(16, conditional_compare=True, extra_registers=True)


TARGET, SIM, STUBS = make_verified()
