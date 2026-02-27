# Forge UI & Security Design

**Date:** 2026-02-28
**Status:** Approved
**Author:** Tarun + Claude

---

## 1. Overview

Transform Forge from a CLI-only orchestrator into a full web application with real-time parallel agent monitoring, comprehensive security, and support for both local and remote execution.

### Decisions

| Decision | Choice |
|----------|--------|
| UI Platform | Web app (browser-based) |
| Frontend | Next.js + TypeScript |
| Backend | FastAPI (Python) |
| Architecture | Monorepo fullstack (`web/` + `forge/`) |
| Repo Access | Existing local, GitHub clone, OR create new |
| Execution | Local + remote (SSH) from day one |
| Auth Model | Forge account + user's own Claude CLI |
| Claude Credentials | Zero storage — native `claude login` only |
| Real-time Updates | WebSocket live streaming |
| Agent Sandboxing | cwd lock + system prompt boundary + configurable dirs |

---

## 2. Bug Fixes

### 2.1 P0: Permission Popups (Music, Downloads, Documents)

**Root cause:** Claude's agent with `bypassPermissions` explores outside the project directory. macOS triggers filesystem access dialogs for protected directories.

**Fix:**
1. Set `cwd` on every `ClaudeCodeOptions` to the task's worktree path
2. Add filesystem boundary to agent system prompt: "Your working directory is `{worktree_path}`. Do NOT read, write, or execute anything outside this directory and the following allowed directories: `{extra_dirs}`"
3. In the UI, users can add extra allowed directories — these are passed to the agent prompt
4. No additional infrastructure needed — prompt + cwd is sufficient

### 2.2 Slow Execution

**Root cause:** No visibility into what's happening. The CLI blocks until completion with no output.

**Fix:** WebSocket streaming (see Section 5) provides real-time agent output, making perceived performance dramatically better even if wall-clock time is similar.

---

## 3. Project & Repo Management

### 3.1 Task Creation Flow

```
User clicks "New Task"
  │
  ├─ Step 1: Select Project
  │   ├─ Option A: Pick existing local repo (path input / recent list)
  │   ├─ Option B: Paste GitHub URL → Forge clones to ~/.forge/projects/{name}/
  │   └─ Option C: No repo → Forge creates at ~/.forge/projects/{name}/
  │
  ├─ Step 2: Task Details
  │   ├─ Task description (rich textarea, markdown)
  │   ├─ Priority / complexity hint (optional)
  │   └─ Additional context (links, specs, file references)
  │
  ├─ Step 3: Directory Permissions
  │   ├─ Project dir: auto-included (locked)
  │   └─ Extra dirs: "Add directory" button (file picker)
  │
  ├─ Step 4: Execution Target
  │   ├─ Local machine (default)
  │   └─ Remote machine (SSH: host, user, key path)
  │
  ├─ Step 5: Cost Estimation
  │   └─ "~3 Claude sessions, estimated 3-5 min"
  │
  └─ Step 6: Review → "Run Task" button
```

### 3.2 Project Storage

- Cloned repos: `~/.forge/projects/{repo-name}/`
- New repos: `~/.forge/projects/{user-chosen-name}/`
- Recent projects: tracked in `~/.forge/config.json`
- Each project remembers its settings (extra dirs, execution target)

---

## 4. Authentication & Login System

### 4.1 Layer 1: Forge Account (User Identity)

**Registration:**
- Email + password (bcrypt hashed, per-user salt)
- OR OAuth (GitHub, Google) via NextAuth.js

**Login:**
- Returns JWT access token (15 min expiry)
- Refresh token (7 days, httpOnly secure cookie)
- Server-side session store (SQLite/Redis) for refresh token tracking & revocation

**Password Reset:**
- Email-based with time-limited token (1 hour)
- Rate limited: 3 requests per hour per email

### 4.2 Layer 2: Claude Access (Execution Capability)

**Local execution:**
- Forge checks `claude --version` on user's machine
- Claude CLI must be pre-authenticated via `claude login`
- Forge NEVER stores Anthropic API keys or OAuth tokens

**Remote execution:**
- User provides SSH connection details (host, user, key path)
- Forge SSHs in and verifies `claude --version` works
- Claude CLI must be pre-authenticated on the remote machine
- SSH private keys stay on user's machine — never uploaded to Forge

### 4.3 Security Hardening

- HTTPS/TLS required in production
- CSRF tokens on all state-changing requests
- Rate limiting: 5 failed logins/min, 100 API calls/min per user
- Input sanitization on all user input
- No secrets in URLs or query params
- Security headers: HSTS, CSP, X-Frame-Options, X-Content-Type-Options
- Audit log: every action logged with timestamp + user ID + IP

---

## 5. Real-Time Dashboard & WebSocket Architecture

### 5.1 Dashboard Layout

**Left sidebar:**
- Project list (clickable, shows recent)
- Settings link
- History link

**Main area:**
- Current task header (description, status)
- Pipeline progress bar: Plan → Execute → Review → Merge
- Agent cards (one per parallel task, live updating)
- Completion summary

