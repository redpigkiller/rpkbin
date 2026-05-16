"""Refresh primitives for the Wave Textual TUI."""

from __future__ import annotations

from textual.widgets import Log, RichLog


class _LogViewAdapter:
    """Small compatibility layer for Textual Log / RichLog widgets.

    Auto-scroll contract
    --------------------
    We use a single decision point per ``write_lines`` call:

    1. Snapshot ``_is_at_bottom()`` *before* writing (using the current
       scroll position, which is stable at this point).
    2. Write the new lines (this extends max_scroll_y asynchronously in
       Textual's layout engine).
    3. Call ``scroll_end`` *only if* we were already at the bottom.

    This avoids the race condition where two separate reads of ``scroll_y``
    disagree because Textual's layout has advanced between them.  The old
    ``set_auto_scroll_for_append`` helper is retired; ``auto_scroll`` on the
    underlying widget is left at its default (False) so Textual never fights
    with our explicit ``scroll_end`` calls.

    Bottom detection uses a tolerance of 5 virtual rows instead of 2 to
    absorb the one-frame delay between a write and the layout recalculation
    that updates ``max_scroll_y``.
    """

    _BOTTOM_TOLERANCE = 5  # rows of slack before we consider "not at bottom"

    def __init__(self, widget: Log | RichLog) -> None:
        self.widget = widget
        # Disable Textual's built-in auto_scroll so we are the sole authority.

    def clear(self) -> None:
        self.widget.clear()

    def write(self, text: str) -> None:
        """Write a single chunk of text (used for status/hint messages)."""
        if isinstance(self.widget, Log):
            self.widget.write_lines(text.splitlines() or [""])
        else:
            self.widget.write(text)

    def write_lines(self, lines: list[str], *, force_scroll: bool = False) -> None:
        """Append *lines* and scroll to end iff the view was already at the bottom.

        When *force_scroll* is True (e.g. after a job switch / clear), scroll to
        the end unconditionally after writing, regardless of prior position.
        This handles the race where `clear()` resets scroll_y to 0 but the layout
        hasn't caught up yet when we inspect `max_scroll_y`.
        """
        if not lines:
            if force_scroll:
                self._scroll_end()
            return
        # Single decision point — read once, before writing.
        follow = force_scroll or self._is_at_bottom()
        self.widget.auto_scroll = follow
        if isinstance(self.widget, Log):
            self.widget.write_lines(lines)
        else:
            self.widget.write("\n".join(lines))
        if force_scroll:
            # Explicit post-write scroll: needed because after clear()+header write
            # the layout hasn't settled yet and Log's internal scroll_end may target
            # a stale (too-small) max_scroll_y.  We call it again after writing.
            self._scroll_end()

    def scroll_to_end(self) -> None:
        """Explicitly scroll to the bottom and re-enable following."""
        self._scroll_end()

    def _is_at_bottom(self) -> bool:
        return self.widget.scroll_y >= max(0, self.widget.max_scroll_y - self._BOTTOM_TOLERANCE)

    def _scroll_end(self) -> None:
        scroll_end = getattr(self.widget, "scroll_end", None)
        if scroll_end is None:
            return
        try:
            scroll_end(animate=False)
        except TypeError:
            scroll_end()


def tail_sync_start(job, limit: int) -> int:
    """Return the log cursor that starts at the last *limit* lines."""
    total_lines = getattr(job, "_total_log_lines", 0)
    if limit <= 0:
        return total_lines
    return max(0, total_lines - limit)


def sync_job_log(
    job,
    log_view: _LogViewAdapter,
    sync_count: int,
    *,
    empty_message: str | None = None,
    record_log_append=None,
    force_scroll: bool = False,
) -> int:
    """Append newly emitted job log lines to *log_view* and return sync count."""
    total_lines = getattr(job, "_total_log_lines", 0)
    if total_lines <= sync_count:
        if empty_message is not None and total_lines == 0:
            log_view.write(empty_message)
        if force_scroll:
            log_view.scroll_to_end()
        return sync_count

    if hasattr(job, "log_snapshot_since"):
        total_lines, new_lines = job.log_snapshot_since(sync_count)
    else:
        new_lines = job.tail(total_lines - sync_count)
    if new_lines:
        log_view.write_lines(new_lines, force_scroll=force_scroll)
        if record_log_append is not None:
            record_log_append(len(new_lines))
    elif force_scroll:
        log_view.scroll_to_end()
    return total_lines
