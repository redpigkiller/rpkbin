# Text-Diff — Plain Text Difference Reporter

[![English](https://img.shields.io/badge/Language-English-blue.svg)](text_diff.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](text_diff_zh.md)

`Text-Diff` is an advanced, terminal-friendly text comparison utility designed for generating highly readable side-by-side diff reports in plain text.

Powered by Google's `diff-match-patch` algorithm under the hood, it accurately tracks modifications down to the character level while flawlessly handling East Asian character widths, smart line wrapping, and automatic folding of unchanged code blocks.

---

## Quick Start (User Guide)

To use the tool, you generally interact with either the file-to-file utility `diff_files` or the direct memory list compiler `diff_lines`.

### 1. Comparing Two Files

The easiest way to generate a side-by-side report is to pass two file paths to `diff_files`. It will read the contents, compare them, and optionally write the fully formatted report to a destination file.

```python
from rpkbin.utils.text_diff import diff_files

# Compares old.txt and new.txt, saving the visual report to diff_out.txt
report_string = diff_files(
    file1_path="old.txt", 
    file2_path="new.txt", 
    output_path="diff_out.txt"
)

print(report_string)
```

### 2. Comparing Memory Lists

If your strings are already loaded into memory (e.g., streaming logs or dynamically generated scripts), use `diff_lines` directly:

```python
from rpkbin.utils.text_diff import diff_lines

left_lines = [
    "def calculate_tax(price, rate):",
    "    return price * rate"
]

right_lines = [
    "def calculate_tax(price, tax_rate):",
    "    return price * tax_rate"
]

# Produce the side-by-side string representation
report = diff_lines(left_lines, right_lines)
print(report)
```

### 3. Understanding the Visualization

`Text-Diff` renders changes using a strict two-column layout separated by a status spine.

- **`|` (Equal)**: The lines are identical. Consecutive identical lines beyond a threshold are automatically folded.
- **`-` (Delete)**: The line was present in the left document but removed in the right.
- **`+` (Insert)**: The line was added to the right document.
- **`*` (Replace)**: The line was modified. If the replacement is exactly 1-to-1 in line count, it performs a deep character-level diff:
  - `~` marks precise deleted characters in the left column.
  - `^` marks precise inserted characters in the right column.

```text
=================================================================================================
   1  # This is a sample file                    |    1  # This is a sample file
   2  def calculate_tax(price, rate):            *    2  def calculate_tax(price, tax_rate):
                               ~~~~                                               ^^^^^^^^
   3      return price * rate                    *    3      return price * tax_rate
                         ~~~~                                               ^^^^^^^^
=================================================================================================
```

---

## API Reference (Detailed Control)

### Core Functions

| API Function | Description |
| --- | --- |
| `diff_files(file1_path, file2_path, output_path=None, **kwargs)` | Reads two files, generates a layout-aligned diff report string, optionally writes to `output_path`, and returns the string. |
| `diff_lines(lines1, lines2, **kwargs)` | Calculates and constructs the entire side-by-side report given two list of strings. |

### Configuration Arguments (`**kwargs`)

You can customize the layout and behavior of the diff engine by passing these parameters to either function:

- **`col_width`** (`int`, default: `40`): The visual character width allocated for **each individual column** (left and right). The total report width will be wider (`num_width*2 + col_width*2 + 9`).
- **`num_width`** (`int`, default: `4`): The minimum character width allocated for line numbers. It dynamically expands if the file length requires more digits.
- **`fold_threshold`** (`int`, default: `6`): The minimum number of consecutive identical lines required to trigger smart folding.
- **`context_lines`** (`int`, default: `1`): The number of surrounding identical lines to keep visible above and below a folded block.
- **`wrap_mode`** (`bool`, default: `True`): If `True`, long lines exceeding `col_width` are smartly wrapped to the next line. If `False`, they are aggressively and irreversibly truncated with `...` (useful for keeping logs strictly 1:1, but content integrity is lost).
- **`show_hints`** (`bool`, default: `True`): If `True`, enables precise character-level insertion/deletion indicators (`^` and `~`) underneath modified lines.
- **`diff_style`** (`str`, default: `"side_by_side"`): The layout format of the diff report. Can be either `"side_by_side"` for the dual-pane column view, or `"unified"` for a standard single-pane layout.

### Architectural Specifics

#### 1. Visual Width Calculation
Because standard `len()` fails to properly format East Asian characters (which occupy 2 fixed-width spaces in a terminal), `Text-Diff` routes all layout math through `unicodedata.east_asian_width`. A Chinese character calculates as width `2`, while an English letter calculates as width `1`.

#### 2. Robust Block Fallbacks
Diffing engines generally struggle to align lines when an unequal number of lines are replaced (e.g. replacing 3 lines with 6 lines). To prevent visually confusing misalignments across the center spine, `Text-Diff` safely falls back to Git-style chunking when `len_left != len_right`. The old chunk is sequentially rendered as total `Delete` (`-`), followed sequentially by the new chunk rendered as total `Insert` (`+`).
