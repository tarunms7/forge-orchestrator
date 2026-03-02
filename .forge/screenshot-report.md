# Forge UI Screenshot Analysis Report

**Report Date:** 2026-03-03
**Analyst:** Forge Coding Agent
**Subject:** Comprehensive UI Analysis of the Forge Orchestration System
**Source Files Examined:** `design/` HTML prototypes + `web/src/` React implementation

> **Note:** No attached screenshot image was found in the task workspace. This report is based on a thorough static analysis of the design prototype HTML files (`design/dashboard.html`, `design/history.html`, `design/pipeline-view.html`, `design/settings.html`) and the corresponding Next.js React implementation under `web/src/`. Together these files fully define the application's visual design and behavior.

---

## 1. Executive Summary

Forge is a pipeline orchestration tool with a polished, dark-themed web UI. The application enables users to create, monitor, and manage AI-driven coding pipelines. The UI is built on a consistent design system with a collapsible sidebar navigation, dark neutral color palette, semantic status colors, and clear workflow progression through planning → execution → review → merge phases.

The interface is implemented twice: once as static HTML/CSS/JS prototypes (in `design/`) and once as a production Next.js 14 + TypeScript application (in `web/`). Both implementations are highly consistent and share the same design language.

---

## 2. Application Overview

| Property | Value |
|---|---|
| Application Name | Forge |
| UI Framework | Next.js 14 (React) + TypeScript |
| Design Prototype | Static HTML/CSS/JS |
| Theme | Dark (zinc neutral palette) |
| Primary Accent | Blue (`#3b82f6`) |
| Font — UI | Inter |
| Font — Code/Paths | JetBrains Mono |
| Navigation | Collapsible sidebar (220px expanded / 56px collapsed) |
| Pages | Dashboard, Pipeline View, History, Settings, Login, Register, New Task |

---

## 3. Design System

### 3.1 Color Palette

**Backgrounds (darkest to lightest)**

| Variable | Hex | Usage |
|---|---|---|
| `--bg-base` | `#09090b` | Page background |
| `--surface-1` | `#111113` | Cards, panels |
| `--surface-2` | `#18181b` | Sidebar background |
| `--surface-3` | `#222225` | Elevated elements |
| `--surface-4` | `#2b2b2f` | Inputs, dropdowns |
| `--hover-bg` | `#313136` | Hover state |

**Text Colors**

| Variable | Hex | Usage |
|---|---|---|
| `--text-primary` | `#fafafa` | Main content |
| `--text-secondary` | `#a1a1aa` | Descriptions, hints |
| `--text-tertiary` | `#71717a` | Placeholder text |
| `--text-dim` | `#52525b` | Disabled/subtle text |

**Semantic Colors**

| Name | Hex | Use Case |
|---|---|---|
| Accent/Blue | `#3b82f6` | Primary buttons, active states, links |
| Success/Green | `#22c55e` | Completed pipelines, system online |
| Error/Red | `#ef4444` | Failed tasks, danger actions |
| Warning/Amber | `#f59e0b` | Warnings, cost alerts |
| Purple | `#a78bfa` | Secondary highlights |

### 3.2 Typography

- **Base size:** 14px, line-height 1.5, `-webkit-font-smoothing: antialiased`
- **Headings:** `.page-title` — large, bold, primary text
- **Subtitles:** `.page-subtitle` — secondary text, lighter weight
- **Code/paths:** JetBrains Mono for all technical strings (paths, IDs, cost amounts)

### 3.3 Spacing & Shape

- **Border radius:** `sm` 6px, `md` 10px, `lg` 14px, `xl` 18px
- **Shadows:** Three levels (sm → md → lg) for depth hierarchy
- **Transition:** `200ms cubic-bezier(0.4, 0, 0.2, 1)` for all interactive elements

---

## 4. Page-by-Page Analysis

### 4.1 Dashboard (`/`)

**Purpose:** Command center showing system health, key metrics, and recent activity.

#### Layout
- Full-viewport dark background
- Left sidebar (collapsible) + right main content
- Main content divided into stacked sections with generous padding

#### UI Elements

**Welcome Banner**
- Personalized heading: *"Welcome back, Tarun"*
- Subtitle: *"Here's what's happening with your pipelines"*

**Quick-Launch Input**
- Large `<textarea>` (3 rows) with placeholder: *"Describe what you want to build or fix..."*
- Model-strategy dropdown alongside: `fast`, `balanced`, `thorough`
- **"Run Pipeline"** button — blue, glowing shadow, play-icon prefix
- Pressing Enter (without Shift) submits and routes to the new task page with description pre-filled

