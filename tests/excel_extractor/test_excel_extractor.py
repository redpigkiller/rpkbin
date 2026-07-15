"""Tests for rpkbin.excel_extractor.

Tests use in-memory InternalGrid for fast logic testing,
and an integration test to verify real Excel file loading.
"""

import re
import tempfile
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest
import xlrd
from unittest.mock import MagicMock

from rpkbin.excel_extractor import (
    match_template,
    Block,
    EmptyRow,
    Group,
    Row,
    Types,
    CellCondition,
    MatchOptions,
)
from rpkbin.excel_extractor.template import AltNode, _parse_repeat
from rpkbin.excel_extractor.normalizer import InternalCell, InternalGrid, normalize_value
from rpkbin.excel_extractor.matcher import TemplateMatcher


# ---------------------------------------------------------------------------
# Helpers for InternalGrid tests
# ---------------------------------------------------------------------------


def make_grid(rows: list[list]) -> InternalGrid:
    """Build an InternalGrid from a 2-D list of raw values."""
    internal_rows = []
    for row in rows:
        internal_row = []
        for cell in row:
            if isinstance(cell, tuple) and cell[0] == "M":
                internal_row.append(
                    InternalCell(
                        value=str(cell[1]) if cell[1] is not None else "",
                        original_value=cell[1],
                        is_merged=True,
                    )
                )
            else:
                val = "" if cell is None else str(cell)
                internal_row.append(
                    InternalCell(
                        value=val,
                        original_value=cell,
                        is_merged=False,
                    )
                )
        internal_rows.append(internal_row)
    return InternalGrid(internal_rows)


def match_blocks(grid: InternalGrid, block: Block, options=None):
    """Run TemplateMatcher and return flat list of BlockMatches for a single template."""
    matcher = TemplateMatcher([block], options or MatchOptions())
    results = matcher.scan_for_blocks(grid)
    return results[0] if results else []


# ===========================================================================
# 1. Types & CellCondition Logic
# ===========================================================================


class TestCellCondition:
    def test_from_pattern_nonempty(self):
        c = CellCondition.from_pattern(r".+")
        assert c.patterns == frozenset([r".+"])
        assert c.is_merged is None

    def test_from_pattern_empty(self):
        c = CellCondition.from_pattern("")
        assert c.patterns == frozenset()

    def test_or_combines_patterns(self):
        a = CellCondition.from_pattern(r"\d+")
        b = CellCondition.from_pattern(r"[a-z]+")
        combined = a | b
        assert combined.patterns == frozenset([r"\d+", r"[a-z]+"])

    def test_or_merged_conflict_becomes_none(self):
        a = CellCondition.from_pattern(r".*", is_merged=False)
        b = CellCondition.from_pattern(r".*", is_merged=True)
        combined = a | b
        assert combined.is_merged is None

    def test_call_repeats(self):
        c = Types.ANY
        assert len(c(3)) == 3
        assert all(x is c for x in c(3))

    def test_call_rejects_negative(self):
        with pytest.raises(ValueError):
            Types.ANY(-1)


