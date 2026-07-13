from rpkbin.codegen.asm import AsmFunction, Instruction


def test_asm_function_optional_indent_preserves_labels_and_verbatim_text():
    asm = AsmFunction(
        "f",
        (
            Instruction("f:"),
            Instruction("MOV", ("r0", "#1")),
            Instruction("  raw one\nraw two", verbatim=True),
        ),
        instruction_indent="    ",
    )

    assert asm.format() == "f:\n    MOV r0, #1\n  raw one\nraw two"


def test_asm_function_format_defaults_remain_unindented():
    asm = AsmFunction("f", (Instruction("f:"), Instruction("RET")))

    assert asm.format() == "f:\nRET"
