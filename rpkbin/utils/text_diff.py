"""
Text-Diff — A plain text difference reporter.

Functions:
    diff_files(file1_path, file2_path, ...)    — Read files and generate report.
    diff_lines(lines1, lines2, ...)            — Generate report from memory lines.
"""

from __future__ import annotations

import difflib
import unicodedata
from typing import Literal

import diff_match_patch as dmp_module

# ---------------------------------------------------------------------------
# System Constants
# ---------------------------------------------------------------------------

COL_WIDTH = 40
NUM_WIDTH = 4
FOLD_THRESHOLD = 6
CONTEXT_LINES = 1
WRAP_MODE = True
SHOW_HINTS = True
DIFF_STYLE = "side_by_side"


# ---------------------------------------------------------------------------
# Visual width and formatting utilities
# ---------------------------------------------------------------------------


def get_char_width(char: str) -> int:
    """Get the visual display width of a character."""
    if char == "\t":
        return 4  # Expand tab to 4 spaces
    width_type = unicodedata.east_asian_width(char)
    return 2 if width_type in ("W", "F") else 1


def get_visual_width(text: str) -> int:
    """Get the total visual width of a string."""
    return sum(get_char_width(c) for c in text)


def pad_to_width(text: str, target_width: int) -> str:
    """Pad a string to exact visual width using spaces; truncate if needed."""
    res = ""
    current_width = 0
    for c in text:
        cw = get_char_width(c)
        if current_width + cw > target_width:
            break
        res += c
        current_width += cw
    return res + " " * (target_width - current_width)


def center_text(text: str, width: int, fillchar: str = " ") -> str:
    """Center text within a visual width."""
    vis_w = get_visual_width(text)
    if vis_w >= width:
        return truncate_to_width(text, width)
    left_pad = (width - vis_w) // 2
    right_pad = width - vis_w - left_pad
    return fillchar * left_pad + text + fillchar * right_pad


def truncate_to_width(text: str, target_width: int) -> str:
    """Truncate to target_width. If exceeded, append '...'."""
    if get_visual_width(text) <= target_width:
        return pad_to_width(text, target_width)

    if target_width <= 3:
        return "." * target_width

    res = ""
    current_width = 0
    for c in text:
        cw = get_char_width(c)
        if current_width + cw > target_width - 3:
            break
        res += c
        current_width += cw

    res += "..."
    return pad_to_width(res, target_width)


def visual_wrap(text: str, max_width: int) -> list[str]:
    """Split string into multiple lines based on visual width (word wrap)."""
    if not text:
        return [""]

    lines = []
    current_line = ""
    current_width = 0

    for char in text:
        cw = get_char_width(char)
        if current_width + cw > max_width:
            lines.append(current_line)
            current_line = char
            current_width = cw
        else:
            current_line += char
            current_width += cw

    if current_line or not lines:
        lines.append(current_line)

    return lines


def format_line(
    num_l: str,
    text_l: str,
    spine: str,
    num_r: str,
    text_r: str,
    num_width: int,
    col_width: int,
) -> str:
    """Format a single diff row.

    Layout formula: [Left Line Num] [Left Content]  [$] [Right Line Num] [Right Content]
    """
    num_l_str = num_l.rjust(num_width) + "  "
    text_l_str = pad_to_width(text_l, col_width) + "   "
    spine_str = spine + " "
    num_r_str = num_r.rjust(num_width) + "  "
    text_r_str = text_r

    line = f"{num_l_str}{text_l_str}{spine_str}{num_r_str}{text_r_str}"
    return line.rstrip()


# ---------------------------------------------------------------------------
# Diff operation engines
# ---------------------------------------------------------------------------


def get_dmp_annotations(s1: str, s2: str) -> tuple[str, str]:
    """Accurate character-level annotation mapping using diff-match-patch."""
    dmp = dmp_module.diff_match_patch()
    diffs = dmp.diff_main(s1, s2, False)
    dmp.diff_cleanupSemantic(diffs)

    ann_l = ""
    ann_r = ""
    for op, text in diffs:
        cwd = get_visual_width(text)
        if op == -1:
            ann_l += "~" * cwd
        elif op == 1:
            ann_r += "^" * cwd
        elif op == 0:
            ann_l += " " * cwd
            ann_r += " " * cwd

    return ann_l, ann_r