### 5.2 Task Execution UI Phases

**Phase 1 — Planning:**
Single card showing planner Claude session output. Streaming text as Claude decomposes the task.

**Phase 2 — Execution (parallel agents):**
Split-pane view with one card per agent. Each card shows:
- Task name + branch name
- Files being worked on
- Live streaming output (what Claude is typing/doing)
- Estimated progress bar
- State badge (WORKING / IN_REVIEW / MERGING / DONE / ERROR)

All parallel agents are visible simultaneously. Users can scroll through active agents and see each one working in real-time.

**Phase 3 — Review (per-task):**
When an agent completes, its card transitions to show:
- Gate 1 (Lint): pass/fail with issue count
- Gate 2 (LLM Review): streaming review output from separate Claude session
- Gate 3 (Merge readiness): pass/fail
- Expandable diff viewer

**Phase 4 — Merge (sequential):**
Card shows rebase + fast-forward progress, success/failure.

**Phase 5 — Complete:**
Summary view: all tasks, total lines, files changed, time taken, agent count, review pass rate. Action buttons: View Full Diff, Open in Editor, Push to GitHub.

### 5.3 WebSocket Protocol

**Server → Client events:**

| Event | Payload | When |
|-------|---------|------|
| `task:state_changed` | `{taskId, oldState, newState}` | Task state transition |
| `task:agent_output` | `{taskId, line, timestamp}` | Agent produces output |
| `task:review_update` | `{taskId, gate, result, details}` | Review gate completes |
| `task:merge_result` | `{taskId, success, error?, linesAdded}` | Merge completes |
| `pipeline:complete` | `{summary}` | All tasks done |
| `system:resource_alert` | `{type, value, threshold}` | Backpressure warning |

**Client → Server events:**

| Event | Payload | When |
|-------|---------|------|
| `task:cancel` | `{taskId}` | User cancels task |
| `task:retry` | `{taskId}` | User retries failed task |

**Connection:**
- WebSocket endpoint: `ws://localhost:8000/ws`
- Auth: JWT token sent in initial handshake
- Reconnection: exponential backoff with jitter
- Each pipeline gets a broadcast channel; client subscribes on task start

### 5.4 Backend Streaming

Agent output captured via async callback during `sdk_query()`:
- Each `MessageBlock` from the SDK stream is forwarded to WebSocket
- FastAPI maintains a connection manager mapping user → active WebSocket(s)
- Multiple browser tabs supported (all receive same events)

---

## 6. Security Architecture

### 6.1 Agent Sandboxing (Per-Task Isolation)

- Each agent runs in its own git worktree (existing mechanism)
- `cwd` locked to worktree path
- System prompt forbids access outside project dir + whitelisted dirs
- Bash commands restricted: no network tools (`curl`, `wget`) unless explicitly allowed
- File access outside boundary → agent instructed to refuse

### 6.2 Secret Protection

- Pre-scan: Forge identifies `.env`, `.credentials`, `*_key*`, `*.pem` and excludes from agent context
- Agent prompt includes: "NEVER commit secrets, API keys, or credentials"
- Pre-merge hook: regex-based secret scanner blocks merge if secrets detected
  - Patterns: AWS keys, GitHub tokens, generic API keys, private keys, passwords in config
- User passwords: bcrypt with per-user salt, never plaintext

### 6.3 Network Security

- HTTPS/TLS in production (self-signed for localhost dev)
- CORS: restricted to Forge frontend origin
- WebSocket: JWT-authenticated handshake
- All API endpoints require valid JWT (except `/auth/login`, `/auth/register`)
- Rate limiting on all endpoints

### 6.4 Execution Isolation

- **Local:** Agents run as user's OS process (same permissions)
- **Remote:** SSH with key-based auth only (no passwords)
- SSH keys stay on user's machine (Forge uses local SSH client)
- Agent output sanitized before display (strip malicious ANSI codes)

### 6.5 Audit Trail

- Every action logged: task CRUD, agent lifecycle, file changes, review results, merges, logins, settings
- Fields: timestamp, user_id, action_type, metadata (JSON)
- Stored in DB, viewable in "History" UI
- Append-only log table — no delete endpoint exposed

### 6.6 Multi-Agent Trust Model

- Agents CANNOT communicate directly with each other
- All coordination goes through Forge daemon (central authority)
- No shared filesystem between parallel agents (separate worktrees)
- Review agent (Gate 2) is a completely separate Claude session with no shared context — prevents self-rubber-stamping

---

## 7. Execution Layer Abstraction

### 7.1 Executor Interface

```python
class Executor(ABC):
    async def run_agent(self, task: TaskRecord, options: ClaudeCodeOptions) -> AgentResult: ...
    async def check_claude(self) -> bool: ...  # Verify Claude CLI available
    async def health_check(self) -> ExecutorHealth: ...
```

### 7.2 LocalExecutor

- Wraps existing `AgentRuntime` + `sdk_query()`
- `cwd` set to worktree path
- Checks `claude --version` locally

### 7.3 RemoteExecutor