class TestTypesConstants:
    """Verify regex patterns via fullmatch on normalised cell values."""

    def _matches(self, cond: CellCondition, value: str, is_merged=False):
        if not cond.patterns:
            compiled = re.compile("")
        else:
            compiled = re.compile("|".join(cond.patterns))

        if cond.is_merged is not None and cond.is_merged != is_merged:
            return False
        return bool(compiled.fullmatch(value or ""))

    def test_basic_types(self):
        assert self._matches(Types.STR, "hello")
        assert not self._matches(Types.STR, "")

        assert self._matches(Types.INT, "42")
        assert self._matches(Types.INT, "-7")
        assert not self._matches(Types.INT, "3.14")

        assert self._matches(Types.FLOAT, "3.14")
        assert self._matches(Types.NUM, "3.14")

        assert self._matches(Types.BOOL, "true")
        assert self._matches(Types.BOOL, "1")
        assert not self._matches(Types.BOOL, "maybe")

    def test_date_and_time(self):
        assert self._matches(Types.DATE_ISO, "2024-01-15")
        assert not self._matches(Types.DATE_ISO, "15/01/2024")

        assert self._matches(Types.DATE_TW, "111/01/01")
        assert self._matches(Types.DATETIME, "2024-01-15 09:30:00")
        assert self._matches(Types.TIME, "09:30")

    def test_structural_types(self):
        assert self._matches(Types.MERGED, "hello", is_merged=True)
        assert not self._matches(Types.MERGED, "hello", is_merged=False)

        assert self._matches(Types.SPACE, "   ")
        assert self._matches(Types.SPACE, "")
        assert not self._matches(Types.SPACE, "x")

        assert self._matches(Types.ANY, "anything")
        assert self._matches(Types.ANY, "anything", is_merged=True)

    def test_number_bases(self):
        assert self._matches(Types.HEX, "0xFF")
        assert not self._matches(Types.HEX, "FF")

    def test_custom_regex(self):
        cond = Types.r(r"[A-Z]{2}\d+")
        assert self._matches(cond, "AB123")
        assert not self._matches(cond, "ab123")


# ===========================================================================
# 2. Template Build Ast
# ===========================================================================


class TestRepeatParsing:
    def test_int(self):
        assert _parse_repeat(3) == (3, 3)

    def test_shortcuts(self):
        assert _parse_repeat("?") == (0, 1)
        assert _parse_repeat("+") == (1, None)
        assert _parse_repeat("*") == (0, None)

    def test_tuple(self):
        assert _parse_repeat((2, 4)) == (2, 4)
        assert _parse_repeat((2, None)) == (2, None)

    def test_invalid(self):
        with pytest.raises(ValueError):
            _parse_repeat("x")
        with pytest.raises(ValueError):
            _parse_repeat(-1)
        with pytest.raises(ValueError):
            _parse_repeat((4, 2))


class TestBlockValidation:
    def test_width_inference(self):
        b = Block(
            Row(pattern=["A", "B"]),
            Row(pattern=[Types.STR, Types.INT], repeat="+"),
        )
        assert b.width == 2

    def test_inconsistent_width_raises(self):
        with pytest.raises(ValueError):
            Block(Row(pattern=["A", "B"]), Row(pattern=[Types.STR]))

    def test_empty_row_expanded(self):
        b = Block(Row(pattern=["A", "B"]), EmptyRow())
        assert len(b.children[1].rules()) == 2

    def test_invalid_orientation_raises(self):
        with pytest.raises(ValueError):
            Block(Row(pattern=["A"]), orientation="diagonal")

    def test_empty_pattern_raises(self):
        with pytest.raises(ValueError):
            Block(Row(pattern=[]))

    def test_alt_node(self):
        alt = Row(pattern=["A"]) | Row(pattern=["B"])
        assert isinstance(alt, AltNode)
        assert len(alt.alternatives) == 2


# ===========================================================================
# 3. InternalGrid Operations
# ===========================================================================


class TestInternalGrid:
    def test_basic_access(self):
        grid = make_grid([["a", "b"], ["c", "d"]])
        assert grid.get_cell(0, 1).value == "b"
        assert grid.get_cell(1, 0).value == "c"

    def test_out_of_bounds(self):
        grid = make_grid([["a"]])
        with pytest.raises(IndexError):
            grid.get_cell(99, 0)

    def test_rectangular_enforced(self):
        with pytest.raises(ValueError):
            make_grid([["a", "b", "c"], ["d"]])

    def test_transpose(self):
        grid = make_grid([["A", "B", "C"], ["D", "E", "F"]])
        t = grid.transpose()
        assert t.num_rows == 3
        assert t.num_cols == 2
        assert t.get_cell(0, 1).value == "D"
        assert t.get_cell(2, 0).value == "C"

    def test_datetime_and_whitespace_normalization(self):
        assert normalize_value(datetime(2025, 1, 2, 3, 4, 5)) == "2025-01-02 03:04:05"
        assert normalize_value("   ") == "   "


# ===========================================================================
# 4. Mock Matrix Pattern Matching
# ===========================================================================


