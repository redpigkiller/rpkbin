"""Machine liveness and shared, guarded P/Q rewrite conditions."""

from dataclasses import replace
from types import SimpleNamespace
from .expand import _Context, _direct
from .ir import Imm
from .machine import _effects_intersect as _intersects, defs_uses
from .target import I, MemLoc, R, T, RegLoc, units
from .verify import _ordered, _physical_cfg


def _live_machine(mir,target,name, *, pool_private=False):
    successors = _physical_cfg(SimpleNamespace(mir=mir,name=name))
    before, after = [set() for _ in mir], [set() for _ in mir]
    effects = []
    for m in mir:
        defined, used, written, read = defs_uses(m, pool_private=pool_private)
        if m.op == "call":
            written = {f.name for f in target.flags}
        effects.append((set(defined)|{("condition",n) for n in written},set(used)|{("condition",n) for n in read}))
    changed = True
    while changed:
        changed = False
        for index in reversed(range(len(mir))):
            out = set().union(*(before[nxt] for nxt in successors[index]))
            defined,used = effects[index]
            incoming = (out-defined)|used
            if out != after[index] or incoming != before[index]:
                before[index],after[index],changed = incoming,out,True
    return after,effects


def _same(target,left,right):
    return _ordered(target,left)==_ordered(target,right)


def _read_value(instruction,key):
    return instruction.values[instruction.form.bind[key]]