**Stats Overview Grid (4 columns)**

| Metric | Sample Value |
|---|---|
| Total Pipelines | 23 |
| Success Rate | 87% (green badge) |
| Total Spend | $21.47 |
| Avg Duration | 6:42 |

Each card: large numeric value, descriptive label below, subtle surface card background.

**Recent Pipelines List**
- Section title + "View all" link to History page
- Up to 5 rows, each showing:
  - Colored status dot (green = success, red = failed, amber = running)
  - Pipeline description/title
  - Abbreviated pipeline ID (hash)
  - Task progress counter (e.g., `5/5 tasks`)
  - Cost in USD
  - Duration
  - Relative timestamp (e.g., *"2h ago"*)
- Entire row is clickable → navigates to pipeline detail view
- Empty state: *"No recent pipelines"*

**System Status Cards (3-column grid)**
Each card: large colored status dot + system name + detail line

| System | Status | Detail |
|---|---|---|
| Claude SDK | Online (green) | Connected • claude-opus-4-6 |
| Git Repository | Online (green) | forge-orchestrator on main |
| Worktrees | Online (green) | /tmp/forge-worktrees • 0 active |

#### Notable Behaviors
- Success rate: calculated as `(completed / total_runs) * 100`, shown only when total > 0
- Duration formatting: converts raw seconds → human-readable (`6m 42s` or `8h 34m`)
- Phase-to-status mapping: `complete` → success, `executing/planning` → running, `error` → failed

---

### 4.2 Pipeline View (`/tasks/view`)

**Purpose:** Deep-dive into a single pipeline execution with phase-by-phase progress and live task tracking.

#### Phase Switcher (Design Tool)
A row of phase buttons at the top of the prototype enables rapid switching between states:
- **Planning** (10% progress)
- **Plan Review** (25% progress)
- **Executing** (50% progress)
- **Complete** (100% progress)
- **Error State** (75% progress)

#### Phase Navigation Bar
- Horizontal step indicator with connecting lines
- Completed phases: checkmark icon, green color
- Current phase: blue/active styling
- Future phases: gray, lower opacity

#### Planning Phase
- Displays task plan cards generated by the AI planner
- Each card shows: task title, description, model strategy, status badge
- Hovering a card highlights its corresponding node in the dependency graph

#### Plan Review Phase (Dependency Graph)
One of the most visually distinctive screens:
- **SVG-based dependency graph** rendered dynamically from real DOM positions
- Three-tier layout:
  - Tier 1: Multiple source tasks in parallel
  - Tier 2: Aggregation task (receives arrows from all Tier 1 nodes)
  - Tier 3: Final task (depends on Tier 2)
- Connecting arrows recalculate on window resize via `requestAnimationFrame`
- Hovering plan cards fades non-related graph nodes to 40% opacity, highlights related node with blue border + glow

#### Executing Phase
- Real-time progress display per task
- Task state indicators: `running` (animated), `queued` (muted), `completed` (green)
- Streaming log output area
- Per-task duration counters

#### Complete Phase
- All tasks show green completed status
- Execution results summary
- Generated artifacts list
- Total cost and duration statistics

#### Error Phase
- Failed task highlighted in red
- Error messages and relevant log excerpts
- Retry/recovery action buttons

#### Detail Panel (Slide-in)
Triggered by clicking any task card or result row:
- Full-height overlay with dark backdrop
- Three tabs: **Output**, **Logs**, **Artifacts**
- Close via button, overlay click, or Escape key

---

### 4.3 History (`/history`)

**Purpose:** Paginated, filterable log of all pipeline runs.

#### Toolbar
- **Search input:** magnifying-glass icon, placeholder *"Search pipelines..."* — case-insensitive, matches title and ID
- **Filter chips:** `All` (default active) | `Running` | `Completed` | `Failed` | `Cancelled`
- **Sort dropdown:**
  - Most Recent
  - Oldest First
  - Highest Cost
  - Longest Duration

#### History Table (6 columns)

| Column | Width | Content |
|---|---|---|
| Status | 110px | Pill badge with dot indicator |
| Pipeline | flexible | Title + ID |
| Tasks | 80px | `completed/total` count |
| Cost | 80px | Right-aligned dollar amount |
| Duration | 80px | Right-aligned time format |
| Date | 120px | Right-aligned relative time |