- SSH connection using `asyncssh` library
- Creates remote worktree via SSH commands
- Streams Claude CLI output back over SSH → WebSocket
- Health check: SSH connection + `claude --version` on remote

### 7.4 Execution Target Config

```json
{
  "type": "local"
}
// or
{
  "type": "remote",
  "ssh": {
    "host": "dev-server.example.com",
    "user": "deploy",
    "key_path": "~/.ssh/id_ed25519",
    "port": 22
  }
}
```

---

## 8. Additional Features (v1)

### 8.1 Task Templates

- Pre-built templates: "Add REST API", "Write Tests", "Refactor Module", etc.
- User-created templates (save any task description as template)
- Template picker in task creation flow
- Stored in `~/.forge/templates/` as JSON

### 8.2 Cost Estimation

- Before running, estimate: number of Claude sessions, estimated time
- Based on task complexity and subtask count from planner
- Shown in Step 5 of task creation flow
- Historical tracking: actual vs estimated (improves over time)

### 8.3 Diff Preview & Manual Approval

- After merge, before any push: side-by-side diff viewer
- Per-file approve/reject
- "Request Changes" button → re-runs specific agent with feedback
- Uses Monaco editor (VS Code's diff component) for rich diff view

### 8.4 GitHub Integration

- Auto-create PR from completed task
- PR description includes: task description, subtask breakdown, review results, agent stats
- Link to GitHub Issues (reference issue number in task)
- OAuth GitHub token stored per-user (encrypted)

### 8.5 Task History & Replay

- Every run saved in DB with full timeline
- History view: list of past runs, click to see full detail
- Detail view: plan, agent outputs, review results, diffs, timing
- Search/filter by date, project, status

### 8.6 Notification System

- Browser notifications when long tasks complete
- Optional webhooks: Slack, email, Discord
- Configurable per-project in Settings
- Notification preferences stored per-user

---

## 9. Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14+ (App Router), TypeScript, Tailwind CSS |
| UI Components | shadcn/ui (Radix primitives) |
| Diff Viewer | Monaco Editor (react-diff-viewer) |
| State Management | Zustand (lightweight, WebSocket-friendly) |
| Backend | FastAPI (Python 3.12+) |
| WebSocket | FastAPI WebSocket + connection manager |
| Database | SQLAlchemy 2.0 async (SQLite dev, PostgreSQL prod) |
| Auth | JWT (PyJWT) + bcrypt + NextAuth.js (frontend OAuth) |
| SSH | asyncssh (remote execution) |
| Task Queue | asyncio (local), Redis optional for scaling |
| Orchestration | Existing Forge daemon (ForgeDaemon) |
| SDK | claude-code-sdk (existing) |

---

## 10. Monorepo Structure

```
claude-does/
├── forge/                  # Existing Python orchestrator (unchanged)
│   ├── cli/
│   ├── core/
│   ├── agents/
│   ├── merge/
│   ├── review/
│   ├── storage/
│   ├── config/
│   ├── registry/
│   ├── tui/
│   └── api/                # NEW: FastAPI app
│       ├── __init__.py
│       ├── app.py          # FastAPI app factory
│       ├── routes/         # REST endpoints
│       │   ├── auth.py
│       │   ├── projects.py
│       │   ├── tasks.py
│       │   └── settings.py
│       ├── ws/             # WebSocket handlers
│       │   └── handler.py
│       ├── security/       # Auth, JWT, rate limiting
│       │   ├── jwt.py
│       │   ├── auth.py
│       │   └── rate_limit.py
│       ├── models/         # API request/response models
│       │   └── schemas.py
│       └── services/       # Business logic
│           ├── executor.py
│           ├── project_manager.py
│           └── secret_scanner.py
├── web/                    # NEW: Next.js frontend
│   ├── package.json
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── next.config.js
│   ├── src/
│   │   ├── app/            # App Router pages
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx            # Dashboard
│   │   │   ├── login/page.tsx
│   │   │   ├── register/page.tsx
│   │   │   ├── projects/page.tsx
│   │   │   ├── tasks/
│   │   │   │   ├── new/page.tsx    # Task creation flow
│   │   │   │   └── [id]/page.tsx   # Task execution view
│   │   │   ├── history/page.tsx
│   │   │   └── settings/page.tsx
│   │   ├── components/
│   │   │   ├── ui/                 # shadcn components
│   │   │   ├── dashboard/
│   │   │   ├── task/
│   │   │   │   ├── AgentCard.tsx
│   │   │   │   ├── PipelineProgress.tsx
│   │   │   │   ├── ReviewPanel.tsx
│   │   │   │   └── CompletionSummary.tsx
│   │   │   ├── project/
│   │   │   ├── auth/
│   │   │   └── diff/
│   │   │       └── DiffViewer.tsx
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts
│   │   │   └── useAuth.ts
│   │   ├── stores/
│   │   │   ├── taskStore.ts
│   │   │   └── authStore.ts
│   │   └── lib/
│   │       ├── api.ts              # REST client
│   │       └── ws.ts               # WebSocket client
│   └── public/
├── docs/plans/
├── pyproject.toml
└── README.md
```