def _wrap_with_annotations(
    text: str, full_ann: str, col_width: int
) -> tuple[list[str], list[str]]:
    """Helper to wrap text alongside its character-level annotations."""
    if not text:
        return [""], [""]
    parts, anns = [], []
    current, current_ann, current_w = "", "", 0
    ann_idx = 0
    for c in text:
        cw = get_char_width(c)
        if current_w + cw > col_width:
            parts.append(current)
            anns.append(current_ann)
            current = c
            current_ann = full_ann[ann_idx : ann_idx + cw]
            current_w = cw
        else:
            current += c
            current_ann += full_ann[ann_idx : ann_idx + cw]
            current_w += cw
        ann_idx += cw

    if current:
        parts.append(current)
        anns.append(current_ann)
    return parts, anns


# ---------------------------------------------------------------------------
# Format Builders
# ---------------------------------------------------------------------------


def _build_side_by_side(
    opcodes: list[
        tuple[Literal["replace", "delete", "insert", "equal"], int, int, int, int]
    ],
    lines1: list[str],
    lines2: list[str],
    col_width: int,
    num_width: int,
    fold_threshold: int,
    context_lines: int,
    wrap_mode: bool,
    show_hints: bool,
    total_width: int,
) -> list[str]:
    result_lines = []

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            equal_chunk = []
            for k in range(i2 - i1):
                idx1 = i1 + k
                idx2 = j1 + k
                text = lines1[idx1].replace("\t", "    ")
                t_trunc = truncate_to_width(text, col_width)
                line = format_line(
                    str(idx1 + 1),
                    t_trunc,
                    "|",
                    str(idx2 + 1),
                    t_trunc,
                    num_width,
                    col_width,
                )
                equal_chunk.append(line)

            if len(equal_chunk) >= fold_threshold:
                top_part = equal_chunk[:context_lines]
                bottom_part = equal_chunk[-context_lines:] if context_lines > 0 else []

                hidden_count = len(equal_chunk) - (context_lines * 2)
                fold_msg = (
                    f".......... ( {hidden_count} identical lines folded ) .........."
                )

                fold_msg_padded = center_text(fold_msg, total_width)

                result_lines.extend(top_part)
                result_lines.append(fold_msg_padded)
                result_lines.extend(bottom_part)
            else:
                result_lines.extend(equal_chunk)

        elif tag == "delete":
            for k in range(i1, i2):
                text_l = lines1[k].replace("\t", "    ")
                wrapped_l = (
                    visual_wrap(text_l, col_width)
                    if wrap_mode
                    else [truncate_to_width(text_l, col_width)]
                )

                for line_idx, part_l in enumerate(wrapped_l):
                    is_first = line_idx == 0
                    n_l = str(k + 1) if is_first else ""
                    spine = "-" if is_first else " "

                    line = format_line(n_l, part_l, spine, "", "", num_width, col_width)
                    result_lines.append(line)

        elif tag == "insert":
            for k in range(j1, j2):
                text_r = lines2[k].replace("\t", "    ")
                wrapped_r = (
                    visual_wrap(text_r, col_width)
                    if wrap_mode
                    else [truncate_to_width(text_r, col_width)]
                )

                for line_idx, part_r in enumerate(wrapped_r):
                    is_first = line_idx == 0
                    n_r = str(k + 1) if is_first else ""
                    spine = "+" if is_first else " "

                    line = format_line("", "", spine, n_r, part_r, num_width, col_width)
                    result_lines.append(line)

        elif tag == "replace":
            len_l = i2 - i1
            len_r = j2 - j1

            if len_l != len_r:
                # Git-style multiline fallback
                for k in range(i1, i2):
                    text_l = lines1[k].replace("\t", "    ")
                    wrapped_l = (
                        visual_wrap(text_l, col_width)
                        if wrap_mode
                        else [truncate_to_width(text_l, col_width)]
                    )
                    for line_idx, part_l in enumerate(wrapped_l):
                        is_first = line_idx == 0
                        n_l = str(k + 1) if is_first else ""
                        spine = "-" if is_first else " "
                        result_lines.append(
                            format_line(
                                n_l, part_l, spine, "", "", num_width, col_width
                            )
                        )

                for k in range(j1, j2):
                    text_r = lines2[k].replace("\t", "    ")
                    wrapped_r = (
                        visual_wrap(text_r, col_width)
                        if wrap_mode
                        else [truncate_to_width(text_r, col_width)]
                    )
                    for line_idx, part_r in enumerate(wrapped_r):
                        is_first = line_idx == 0
                        n_r = str(k + 1) if is_first else ""
                        spine = "+" if is_first else " "
                        result_lines.append(
                            format_line(
                                "", "", spine, n_r, part_r, num_width, col_width
                            )
                        )
            else:
                # 1:1 intra-line DMP character diff
                for k in range(len_l):
                    idx1 = i1 + k
                    idx2 = j1 + k

                    text_l = lines1[idx1].replace("\t", "    ")
                    text_r = lines2[idx2].replace("\t", "    ")

                    full_ann_l, full_ann_r = get_dmp_annotations(text_l, text_r)

                    parts_l = []
                    parts_r = []
                    anns_l = []
                    anns_r = []

                    # Left pane processing
                    if wrap_mode and text_l:
                        parts_l, anns_l = _wrap_with_annotations(
                            text_l, full_ann_l, col_width
                        )
                    elif text_l:
                        parts_l = [truncate_to_width(text_l, col_width)]
                        anns_l = [full_ann_l[:col_width]]
                    else:
                        parts_l = [""]
                        anns_l = [""]

                    # Right pane processing
                    if wrap_mode and text_r:
                        parts_r, anns_r = _wrap_with_annotations(
                            text_r, full_ann_r, col_width
                        )
                    elif text_r:
                        parts_r = [truncate_to_width(text_r, col_width)]
                        anns_r = [full_ann_r[:col_width]]
                    else:
                        parts_r = [""]
                        anns_r = [""]

                    # Line stitching
                    max_wrap_lines = max(len(parts_l), len(parts_r))
                    for line_idx in range(max_wrap_lines):
                        p_l = parts_l[line_idx] if line_idx < len(parts_l) else ""
                        p_r = parts_r[line_idx] if line_idx < len(parts_r) else ""
                        a_l = anns_l[line_idx] if line_idx < len(anns_l) else ""
                        a_r = anns_r[line_idx] if line_idx < len(anns_r) else ""

                        is_first = line_idx == 0
                        n_l = str(idx1 + 1) if is_first else ""
                        n_r = str(idx2 + 1) if is_first else ""
                        spine = "*" if is_first else " "

                        line = format_line(
                            n_l, p_l, spine, n_r, p_r, num_width, col_width
                        )
                        result_lines.append(line)

                        if show_hints and ("~" in a_l or "^" in a_r):
                            ann_line = format_line(
                                "",
                                a_l.rstrip(),
                                " ",
                                "",
                                a_r.rstrip(),
                                num_width,
                                col_width,
                            )
                            result_lines.append(ann_line)

    return result_lines