**Status Pills**

| State | Color | Dot |
|---|---|---|
| Running | Blue/Amber | Animated |
| Success | Green | Static |
| Failed | Red | Static |
| Cancelled | Gray | Static |

**Sample Data Rows**

| Pipeline | Tasks | Cost | Duration | When |
|---|---|---|---|---|
| Add JWT refresh token rotation (running) | 2/4 | $0.41 | 2:18 | Just now |
| Various completed examples | 5/5 | varies | varies | hours/days ago |
| Failed examples | 3/5 | varies | varies | varies |

#### Pagination
- Shows: *"Showing 1–10 of 23 pipelines"*
- Page buttons: `1` `2` `3` with Previous / Next controls
- Previous disabled on page 1

#### Client-Side Interactivity
- Filter chips toggle `display` on rows via `data-status` attribute
- Search filters on title/ID substring match
- Row click navigates to pipeline detail

---

### 4.4 Settings (`/settings`)

**Purpose:** User preferences, pipeline defaults, Git config, SDK connection, and notifications.

#### Profile
- Display Name (text input): *"Tarun M."*
- Email (text input): used for Git commit authorship
- Hint text explains purpose of each field

#### Pipeline Defaults
- Default Model: `fast` | **`balanced`** | `thorough`
- Max Parallel Workers: 1–5 spinner (default: **3**)
- Review Gates (checkboxes): **L1 Syntax** ✓ | **L2 LLM Review** ✓ | **Merge Check** ✓
- Auto-merge on success: Toggle (enabled)
- Auto-create Pull Request: Toggle (disabled)

#### Git Configuration
- Default Branch: `main`
- Worktree Directory: `/tmp/forge-worktrees` (monospace font)
- Auto-cleanup worktrees: Toggle (enabled)

#### Claude SDK
- Auth Status: Green dot + *"Connected"* + *"Last authenticated 2 days ago"* + **Reconnect** button
- Preferred Model: `claude-haiku-4-5` | `claude-sonnet-4-6` | **`claude-opus-4-6`**
- Max Cost per Pipeline: `$5.00`

#### Notifications
- Pipeline completion: Toggle (enabled)
- Review gate failures: Toggle (enabled)
- Cost alerts: Toggle (disabled)

#### Danger Zone (red-themed section)
- **Clear Pipeline History** — *"Permanently delete all pipeline run data. This cannot be undone."*
- **Reset All Settings** — *"Restore all settings to their default values."*

---

### 4.5 New Task (`/tasks/new`)

**Purpose:** Guided 3-step wizard for launching a new pipeline.

#### Step Indicator
- Three labeled steps with connecting lines
- Completed: green background + checkmark icon
- Current: blue accent
- Future: gray

#### Step 1 — Project Selection
Component: `ProjectSelector`
- Three source options (radio-style selection):
  1. **Existing repo** — text input for local path
  2. **Clone from GitHub** — text input for GitHub URL
  3. **Create new project** — text input for project name
- `canAdvance()` requires non-empty path/URL/name

#### Step 2 — Task Details
Components: `TemplatePicker`, `TaskForm`
- Template picker for common task patterns
- Description textarea (required, minimum length > 0)
- Priority dropdown: `low` | `medium` | `high`
- Additional context textarea
- Image file upload with base64 preview
- `canAdvance()` requires description > 0 characters

#### Step 3 — Execution & Review
Components: `ExecutionTargetSelector`, `ReviewSummary`
- Target: **Local** or **Remote**
  - Remote: SSH user, SSH host, SSH port fields
- Review summary card shows all entered data before submission:
  - Project (with source type label)
  - Priority
  - Target details
  - Number of attached images
  - Full task description
- `canAdvance()` always true; becomes a **Submit** button

#### Submission
- Converts images to base64 data URIs
- POST to `/tasks` endpoint with:
  - `description` (with any additional context appended)
  - `project_path`
  - `extra_dirs: []`
  - `model_strategy: "auto"`
  - `images` (optional)
- Success → navigate to `/tasks/view?id=<pipeline_id>`
- Failure → red error message displayed inline

#### Navigation Buttons
- Previous: disabled on Step 1
- Next: disabled until `canAdvance()` is true
- Submit (Step 3): shows spinner + disabled during API call (opacity 0.4, `cursor: not-allowed`)

---

## 5. Sidebar Navigation

