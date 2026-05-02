from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
import re
import warnings

import xlrd
import openpyxl
from rapidfuzz import fuzz
from rpkbin.excel_extractor.types import CellCondition
from rpkbin.excel_extractor.template import (
    Block,
    Group,
    TemplateNode,
    AltNode,
)
from rpkbin.excel_extractor.result import (
    MatchOptions,
    CellMatch,
    RowMatch,
    BlockMatch,
    MatchOutput
)
from rpkbin.excel_extractor.normalizer import InternalCell, InternalGrid
from rpkbin.excel_extractor.normalizer import _load_xls_from_wb, _load_xlsx_from_wb


# ---------------------------------------------------------------------------
# TemplateMatcher
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CompiledCellCondition:
    pattern: str | re.Pattern
    is_merged: bool | None
    normalize: bool
    min_similarity : float | None


@dataclass(frozen=True, slots=True)
class CompiledCellConditionId:
    value: int
    def __repr__(self) -> str:
        return f"CId({self.value})"


@dataclass(frozen=True, slots=True)
class CompiledRule:
    rules: tuple[CompiledCellConditionId, ...]
    match_ratio: float | None
    node_id: str | None


@dataclass(frozen=True, slots=True)
class CompiledRuleId:
    value: int
    def __repr__(self) -> str:
        return f"RId({self.value})"
    
    @property
    def symbol(self) -> str:
        return f"{self.value}d"


@dataclass(frozen=True, slots=True)
class CompiledTemplate:
    regex: re.Pattern
    rule_id_map: dict[CompiledRuleId, CompiledRule]
    cond_id_map_rv: dict[CompiledCellCondition, CompiledCellConditionId]
    width: int
    block_id: str | None
    orientation: str


