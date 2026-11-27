"""Bijective declaration, callback, source and native-access adapters."""

import ast
from dataclasses import replace

from rpkbin.mcucc.target import M,P,R,MemLoc


def renamed(target,binding):
    declared=(*target.spaces,*target.regs,*target.aliases,*target.classes,*target.flags)
    names={item.name:f'_renamed_{index}' for index,item in enumerate(declared)}
    reverse={new:old for old,new in names.items()}
    def location(value):
        if isinstance(value,MemLoc):
            return replace(value,space=names[value.space])
        return names[value]
    forms=[]
    for form in target.forms:
        operands=[]
        for operand in form.operands:
            if isinstance(operand,P):
                operand=replace(operand,cls=names[operand.cls],space=names[operand.space])
            elif isinstance(operand,R):
                operand=replace(operand,cls=names[operand.cls])
            elif isinstance(operand,M):
                operand=replace(operand,space=names[operand.space])
            operands.append(operand)
        def adapter(original):
            def encode(values):
                translated=dict(values)
                for operand in original.operands:
                    if isinstance(operand,(R,P)):
                        translated[operand.name]=reverse[translated[operand.name]]
                return original.encode(translated)
            return encode
        forms.append(replace(form,operands=tuple(operands),
            flags_read={names[key]:value for key,value in (form.flags_read or {}).items()} or None,
            flags_write={names[key]:value for key,value in (form.flags_write or {}).items()} or None,
            clobbers=tuple(names[value] for value in form.clobbers),encode=adapter(form) if form.encode else None))
    modified=replace(target,name='renamed_target',
        spaces=tuple(replace(s,name=names[s.name]) for s in target.spaces),
        regs=tuple(replace(r,name=names[r.name]) for r in target.regs),
        aliases=tuple(replace(a,name=names[a.name],parts=tuple(names[p] for p in a.parts)) for a in target.aliases),
        classes=tuple(replace(c,name=names[c.name],members=tuple(names[m] for m in c.members)) for c in target.classes),
        flags=tuple(replace(f,name=names[f.name]) for f in target.flags),forms=tuple(forms),
        abi=replace(target.abi,arg_locs={width:tuple(location(v) for v in choices) for width,choices in target.abi.arg_locs.items()},
            ret_locs={width:location(v) for width,v in target.abi.ret_locs.items()},
            callee_saved=tuple(names[v] for v in target.abi.callee_saved)))
    native=replace(binding,names={names[item.name]:(binding.names or {}).get(item.name,item.name)
        for item in (*target.spaces,*target.regs,*target.aliases,*target.flags)})
    return modified,native,names


def source_names(source,names):
    class LiteralNames(ast.NodeTransformer):
        def visit_Constant(self,node):
            if isinstance(node.value,str) and node.value in names:
                return ast.copy_location(ast.Constant(names[node.value]),node)
            return node
    class SourceNames(ast.NodeTransformer):
        def visit_Call(self,node):
            self.generic_visit(node)
            if isinstance(node.func,ast.Name):
                name=node.func.id
                if name=='Ptr' and len(node.args)==1 and isinstance(node.args[0],ast.Constant):
                    node.args[0]=LiteralNames().visit(node.args[0])
                allowed=('args','ret','preserves','clobbers') if name in ('fn','extern') else ('ins','outs','clobbers') if name=='asm' else ()
                for keyword in node.keywords:
                    if keyword.arg in allowed:
                        keyword.value=LiteralNames().visit(keyword.value)
            return node
        def visit_Attribute(self,node):
            self.generic_visit(node)
            if isinstance(node.value,ast.Name) and node.value.id=='mem' and node.attr in names:
                node.attr=names[node.attr]
            return node
    return ast.unparse(ast.fix_missing_locations(SourceNames().visit(ast.parse(source))))