**Structure:**
```
[Sidebar Header]
  ▸ Logo / Brand
  ▸ Collapse toggle button

[Navigation List]
  ▸ Dashboard    (icon: grid)
  ▸ History      (icon: clock)
  ▸ Settings     (icon: gear)

[Footer]
  ▸ User avatar (circle with initial "T")
  ▸ "Tarun M." label
```

**Behavior:**
- Expanded (220px): logo text visible, nav item labels visible, full user pill
- Collapsed (56px): icons only, no text, compact avatar
- State persisted in `localStorage` under key `"forge-sidebar"`
- Active page detection: exact match for `/`, `startsWith` for all other routes
- Active nav item receives `.active` class → blue accent, highlighted background

---

## 6. Authentication Flow

- JWT-based authentication stored in Zustand `authStore`
- `AuthGuard` component wraps all protected routes
- Public paths: `/login` and `/register`
- `AppShell` conditionally renders sidebar only when authenticated and on non-public paths
- On unauthenticated access → redirected to `/login`

---

## 7. WebSocket Integration

The `useWebSocket` hook (`web/src/hooks/useWebSocket.ts`) enables real-time updates:
- Connects to backend WS endpoint for live pipeline status
- Used on the pipeline view page for streaming logs and task state updates
- Falls back gracefully when disconnected

---

## 8. Notable UI Features & Patterns

| Feature | Description |
|---|---|
| **Glowing CTA Button** | Primary "Run Pipeline" button has animated blue glow shadow (`box-shadow` with rgba blue) |
| **SVG Dependency Graph** | Real-time DOM measurement → SVG lines connecting task nodes; recalculates on resize |
| **Hover Highlighting** | Hovering a plan card dims all unrelated graph nodes to 40% opacity |
| **Phase Switcher** | Design prototype-only control for rapidly previewing all execution phases |
| **Keyboard Support** | Escape closes any open detail panel overlay |
| **Collapsible Sidebar** | Smooth CSS transition; state persisted in localStorage |
| **Phase Progress Bar** | Fills from 0–100% as pipeline advances through phases |
| **Base64 Image Upload** | Task images converted client-side before POSTing to API |
| **Query Param Pre-fill** | Dashboard description input pre-fills Step 2 textarea via `?desc=` |
| **Step Completion Guard** | Next button disabled until all required fields are valid for the current step |

---

## 9. Observations & Notable Design Decisions

1. **Two-Layer Implementation:** The `design/` folder contains fully functional static HTML prototypes with realistic mock data — an excellent approach for rapid iteration before connecting the React implementation.

2. **Component Granularity:** The React implementation splits complex forms into focused sub-components (`ProjectSelector`, `TemplatePicker`, `TaskForm`, `ExecutionTargetSelector`) — good separation of concerns.

3. **User Personalization:** The dashboard shows *"Welcome back, Tarun"* and the sidebar footer shows *"Tarun M."* — the system is designed for a single-user deployment with personal branding.

4. **Cost Tracking:** Cost is surfaced prominently on every pipeline row and in settings (`Max Cost per Pipeline: $5.00`). This reflects awareness of LLM API costs.

5. **Model Strategy Selection:** Three tiers (`fast`, `balanced`, `thorough`) abstract underlying model selection. The implementation maps to specific Claude model variants (haiku, sonnet, opus).

6. **Danger Zone Pattern:** Destructive settings actions are visually separated in a red-themed section — clear UX affordance for high-risk operations.

7. **Monospace Consistency:** Technical strings (file paths, pipeline IDs, costs) consistently use `JetBrains Mono` — enhances readability and visual distinction.

8. **No Errors or Broken Elements Found:** The design system is internally consistent with no missing classes, broken layouts, or visual contradictions detected during static analysis.

---

## 10. Summary

The Forge UI is a polished, well-structured dark-theme web application for AI pipeline orchestration. It features:

- **4 main views** (Dashboard, Pipeline View, History, Settings) plus auth and task-creation flows
- A **consistent design system** with semantic colors, typography scale, and component library
- **Real-time capabilities** via WebSocket connections for live pipeline monitoring
- **Progressive disclosure** via a 3-step task creation wizard
- **Interactive visualizations** including a dynamic SVG dependency graph
- **Strong UX polish**: keyboard shortcuts, hover states, smooth transitions, persistent sidebar, loading/disabled states, and error handling throughout

The application is production-quality in design and implementation, with both a static prototype layer and a complete React/Next.js frontend that communicates with a FastAPI backend.