def _build_unified(
    opcodes: list[
        tuple[Literal["replace", "delete", "insert", "equal"], int, int, int, int]
    ],
    lines1: list[str],
    lines2: list[str],
    _col_width: int,
    num_width: int,
    fold_threshold: int,
    context_lines: int,
    wrap_mode: bool,
    show_hints: bool,
    total_width: int,
) -> list[str]:
    result_lines = []

    # In unified view, the prefix is "[N1] [N2] | "
    # Width of prefix = num_width + 1 (space) + num_width + 1 (space) + 1 (sign) + 1 (space)
    prefix_width = num_width * 2 + 4
    content_width = total_width - prefix_width
    if content_width < 20:
        content_width = 20  # Fallback for very small total_widths

    def format_u_line(n1: str, n2: str, sign: str, text: str) -> str:
        n1_s = n1.rjust(num_width)
        n2_s = n2.rjust(num_width)
        return f"{n1_s} {n2_s} {sign} {text}".rstrip()

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            equal_chunk = []
            for k in range(i2 - i1):
                idx1 = i1 + k
                idx2 = j1 + k
                text = lines1[idx1].replace("\t", "    ")
                t_trunc = truncate_to_width(text, content_width)
                equal_chunk.append(
                    format_u_line(str(idx1 + 1), str(idx2 + 1), " ", t_trunc)
                )

            if len(equal_chunk) >= fold_threshold:
                top_part = equal_chunk[:context_lines]
                bottom_part = equal_chunk[-context_lines:] if context_lines > 0 else []

                hidden_count = len(equal_chunk) - (context_lines * 2)
                fold_msg = (
                    f".......... ( {hidden_count} identical lines folded ) .........."
                )

                fold_msg_padded = center_text(fold_msg, total_width)

                result_lines.extend(top_part)
                result_lines.append(fold_msg_padded)
                result_lines.extend(bottom_part)
            else:
                result_lines.extend(equal_chunk)

        elif tag == "delete":
            for k in range(i1, i2):
                text_l = lines1[k].replace("\t", "    ")
                wrapped_l = (
                    visual_wrap(text_l, content_width)
                    if wrap_mode
                    else [truncate_to_width(text_l, content_width)]
                )

                for line_idx, part_l in enumerate(wrapped_l):
                    is_first = line_idx == 0
                    n_l = str(k + 1) if is_first else ""
                    sign = "-" if is_first else " "

                    result_lines.append(format_u_line(n_l, "", sign, part_l))

        elif tag == "insert":
            for k in range(j1, j2):
                text_r = lines2[k].replace("\t", "    ")
                wrapped_r = (
                    visual_wrap(text_r, content_width)
                    if wrap_mode
                    else [truncate_to_width(text_r, content_width)]
                )

                for line_idx, part_r in enumerate(wrapped_r):
                    is_first = line_idx == 0
                    n_r = str(k + 1) if is_first else ""
                    sign = "+" if is_first else " "

                    result_lines.append(format_u_line("", n_r, sign, part_r))

        elif tag == "replace":
            len_l = i2 - i1
            len_r = j2 - j1

            if len_l != len_r:
                # Git-style multiline fallback
                for k in range(i1, i2):
                    text_l = lines1[k].replace("\t", "    ")
                    wrapped_l = (
                        visual_wrap(text_l, content_width)
                        if wrap_mode
                        else [truncate_to_width(text_l, content_width)]
                    )
                    for line_idx, part_l in enumerate(wrapped_l):
                        is_first = line_idx == 0
                        n_l = str(k + 1) if is_first else ""
                        sign = "-" if is_first else " "
                        result_lines.append(format_u_line(n_l, "", sign, part_l))

                for k in range(j1, j2):
                    text_r = lines2[k].replace("\t", "    ")
                    wrapped_r = (
                        visual_wrap(text_r, content_width)
                        if wrap_mode
                        else [truncate_to_width(text_r, content_width)]
                    )
                    for line_idx, part_r in enumerate(wrapped_r):
                        is_first = line_idx == 0
                        n_r = str(k + 1) if is_first else ""
                        sign = "+" if is_first else " "
                        result_lines.append(format_u_line("", n_r, sign, part_r))
            else:
                # 1:1 intra-line DMP character diff
                for k in range(len_l):
                    idx1 = i1 + k
                    idx2 = j1 + k

                    text_l = lines1[idx1].replace("\t", "    ")
                    text_r = lines2[idx2].replace("\t", "    ")

                    full_ann_l, full_ann_r = get_dmp_annotations(text_l, text_r)

                    parts_l = []
                    parts_r = []
                    anns_l = []
                    anns_r = []

                    # Left pane processing
                    if wrap_mode and text_l:
                        parts_l, anns_l = _wrap_with_annotations(
                            text_l, full_ann_l, content_width
                        )
                    elif text_l:
                        parts_l = [truncate_to_width(text_l, content_width)]
                        anns_l = [full_ann_l[:content_width]]
                    else:
                        parts_l = [""]
                        anns_l = [""]

                    # Right pane processing
                    if wrap_mode and text_r:
                        parts_r, anns_r = _wrap_with_annotations(
                            text_r, full_ann_r, content_width
                        )
                    elif text_r:
                        parts_r = [truncate_to_width(text_r, content_width)]
                        anns_r = [full_ann_r[:content_width]]
                    else:
                        parts_r = [""]
                        anns_r = [""]

                    # Line stitching (unified prints `-` lines then `+` lines)
                    for line_idx, _ in enumerate(parts_l):
                        p_l = parts_l[line_idx]
                        a_l = anns_l[line_idx] if line_idx < len(anns_l) else ""

                        is_first = line_idx == 0
                        n_l = str(idx1 + 1) if is_first else ""
                        sign = "-" if is_first else " "

                        result_lines.append(format_u_line(n_l, "", sign, p_l))

                        if show_hints and "~" in a_l:
                            result_lines.append(
                                format_u_line("", "", " ", a_l.rstrip())
                            )

                    for line_idx, _ in enumerate(parts_r):
                        p_r = parts_r[line_idx]
                        a_r = anns_r[line_idx] if line_idx < len(anns_r) else ""

                        is_first = line_idx == 0
                        n_r = str(idx2 + 1) if is_first else ""
                        sign = "+" if is_first else " "

                        result_lines.append(format_u_line("", n_r, sign, p_r))

                        if show_hints and "^" in a_r:
                            result_lines.append(
                                format_u_line("", "", " ", a_r.rstrip())
                            )

    return result_lines


