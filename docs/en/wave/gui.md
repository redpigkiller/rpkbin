# Web GUI Guide

Interactive guide to Wave's browser layouts, queue management, Flow/Log inspection panels, archive browser, and waveform keyboard shortcuts.

## Workspaces, Layouts, and Header Controls

The top bar switches between four workspace layouts and global queue controls:

- **Layout Views**: `Jobs` (queue focus), `Split` (queue alongside Inspect), `Inspect` (full Flow and Log viewer), and `Charts` (full waveform, XY, scatter, and histogram viewer).
- **Open Dialog**: Opens `.py` work files, existing log files/directories, or saved `.sqlite3` archives from the catalog. Under **Archive (.sqlite3) -> Recent Archives**, clicking the trash icon on any closed, non-active archive activates **two-step archive delete confirmation** inline (`Delete <name> permanently?` with **Confirm Delete** and **Cancel**).
- **Preview vs Active Controls**: In Preview mode (before running), press **Start** (or **Resume**) to launch processes, or edit the queue first without spawning anything. Use **Pause / Resume** to freeze scheduling of new attempts, and **Stop All** to terminate active processes.
- **Settings Modal**: Displays and updates runtime `max_workers`, `max_history`, `scheduling_mode`, and the active `Archive retention` mode and day limits.

## Queue and Jobs Pane

- **Filtering and Search**: Filter jobs by status (`RUNNING`, `PENDING`, `HELD`, `PASS`, `FAIL`, etc.) or search by name and command.
- **Hold and Release (`HELD`)**: Place pending jobs on hold (`Held`) to exclude them from scheduler dispatch without altering their priority rank; release them when ready.
- **Drag-and-Drop Reorder**: Drag pending or active rows in the queue to reassign relative priorities (`reorder_queue`).
- **Soft Remove and Restore**: Remove unneeded unstarted or completed jobs (`Remove`). Removed entries remain recoverable in the **Removed Jobs** drawer (`Restore`), preserving their bound `Flow` pipelines.
- **Interactive Input and POSIX Signals**: Send text lines to running processes with an explicit **newline** toggle (`\n` vs raw text) or send POSIX signals from the **Signal** action menu (`SIGINT`, `SIGTERM`, `SIGTSTP`, `SIGCONT`, `SIGUSR1`, `SIGUSR2`, `SIGHUP`, `SIGQUIT` on POSIX hosts).

## Inspect Pane

### Flow Tab

Switch subtabs between **Parsed Records**, **Analysis Reports**, and **Automation** history:

- **Summary Toggle (`Latest: Record / Merged`)**: Switch between **Record** (shows every field of the newest single publication) and **Merged** (merges the latest seen value per field across recent publications).
- **Filtering and Evidence**: Filter fields by name substring or numeric min/max bounds. Clicking an evidence link highlights and opens the originating raw log line in the **Original log context** drawer.

### Log Tab

- **Scoped `Ctrl+F`**: Pressing `Ctrl+F` activates Wave's inline Log Find bar only when the layout is `Split` or `Inspect`, the active Inspect tab is `Log`, and focus or pointer target is inside the Log pane or Find controls. Outside those panels, browser native `Ctrl+F` works normally. Pressing `/` jumps directly to Log Find or Jobs Search depending on focus.
- **Find Bar Options**: Toggle case sensitivity (`Aa`), whole word (`ab`), regular expressions (`.*`), and **Filter matching lines** to hide non-matching lines. Press `Enter` / `Shift+Enter` to move Next / Previous.
- **Log Options Menu**:
  - **Elapsed Range**: Filter log records by elapsed second bounds (`Start` and `End` -> **Apply**), adding an active filter chip above the log viewer.
  - **Display Controls**: Adjust **Log font** size (`9` to `20` px or **Reset font to 13 px**), toggle **Wrap long lines**, toggle **ANSI colors**, and jump via **Jump to beginning (`Ctrl+Home`)** or **Follow latest output (`Ctrl+End`)**.
- **Original Log Context Drawer**: Opens automatically when jumping to a raw line, double-clicking a matching filtered log row, clicking Parsed/Analysis evidence, or choosing `Reveal in Raw Log` from a chart point. Switch placement between side-by-side and stacked below, drag the divider (or resize with arrow keys / `Home` reset), and press `Escape` to close.
- **Full Raw Chunk Reader (`Preview · read full`)**: In archived logs, lines longer than 1024 characters show a **Preview · read full** action button. Clicking it opens the chunked `Raw Log` dialog reading **65,536-byte** slices with **Start**, **Next chunk**, and **Original Log context** controls.

## Charts and Waveform Viewer

### Signal Picker and Hierarchy

- **Get Signals (`g`)**: Search signals by substring, glob, or regex, add signals to the active view, replace selection, or group signals under named headings (`Shift+G` groups into `= G...`).
- **Defaults**: Clicking **Defaults** resets displayed signals to the view's declared defaults (or the first **8** signals in `long` waveform views when no defaults are configured, and at most **1** in histogram mode) and clears custom row overrides.
- **Row Drag-Into-Group**: Drag a signal row onto the middle zone (`28% to 72%` row height) of a group row to move it inside and automatically expand that group. Dropping near the top or bottom edge reorders above or below the row.
- **Bit Expansion and Slicing**: Right-click or use the row menu for **Expand Bits `[msb:0]`** (available when vector width is `<= 64` bits) and **Bit Select `[msb:lsb]`…** (displays up to **16** slices per signal).
- **Waveform File Browser**: Load `.rc`, `.vcd`, or `.json` files via the Waveform File browser dialog (**Back**, **Forward**, **Parent folder**, **Refresh**, folder shortcuts, and **Browse Local…** when opening a local OS file). Also export waveform traces as VCD (`export_vcd`).

### Waveform Keyboard and Mouse Shortcuts

| Input | Action |
| --- | --- |
| `Ctrl+Wheel` | Zoom centered at mouse x-coordinate (on non-waveform charts, `Shift+Wheel` also zooms). |
| `Shift+Wheel` (waveform) | Pan timeline horizontally. |
| `Z`, `+`, `=` / `z`, `-` | Zoom in / zoom out. |
| `f` or `Home` | Fit 100% of the domain range into view. |
| `l` / `Shift+L` | Return to previous zoom/pan view / reload chart data from archive. |
| `Shift+Left` / `Shift+Right` | Pan waveform window left or right by 25%. |
| `Left` / `Right` arrows | Step Cursor A to the previous/next transition on the **currently selected signal row**, regardless of the `By:` dropdown. |
| `n` / `N` | Jump Cursor A forward / backward to the next match of the **`By:`** condition (`Any Edge`, `Rising`, `Falling`, `= Bus Value`). |
| `Up` / `Down` arrows | Move selection to previous / next row in the signal pane. |
| `c` | Cycle the selected signal or group color through the 7 waveform palette colors. |
| `h` | Toggle full hierarchy paths vs leaf names in the signal list. |
| `Delete` / `Backspace` | Remove selected signal, bit slice, or group, or collapse expanded bit rows. |
| `Escape` | Close the topmost open dialog/menu/Get Signals drawer, or clear Cursor B on the waveform canvas. |

## See Also

- [Wave Overview](README.md)
- [Flow Pipelines](flow.md)
- [History and Archives](history.md)
- [Troubleshooting](troubleshooting.md)