def _matches(mir,target,name,rule, *, pool_private=False):
    if not mir:
        return []
    after,effects = _live_machine(mir,target,name,pool_private=pool_private)
    context = _Context(target,{})
    matches = []
    def mov(source,destination,width,original,index):
        src = source if isinstance(source, Imm) else RegLoc(source)
        plans = _direct(context,src,RegLoc(destination),width,False)
        allowed = ("const",) if isinstance(source, Imm) else ("mov",)
        plans = [p for p in plans if p[0].form.op in allowed and p[0].bytes <= original.bytes]
        result = []
        for plan in plans:
            replacement = replace(original,form=plan[0].form,values=dict(plan[0].values))
            defined,_,written,_ = defs_uses(replacement, pool_private=pool_private)
            old_defs,_,old_flags,_ = defs_uses(original, pool_private=pool_private)
            if not _intersects(set(defined)-set(old_defs),after[index]) and not {("condition",n) for n in set(written)|set(old_flags)} & after[index]:
                result.append(replacement)
        return min(result,key=lambda m:context._key([m])) if result else None
    for index,instruction in enumerate(mir):
        op,form = instruction.op,instruction.form
        action = None
        if rule==1 and op=="mov" and _same(target,_read_value(instruction,"dst"),_read_value(instruction,"a")) and not (effects[index][0]-set(units(target,_read_value(instruction,"dst"))))&after[index]:
            action = {index:()}
        elif rule==2 and form is not None and op not in ("call","ret","jmp","br","st","st_ind","push","pop") and effects[index][0] and not effects[index][0]&after[index]:
            action = {index:()}
        elif rule==3 and op=="mov":
            destination,source = _read_value(instruction,"dst"),_read_value(instruction,"a")
            watched = units(target,destination)|units(target,source)
            for nxt in range(index+1,len(mir)):
                consumer = mir[nxt]
                if consumer.form is None or consumer.op in ("jmp","br","ret","call","asm"):
                    break
                defined,used = effects[nxt]
                if units(target,destination)&used:
                    if units(target,destination)&after[nxt]:
                        break
                    values,valid,changed = dict(consumer.values),True,False
                    for operand in consumer.form.operands:
                        reads = any(key!="dst" and field==operand.name for key,field in consumer.form.bind.items())
                        if isinstance(operand,R) and reads and _same(target,values[operand.name],destination):
                            if consumer.form.bind.get("dst")==operand.name or source not in context.classes[operand.cls]:
                                valid=False
                                break
                            values[operand.name],changed = source,True
                    if valid and changed:
                        action={index:(),nxt:(replace(consumer,values=values),)}
                    break
                if _intersects(defined,watched):
                    break
        elif rule==4 and op=="jmp":
            destination = next(instruction.values[o.name] for o in form.operands if isinstance(o,T))
            pointer=index+1
            while pointer<len(mir) and mir[pointer]._label is not None:
                if mir[pointer]._label==destination:
                    action={index:()}
                    break
                pointer+=1
            if action is None:
                start=next((j for j,m in enumerate(mir) if m._label==destination),None)
                if start is not None:
                    stop=start+1
                    while stop<len(mir) and mir[stop]._label is None:
                        stop+=1
                    body=[m for m in mir[start+1:stop] if m.form is not None or m._asm]
                    if len(body)==1 and body[0].op=="ret" and body[0].bytes<=instruction.bytes:
                        action={index:(replace(body[0],_origin=instruction._origin,_block=instruction._block,_role="ret"),)}
        elif rule==5 and op=="cmp" and 0<index<len(mir)-1 and mir[index+1].op=="br":
            previous,branch=mir[index-1],mir[index+1]
            if previous.form is not None and "dst" in previous.form.bind and isinstance(next(o for o in form.operands if o.name==form.bind["b"]),I) and _read_value(instruction,"b")==0:
                flag=next(iter(branch.form.flags_read))
                prev_dst = _read_value(previous, "dst")
                cmp_a = _read_value(instruction, "a")
                if (
                    (form.flags_write or {}).get(flag) == "zero"
                    and (previous.form.flags_write or {}).get(flag) == "zero"
                    and previous.form.width == form.width
                    and isinstance(prev_dst, (str, MemLoc))
                    and isinstance(cmp_a, (str, MemLoc))
                    and _same(target, prev_dst, cmp_a)
                ):
                    action={index:()}
        elif rule==6 and op=="ld":
            memory,destination=_read_value(instruction,"a"),_read_value(instruction,"dst")
            changed=set()
            for previous in reversed(mir[:index]):
                if previous.form is None or previous.op in ("jmp","br","ret","call","asm","st_ind"):
                    break
                if previous.op in ("ld","st"):
                    address=_read_value(previous,"a")
                    source=_read_value(previous,"dst" if previous.op=="ld" else "b")
                    source_units = set() if type(source) is int else set(units(target, source))
                    if address==memory and not _intersects(changed, source_units | set(units(target, memory))):
                        if type(source) is not int and _same(target,source,destination) and not {("condition",n) for n in (form.flags_write or {})}&after[index]:
                            action={index:()}
                        else:
                            arg = Imm(source, form.width) if type(source) is int else source
                            replacement=mov(arg,destination,form.width,instruction,index)
                            if replacement is not None:
                                action={index:(replacement,)}
                        break
                changed.update(defs_uses(previous, pool_private=pool_private)[0])
        elif rule==7 and op=="push" and index+1<len(mir) and mir[index+1].op=="pop" and form.width==mir[index+1].form.width:
            pop=mir[index+1]
            source,destination=_read_value(instruction,"a"),_read_value(pop,"dst")
            if _same(target,source,destination):
                if not ((effects[index][0]|effects[index+1][0])-set(units(target,destination)))&after[index+1]:
                    action={index:(),index+1:()}
            else:
                replacement=mov(source,destination,form.width,pop,index+1)
                if replacement is not None and replacement.bytes<=instruction.bytes+pop.bytes:
                    action={index:(),index+1:(replacement,)}
        elif rule==8 and op=="const":
            destination,value=_read_value(instruction,"dst"),_read_value(instruction,"a")
            if not {("condition",n) for n in (form.flags_write or {})}&after[index]:
                for previous in reversed(mir[:index]):
                    if previous.form is None or previous.op in ("jmp","br","ret","call","asm"):
                        break
                    if previous.op=="const" and _same(target,_read_value(previous,"dst"),destination) and _read_value(previous,"a")==value:
                        action={index:()}
                        break
                    if _intersects(set(defs_uses(previous, pool_private=pool_private)[0]),set(units(target,destination))):
                        break
        elif rule==9 and instruction._label is None:
            pointer=index-1
            while pointer>=0 and mir[pointer]._label is None:
                if mir[pointer].op in ("jmp","ret"):
                    action={index:()}
                    break
                pointer-=1
        if action is not None:
            matches.append((index,action))
    return matches


def _peephole(mir,target,name, *, pool_private=False):
    sequence=tuple(mir)
    for _ in range(20):
        changed=False
        for rule in range(1,10):
            while matches:=_matches(sequence,target,name,rule,pool_private=pool_private):
                _,action=matches[0]
                sequence=tuple(item for index,m in enumerate(sequence) for item in action.get(index,(m,)))
                changed=True
        if not changed:
            break
    return sequence
