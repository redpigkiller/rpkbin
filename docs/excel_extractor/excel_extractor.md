# Excel Extractor — Template-Based Excel Data Extraction

[![English](https://img.shields.io/badge/Language-English-blue.svg)](excel_extractor.md)
[![繁體中文](https://img.shields.io/badge/語言-繁體中文-blue.svg)](excel_extractor_zh.md)

Reading structured data from non-standard Excel files is tedious. Every time the layout shifts a row or offsets a column, your fixed-coordinate `sheet.cell(row, col)` code breaks completely.

**Excel Extractor** takes a fundamentally different approach: you describe the *shape* of the data you expect (a template "Block" with column definitions), and the engine scans the sheet to automatically find where it matches and what its contents are.

---

## Quick Start (User Guide)

### Step 1: Describe Your Template

Install with `pip install -e .[excel]` to include Excel parsing and optional fuzzy matching dependencies.

Suppose you want to extract a salary breakdown table consistently from a series of reports, no matter where it currently resides (top-left, center, bottom-right) on the sheets:

| Dept | Name  | Salary |
|------|-------|--------|
| IT   | Alice | 50000  |
| IT   | Bob   | 60000  |
| HR   | Carol | 55000  |

You can define a blueprint of this expected configuration using the template API:

```python
from rpkbin.excel_extractor import match_template, Block, Row, Types

# Define the table's footprint
template = Block(
    # First row: Look for an exact match of these three string headers
    Row(pattern=["Dept", "Name", "Salary"], node_id="header"),      
    
    # Subsequent rows: Must be String, String, Integer. 
    # repeat="+" implies "at least one data row, keeping consuming as many as valid"
    Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
    
    # Tag this template block to easily track results
    block_id="salary_table",
)
```

### Step 2: Run & Parse Results

Feed the target file and your template block to `match_template` to perform spatial evaluation:

```python
# match_template returns a MatchOutput object containing a flat list of all matches
results = match_template("report.xlsx", template)

for block_match in results.blocks:
    print(f"Table found on sheet '{block_match.sheet_name}' at: {block_match.start} → {block_match.end}")
    
    # Print out all the row data
    for row in block_match.rows:
        row_idx = row.row
        values = [cell.value for cell in row.cells]

        if row.node_id == "header":
            print(f"- [Header at Row {row_idx}] {values}")
        elif row.node_id == "data":
            print(f"  [Data at Row {row_idx}]   {values}")
```

In just a few lines of readable code, you not only overcome spatial layout shuffling natively, but also naturally weed out dirty formatting thanks to the rigid constraint typing inside your blueprints!

---

## API Reference (Detailed Control)

This portion outlines the breadth of robust APIs supplied by Excel Extractor, giving you fine-tuned capability to wrangle even the messiest layouts.

### 1. Cell Type Constants (`Types`)

When populating your `Row` pattern arrays, you can scatter raw matching strings alongside these `Types` properties mapping specific validations:

| Constant | Resolves Against |
|------|---------|
| `Types.STR` (also as `Types.NONEMPTY`) | Any non-empty string. Fits practically anything containing a visible length. |
| `Types.INT` | Standard integers (`42`, `-7`) |
| `Types.POS_INT` / `Types.NEG_INT` | Positives / Negatives strictly |
| `Types.FLOAT` | Floats (`3.14`) |
| `Types.NUM` | Any numerical resolution (`INT` or `FLOAT`) |
| `Types.BOOL` | Boolean-alikes (`true`, `false`, `yes`, `1`, `0`) |
| `Types.DATE` / `Types.TIME` | Valid Dates, ISO formatted Timestamps, localized dates. |
| `Types.MERGED` | The physical cell data arose by proxy from a "Merged Cell" structure. |
| `Types.SPACE` / `Types.BLANK` | Cells containing exclusively whitespace vectors. |
| `Types.EMPTY` | An exact empty cell footprint post normalisation (`""`). |
| `Types.ANY` | Absolute wildcard. Eats anything including empties. |

**Advanced Usages:**
- **Custom Regular Expressions**: Cast bespoke parameters manually using `Types.r(r"(?i)^[A-Z]\d{4}$")`. 
  > [!WARNING]
  > `Row` has `normalize=True` enabled by default, which lowercases all cell values before matching. If your regex requires matching uppercase letters, it will fail. You must either use the regex ignore-case flag `(?i)` or set `normalize=False` in your `Row` component.
- **OR Union Combos**: Combine types via bitwise operator `|`. To check if a cell contains a string OR gets left empty, evaluate `Types.STR | Types.BLANK`.
- **Sequence Unrolling Trick**: Supply an integer explicitly to `__call__` the structural requirement linearly. `Row(pattern=[Types.ANY(3), Types.INT])` interprets as 3 any-boxes appended sequentially to an integer condition.

---

### 2. Template Build Elements

Nodes coalesce into the primary parent `Block`. Use the below schema layouts safely nested:

#### `Row` (Data Rows)
Fundamental horizontal definition match component.
```python
Row(
    pattern=["A", Types.INT], # The ordered horizontal list of Types or literals
    repeat=1,                 # Times it can chain. (ex: "+", "*", tuple params)
    node_id=None,             # An identifiable label piped backwards after extraction. 
    normalize=True,           # Strips tails/lowercases inputs to assist strict matching
    min_similarity=None,      # Fuzzy matching for literal strings; Types/regex stay exact
    match_ratio=None          # Threshold float (0~1.0) of cells permitted to fail evaluating
)
```

#### `EmptyRow` (Spacing Definition)
Provides swift structural gaps mapping equivalents, basically syntactically rolling an entire row of `Types.BLANK`.
```python
EmptyRow(repeat=1, node_id=None, allow_whitespace=True)
```

#### `Group` (Cluster Collections)
Groupings define cluster loops to enforce layout rules that are repetitive. Perfect for nested data with recurring spacer formats.
```python
# Ex: Datasets spanning two rows, recurring repeatedly down the sheet
Group(children=[
    Row(pattern=[Types.MERGED, Types.STR], repeat="+"),
    EmptyRow(repeat=1),
], repeat="+")
```

#### `AltNode`（Alternatives / OR Behavior）
Instantiated purely by applying the `|` operator against variations of `Row`s. Exceedingly powerful if report formats swap minor titles organically.
```python
Row(pattern=["A", "B"]) | Row(pattern=["A*", "B*"])
```

#### Parameterizing `repeat`
| Input Syntax | Application Directive |
|----|------|
| `1` (default) | Distinctly exactly 1 match needed. |
| `"?"` | 0 or 1 instances (optional match tier). |
| `"+"` | 1 or greater iterations (will greedy-eat down). |
| `"*"` | 0 or greater iterations (will greedy-eat down). |
| `(2, 5)` | Mandated min of 2 bounding max range against 5 matches. |
| `(3, None)` | Min 3 required, open max. |

---

### 3. Emitted Output Map Mechanics

As shown earlier, referencing `match_template` kicks out a tree branching through properties of specific matches:

#### `MatchOutput` (The Container)
```python
results.blocks                                # list[BlockMatch] — Flat list of all matches found
results.get_blocks_by_id("salary_table")      # Helper to filter blocks by block_id
results.get_blocks_by_sheet("Sheet1")         # Helper to filter blocks by sheet_name
```

#### `BlockMatch` (The Holistic Payload)
```python
block.start       # (row, col) — 0-based Top-Left matrix start tuple
block.end         # (row, col) — 0-based Bottom-Right matrix limit tuple
block.rows        # list[RowMatch] — List array housing the subrow logic chunks
block.block_id    # string — The 'block_id' supplied to the parent Block
block.sheet_name  # string — The name of the sheet on which the match was found
```

#### `RowMatch` (Iterated Row Item)
```python
row.row                 # 0-based index denoting vertical depth context inside the sheet
row.cells               # list[CellMatch] — Iteratable lists of cells
row.node_id             # string — Mapped label applied at initialization
```

#### `CellMatch` (Extracted Property Leaf)
```python
cell.row                # Absolute 0-based vertical location
cell.col                # Absolute 0-based horizontal position
cell.value              # String literal interpreted back resolving constraints
cell.is_merged          # Boolean informing whether span expansion inflated this cell
```

---

### 4. Extra Search Optimizations & Dual Parsing

#### Limiting Iteration Time `MatchOptions`
Sometimes scanning a workbook 50 times deep for specific data when the data will only show up 'once' represents a tremendous waste of CPU processing. 
```python
from rpkbin.excel_extractor import MatchOptions

results = match_template(
    "report.xlsx", 
    template,
    sheet=["Sheet1", "Sheet3"], # Force scans strictly inside these sheets
    options=MatchOptions(
        # Halt execution abruptly after finding matched templates on 1 sheet
        max_matched_sheets=1 
    )
)
```

#### Template Injection Overloading
When digging for isolated tables scattered simultaneously in identical docs, stack Blocks into a collection and map it jointly preventing duplicate filesystem I/O operations.
```python
header_block = Block(Row(pattern=["Report", "Date"]), block_id="header")
data_block   = Block(Row(pattern=["Dept", "Name", "Salary"]), block_id="data_table")

results = match_template("report.xlsx", [header_block, data_block])

# Returned hierarchy indexes dynamically trace back matching indexes.
# header_hits = results.get_blocks_by_id("header")
# data_hits = results.get_blocks_by_id("data_table")
```