# ---------------------------------------------------------------------------
# User-facing API
# ---------------------------------------------------------------------------


def diff_lines(
    lines1: list[str],
    lines2: list[str],
    col_width: int = COL_WIDTH,
    num_width: int = NUM_WIDTH,
    fold_threshold: int = FOLD_THRESHOLD,
    context_lines: int = CONTEXT_LINES,
    wrap_mode: bool = WRAP_MODE,
    show_hints: bool = SHOW_HINTS,
    diff_style: Literal["side_by_side", "unified"] = DIFF_STYLE,
) -> str:
    """Compute and format the diff layout in memory."""
    if isinstance(lines1, str) or isinstance(lines2, str):
        raise TypeError(
            "diff_lines expects lists of strings, not a raw string. "
            "Use str.splitlines() first."
        )

    # Defensively clean inputs from stray newlines and clamp context bounds
    lines1 = [line.rstrip("\n\r") for line in lines1]
    lines2 = [line.rstrip("\n\r") for line in lines2]
    context_lines = max(0, context_lines)

    # Scale num_width dynamically based on max lines
    max_lines = max(len(lines1), len(lines2))
    num_width = max(num_width, len(str(max_lines)))

    total_width = num_width * 2 + col_width * 2 + 9
    border_line = "=" * total_width

    matcher = difflib.SequenceMatcher(None, lines1, lines2)
    opcodes = matcher.get_opcodes()

    if diff_style == "unified":
        result_lines = _build_unified(
            opcodes,
            lines1,
            lines2,
            col_width,
            num_width,
            fold_threshold,
            context_lines,
            wrap_mode,
            show_hints,
            total_width,
        )
    else:
        result_lines = _build_side_by_side(
            opcodes,
            lines1,
            lines2,
            col_width,
            num_width,
            fold_threshold,
            context_lines,
            wrap_mode,
            show_hints,
            total_width,
        )

    report = [border_line] + result_lines + [border_line]
    return "\n".join(report)


def diff_files(
    file1_path: str, file2_path: str, output_path: str | None = None, **kwargs
) -> str:
    """Compare two files and return/write the Diffs.

    Args:
        file1_path: Path to the old/left file.
        file2_path: Path to the new/right file.
        output_path: Optional path to write output layout text.
        **kwargs: Additional config passed to generate_diff_report.

    Returns:
        The formatted string report.
    """
    with open(file1_path, "r", encoding="utf-8") as f:
        lines1 = [line.rstrip("\n\r") for line in f]
    with open(file2_path, "r", encoding="utf-8") as f:
        lines2 = [line.rstrip("\n\r") for line in f]

    report = diff_lines(lines1, lines2, **kwargs)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report + "\n")

    return report