class TemplateMatcher:
    """Matches one or more Block templates against an InternalGrid."""

    def __init__(self, templates: list[Block], options: MatchOptions):
        self.templates = templates
        self.options = options

        self.compiled_templates: list[CompiledTemplate] = []
        for template in self.templates:
            regex, rule_id_map, cond_id_map_rv = self._compile(template)
            self.compiled_templates.append(
                CompiledTemplate(
                    regex=re.compile(regex),
                    rule_id_map=rule_id_map,
                    cond_id_map_rv=cond_id_map_rv,
                    width=template.width,
                    block_id=template.block_id,
                    orientation=template.orientation,
                )
            )
            
    # ------------------------------------------------------------------
    # Match Parts
    # ------------------------------------------------------------------

    def scan_for_blocks(self, grid: InternalGrid) -> list[list[BlockMatch]]:
        match_results = []
        for compiled_template in self.compiled_templates:
            is_horizontal = compiled_template.orientation == "horizontal"
            work_grid = grid.transpose() if is_horizontal else grid

            cid_grid = self._match_grid(work_grid, compiled_template.cond_id_map_rv)
            width = compiled_template.width

            # Search for matches
            match_result: list[BlockMatch] = []
            for i in range(work_grid.num_rows):
                for j in range(work_grid.num_cols - width + 1):
                    sub_cid_grid = [row[j:j+width] for row in cid_grid[i:]]

                    is_match, matched_rows = self._match_template(sub_cid_grid, compiled_template)
                    if is_match:
                        block_match = self._build_match_result(
                            work_grid, (i, j), matched_rows, compiled_template
                        )
                        if is_horizontal:
                            block_match = self._swap_coordinates(block_match)
                        match_result.append(block_match)
            match_results.append(match_result)
        return match_results

    def _match_grid(
            self,
            grid: InternalGrid,
            cond_id_map_rv: dict[CompiledCellCondition, CompiledCellConditionId]
    ) -> list[list[set[CompiledCellConditionId]]]:
        cid_grid = []
        for i in range(grid.num_rows):
            cid_row = []
            for j in range(grid.num_cols):
                cid_set = set()
                for cell_condition, cell_condition_id in cond_id_map_rv.items():
                    if self._cell_matches(grid.get_cell(i, j), cell_condition):
                        cid_set.add(cell_condition_id)
                cid_row.append(cid_set)
            cid_grid.append(cid_row)
        return cid_grid

    def _cell_matches(self, cell: InternalCell, rule: CompiledCellCondition) -> bool:
        if rule.is_merged is not None:
            if rule.is_merged != cell.is_merged:
                return False
        
        cell_value = cell.value or ""
        if isinstance(rule.pattern, str):
            if rule.normalize:
                cell_value = cell_value.strip().lower()

            if rule.min_similarity is not None:
                return fuzz.ratio(rule.pattern, cell_value) / 100.0 >= rule.min_similarity
            return rule.pattern == cell_value
        return bool(rule.pattern.fullmatch(cell_value))

    def _match_template(
        self,
        cid_grid: list[list[set[CompiledCellConditionId]]],
        compiled_template: CompiledTemplate,
    ) -> tuple[bool, list[tuple[int, CompiledRuleId]]]:
        row_symbols: list[tuple[int, CompiledRuleId]] = []
        for idx, cid_row in enumerate(cid_grid):
            symbol = self._match_row(cid_row, compiled_template)
            if symbol is not None:
                row_symbols.append((idx, symbol))
            else:
                break
        joined = "".join(rid.symbol for _, rid in row_symbols)

        # TODO Maybe use other method to deal with group, '*', '+', ... operation
        m = re.match(compiled_template.regex, joined)
        if m:
            matched_count = len(m.group(0))
            return True, row_symbols[:matched_count]
        return False, []

    def _match_row(self, cid_row: list[set[CompiledCellConditionId]], compiled_template: CompiledTemplate) -> CompiledRuleId | None:
        for compile_rule_id, compiled_rule in compiled_template.rule_id_map.items():
            if len(cid_row) != len(compiled_rule.rules):
                continue

            matched = sum(rule in cid_set for rule, cid_set in zip(compiled_rule.rules, cid_row))
            if compiled_rule.match_ratio is not None:
                if (matched / len(cid_row)) >= compiled_rule.match_ratio:
                    return compile_rule_id
            elif matched == len(cid_row):
                return compile_rule_id
        return None

    def _build_match_result(
        self,
        grid: InternalGrid,
        start_position: tuple[int, int],
        matched_rows: list[tuple[int, CompiledRuleId]],
        compiled_template: CompiledTemplate,
    ) -> BlockMatch:
        start_row, start_col = start_position

        row_matches = []
        for rel_row, rule_id in matched_rows:
            compiled_rule = compiled_template.rule_id_map[rule_id]

            cell_matches = []
            for j in range(len(compiled_rule.rules)):
                cell = grid.get_cell(start_row + rel_row, start_col + j)
                cell_matches.append(CellMatch(
                    row=start_row + rel_row,
                    col=start_col + j,
                    value=cell.value,
                    is_merged=cell.is_merged,
                ))
            row_matches.append(RowMatch(
                row=start_row + rel_row,
                cells=cell_matches,
                node_id=compiled_rule.node_id,
            ))

        last_rel_row = matched_rows[-1][0] if matched_rows else 0
        return BlockMatch(
            start=(start_row, start_col),
            end=(start_row + last_rel_row, start_col + compiled_template.width - 1),
            rows=row_matches,
            block_id=compiled_template.block_id,
        )

    @staticmethod
    def _swap_coordinates(block_match: BlockMatch) -> BlockMatch:
        """Swap row/col in all coordinates (used for horizontal → original mapping)."""
        new_rows = []
        for row in block_match.rows:
            new_cells = [
                CellMatch(row=c.col, col=c.row, value=c.value, is_merged=c.is_merged)
                for c in row.cells
            ]
            
            # Using the original row coordinate for horizontal matches
            original_row = new_cells[0].row if new_cells else row.row
            new_rows.append(RowMatch(row=original_row, cells=new_cells, node_id=row.node_id))
        return BlockMatch(
            start=(block_match.start[1], block_match.start[0]),
            end=(block_match.end[1], block_match.end[0]),
            rows=new_rows,
            block_id=block_match.block_id,
            sheet_name=block_match.sheet_name,
        )
    
    # ------------------------------------------------------------------
    # Compile Parts
    # ------------------------------------------------------------------

    def _compile(self, block: Block) -> tuple[str, dict[CompiledRuleId, CompiledRule], dict[CompiledCellCondition, CompiledCellConditionId]]:
        self._seen: dict[CompiledRule, CompiledRuleId] = {}
        self._rule_id_map: dict[CompiledRuleId, CompiledRule] = {}
        self._cond_id_map_rv: dict[CompiledCellCondition, CompiledCellConditionId] = {}
        parts = [self._visit(child) for child in block.children]
        return "".join(parts), self._rule_id_map, self._cond_id_map_rv

    def _compile_condition(
            self,
            condition: str | CellCondition,
            normalize: bool,
            min_similarity: float | None
    ) -> CompiledCellCondition:
        if isinstance(condition, str):
            if normalize:
                condition = condition.strip().lower()
            return CompiledCellCondition(
                pattern=condition,
                is_merged=False,
                normalize=normalize,
                min_similarity=min_similarity,
            )
            
        if normalize:
            for pat in condition.patterns:
                if any(c.isupper() for c in pat):
                    warnings.warn(
                        f"Regex pattern {pat!r} contains uppercase characters while normalize=True. "
                        "The matched cell value will be lowercased, causing the regex to likely fail. "
                        "Consider using '(?i)' for case-insensitivity or setting normalize=False.",
                        UserWarning,
                        stacklevel=2
                    )
        return CompiledCellCondition(
            pattern=re.compile('|'.join(list(condition.patterns))),
            is_merged=condition.is_merged,
            normalize=normalize,
            min_similarity=min_similarity,
        )

    def _register(self, node: TemplateNode) -> str:
        rules = [
            self._compile_condition(
                condition=r,
                normalize=node.normalize,
                min_similarity=node.min_similarity,
            ) for r in node.rules()
        ]

        # Record condition first
        new_rules = set(rule for rule in rules if rule not in self._cond_id_map_rv)
        cnt = len(self._cond_id_map_rv)
        for i, r in enumerate(new_rules):
            self._cond_id_map_rv[r] = CompiledCellConditionId(cnt+i)

        key = CompiledRule(
            rules=tuple(self._cond_id_map_rv[r] for r in rules),
            match_ratio=node.match_ratio,
            node_id=node.node_id
        )

        if key not in self._seen:
            num_rule = len(self._seen)
            self._seen[key] = CompiledRuleId(num_rule)
            self._rule_id_map[CompiledRuleId(num_rule)] = key
        return self._seen[key].symbol

    @staticmethod
    def _repeat_suffix(node: TemplateNode) -> str:
        lo, hi = node.repeat_range
        if (lo, hi) == (1, 1):
            return ""
        if (lo, hi) == (0, 1):
            return "?"
        if (lo, hi) == (0, None):
            return "*"
        if (lo, hi) == (1, None):
            return "+"
        if lo == hi:
            return f"{{{lo}}}"
        if hi is None:
            return f"{{{lo},}}"
        return f"{{{lo},{hi}}}"

    def _visit(self, node: TemplateNode) -> str:
        suffix = self._repeat_suffix(node)
        if isinstance(node, AltNode):
            parts = [self._visit(alt) for alt in node.alternatives]
            return f"({'|'.join(parts)}){suffix}"
        if isinstance(node, Group):
            parts = [self._visit(child) for child in node.children]
            return f"({''.join(parts)}){suffix}"
        return f"({self._register(node)}){suffix}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def match_template(
    file_path: str | Path,
    template: Block | list[Block],
    sheet: str | int | list[str | int] | None = None,
    options: MatchOptions | None = None,
) -> MatchOutput:
    """Extract data from an Excel file using a template description.

    Parameters
    ----------
    file_path   : path to the Excel file
    template    : a Block or list of Block objects describing the expected layout
    sheet       : sheet name, 0-based index, list of names/indices, or ``None``
                   to scan **all** sheets (default).
                   Pass ``"*"`` as an alias for None (scan all sheets).
    options     : MatchOptions instance; defaults to MatchOptions()

    Returns
    -------
    MatchOutput containing matched blocks (and near-miss hints if configured).
    All MatchResult objects carry the ``sheet`` attribute indicating which
    sheet the match was found on.
    """
    if options is None:
        options = MatchOptions()

    templates = template if isinstance(template, list) else [template]
    if not templates:
        raise ValueError("At least one template Block must be provided.")

    # Resolve the list of sheets to scan
    path_str = str(file_path)
    wb_xlrd = None
    wb_openpyxl = None
    all_sheet_names = []

    if path_str.lower().endswith(".xls"):
        wb_xlrd = xlrd.open_workbook(path_str, formatting_info=True)
        all_sheet_names = wb_xlrd.sheet_names()

    elif path_str.lower().endswith(".xlsx") or path_str.lower().endswith(".xlsm"):
        wb_openpyxl = openpyxl.load_workbook(path_str, data_only=True)
        all_sheet_names = wb_openpyxl.sheetnames

    if (wb_xlrd is None and wb_openpyxl is None) or not all_sheet_names:
        raise ValueError(f"Cannot read the specified Excel file: {path_str}")

    if sheet is None:
        sheets_to_scan = all_sheet_names
    else:
        if not isinstance(sheet, list):
            sheet = [sheet]
        try:
            sheets_to_scan: list[str] = [
                all_sheet_names[s] if isinstance(s, int) else s for s in sheet
            ]
        except IndexError as e:
            raise ValueError(
                f"Sheet index out of range. It should be less than {len(all_sheet_names)}."
            ) from e

        not_found_sheet = [s for s in sheets_to_scan if s not in all_sheet_names]
        if not_found_sheet:
            raise ValueError(f"Sheet(s) not found: {', '.join(not_found_sheet)}")

    all_matched_blocks = []
    matched_cnt = 0

    template_matcher = TemplateMatcher(templates, options)

    for sheet_name in sheets_to_scan:
        if wb_xlrd is not None:
            grid = _load_xls_from_wb(wb_xlrd, sheet_name)
        elif wb_openpyxl is not None:
            grid = _load_xlsx_from_wb(wb_openpyxl, sheet_name)
        else:
            raise ValueError("Unexpected error: No sheet was loaded.")

        # Start matching
        output = template_matcher.scan_for_blocks(grid)

        # Check progress and populate sheet_name
        current_matched_blocks = []
        for template_matches in output:
            for bm in template_matches:
                bm.sheet_name = sheet_name
                current_matched_blocks.append(bm)
        
        if current_matched_blocks:
            matched_cnt += 1
            all_matched_blocks.extend(current_matched_blocks)

        if options.max_matched_sheets > 0 and matched_cnt >= options.max_matched_sheets:
            break

    # Clean up
    if wb_openpyxl is not None:
        wb_openpyxl.close()

    return MatchOutput(blocks=all_matched_blocks)
