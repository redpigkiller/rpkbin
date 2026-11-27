"""Declared stack groups and private-slot alternatives for boundary saves."""

from .expand import MInstr,_conflicts,_move,_writes
from .target import R,_width,units


def _save_plans(ctx,location,slot,width,protected,restored,destination,pushed):
    source=ctx._units(location)
    result=[]
    names=[]
    for form in ctx.target.forms:
        if form.op=="push":
            operand=next(o for o in form.operands if isinstance(o,R))
            names.extend(ctx.classes[operand.cls])
    for name in sorted(set(names),key=lambda n:(len(units(ctx.target,n)),ctx.reg_order[n])):
        covered=units(ctx.target,name)
        if not source<=covered or covered&destination:
            continue
        if name in pushed:
            return [((),(),name,covered)]
        if any(covered&units(ctx.target,other) for other in pushed):
            continue
        pairs=[]
        for push in ctx.forms.get(("push",_width(ctx.target,name)),()):
            pin=next(o for o in push.operands if isinstance(o,R))
            if name not in ctx.classes[pin.cls]:
                continue
            before=MInstr(push,{pin.name:name})
            if _conflicts(ctx,_writes(ctx,before),protected):
                continue
            for pop in ctx.forms.get(("pop",push.width),()):
                pout=next(o for o in pop.operands if isinstance(o,R))
                if name not in ctx.classes[pout.cls]:
                    continue
                after=MInstr(pop,{pout.name:name})
                if not _conflicts(ctx,_writes(ctx,after),restored):
                    pairs.append((before,after))
        if pairs:
            for before,after in sorted(pairs,key=lambda p:ctx._key(p)):
                result.append(((before,),(after,),name,covered))
            break
    before=_move(ctx,location,slot,width,set(),protected)
    after=_move(ctx,slot,location,width,restored,set())
    if before is not None and after is not None:
        result.append((tuple(before),tuple(after),None,source))
    return sorted(result,key=lambda p:(sum(m.bytes for m in (*p[0],*p[1])),int(p[2] is None),ctx._key((*p[0],*p[1]))))
