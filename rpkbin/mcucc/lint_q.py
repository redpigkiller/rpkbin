"""Residual rewrite findings using the optimizer's own predicates."""

from .peephole import _matches


def _findings(mir,target,name, *, pool_private=False):
    return tuple({"rule":f"Q{rule}","function":name,"index":index} for rule in range(1,10) for index,_ in _matches(mir,target,name,rule,pool_private=pool_private))
