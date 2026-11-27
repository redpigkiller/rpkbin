"""Build machine sequences from a complete location assignment."""

from dataclasses import replace

from .errors import AllocationError, UnsupportedError
from .expand import MInstr, _TargetRef, _conflicts, _expand_branch, _move, _parallel, _writes, expand
from .ir import Imm, VReg, _ir_defs_uses, _live
from .machine import _call_units, defs_uses
from .target import MemLoc, RegLoc, SlotLoc, T, _width, units
from .saves import _save_plans


def _active(function):
    result = set()
    for block in function.blocks:
        for instruction in (*block.instrs, block.term):
            defined, used = _ir_defs_uses(instruction)
            result.update(defined | used)
    return sorted(result, key=lambda v: v.id)


def _temporary_assignment(function, ctx):
    if _active(function) and not ctx.pool:
        raise AllocationError(f"{function.name}: temporary allocation needs a spill pool")
    return {v: SlotLoc("vreg", v.id, 0, v.width // ctx.target.cell_bits) for v in _active(function)}


class _Builder:
    def __init__(self, function, ctx, locations, interfaces, order=None):
        self.function, self.ctx, self.locations, self.interfaces = function, ctx, locations, interfaces
        self.order = order or [b.id for b in function.blocks]
        self.points = ctx.park_points if ctx.park_points is not None else _live(function)[2]
        self.ptemp = 0
        self._relaxed_dst = False

    def _position(self, value):
        return value if isinstance(value, Imm) else self.locations[value]

    def _units(self, values):
        return set().union(*(self.ctx._units(self._position(v)) for v in sorted(values, key=lambda v: (v.id if isinstance(v, VReg) else -1, repr(v)))))

    def _tag(self, sequence, bid, index, role, callee=None):
        if sequence is None:
            raise AllocationError(f"{self.function.name}: block {bid}, instruction {index}: no legal {role} move; register class, scratch or slot unavailable")
        remap = {}
        tagged = []
        for instruction in sequence:
            values = {}
            for name, value in instruction.values.items():
                if isinstance(value, SlotLoc) and value.kind == "ptemp":
                    if value.key not in remap:
                        remap[value.key] = self.ptemp
                        self.ptemp += 1
                    value = replace(value, key=remap[value.key])
                values[name] = value
            tagged.append(replace(instruction, values=values, _origin=(bid, index), _block=bid, _role=role,
                                  _target=self.ctx.target, _abi=self.function.abi,
                                  _callee_abi=dict(callee) if callee is not None else None,
                                  _pool_bounds=tuple(self.ctx.pool.items()) if self.ctx.pool_private else ()))
        return tagged

    def _return_form(self):
        occupied = set()
        for value in (self.function.abi.get("ret"), *self.function.abi.get("preserves", ())):
            if value is not None:
                occupied.update(units(self.ctx.target, value))
        candidates = [f for f in self.ctx.forms.get(("ret", 0), ()) if not set().union(*(units(self.ctx.target, r) for r in f.clobbers)) & occupied]
        if not candidates:
            details = [(f.id, f.clobbers) for f in self.ctx.forms.get(("ret", 0), ())]
            raise UnsupportedError(f"{self.function.name}: ret forms {details!r} overlap return/preserved registers {sorted(occupied, key=repr)!r}")
        return min(candidates, key=lambda f: (f.bytes, self.ctx.form_order[f.id]))

    def _call(self, instruction, bid, index):
        callee = self.interfaces[instruction.meta["callee"]]
        if any(loc is None for loc in callee["args"]) or (instruction.dst is not None and callee["ret"] is None):
            raise UnsupportedError(f"{self.function.name}:{instruction.meta['line']}: callee ABI lacks argument/return width")
        destroyed = _call_units(self.ctx.target, callee)
        live = self.points[bid, index] - {instruction.dst}
        saved = [v for v in sorted(live, key=lambda v: v.id) if self.ctx._units(self.locations[v]) & destroyed]
        if any(isinstance(self.locations[v], MemLoc) for v in saved):
            raise AllocationError(f"{self.function.name}:{instruction.meta['line']}: call clobbers a fixed ABI memory value")
        stable = live - set(saved)
        calls = [f for f in self.ctx.forms.get(("call", 0), ()) if all(not isinstance(o, T) or o.abs_bits is not None for o in f.operands)]
        if not calls:
            raise UnsupportedError(f"{self.function.name}:{instruction.meta['line']}: missing absolute call form")
        form = min(calls, key=lambda f: (f.bytes, self.ctx.form_order[f.id]))
        operand = next(o for o in form.operands if isinstance(o, T))
        destination = self.ctx._units(self.locations[instruction.dst]) if instruction.dst is not None and not self._relaxed_dst else frozenset()
        stable_units=self._units(stable)
        def finish(prefix,backups):
            sequence=list(prefix)
            pairs=[(self._position(value),RegLoc(dest) if isinstance(dest,str) else dest,value.width) for value,dest in zip(instruction.args,callee["args"])]
            sequence.extend(self._tag(_parallel(self.ctx,pairs,stable_units),bid,index,"call-args"))
            sequence.extend(self._tag([MInstr(form,{operand.name:instruction.meta["callee"]})],bid,index,"call",callee))
            if instruction.dst is not None:
                ret=RegLoc(callee["ret"]) if isinstance(callee["ret"],str) else callee["ret"]
                sequence.extend(self._tag(_move(self.ctx,ret,self.locations[instruction.dst],instruction.dst.width,stable_units,set()),bid,index,"call-result"))
            protected=set(stable_units)|set(destination)
            for restore,covered in reversed(backups):
                if any(_conflicts(self.ctx,_writes(self.ctx,m),protected) for m in restore):
                    return None
                sequence.extend(self._tag(restore,bid,index,"call-restore"))
                protected.update(covered)
            return sequence
        def plan(position,prefix,backups,pushed):
            checkpoint=self.ptemp
            if position==len(saved):
                try:
                    return finish(prefix,backups)
                except AllocationError:
                    return None
            value=saved[position]
            slot=SlotLoc("callsave",value.id,0,value.width//self.ctx.target.cell_bits)
            protected=self._units((*instruction.args,*stable,*saved[position+1:]))
            options=_save_plans(self.ctx,self.locations[value],slot,value.width,protected,stable_units|set(destination),destination,pushed)
            for before,after,name,covered in options:
                self.ptemp=checkpoint
                tagged=self._tag(before,bid,index,"call-save")
                result=plan(position+1,[*prefix,*tagged],[*backups,*([(after,covered)] if after else [])],(*pushed,name) if name is not None and name not in pushed else pushed)
                if result is not None:
                    return result
            self.ptemp=checkpoint
            return None
        result=plan(0,[],[],())
        if result is None:
            raise AllocationError(f"{self.function.name}:{instruction.meta['line']}: call has no legal save/restore; missing stack group or private slot/scratch")
        return result

    def _assembly(self,instruction,bid,index):
        meta=instruction.meta
        inputs,outputs=meta["inputs"],meta["outputs"]
        for value,hint in (*inputs,*outputs):
            location=self.locations[value]
            if not isinstance(location,RegLoc) or (hint is not None and location.name!=hint):
                raise AllocationError(f"{self.function.name}:{meta['line']}: asm v{value.id} needs register hint {hint!r}")
        out_values=set(meta["outs"])
        destination=self._units(out_values)
        written=set(destination)|set().union(*(units(self.ctx.target,n) for n in meta["clobbers"]))
        live=self.points[bid,index]-out_values
        saved=sorted((v for v in live if self.ctx._units(self.locations[v])&written),key=lambda v:v.id)
        stable=self._units(live-set(saved))
        names=[]
        seen=set()
        for value,_ in (*inputs,*outputs):
            if value not in seen:
                seen.add(value)
                names.append(self.locations[value].name)
        description={"rendered":meta["text"].format(*names),"nbytes":meta["nbytes"],
            "clobbers":meta["clobbers"],"flags_ok":meta["flags_ok"],
            "in_locs":tuple(self.locations[v] for v,_ in inputs),
            "out_locs":tuple(self.locations[v] for v,_ in outputs),"out_values":tuple(v for v,_ in outputs)}
        def plan(position,records,pushed):
            if position==len(saved):
                protected=set(stable)|set(destination)
                for _,after,covered in reversed(records):
                    if any(_conflicts(self.ctx,_writes(self.ctx,m),protected) for m in after):
                        return None
                    protected.update(covered)
                return records
            value=saved[position]
            slot=SlotLoc("callsave",value.id,0,value.width//self.ctx.target.cell_bits)
            protected=self._units((*instruction.args,*saved[position+1:]))|stable
            for before,after,group,covered in _save_plans(self.ctx,self.locations[value],slot,value.width,protected,stable|set(destination),destination,pushed):
                groups=(*pushed,group) if group is not None and group not in pushed else pushed
                selected=plan(position+1,[*records,*([(before,after,covered)] if before or after else [])],groups)
                if selected is not None:
                    return selected
            return None
        records=plan(0,[],())
        if records is None:
            raise AllocationError(f"{self.function.name}:{meta['line']}: asm lacks a legal stack group/slot save and restore")
        sequence=[]
        for before,_,_ in records:
            sequence.extend(self._tag(before,bid,index,"asm-save"))
        sequence.extend(self._tag([MInstr(None,{},_asm=description)],bid,index,"asm"))
        for _,after,_ in reversed(records):
            sequence.extend(self._tag(after,bid,index,"asm-restore"))
        return sequence

    def _run(self):
        blocks, ret_form = {}, self._return_form()
        if any(self.function.abi["args"][self.function.params.index(v)] is None for v in self.function.params):
            raise UnsupportedError(f"{self.function.name}: ABI has no argument location for requested width")
        if self.function.ret_width is not None and self.function.abi["ret"] is None:
            raise UnsupportedError(f"{self.function.name}: ABI has no return location for width {self.function.ret_width}")
        mapping = {b.id: b for b in self.function.blocks}
        for position, bid in enumerate(self.order):
            block = mapping[bid]
            sequence = []
            following = self.order[position + 1] if position + 1 < len(self.order) else None
            for index, instruction in enumerate(block.instrs):
                if self.ctx.parked:
                    sequence.extend(self._tag(self.ctx.park_plan.sequence((bid, index), self.locations),
                                              bid, index, "park"))
                busy = self._units(self.points[bid, index] - {instruction.dst})
                if instruction.op == "entry":
                    pairs = []
                    for param in instruction.args:
                        abi_loc = self.function.abi["args"][self.function.params.index(param)]
                        src = RegLoc(abi_loc) if isinstance(abi_loc, str) else abi_loc
                        pairs.append((src, self.locations[param], param.width))
                    sequence.extend(self._tag(_parallel(self.ctx, pairs, set()), bid, index, "entry"))
                elif instruction.op == "call":
                    sequence.extend(self._call(instruction, bid, index))
                elif instruction.op == "asm":
                    sequence.extend(self._assembly(instruction,bid,index))
                else:
                    if instruction.op in ("load", "store"):
                        operation = ("ld" if instruction.op == "load" else "st") + ("_ind" if isinstance(instruction.args[0], VReg) else "")
                        if not self.ctx.forms.get((operation, 8)):
                            raise UnsupportedError(f"{self.function.name}:{instruction.meta['line']}: missing {operation} form for data access")
                    result = expand(self.ctx, instruction, self.locations, busy)
                    if result is None:
                        raise AllocationError(f"{self.function.name}:{instruction.meta['line']}: no expansion for {(instruction.op, instruction.dst.width if instruction.dst else 8)!r}; register class/scratch unavailable")
                    sequence.extend(self._tag(result.mir, bid, index, "body"))
            term, index = block.term, len(block.instrs)
            if self.ctx.parked:
                sequence.extend(self._tag(self.ctx.park_plan.sequence((bid, index), self.locations),
                                          bid, index, "park"))
            if term.op == "ret":
                if term.args:
                    ret = self.function.abi["ret"]
                    destination = RegLoc(ret) if isinstance(ret, str) else ret
                    moves = _move(self.ctx, self._position(term.args[0]), destination, self.function.ret_width, set(), set())
                    sequence.extend(self._tag(moves, bid, index, "return"))
                sequence.extend(self._tag([MInstr(ret_form, {})], bid, index, "ret"))
            elif term.op == "jmp":
                if not self.ctx._optimistic and term.meta["target"] != following:
                    busy = frozenset(self._units(self.points[bid, index]))
                    forms = [f for f in self.ctx.forms["jmp", 0] if not any(units(self.ctx.target, name) & busy for name in f.clobbers)]
                    if not forms:
                        raise UnsupportedError(f"{self.function.name}: no jump form preserves live units in block {bid}")
                    form = min(forms, key=lambda f: (f.bytes, self.ctx.form_order[f.id]))
                    operand = next(o for o in form.operands if isinstance(o, T))
                    sequence.extend(self._tag([MInstr(form, {operand.name: _TargetRef(term.meta["target"])})], bid, index, "jump"))
            else:
                self.ctx._branch_targets = term.meta["true"], term.meta["false"]
                result = _expand_branch(self.ctx, term.meta["cond"], *term.args, self.locations,
                                        self._units(self.points[bid, index]), following,
                                        msb0_ok=term.meta.get("msb0_ok"))
                if result is None:
                    raise AllocationError(f"{self.function.name}:{term.meta.get('line', 1)}: no legal cmp/branch expansion for {term.meta['cond']}; register class/scratch unavailable")
                sequence.extend(self._tag(result.mir, bid, index, "branch"))
            blocks[bid] = sequence
        written = set().union(*(defs_uses(m, pool_private=self.ctx.pool_private)[0] for sequence in blocks.values() for m in sequence))
        preserved = [p for p in self.function.abi.get("preserves", ()) if units(self.ctx.target, p) & written]
        prologue, restore = [], []
        abi_inputs = set().union(*(units(self.ctx.target, loc) for loc in self.function.abi["args"] if loc is not None))
        preserved=sorted(preserved,key=lambda r:self.ctx.reg_order[r])
        destination=units(self.ctx.target,self.function.abi["ret"]) if self.function.abi["ret"] is not None else frozenset()
        def preserve_plan(index,records,pushed):
            if index==len(preserved):
                protected=set(destination)
                for _,after,covered in reversed(records):
                    if any(_conflicts(self.ctx,_writes(self.ctx,m),protected) for m in after):
                        return None
                    protected.update(covered)
                return records
            name=preserved[index]
            width = _width(self.ctx.target, name)
            slot = SlotLoc("preserve", self.ctx.reg_order[name], 0, width // self.ctx.target.cell_bits)
            protected = abi_inputs | set().union(*(units(self.ctx.target, other) for other in self.function.abi.get("preserves", ()) if other != name))
            options=_save_plans(self.ctx,RegLoc(name),slot,width,protected,destination,destination,pushed)
            for before,after,group,covered in options:
                groups=(*pushed,group) if group is not None and group not in pushed else pushed
                selected=preserve_plan(index+1,[*records,*([(before,after,covered)] if before or after else [])],groups)
                if selected is not None:
                    return selected
            return None
        records=preserve_plan(0,[],())
        if records is None:
            raise AllocationError(f"{self.function.name}: preserved registers {preserved!r} lack a legal stack/slot save and reverse restore")
        for before,after,covered in records:
            prologue.extend(self._tag(before,self.order[0],-1,"preserve-save"))
            if after:
                restore.insert(0,(after,covered))
        blocks[self.order[0]] = [*prologue, *blocks[self.order[0]]]
        for bid, sequence in blocks.items():
            if sequence and sequence[-1].op == "ret":
                tail = []
                protected = units(self.ctx.target, self.function.abi["ret"]) if self.function.abi["ret"] is not None else set()
                for after,covered in restore:
                    if any(_conflicts(self.ctx,_writes(self.ctx,m),protected) for m in after):
                        raise AllocationError(f"{self.function.name}: preserve restore clobbers an already restored/return location")
                    tail.extend(self._tag(after,bid,sequence[-1]._origin[1],"preserve-restore"))
                    protected=set(protected)|set(covered)
                blocks[bid] = [*sequence[:-1], *tail, sequence[-1]]
        return blocks


def _concretize(function, blocks, locations, ctx, occupied):
    slots = {}
    def record(value):
        if isinstance(value, SlotLoc):
            slots[value.kind, value.key] = max(slots.get((value.kind, value.key), 0), value.first + value.n)
    for location in locations.values():
        record(location)
    for sequence in blocks.values():
        for instruction in sequence:
            for location in instruction.values.values():
                record(location)
    if slots and not ctx.pool:
        raise AllocationError(f"{function.name}: private slots require a spill pool")
    allocated = {}
    public = []
    if slots:
        space, (low, high) = next(iter(ctx.pool.items()))
        for kind, key in sorted(slots, key=lambda p: (("vreg", "callsave", "preserve", "ptemp").index(p[0]), p[1])):
            count = slots[kind, key]
            start = low
            while start + count - 1 <= high and any((space, address) in occupied for address in range(start, start + count)):
                start += 1
            if start + count - 1 > high:
                raise AllocationError(f"{function.name}: spill pool needs {count} contiguous cells, available {sum((space, a) not in occupied for a in range(low, high + 1))}")
            allocated[kind, key] = MemLoc(space, start, count)
            occupied.update((space, address) for address in range(start, start + count))
            public.append((space, start, count))
    def concrete(location):
        if isinstance(location, SlotLoc):
            base = allocated[location.kind, location.key]
            return MemLoc(base.space, base.addr + location.first, location.n)
        return location
    locations = {v: concrete(loc) for v, loc in locations.items()}
    def instruction_with_addresses(instruction):
        role = instruction._role
        if ctx.pool_private and role == "body" and instruction.op in ("ld", "st"):
            if any(isinstance(value, SlotLoc) for value in instruction.values.values()):
                role = "reload" if instruction.op == "ld" else "spill"
        return replace(instruction, _role=role, values={
            key: concrete(value) for key, value in instruction.values.items()})

    blocks = {bid: [instruction_with_addresses(m) for m in sequence]
              for bid, sequence in blocks.items()}
    return blocks, locations, tuple(public)
