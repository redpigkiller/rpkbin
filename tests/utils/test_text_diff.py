import pytest
import os
from rpkbin.utils.text_diff import (
    get_char_width,
    get_visual_width,
    pad_to_width,
    truncate_to_width,
    center_text,
    visual_wrap,
    format_line,
    get_dmp_annotations,
    diff_lines,
    diff_files
)

def test_diff_files(tmp_path):
    f1 = tmp_path / "file1.txt"
    f2 = tmp_path / "file2.txt"
    out = tmp_path / "diff.txt"
    
    f1.write_text("Line 1\nLine 2", encoding="utf-8")
    f2.write_text("Line 1\nLine 2 mod", encoding="utf-8")
    
    report = diff_files(str(f1), str(f2), output_path=str(out))
    
    assert "Line 1" in report
    assert "Line 2" in report
    assert os.path.exists(str(out))
    with open(out, "r", encoding="utf-8") as f:
        assert f.read().strip() == report.strip()

def test_character_width():
    assert get_char_width("A") == 1
    assert get_char_width("1") == 1
    assert get_char_width(" ") == 1
    assert get_char_width("中") == 2
    assert get_char_width("，") == 2  # Full-width punctuation
    assert get_char_width("\t") == 4
    
    assert get_visual_width("A中B") == 4

def test_padding_and_truncation():
    # Padding
    assert pad_to_width("A", 4) == "A   "
    assert pad_to_width("中文", 5) == "中文 "
    
    # Truncation
    assert truncate_to_width("HelloWorld", 5) == "He..."
    assert truncate_to_width("中文測試", 5) == "中..." # 中(2) + ...(3) = 5
    assert truncate_to_width("AB", 1) == "."
    
    # Centering
    assert center_text("A", 5) == "  A  "
    assert center_text("中文", 6) == " 中文 "

def test_visual_wrap():
    # Max width 5
    lines = visual_wrap("Hello, World!", 5)
    assert lines == ["Hello", ", Wor", "ld!"]
    
    # Max width 4 with Chinese (width 2 each)
    # "中文測試" -> ["中文", "測試"]
    lines = visual_wrap("中文測試", 4)
    assert lines == ["中文", "測試"]

def test_dmp_annotations():
    # Left:    "abc"
    # Right:   "adc"
    # Diff:    a (eq), b (del), d (ins), c (eq)
    ann_l, ann_r = get_dmp_annotations("abc", "adc")
    assert ann_l == " ~ "
    assert ann_r == " ^ "
    
    # Left:    "測試字元"
    # Right:   "測試新字"
    # Diff:    測試 (eq), 字 (del), 新 (ins), 元 (del), 字 (ins)
    # "字元" -> "新字"
    # DMP might match "字" if it thinks it's a transpose, but it checks text seq.
    # Actually, DMP behavior on "測試字元" vs "測試新字":
    # 測試 (eq), 字元(del), 新字(ins)
    ann_l, ann_r = get_dmp_annotations("abc", "xyz")
    assert ann_l == "~~~"
    assert ann_r == "^^^"

def test_format_line():
    line = format_line("1", "Left", "|", "2", "Right", 4, 10)
    # [num 4][2 space][text 10][3 space][spine 1][1 space][num 4][2 space][text...]
    # "   1  " (6) + "Left      " (10) + "   " (3) + "| " (2) + "   2  " (6) + "Right"
    assert "   1  Left         |    2  Right" in line

def test_diff_lines_identical_folding():
    lines1 = [f"Line {i}" for i in range(10)]
    lines2 = [f"Line {i}" for i in range(10)]
    
    # Fold threshold is 6, so 10 identical lines should fold
    report = diff_lines(lines1, lines2, fold_threshold=6, context_lines=2)
    assert "identical lines folded" in report
    assert "Line 0" in report # Top context
    assert "Line 1" in report
    assert "Line 8" in report # Bottom context
    assert "Line 9" in report
    assert "Line 4" not in report # Folded away

def test_diff_lines_1to1_replace():
    lines1 = ["def compute(a, b):"]
    lines2 = ["def compute(x, y):"]
    report = diff_lines(lines1, lines2)
    
    # Should contain * as spine, and ~ for a, b and ^ for x, y
    assert "*" in report
    assert "~" in report
    assert "^" in report

def test_diff_lines_uneven_replace():
    lines1 = ["A", "B"]
    lines2 = ["A", "X", "Y", "Z"]
    
    report = diff_lines(lines1, lines2)
    
    # 1 equal, then 1 delete ("B") vs 3 inserts ("X", "Y", "Z")
    # Because length 1 != length 3, it should fall back to block mode (- then +)
    assert "-" in report
    assert "+" in report
    # Should not use character level annotation
    assert "~" not in report
    assert "^" not in report

def test_diff_lines_type_error_on_string():
    with pytest.raises(TypeError) as excinfo:
        diff_lines("hello", "world")
    
    assert "diff_lines expects lists of strings" in str(excinfo.value)

def test_diff_lines_show_hints_off():
    lines1 = ["def compute(a):"]
    lines2 = ["def compute(x):"]
    # With show_hints=False, there should be no ~ or ^ in the output
    report = diff_lines(lines1, lines2, show_hints=False)
    assert "~" not in report
    assert "^" not in report

def test_diff_lines_unified_style():
    lines1 = ["Line 1", "Line 2 (old)", "Line 3"]
    lines2 = ["Line 1", "Line 2 (new)", "Line 3"]
    report = diff_lines(lines1, lines2, diff_style="unified")
    
    # Unified style should contain - for old and + for new in separate lines
    assert "-" in report
    assert "+" in report
    # Should contain the line indicators
    assert "Line 2 (old)" in report
    assert "Line 2 (new)" in report
    # Characters hints should still be there if show_hints is True (default)
    assert "^" in report
    assert "~" in report

def test_diff_lines_unified_no_hints():
    lines1 = ["Line 1", "Line 2 (old)"]
    lines2 = ["Line 1", "Line 2 (new)"]
    report = diff_lines(lines1, lines2, diff_style="unified", show_hints=False)
    
    assert "-" in report
    assert "+" in report
    # Should not have character hints
    assert "^" not in report
    assert "~" not in report