class TestPatternMatch:
    def test_vertical_simple_table(self):
        grid = make_grid(
            [
                ["Dept", "Name", "Salary"],
                ["IT", "Alice", 1000],
                ["HR", "Bob", 2000],
            ]
        )
        block = Block(
            Row(pattern=["Dept", "Name", "Salary"], node_id="header"),
            Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
        )
        matches = match_blocks(grid, block)
        assert len(matches) == 1

        bm = matches[0]
        assert bm.start == (0, 0)
        assert len(bm.rows) == 3
        assert bm.rows[0].node_id == "header"
        assert bm.rows[1].node_id == "data"

    def test_no_match_wrong_header(self):
        grid = make_grid([["X", "Y"]])
        block = Block(Row(pattern=["A", "B"]))
        matches = match_blocks(grid, block)
        assert len(matches) == 0

    def test_offset_coordinates(self):
        grid = make_grid(
            [
                [None, None, None],
                [None, None, None],
                [None, "Header", "Val"],
                [None, "IT", "100"],
            ]
        )
        block = Block(
            Row(pattern=["Header", "Val"]),
            Row(pattern=[Types.STR, Types.INT]),
        )
        matches = match_blocks(grid, block)
        assert len(matches) == 1
        bm = matches[0]

        # Absolute locations inside the grid
        assert bm.rows[0].row == 2
        assert bm.rows[0].cells[1].col == 2
        assert bm.rows[0].cells[0].value == "Header"
        assert bm.rows[1].row == 3

        grid = make_grid(
            [
                ["Header", "Val"],
                ["Row1", 100],
                ["!!!", "???"],  # Non-matching junk row
                ["Row3", 300],
            ]
        )
        block = Block(
            Row(pattern=["Header", "Val"]),
            Row(pattern=[Types.STR, Types.INT], repeat="+"),
        )
        matches = match_blocks(grid, block)

        # The matcher looks for contiguous matches. "!!!" breaks the chain, so we only match up to Row1
        assert len(matches) >= 1
        bm = matches[0]
        assert bm.rows[1].cells[0].value == "Row1"
        assert len(bm.rows) == 2

    def test_group_with_empty_row(self):
        grid = make_grid(
            [
                ["*", "Name", "Salary"],
                [("M", "IT"), "Alice", 1000],
                [("M", "IT"), "Bob", 2000],
                [None, None, None],
                [("M", "HR"), "Carol", 3000],
                [None, None, None],
            ]
        )
        block = Block(
            Row(pattern=[Types.ANY, "Name", "Salary"]),
            Group(
                children=[
                    Row(pattern=[Types.MERGED, Types.STR, Types.INT], repeat="+"),
                    EmptyRow(repeat="?"),
                ],
                repeat="+",
            ),
        )
        matches = match_blocks(grid, block)
        assert len(matches) == 1

    def test_alt_header(self):
        grid = make_grid([["Dept", "Salary"], ["IT", 1000]])
        block = Block(
            Row(pattern=["部門", "月薪"]) | Row(pattern=["Dept", "Salary"]),
            Row(pattern=[Types.STR, Types.INT], repeat="+"),
        )
        matches = match_blocks(grid, block)
        assert len(matches) == 1

    def test_horizontal_basic(self):
        grid = make_grid(
            [
                ["Label", "Jan", "Feb"],
                ["Target", 100, 200],
            ]
        )
        block = Block(
            Row(pattern=["Label", "Target"]),
            Row(pattern=[Types.STR, Types.INT], repeat="+"),
            orientation="horizontal",
        )
        matches = match_blocks(grid, block)
        assert len(matches) == 1

        bm = matches[0]
        for row in bm.rows:
            for cell in row.cells:
                actual = grid.get_cell(cell.row, cell.col).value
                assert cell.value == actual

        # Ensure that the swapped RowMatch.row has the starting row of its cells, not the column index.
        assert bm.rows[0].row == 0
        assert bm.rows[1].row == 0
        assert bm.rows[2].row == 0

    def test_is_merged_none_does_not_skip_pattern(self):
        # A cell that is merged, but its value doesn't match the required pattern
        grid = make_grid([
            [("M", "WrongValue")]  # value is "WrongValue", is_merged=True
        ])
        cond = CellCondition.from_pattern("TargetValue", is_merged=None)
        block = Block(Row(pattern=[cond], normalize=False))
        
        matches = match_blocks(grid, block)
        # Because of the fix, this should be 0. (Previously it wrongly returned 1)
        assert len(matches) == 0

    def test_generic_repeat_can_be_followed_by_specific_row(self):
        grid = make_grid([["Alice"], ["Total"]])
        block = Block(
            Row(pattern=[Types.STR], repeat="+", node_id="data"),
            Row(pattern=["Total"], node_id="total"),
        )

        matches = match_blocks(grid, block)

        assert len(matches) == 1
        assert [row.node_id for row in matches[0].rows] == ["data", "total"]

    def test_more_than_ten_rules_do_not_overcapture_rows(self):
        block = Block(*(Row(pattern=[f"r{i}"]) for i in range(11)))
        grid = make_grid([[f"r{i}"] for i in range(11)] + [["r0"]] * 20)

        matches = match_blocks(grid, block)

        assert len(matches) == 1
        assert len(matches[0].rows) == 11
        assert matches[0].end == (10, 0)

    def test_regex_normalization_is_applied(self):
        grid = make_grid([["ABC"]])
        normalized = Block(Row(pattern=[Types.r(r"[A-Z]+")]))
        with pytest.warns(UserWarning):
            assert match_blocks(grid, normalized) == []

        assert len(match_blocks(grid, Block(Row(pattern=[Types.r(r"[A-Z]+")], normalize=False)))) == 1

    def test_fuzzy_matching_only_applies_to_literals(self):
        matches = match_blocks(
            make_grid([[123, "Departmnt"]]),
            Block(Row(pattern=[Types.INT, "Department"], min_similarity=0.8)),
        )
        assert len(matches) == 1

    def test_empty_row_can_distinguish_whitespace(self):
        grid = make_grid([["Header"], ["   "]])

        assert match_blocks(grid, Block(Row(pattern=["Header"]), EmptyRow(allow_whitespace=False))) == []
        assert len(match_blocks(grid, Block(Row(pattern=["Header"]), EmptyRow()))) == 1

    def test_fuzzy_matching(self):
        # "Department" is misspelled as "Departmnt"
        grid = make_grid([["Departmnt", "Name"], ["IT", "Alice"]])
        # Requires 85% similarity
        block = Block(
            Row(pattern=["Department", "Name"], min_similarity=0.85),
            Row(pattern=[Types.STR, Types.STR], repeat="+"),
        )
        matches = match_blocks(grid, block)
        assert len(matches) == 1
        assert matches[0].rows[0].cells[0].value == "Departmnt"

    def test_match_ratio(self):
        # 1 out of 3 cells gets corrupted (doesn't match Types.INT)
        grid = make_grid([["A", "B", "C"], [100, "ERR", 300]])
        block = Block(
            Row(pattern=["A", "B", "C"]),
            # Require only 66% (2 out of 3) cells to match
            Row(pattern=[Types.INT, Types.INT, Types.INT], match_ratio=0.66),
        )
        matches = match_blocks(grid, block)
        assert len(matches) == 1
        assert matches[0].rows[1].cells[1].value == "ERR"


