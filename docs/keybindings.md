# Forge TUI Keybindings

Complete reference of every keyboard shortcut in the Forge TUI, organized by screen.

---

## Global (Available on All Screens)

| Key | Action |
|-----|--------|
| `Ctrl+1` | Switch to Home screen |
| `Ctrl+2` | Switch to Pipeline screen |
| `Ctrl+3` | Switch to Review screen |
| `Ctrl+4` | Switch to Settings screen |
| `Ctrl+Q` | Quit (press twice if pipeline is running) |
| `Ctrl+P` | Open command palette |
| `?` | Show help overlay |
| `Tab` | Cycle focus between questions/inputs |
| `s` | Export screenshot |

---

## Home Screen

| Key | Action |
|-----|--------|
| `Ctrl+S` | Submit prompt |
| `Ctrl+U` | Clear input |
| `Tab` | Switch focus between prompt and pipeline list |

---

## Pipeline Screen (Execution)

### Navigation

| Key | Action |
|-----|--------|
| `Ctrl+J` | Select next task |
| `Ctrl+K` | Select previous task |
| `Tab` | Cycle to next active agent |
| `1`-`9` | Jump to task by position |

### Views

| Key | Action |
|-----|--------|
| `Ctrl+O` | Switch to Output view |
| `Ctrl+T` | Switch to Chat view |
| `Ctrl+D` | Switch to Diff view |
| `Ctrl+R` | Open Review screen for selected task |
| `Ctrl+G` | Toggle DAG overlay |
| `i` | Toggle task description overlay |

### Actions

| Key | Action |
|-----|--------|
| `Ctrl+Y` | Enter copy mode |
| `C` | Copy all output to clipboard |
| `R` | Retry failed task |
| `Ctrl+X` | Skip errored task |
| `/` | Open search |
| `n` | Next search match |
| `N` | Previous search match |
| `Escape` | Dismiss copy overlay / Back (read-only mode) |

---

## Copy Mode (Inside Pipeline Screen)

Activated with `Ctrl+Y`. Overlays the output view for line-by-line selection.

| Key | Action |
|-----|--------|
| `j` | Move cursor down |
| `k` | Move cursor up |
| `Space` | Toggle line selection |
| `Enter` | Copy selected lines to clipboard |
| `Escape` | Cancel and exit copy mode |

---

## Search Overlay (Pipeline / Review)

Activated with `/`.

| Key | Action |
|-----|--------|
| `Enter` | Confirm search query |
| `Escape` | Dismiss search overlay |
| `n` | Next match (from parent screen) |
| `N` | Previous match (from parent screen) |

---

## Plan Approval Screen

### Navigation

| Key | Action |
|-----|--------|
| `Ctrl+J` | Select next task |
| `Ctrl+K` | Select previous task |
| `Ctrl+Down` | Move selected task down (reorder) |
| `Ctrl+Up` | Move selected task up (reorder) |

### Editing

| Key | Action |
|-----|--------|
| `Ctrl+E` | Edit task description |
| `Ctrl+F` | Edit task file list |
| `Ctrl+L` | Cycle task complexity (low/medium/high) |
| `Ctrl+N` | Add note to task |
| `Ctrl+A` | Add new task |
| `Ctrl+X` | Remove selected task |
| `Ctrl+Z` | Undo last remove |

### Actions

| Key | Action |
|-----|--------|
| `Enter` | Approve plan and start execution |
| `Escape` | Cancel / close edit mode |

---

## Review Screen

### Navigation

| Key | Action |
|-----|--------|
| `Ctrl+J` | Select next task |
| `Ctrl+K` | Select previous task |
| `1`-`9` | Jump to task by position |

### Actions

| Key | Action |
|-----|--------|
| `Ctrl+A` | Approve task |
| `Ctrl+X` | Reject task |
| `Ctrl+E` | Open diff in `$EDITOR` |
| `/` | Open search |
| `n` | Next search match |
| `N` | Previous search match |
| `Escape` | Back to previous screen |

---

## Final Approval Screen

| Key | Action |
|-----|--------|
| `Enter` | Create pull request |
| `Ctrl+D` | View full diff |
| `Ctrl+R` | Re-run failed tasks |
| `Ctrl+F` | Focus follow-up input |
| `Ctrl+N` | Start new task |
| `Ctrl+S` | Submit follow-up message |
| `Escape` | Back to previous screen |
| `q` | Back to previous screen |

---

## Settings Screen

| Key | Action |
|-----|--------|
| `Up` | Previous setting |
| `Down` | Next setting |
| `Left` | Decrease value / previous option |
| `Right` | Increase value / next option |
| `Enter` | Toggle boolean / edit config |
| `Escape` | Close settings |
