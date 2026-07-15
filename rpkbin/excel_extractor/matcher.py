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


@dataclass(frozen=True, slots=True)
class CompiledTemplate:
    nodes: tuple[TemplateNode, ...]
    rule_id_map: dict[CompiledRuleId, CompiledRule]
    cond_id_map_rv: dict[CompiledCellCondition, CompiledCellConditionId]
    node_rule_ids: dict[int, CompiledRuleId]
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
            rule_id_map, cond_id_map_rv, node_rule_ids = self._compile(template)
            self.compiled_templates.append(
                CompiledTemplate(
                    nodes=tuple(template.children),
                    rule_id_map=rule_id_map,
                    cond_id_map_rv=cond_id_map_rv,
                    node_rule_ids=node_rule_ids,
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

            # Classify each row/column window once instead of rebuilding and
            # rescanning the remaining grid for every possible start row.
            row_rule_grids = [
                [
                    self._match_row(cid_row[j:j + width], compiled_template)
                    for cid_row in cid_grid
                ]
                for j in range(work_grid.num_cols - width + 1)
            ]

            # Search for matches
            match_result: list[BlockMatch] = []
            for i in range(work_grid.num_rows):
                for j in range(work_grid.num_cols - width + 1):
                    is_match, matched_rows = self._match_template(
                        row_rule_grids[j], compiled_template, i
                    )
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
        if rule.normalize:
            cell_value = cell_value.strip().lower()

        if isinstance(rule.pattern, str):
            if rule.min_similarity is not None:
                return fuzz.ratio(rule.pattern, cell_value) / 100.0 >= rule.min_similarity
            return rule.pattern == cell_value
        return bool(rule.pattern.fullmatch(cell_value))

    def _match_template(
        self,
        row_rule_ids: list[set[CompiledRuleId]],
        compiled_template: CompiledTemplate,
        start: int,
    ) -> tuple[bool, list[tuple[int, CompiledRuleId]]]:
        matches = self._match_sequence(
            compiled_template.nodes, row_rule_ids, compiled_template, start
        )
        nonempty = [(end, path) for end, path in matches.items() if end > start]
        if not nonempty:
            return False, []
        _, path = max(nonempty, key=lambda item: item[0])
        return True, [(row - start, rule_id) for row, rule_id in path]

    def _match_sequence(
        self,
        nodes: tuple[TemplateNode, ...] | list[TemplateNode],
        row_rule_ids: list[set[CompiledRuleId]],
        compiled_template: CompiledTemplate,
        start: int,
    ) -> dict[int, list[tuple[int, CompiledRuleId]]]:
        states: dict[int, list[tuple[int, CompiledRuleId]]] = {start: []}
        for node in nodes:
            next_states: dict[int, list[tuple[int, CompiledRuleId]]] = {}
            for position, path in states.items():
                for end, node_path in self._match_node(
                    node, row_rule_ids, compiled_template, position
                ).items():
                    next_states.setdefault(end, path + node_path)
            states = next_states
            if not states:
                break
        return states

    def _match_node(
        self,
        node: TemplateNode,
        row_rule_ids: list[set[CompiledRuleId]],
        compiled_template: CompiledTemplate,
        start: int,
    ) -> dict[int, list[tuple[int, CompiledRuleId]]]:
        lo, hi = node.repeat_range
        limit = hi if hi is not None else len(row_rule_ids) - start + 1
        states: dict[int, list[tuple[int, CompiledRuleId]]] = {start: []}
        accepted: dict[int, list[tuple[int, CompiledRuleId]]] = {}

        for count in range(limit + 1):
            if count >= lo:
                for end, path in states.items():
                    accepted.setdefault(end, path)
            if count == limit:
                break

            next_states: dict[int, list[tuple[int, CompiledRuleId]]] = {}
            for position, path in states.items():
                for end, atom_path in self._match_atom(
                    node, row_rule_ids, compiled_template, position
                ).items():
                    if end > position:  # prevent infinite zero-width repetition
                        next_states.setdefault(end, path + atom_path)
            if not next_states:
                break
            states = next_states

        return accepted

    def _match_atom(
        self,
        node: TemplateNode,
        row_rule_ids: list[set[CompiledRuleId]],
        compiled_template: CompiledTemplate,
        start: int,
    ) -> dict[int, list[tuple[int, CompiledRuleId]]]:
        if isinstance(node, AltNode):
            matches: dict[int, list[tuple[int, CompiledRuleId]]] = {}
            for alternative in node.alternatives:
                for end, path in self._match_node(
                    alternative, row_rule_ids, compiled_template, start
                ).items():
                    matches.setdefault(end, path)
            return matches

        if isinstance(node, Group):
            return self._match_sequence(
                node.children, row_rule_ids, compiled_template, start
            )

        if start >= len(row_rule_ids):
            return {}
        rule_id = compiled_template.node_rule_ids[id(node)]
        if rule_id not in row_rule_ids[start]:
            return {}
        return {start + 1: [(start, rule_id)]}

    def _match_row(self, cid_row: list[set[CompiledCellConditionId]], compiled_template: CompiledTemplate) -> set[CompiledRuleId]:
        matches = set()
        for compile_rule_id, compiled_rule in compiled_template.rule_id_map.items():
            if len(cid_row) != len(compiled_rule.rules):
                continue

            matched = sum(rule in cid_set for rule, cid_set in zip(compiled_rule.rules, cid_row))
            if compiled_rule.match_ratio is not None:
                if (matched / len(cid_row)) >= compiled_rule.match_ratio:
                    matches.add(compile_rule_id)
            elif matched == len(cid_row):
                matches.add(compile_rule_id)
        return matches

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

    def _compile(self, block: Block) -> tuple[
        dict[CompiledRuleId, CompiledRule],
        dict[CompiledCellCondition, CompiledCellConditionId],
        dict[int, CompiledRuleId],
    ]:
        self._seen: dict[CompiledRule, CompiledRuleId] = {}
        self._rule_id_map: dict[CompiledRuleId, CompiledRule] = {}
        self._cond_id_map_rv: dict[CompiledCellCondition, CompiledCellConditionId] = {}
        self._node_rule_ids: dict[int, CompiledRuleId] = {}
        for child in block.children:
            self._visit(child)
        return self._rule_id_map, self._cond_id_map_rv, self._node_rule_ids

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
                is_merged=None,
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
            # EMPTY must remain distinguishable from whitespace-only cells.
            normalize=normalize if condition.patterns else False,
            min_similarity=None,
        )

    def _register(self, node: TemplateNode) -> CompiledRuleId:
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
        rule_id = self._seen[key]
        self._node_rule_ids[id(node)] = rule_id
        return rule_id

    def _visit(self, node: TemplateNode) -> None:
        if isinstance(node, AltNode):
            for alternative in node.alternatives:
                self._visit(alternative)
        elif isinstance(node, Group):
            for child in node.children:
                self._visit(child)
        else:
            self._register(node)


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
    MatchOutput containing matched blocks. Every BlockMatch carries the
    ``sheet_name`` indicating where the match was found.
    """
    if options is None:
        options = MatchOptions()

    templates = template if isinstance(template, list) else [template]
    if not templates:
        raise ValueError("At least one template Block must be provided.")
    if not all(isinstance(item, Block) for item in templates):
        raise TypeError("template must be a Block or list of Block objects")

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

    try:
        if (wb_xlrd is None and wb_openpyxl is None) or not all_sheet_names:
            raise ValueError(f"Cannot read the specified Excel file: {path_str}")

        if sheet is None or sheet == "*":
            sheets_to_scan = all_sheet_names
        else:
            selected_sheets = sheet if isinstance(sheet, list) else [sheet]
            try:
                sheets_to_scan: list[str] = [
                    all_sheet_names[s] if isinstance(s, int) else s
                    for s in selected_sheets
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

            output = template_matcher.scan_for_blocks(grid)
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

        return MatchOutput(blocks=all_matched_blocks)
    finally:
        if wb_openpyxl is not None:
            wb_openpyxl.close()
        if wb_xlrd is not None:
            wb_xlrd.release_resources()