# ===========================================================================
# 5. MatchOptions
# ===========================================================================


class TestMatchOptions:
    def test_return_mode_zero(self):
        grid = make_grid([["A"], ["B"]])
        block = Block(Row(pattern=["A"]), Row(pattern=[Types.STR]))
        # Ensure it runs without issues
        matches = match_blocks(grid, block, MatchOptions(max_matched_sheets=0))
        assert len(matches) == 1

    def test_negative_max_matched_sheets_raises(self):
        with pytest.raises(ValueError):
            MatchOptions(max_matched_sheets=-1)


# ===========================================================================
# 6. Integration Test with Real Excel File
# ===========================================================================


class TestIntegrationRealExcel:
    @pytest.fixture
    def sample_excel_file(self):
        """Creates a temporary Excel file matching a generic payroll format."""
        wb = openpyxl.Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "Payroll"

        # Inject junk rows at the start
        ws.append(["Company XYZ Report"])
        ws.append([])

        ws.append(["Dept", "Name", "Salary"])
        ws.append(["IT", "Alice", 50000])
        ws.append(["IT", "Bob", 60000])
        ws.append(["HR", "Carol", 55000])
        ws.append([])
        ws.append(
            ["Finance", "Dave", "Confidential"]
        )  # Should stop data block matching here

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            temp_path = f.name
            wb.save(temp_path)

        yield temp_path

        Path(temp_path).unlink()

    def test_match_template_on_real_file(self, sample_excel_file):
        template = Block(
            Row(pattern=["Dept", "Name", "Salary"], node_id="header"),
            Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
            block_id="salary_table",
        )

        # Calling the public API directly
        results = match_template(
            sample_excel_file, template, sheet="Payroll", options=MatchOptions()
        )

        # Ensure we found exactly 1 match on the first sheet
        assert len(results.blocks) == 1
        bm = results.blocks[0]
        assert bm.block_id == "salary_table"
        # The header should be on row 3 (0-indexed: row 2)
        assert bm.rows[0].row == 2
        assert bm.rows[0].node_id == "header"

        # And it should capture exactly our 3 valid data rows (Alice, Bob, Carol)
        data_rows = [r for r in bm.rows if r.node_id == "data"]
        assert len(data_rows) == 3

        assert data_rows[0].cells[1].value == "Alice"
        assert int(data_rows[0].cells[2].value) == 50000
        assert data_rows[2].cells[1].value == "Carol"

    def test_star_scans_all_sheets(self, sample_excel_file):
        template = Block(Row(pattern=["Dept", "Name", "Salary"]))

        results = match_template(sample_excel_file, template, sheet="*")

        assert len(results.blocks) == 1
        assert results.blocks[0].sheet_name == "Payroll"

    def test_match_template_on_mocked_xls(self, monkeypatch):
        """Mock the xlrd loading mechanism to test .xls logic without binary blobs."""

        # Create a mock Sheet
        mock_sheet = MagicMock()
        mock_sheet.nrows = 3
        mock_sheet.ncols = 3
        
        # sh.merged_cells is a list of tuples (rlo, rhi, clo, chi)
        # We simulate a merged cell across row 0, cols 0 to 3
        mock_sheet.merged_cells = [(0, 1, 0, 3)] 
        
        # Define mock cell data (raw_val, cell_type)
        # We will put the header in row 0, and data in row 1, 2
        
        def cell_value(rowx, colx):
            data = [
                ["Merged Title", None, None],
                ["Dept", "Name", "Salary"],
                ["IT", "Alice", 50000]
            ]
            return data[rowx][colx]
            
        def cell_type(rowx, colx):
            return xlrd.XL_CELL_TEXT if isinstance(cell_value(rowx, colx), str) else xlrd.XL_CELL_NUMBER

        mock_sheet.cell_value = cell_value
        mock_sheet.cell_type = cell_type
        
        # Create a mock Workbook
        mock_wb = MagicMock()
        mock_wb.sheet_names.return_value = ["LegacyPayroll"]
        mock_wb.sheet_by_name.return_value = mock_sheet
        mock_wb.datemode = 0
        
        # Patch xlrd.open_workbook
        monkeypatch.setattr(xlrd, "open_workbook", lambda filename, **kwargs: mock_wb)

        template = Block(
            Row(pattern=["Dept", "Name", "Salary"], node_id="header"),
            Row(pattern=[Types.STR, Types.STR, Types.INT], repeat="+", node_id="data"),
            block_id="xls_table"
        )
        
        results = match_template("dummy.xls", template)
        
        assert len(results.blocks) == 1
        bm = results.blocks[0]
        
        assert bm.block_id == "xls_table"
        assert bm.rows[0].row == 1
        assert bm.rows[1].cells[1].value == "Alice"
