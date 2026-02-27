# Forge Web UI & Security Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform Forge from CLI-only into a full web app with real-time parallel agent monitoring, auth, security, and local+remote execution.

**Architecture:** Monorepo fullstack — FastAPI backend (`forge/api/`) wraps existing daemon, Next.js frontend (`web/`). WebSocket streams agent output in real-time. JWT auth for Forge accounts, Claude access via native CLI.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, PyJWT, bcrypt, Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui, Zustand, asyncssh

**Design doc:** `docs/plans/2026-02-28-forge-ui-design.md`

---

## Phase 1: P0 Bug Fix — Agent Sandboxing

Fix the permission popups (Music, Downloads, Documents access) by locking agents to project directory.

### Task 1.1: Add cwd + directory boundary to ClaudeAdapter

**Files:**
- Modify: `forge/agents/adapter.py:11-20` (AGENT_SYSTEM_PROMPT)
- Modify: `forge/agents/adapter.py:54-67` (ClaudeCodeOptions construction)
- Test: `forge/agents/adapter_test.py`

**Step 1: Write the failing test**

```python
# In forge/agents/adapter_test.py — add new test
def test_adapter_sets_cwd_to_worktree():
    adapter = ClaudeAdapter()
    options = adapter._build_options("/tmp/test-worktree", [])
    assert options.cwd == "/tmp/test-worktree"

def test_adapter_system_prompt_includes_directory_boundary():
    adapter = ClaudeAdapter()
    options = adapter._build_options("/tmp/test-worktree", [])
    assert "/tmp/test-worktree" in options.system_prompt
    assert "Do NOT read, write, or execute anything outside" in options.system_prompt

def test_adapter_system_prompt_includes_extra_dirs():
    adapter = ClaudeAdapter()
    options = adapter._build_options("/tmp/test-worktree", ["/tmp/shared-lib"])
    assert "/tmp/shared-lib" in options.system_prompt
```

**Step 2: Run tests to verify they fail**

Run: `pytest forge/agents/adapter_test.py -v -k "test_adapter_sets_cwd or test_adapter_system_prompt"`
Expected: FAIL — `_build_options` method doesn't exist yet

**Step 3: Refactor ClaudeAdapter to extract _build_options and add boundary**

Modify `forge/agents/adapter.py`:
- Extract options construction into `_build_options(worktree_path: str, allowed_dirs: list[str]) -> ClaudeCodeOptions`
- Update `AGENT_SYSTEM_PROMPT` to include: `"Your working directory is {cwd}. Do NOT read, write, or execute anything outside this directory{extra_dirs_clause}."`
- Set `cwd=worktree_path` on `ClaudeCodeOptions`
- Update `run()` to accept `allowed_dirs` parameter and call `_build_options()`

**Step 4: Run tests to verify they pass**

Run: `pytest forge/agents/adapter_test.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `pytest forge/ -v`
Expected: ALL 117+ tests pass

**Step 6: Commit**

```bash
git add forge/agents/adapter.py forge/agents/adapter_test.py
git commit -m "fix(P0): lock agents to project dir via cwd + system prompt boundary"
```

### Task 1.2: Pass allowed_dirs through daemon → adapter

**Files:**
- Modify: `forge/core/daemon.py:146-216` (_execute_task)
- Modify: `forge/config/settings.py` (add allowed_dirs setting)
- Test: `forge/config/settings_test.py`

**Step 1: Add allowed_dirs to ForgeSettings**

```python
# forge/config/settings.py — add field
allowed_dirs: list[str] = []  # Extra directories agents can access
```

**Step 2: Write test for setting**

```python
# forge/config/settings_test.py — add test
def test_allowed_dirs_default_empty():
    s = ForgeSettings()
    assert s.allowed_dirs == []
```

**Step 3: Run test**

Run: `pytest forge/config/settings_test.py -v`
Expected: PASS

**Step 4: Thread allowed_dirs through daemon._execute_task()**

In `daemon.py:_execute_task()`, pass `self._settings.allowed_dirs` to the adapter's `run()` call. The adapter already accepts it from Task 1.1.

**Step 5: Run full test suite**

Run: `pytest forge/ -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add forge/core/daemon.py forge/config/settings.py forge/config/settings_test.py
git commit -m "fix(P0): thread allowed_dirs from settings through daemon to adapter"
```

---

## Phase 2: FastAPI Backend Foundation

### Task 2.1: Add FastAPI dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add dependencies**

Add to `[project.dependencies]`:
```
"fastapi>=0.115",
"uvicorn[standard]>=0.30",
"python-jose[cryptography]>=3.3",
"passlib[bcrypt]>=1.7",
"python-multipart>=0.0.9",
```

Add to `[project.optional-dependencies]`:
```
remote = ["asyncssh>=2.14"]
```

**Step 2: Install**

Run: `pip install -e ".[dev]"`

**Step 3: Verify imports**

Run: `python -c "import fastapi; import jose; import passlib; print('OK')"`
Expected: OK

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add FastAPI, JWT, and auth dependencies"
```

### Task 2.2: Create FastAPI app factory with health check

**Files:**
- Create: `forge/api/__init__.py`
- Create: `forge/api/app.py`
- Create: `forge/api/app_test.py`

**Step 1: Write the failing test**

```python
# forge/api/app_test.py
import pytest
from httpx import AsyncClient, ASGITransport
from forge.api.app import create_app

@pytest.mark.asyncio
async def test_health_check():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
```

**Step 2: Run test to verify it fails**

Run: `pytest forge/api/app_test.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Create app factory**

```python
# forge/api/__init__.py
(empty)

# forge/api/app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

def create_app() -> FastAPI:
    app = FastAPI(title="Forge", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app
```

**Step 4: Run test**

Run: `pytest forge/api/app_test.py -v`
Expected: PASS

**Step 5: Add httpx dev dependency**

Add `"httpx>=0.27"` to `[project.optional-dependencies] dev`.

**Step 6: Commit**

```bash
git add forge/api/ pyproject.toml
git commit -m "feat: FastAPI app factory with health endpoint and CORS"
```

### Task 2.3: User model and auth database tables

**Files:**
- Create: `forge/api/models/__init__.py`
- Create: `forge/api/models/user.py`
- Create: `forge/api/models/user_test.py`

**Step 1: Write the failing test**

```python
# forge/api/models/user_test.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from forge.api.models.user import UserRow, AuditLogRow, Base

@pytest.mark.asyncio
async def test_create_user():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        user = UserRow(email="test@test.com", password_hash="hashed", display_name="Test")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        assert user.id is not None
        assert user.email == "test@test.com"
        assert user.created_at is not None
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/models/user_test.py -v`
Expected: FAIL

**Step 3: Implement user model**

```python
# forge/api/models/user.py
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

class AuditLogRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
```

**Step 4: Run test**

Run: `pytest forge/api/models/user_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/models/
git commit -m "feat: user and audit log database models"
```

### Task 2.4: JWT token utilities

**Files:**
- Create: `forge/api/security/__init__.py`
- Create: `forge/api/security/jwt.py`
- Create: `forge/api/security/jwt_test.py`

**Step 1: Write the failing test**

```python
# forge/api/security/jwt_test.py
import pytest
from forge.api.security.jwt import create_access_token, create_refresh_token, decode_token

def test_create_and_decode_access_token():
    token = create_access_token(user_id="user-123", secret="test-secret")
    payload = decode_token(token, secret="test-secret")
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"

def test_create_and_decode_refresh_token():
    token = create_refresh_token(user_id="user-123", secret="test-secret")
    payload = decode_token(token, secret="test-secret")
    assert payload["sub"] == "user-123"
    assert payload["type"] == "refresh"

def test_expired_token_raises():
    token = create_access_token(user_id="u1", secret="s", expires_minutes=-1)
    with pytest.raises(Exception):
        decode_token(token, secret="s")
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/security/jwt_test.py -v`
Expected: FAIL

**Step 3: Implement JWT utils**

```python
# forge/api/security/jwt.py
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError

ALGORITHM = "HS256"

def create_access_token(user_id: str, secret: str, expires_minutes: int = 15) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    return jwt.encode({"sub": user_id, "type": "access", "exp": exp}, secret, algorithm=ALGORITHM)

def create_refresh_token(user_id: str, secret: str, expires_days: int = 7) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=expires_days)
    return jwt.encode({"sub": user_id, "type": "refresh", "exp": exp}, secret, algorithm=ALGORITHM)

def decode_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
```

**Step 4: Run test**

Run: `pytest forge/api/security/jwt_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/security/
git commit -m "feat: JWT access and refresh token utilities"
```

### Task 2.5: Auth service (register, login, password hashing)

**Files:**
- Create: `forge/api/services/__init__.py`
- Create: `forge/api/services/auth_service.py`
- Create: `forge/api/services/auth_service_test.py`

**Step 1: Write the failing test**

```python
# forge/api/services/auth_service_test.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from forge.api.models.user import Base, UserRow
from forge.api.services.auth_service import AuthService

@pytest.fixture
async def auth_service():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return AuthService(session_factory=session_factory, jwt_secret="test-secret")

@pytest.mark.asyncio
async def test_register_creates_user(auth_service):
    svc = await auth_service
    result = await svc.register(email="a@b.com", password="securepass123", display_name="Test")
    assert result["user_id"] is not None
    assert result["access_token"] is not None
    assert result["refresh_token"] is not None

@pytest.mark.asyncio
async def test_register_duplicate_email_fails(auth_service):
    svc = await auth_service
    await svc.register(email="a@b.com", password="pass123", display_name="Test")
    with pytest.raises(ValueError, match="already registered"):
        await svc.register(email="a@b.com", password="pass456", display_name="Test2")

@pytest.mark.asyncio
async def test_login_correct_password(auth_service):
    svc = await auth_service
    await svc.register(email="a@b.com", password="pass123", display_name="Test")
    result = await svc.login(email="a@b.com", password="pass123")
    assert result["access_token"] is not None

@pytest.mark.asyncio
async def test_login_wrong_password_fails(auth_service):
    svc = await auth_service
    await svc.register(email="a@b.com", password="pass123", display_name="Test")
    with pytest.raises(ValueError, match="Invalid"):
        await svc.login(email="a@b.com", password="wrong")
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/services/auth_service_test.py -v`
Expected: FAIL

**Step 3: Implement auth service**

```python
# forge/api/services/auth_service.py
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from forge.api.models.user import UserRow
from forge.api.security.jwt import create_access_token, create_refresh_token

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class AuthService:
    def __init__(self, session_factory: async_sessionmaker, jwt_secret: str):
        self._session_factory = session_factory
        self._jwt_secret = jwt_secret

    async def register(self, email: str, password: str, display_name: str) -> dict:
        async with self._session_factory() as session:
            existing = await session.execute(select(UserRow).where(UserRow.email == email))
            if existing.scalar_one_or_none():
                raise ValueError(f"Email {email} already registered")
            user = UserRow(
                email=email,
                password_hash=pwd_context.hash(password),
                display_name=display_name,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return {
                "user_id": user.id,
                "access_token": create_access_token(user.id, self._jwt_secret),
                "refresh_token": create_refresh_token(user.id, self._jwt_secret),
            }

    async def login(self, email: str, password: str) -> dict:
        async with self._session_factory() as session:
            result = await session.execute(select(UserRow).where(UserRow.email == email))
            user = result.scalar_one_or_none()
            if not user or not pwd_context.verify(password, user.password_hash):
                raise ValueError("Invalid email or password")
            return {
                "user_id": user.id,
                "access_token": create_access_token(user.id, self._jwt_secret),
                "refresh_token": create_refresh_token(user.id, self._jwt_secret),
            }
```

**Step 4: Run test**

Run: `pytest forge/api/services/auth_service_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/services/
git commit -m "feat: auth service with register, login, bcrypt hashing"
```

### Task 2.6: Auth REST endpoints

**Files:**
- Create: `forge/api/routes/__init__.py`
- Create: `forge/api/routes/auth.py`
- Create: `forge/api/routes/auth_test.py`
- Modify: `forge/api/app.py` (register router)

**Step 1: Write the failing test**

```python
# forge/api/routes/auth_test.py
import pytest
from httpx import AsyncClient, ASGITransport
from forge.api.app import create_app

@pytest.mark.asyncio
async def test_register_endpoint():
    app = create_app(db_url="sqlite+aiosqlite:///:memory:")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/auth/register", json={
            "email": "test@test.com",
            "password": "securepass123",
            "display_name": "Test User",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

@pytest.mark.asyncio
async def test_login_endpoint():
    app = create_app(db_url="sqlite+aiosqlite:///:memory:")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/auth/register", json={
            "email": "a@b.com", "password": "pass123", "display_name": "T",
        })
        resp = await client.post("/auth/login", json={
            "email": "a@b.com", "password": "pass123",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/routes/auth_test.py -v`
Expected: FAIL

**Step 3: Implement auth routes**

```python
# forge/api/routes/auth.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from forge.api.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    user_id: str

def create_auth_router(auth_service: AuthService) -> APIRouter:
    @router.post("/register", response_model=TokenResponse)
    async def register(req: RegisterRequest):
        try:
            result = await auth_service.register(req.email, req.password, req.display_name)
            return result
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.post("/login", response_model=TokenResponse)
    async def login(req: LoginRequest):
        try:
            result = await auth_service.login(req.email, req.password)
            return result
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e))

    return router
```

**Step 4: Update app.py to wire auth router with DB**

Update `create_app()` to accept `db_url` parameter, create engine + sessionmaker, register auth router at startup, and run `Base.metadata.create_all` on startup event.

**Step 5: Run tests**

Run: `pytest forge/api/routes/auth_test.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add forge/api/routes/ forge/api/app.py
git commit -m "feat: auth REST endpoints (register + login)"
```

### Task 2.7: Rate limiting middleware

**Files:**
- Create: `forge/api/security/rate_limit.py`
- Create: `forge/api/security/rate_limit_test.py`

**Step 1: Write the failing test**

```python
# forge/api/security/rate_limit_test.py
import pytest
from forge.api.security.rate_limit import RateLimiter

@pytest.mark.asyncio
async def test_rate_limiter_allows_under_limit():
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        assert await limiter.check("user-1") is True

@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_limit():
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    assert await limiter.check("user-1") is True
    assert await limiter.check("user-1") is True
    assert await limiter.check("user-1") is False
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/security/rate_limit_test.py -v`
Expected: FAIL

**Step 3: Implement in-memory rate limiter**

```python
# forge/api/security/rate_limit.py
import time
from collections import defaultdict

class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def check(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self._window
        self._requests[key] = [t for t in self._requests[key] if t > window_start]
        if len(self._requests[key]) >= self._max:
            return False
        self._requests[key].append(now)
        return True
```

**Step 4: Run test**

Run: `pytest forge/api/security/rate_limit_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/security/rate_limit.py forge/api/security/rate_limit_test.py
git commit -m "feat: in-memory rate limiter for auth endpoints"
```

---

## Phase 3: WebSocket Streaming & Event System

### Task 3.1: WebSocket connection manager

**Files:**
- Create: `forge/api/ws/__init__.py`
- Create: `forge/api/ws/manager.py`
- Create: `forge/api/ws/manager_test.py`

**Step 1: Write the failing test**

```python
# forge/api/ws/manager_test.py
import pytest
import asyncio
import json
from forge.api.ws.manager import ConnectionManager

class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.accepted = False
    async def accept(self):
        self.accepted = True
    async def send_text(self, data: str):
        self.sent.append(data)

@pytest.mark.asyncio
async def test_connect_and_broadcast():
    mgr = ConnectionManager()
    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket()
    await mgr.connect(ws1, user_id="u1", pipeline_id="p1")
    await mgr.connect(ws2, user_id="u1", pipeline_id="p1")
    await mgr.broadcast("p1", {"event": "task:state_changed", "data": {"taskId": "t1"}})
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    assert json.loads(ws1.sent[0])["event"] == "task:state_changed"

@pytest.mark.asyncio
async def test_disconnect_removes_connection():
    mgr = ConnectionManager()
    ws = FakeWebSocket()
    await mgr.connect(ws, user_id="u1", pipeline_id="p1")
    mgr.disconnect(ws, pipeline_id="p1")
    await mgr.broadcast("p1", {"event": "test"})
    assert len(ws.sent) == 0  # Not received after disconnect
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/ws/manager_test.py -v`
Expected: FAIL

**Step 3: Implement connection manager**

```python
# forge/api/ws/manager.py
import json
from collections import defaultdict
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, websocket: WebSocket, user_id: str, pipeline_id: str):
        await websocket.accept()
        self._connections[pipeline_id].append(websocket)

    def disconnect(self, websocket: WebSocket, pipeline_id: str):
        self._connections[pipeline_id] = [
            ws for ws in self._connections[pipeline_id] if ws is not websocket
        ]

    async def broadcast(self, pipeline_id: str, message: dict):
        dead = []
        for ws in self._connections[pipeline_id]:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, pipeline_id)
```

**Step 4: Run test**

Run: `pytest forge/api/ws/manager_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/ws/
git commit -m "feat: WebSocket connection manager with broadcast"
```

### Task 3.2: Add streaming callback to sdk_query

**Files:**
- Modify: `forge/core/sdk_helpers.py:39-65`
- Test: `forge/core/sdk_helpers_test.py`

**Step 1: Write the failing test**

```python
# forge/core/sdk_helpers_test.py — add new test
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_sdk_query_calls_on_message_callback(monkeypatch):
    """sdk_query should call on_message for each streamed message."""
    from claude_code_sdk import ResultMessage
    from forge.core import sdk_helpers

    fake_result = ResultMessage(
        type="result",
        subtype="success",
        cost_usd=0.01,
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="test",
        total_cost_usd=0.01,
        result="done",
        usage={"input": 100, "output": 50},
    )

    async def fake_query(**kwargs):
        yield fake_result

    monkeypatch.setattr(sdk_helpers, "query", fake_query)
    callback = AsyncMock()

    result = await sdk_helpers.sdk_query("test", options=None, on_message=callback)
    callback.assert_called_once_with(fake_result)
```

**Step 2: Run to verify fail**

Run: `pytest forge/core/sdk_helpers_test.py::test_sdk_query_calls_on_message_callback -v`
Expected: FAIL — sdk_query doesn't accept on_message yet

**Step 3: Add on_message callback parameter to sdk_query**

Modify `sdk_query()` signature to accept `on_message: Callable | None = None`. Inside the async for loop, call `await on_message(message)` if provided, for every message (not just ResultMessage).

```python
async def sdk_query(prompt, options, on_message=None):
    # ... existing code ...
    async for message in query(prompt=prompt, options=options):
        if on_message:
            await on_message(message)
        if isinstance(message, ResultMessage):
            last_result = message
    # ...
```

**Step 4: Run test**

Run: `pytest forge/core/sdk_helpers_test.py -v`
Expected: PASS

**Step 5: Run full suite**

Run: `pytest forge/ -v`
Expected: ALL PASS (existing calls pass on_message=None by default)

**Step 6: Commit**

```bash
git add forge/core/sdk_helpers.py forge/core/sdk_helpers_test.py
git commit -m "feat: add on_message streaming callback to sdk_query"
```

### Task 3.3: Wire WebSocket events through daemon execution

**Files:**
- Modify: `forge/core/daemon.py` (add event_emitter parameter)
- Create: `forge/core/events.py`
- Create: `forge/core/events_test.py`

**Step 1: Write the failing test**

```python
# forge/core/events_test.py
import pytest
from forge.core.events import EventEmitter

@pytest.mark.asyncio
async def test_event_emitter_collects_events():
    collected = []
    async def handler(event):
        collected.append(event)
    emitter = EventEmitter()
    emitter.on("task:state_changed", handler)
    await emitter.emit("task:state_changed", {"taskId": "t1", "newState": "in_progress"})
    assert len(collected) == 1
    assert collected[0]["taskId"] == "t1"
```

**Step 2: Run to verify fail**

Run: `pytest forge/core/events_test.py -v`
Expected: FAIL

**Step 3: Implement EventEmitter**

```python
# forge/core/events.py
from collections import defaultdict
from typing import Callable, Any

class EventEmitter:
    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, handler: Callable):
        self._handlers[event].append(handler)

    async def emit(self, event: str, data: dict):
        for handler in self._handlers[event]:
            await handler(data)
```

**Step 4: Run test**

Run: `pytest forge/core/events_test.py -v`
Expected: PASS

**Step 5: Wire into daemon**

Add `event_emitter: EventEmitter | None = None` parameter to `ForgeDaemon.__init__()`. In `_execute_task()`, emit events at each state transition:
- `task:state_changed` when task moves states
- `task:agent_output` when sdk_query's on_message fires
- `task:review_update` after each gate
- `task:merge_result` after merge

**Step 6: Run full suite**

Run: `pytest forge/ -v`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add forge/core/events.py forge/core/events_test.py forge/core/daemon.py
git commit -m "feat: event emitter for real-time WebSocket streaming"
```

### Task 3.4: WebSocket endpoint wired to pipeline events

**Files:**
- Create: `forge/api/ws/handler.py`
- Modify: `forge/api/app.py` (add ws endpoint)

**Step 1: Implement WebSocket endpoint**

```python
# forge/api/ws/handler.py
from fastapi import WebSocket, WebSocketDisconnect
from forge.api.ws.manager import ConnectionManager
from forge.api.security.jwt import decode_token

async def websocket_endpoint(
    websocket: WebSocket,
    pipeline_id: str,
    manager: ConnectionManager,
    jwt_secret: str,
):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return
    try:
        payload = decode_token(token, jwt_secret)
        user_id = payload["sub"]
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await manager.connect(websocket, user_id=user_id, pipeline_id=pipeline_id)
    try:
        while True:
            data = await websocket.receive_json()
            # Handle client events (cancel, retry) here
    except WebSocketDisconnect:
        manager.disconnect(websocket, pipeline_id=pipeline_id)
```

**Step 2: Register in app.py**

Add WebSocket route at `/ws/{pipeline_id}` using the handler.

**Step 3: Commit**

```bash
git add forge/api/ws/handler.py forge/api/app.py
git commit -m "feat: authenticated WebSocket endpoint for pipeline events"
```

---

## Phase 4: Execution Layer Abstraction

### Task 4.1: Executor interface and LocalExecutor

**Files:**
- Create: `forge/api/services/executor.py`
- Create: `forge/api/services/executor_test.py`

**Step 1: Write the failing test**

```python
# forge/api/services/executor_test.py
import pytest
from unittest.mock import AsyncMock, patch
from forge.api.services.executor import LocalExecutor

@pytest.mark.asyncio
async def test_local_executor_check_claude():
    executor = LocalExecutor()
    # Mock subprocess to return success
    with patch("asyncio.create_subprocess_exec") as mock_proc:
        mock_proc.return_value.communicate = AsyncMock(return_value=(b"claude-code v1.0", b""))
        mock_proc.return_value.returncode = 0
        result = await executor.check_claude()
        assert result is True

@pytest.mark.asyncio
async def test_local_executor_health_check():
    executor = LocalExecutor()
    with patch.object(executor, "check_claude", return_value=True):
        health = await executor.health_check()
        assert health.available is True
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/services/executor_test.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# forge/api/services/executor.py
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ExecutorHealth:
    available: bool
    error: str | None = None

class Executor(ABC):
    @abstractmethod
    async def check_claude(self) -> bool: ...
    @abstractmethod
    async def health_check(self) -> ExecutorHealth: ...

class LocalExecutor(Executor):
    async def check_claude(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode == 0
        except FileNotFoundError:
            return False

    async def health_check(self) -> ExecutorHealth:
        ok = await self.check_claude()
        return ExecutorHealth(available=ok, error=None if ok else "Claude CLI not found or not authenticated")
```

**Step 4: Run test**

Run: `pytest forge/api/services/executor_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/services/executor.py forge/api/services/executor_test.py
git commit -m "feat: executor abstraction with LocalExecutor"
```

### Task 4.2: RemoteExecutor (SSH-based)

**Files:**
- Modify: `forge/api/services/executor.py` (add RemoteExecutor)
- Test: `forge/api/services/executor_test.py` (add remote tests)

**Step 1: Write the failing test**

```python
# Add to executor_test.py
@pytest.mark.asyncio
async def test_remote_executor_health_check_with_mock():
    from forge.api.services.executor import RemoteExecutor, SSHConfig
    config = SSHConfig(host="test.example.com", user="deploy", key_path="/fake/key")
    executor = RemoteExecutor(config)
    with patch.object(executor, "check_claude", return_value=False):
        health = await executor.health_check()
        assert health.available is False
        assert "not found" in health.error
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/services/executor_test.py -v -k remote`
Expected: FAIL

**Step 3: Implement RemoteExecutor**

```python
# Add to executor.py
from dataclasses import dataclass

@dataclass
class SSHConfig:
    host: str
    user: str
    key_path: str
    port: int = 22

class RemoteExecutor(Executor):
    def __init__(self, config: SSHConfig):
        self._config = config

    async def check_claude(self) -> bool:
        try:
            import asyncssh
            async with asyncssh.connect(
                self._config.host,
                username=self._config.user,
                client_keys=[self._config.key_path],
                port=self._config.port,
                known_hosts=None,
            ) as conn:
                result = await conn.run("claude --version")
                return result.exit_status == 0
        except Exception:
            return False

    async def health_check(self) -> ExecutorHealth:
        ok = await self.check_claude()
        return ExecutorHealth(
            available=ok,
            error=None if ok else f"Claude CLI not found on {self._config.host} or SSH failed",
        )
```

**Step 4: Run test**

Run: `pytest forge/api/services/executor_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/services/executor.py forge/api/services/executor_test.py
git commit -m "feat: RemoteExecutor with SSH-based Claude CLI access"
```

---

## Phase 5: Secret Scanner & Audit Log

### Task 5.1: Secret scanner

**Files:**
- Create: `forge/api/services/secret_scanner.py`
- Create: `forge/api/services/secret_scanner_test.py`

**Step 1: Write the failing test**

```python
# forge/api/services/secret_scanner_test.py
import pytest
from forge.api.services.secret_scanner import SecretScanner

def test_detects_aws_key():
    scanner = SecretScanner()
    findings = scanner.scan_text("AKIAIOSFODNN7EXAMPLE")
    assert len(findings) > 0
    assert any("AWS" in f.pattern_name for f in findings)

def test_detects_generic_api_key():
    scanner = SecretScanner()
    findings = scanner.scan_text('api_key = "sk-1234567890abcdef"')
    assert len(findings) > 0

def test_no_false_positive_on_normal_code():
    scanner = SecretScanner()
    findings = scanner.scan_text("def calculate_total(items):\n    return sum(items)")
    assert len(findings) == 0

def test_scan_file_detects_env():
    scanner = SecretScanner()
    assert scanner.is_sensitive_file(".env")
    assert scanner.is_sensitive_file("credentials.json")
    assert scanner.is_sensitive_file("id_rsa.pem")
    assert not scanner.is_sensitive_file("main.py")
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/services/secret_scanner_test.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# forge/api/services/secret_scanner.py
import re
from dataclasses import dataclass
from pathlib import Path

@dataclass
class SecretFinding:
    pattern_name: str
    match: str
    line_number: int | None = None

PATTERNS = [
    ("AWS Access Key", r"AKIA[0-9A-Z]{16}"),
    ("AWS Secret Key", r"(?i)aws_secret_access_key\s*=\s*\S+"),
    ("GitHub Token", r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    ("Generic API Key", r'(?i)(api[_-]?key|apikey|secret[_-]?key)\s*[=:]\s*["\']?\S{8,}'),
    ("Private Key", r"-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----"),
    ("Generic Secret", r'(?i)(password|passwd|secret)\s*[=:]\s*["\'][^"\']{8,}["\']'),
]

SENSITIVE_FILE_PATTERNS = [
    ".env", ".env.local", ".env.production",
    "credentials.json", "credentials.yaml",
    "*.pem", "*.key", "*_rsa", "*_dsa", "*_ed25519",
]

class SecretScanner:
    def scan_text(self, text: str) -> list[SecretFinding]:
        findings = []
        for name, pattern in PATTERNS:
            for match in re.finditer(pattern, text):
                findings.append(SecretFinding(pattern_name=name, match=match.group()[:20] + "..."))
        return findings

    def is_sensitive_file(self, filename: str) -> bool:
        name = Path(filename).name
        for pattern in SENSITIVE_FILE_PATTERNS:
            if pattern.startswith("*"):
                if name.endswith(pattern[1:]):
                    return True
            elif name == pattern:
                return True
        return False
```

**Step 4: Run test**

Run: `pytest forge/api/services/secret_scanner_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/services/secret_scanner.py forge/api/services/secret_scanner_test.py
git commit -m "feat: secret scanner for pre-merge protection"
```

### Task 5.2: Audit log service

**Files:**
- Create: `forge/api/services/audit_service.py`
- Create: `forge/api/services/audit_service_test.py`

**Step 1: Write the failing test**

```python
# forge/api/services/audit_service_test.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from forge.api.models.user import Base, AuditLogRow
from forge.api.services.audit_service import AuditService

@pytest.mark.asyncio
async def test_log_action():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = AuditService(session_factory)
    await svc.log("user-1", "task:created", {"task_id": "t1"})
    logs = await svc.list_for_user("user-1")
    assert len(logs) == 1
    assert logs[0].action == "task:created"
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/services/audit_service_test.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# forge/api/services/audit_service.py
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from forge.api.models.user import AuditLogRow

class AuditService:
    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def log(self, user_id: str, action: str, metadata: dict | None = None, ip: str | None = None):
        async with self._session_factory() as session:
            entry = AuditLogRow(
                user_id=user_id,
                action=action,
                metadata_json=json.dumps(metadata) if metadata else None,
                ip_address=ip,
            )
            session.add(entry)
            await session.commit()

    async def list_for_user(self, user_id: str, limit: int = 100) -> list[AuditLogRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AuditLogRow)
                .where(AuditLogRow.user_id == user_id)
                .order_by(AuditLogRow.timestamp.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
```

**Step 4: Run test**

Run: `pytest forge/api/services/audit_service_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/services/audit_service.py forge/api/services/audit_service_test.py
git commit -m "feat: audit log service for action tracking"
```

---

## Phase 6: Task & Project REST API

### Task 6.1: Project manager service

**Files:**
- Create: `forge/api/services/project_manager.py`
- Create: `forge/api/services/project_manager_test.py`

**Step 1: Write the failing test**

```python
# forge/api/services/project_manager_test.py
import pytest
import tempfile
from pathlib import Path
from forge.api.services.project_manager import ProjectManager

def test_create_new_project():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ProjectManager(projects_dir=tmpdir)
        path = mgr.create_project("my-app")
        assert Path(path).exists()
        assert (Path(path) / ".git").exists()
        assert (Path(path) / ".forge").exists()

def test_list_projects():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ProjectManager(projects_dir=tmpdir)
        mgr.create_project("app1")
        mgr.create_project("app2")
        projects = mgr.list_projects()
        assert len(projects) == 2

def test_validate_existing_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ProjectManager(projects_dir=tmpdir)
        path = mgr.create_project("test")
        assert mgr.validate_repo(path) is True
        assert mgr.validate_repo("/nonexistent") is False
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/services/project_manager_test.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# forge/api/services/project_manager.py
import subprocess
from pathlib import Path

class ProjectManager:
    def __init__(self, projects_dir: str = "~/.forge/projects"):
        self._dir = Path(projects_dir).expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)

    def create_project(self, name: str) -> str:
        path = self._dir / name
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=path, capture_output=True)
        subprocess.run(["git", "branch", "-m", "main"], cwd=path, capture_output=True)
        # Create initial commit
        readme = path / "README.md"
        readme.write_text(f"# {name}\n")
        subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True)
        # Init forge
        forge_dir = path / ".forge"
        forge_dir.mkdir(exist_ok=True)
        return str(path)

    def clone_project(self, url: str) -> str:
        name = url.rstrip("/").split("/")[-1].replace(".git", "")
        path = self._dir / name
        subprocess.run(["git", "clone", url, str(path)], capture_output=True, check=True)
        (path / ".forge").mkdir(exist_ok=True)
        return str(path)

    def list_projects(self) -> list[dict]:
        projects = []
        for p in self._dir.iterdir():
            if p.is_dir() and (p / ".git").exists():
                projects.append({"name": p.name, "path": str(p)})
        return projects

    def validate_repo(self, path: str) -> bool:
        p = Path(path)
        return p.exists() and (p / ".git").exists()
```

**Step 4: Run test**

Run: `pytest forge/api/services/project_manager_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/services/project_manager.py forge/api/services/project_manager_test.py
git commit -m "feat: project manager for create, clone, list, validate"
```

### Task 6.2: Task routes (create task, get status, list tasks)

**Files:**
- Create: `forge/api/routes/tasks.py`
- Create: `forge/api/routes/tasks_test.py`
- Create: `forge/api/models/schemas.py`

**Step 1: Write the failing test**

```python
# forge/api/routes/tasks_test.py
import pytest
from httpx import AsyncClient, ASGITransport
from forge.api.app import create_app

@pytest.mark.asyncio
async def test_create_task():
    app = create_app(db_url="sqlite+aiosqlite:///:memory:")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register and get token
        reg = await client.post("/auth/register", json={
            "email": "a@b.com", "password": "pass123", "display_name": "T",
        })
        token = reg.json()["access_token"]
        # Create task
        resp = await client.post("/tasks", json={
            "description": "Build fibonacci function",
            "project_path": "/tmp/test-project",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "pipeline_id" in resp.json()
```

**Step 2: Run to verify fail**

Run: `pytest forge/api/routes/tasks_test.py -v`
Expected: FAIL

**Step 3: Implement schemas and task routes**

Create `forge/api/models/schemas.py` with `CreateTaskRequest`, `TaskStatusResponse`, `PipelineResponse`.

Create `forge/api/routes/tasks.py` with:
- `POST /tasks` — creates a pipeline, starts daemon in background asyncio task
- `GET /tasks/{pipeline_id}` — returns current task states
- `GET /tasks` — lists all pipelines for user

Wire into `app.py`.

**Step 4: Run test**

Run: `pytest forge/api/routes/tasks_test.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add forge/api/routes/tasks.py forge/api/routes/tasks_test.py forge/api/models/schemas.py
git commit -m "feat: task REST endpoints (create, status, list)"
```

---

## Phase 7: Next.js Frontend Foundation

### Task 7.1: Scaffold Next.js project

**Files:**
- Create: `web/` directory with Next.js boilerplate

**Step 1: Create Next.js app**

Run:
```bash
cd /Users/mtarun/Desktop/SideHustles/claude-does
npx create-next-app@latest web --typescript --tailwind --eslint --app --src-dir --no-import-alias
```

**Step 2: Install additional deps**

```bash
cd web
npm install zustand
npx shadcn@latest init
```

**Step 3: Verify dev server starts**

Run: `cd web && npm run dev` (Ctrl+C after confirming it starts)

**Step 4: Commit**

```bash
git add web/
git commit -m "feat: scaffold Next.js frontend with Tailwind and shadcn"
```

### Task 7.2: Auth pages (login + register)

**Files:**
- Create: `web/src/lib/api.ts` (REST client)
- Create: `web/src/stores/authStore.ts`
- Create: `web/src/app/login/page.tsx`
- Create: `web/src/app/register/page.tsx`

**Step 1: Implement REST client**

```typescript
// web/src/lib/api.ts
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function apiPost(path: string, body: Record<string, unknown>, token?: string) {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

export async function apiGet(path: string, token: string) {
  const res = await fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}
```

**Step 2: Implement auth store**

```typescript
// web/src/stores/authStore.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  token: string | null;
  userId: string | null;
  setAuth: (token: string, userId: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      userId: null,
      setAuth: (token, userId) => set({ token, userId }),
      logout: () => set({ token: null, userId: null }),
    }),
    { name: "forge-auth" }
  )
);
```

**Step 3: Implement login page with form**

Clean login page with email/password fields, submit button, link to register.

**Step 4: Implement register page with form**

Register page with email, password, display name fields.

**Step 5: Verify pages render**

Run: `cd web && npm run build`
Expected: Build succeeds

**Step 6: Commit**

```bash
git add web/src/
git commit -m "feat: auth pages with login, register, and auth store"
```

### Task 7.3: App layout with sidebar

**Files:**
- Modify: `web/src/app/layout.tsx`
- Create: `web/src/components/Sidebar.tsx`
- Create: `web/src/components/AuthGuard.tsx`

**Step 1: Create sidebar component**

Left sidebar with: Forge logo, project list, New Task button, History, Settings, user avatar + logout.

**Step 2: Create AuthGuard**

Wrapper component that redirects to `/login` if no token in auth store.

**Step 3: Wire into layout**

Root layout wraps children in AuthGuard + Sidebar.

**Step 4: Verify build**

Run: `cd web && npm run build`
Expected: PASS

**Step 5: Commit**

```bash
git add web/src/
git commit -m "feat: app layout with sidebar and auth guard"
```

---

## Phase 8: Real-Time Task Dashboard

### Task 8.1: WebSocket hook

**Files:**
- Create: `web/src/hooks/useWebSocket.ts`
- Create: `web/src/lib/ws.ts`

**Step 1: Implement WebSocket client**

```typescript
// web/src/lib/ws.ts
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export function createWebSocket(pipelineId: string, token: string): WebSocket {
  return new WebSocket(`${WS_BASE}/ws/${pipelineId}?token=${token}`);
}
```

**Step 2: Implement useWebSocket hook**

```typescript
// web/src/hooks/useWebSocket.ts
import { useEffect, useRef, useCallback } from "react";
import { createWebSocket } from "@/lib/ws";
import { useAuthStore } from "@/stores/authStore";

export function useWebSocket(pipelineId: string | null, onMessage: (event: any) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const token = useAuthStore((s) => s.token);

  useEffect(() => {
    if (!pipelineId || !token) return;
    const ws = createWebSocket(pipelineId, token);
    wsRef.current = ws;
    ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    ws.onclose = () => { /* reconnection logic with backoff */ };
    return () => ws.close();
  }, [pipelineId, token, onMessage]);

  const send = useCallback((data: any) => {
    wsRef.current?.send(JSON.stringify(data));
  }, []);

  return { send };
}
```

**Step 3: Commit**

```bash
git add web/src/hooks/ web/src/lib/ws.ts
git commit -m "feat: WebSocket hook for real-time pipeline events"
```

### Task 8.2: Task store with WebSocket integration

**Files:**
- Create: `web/src/stores/taskStore.ts`

**Step 1: Implement task store**

```typescript
// web/src/stores/taskStore.ts
import { create } from "zustand";

interface TaskState {
  id: string;
  title: string;
  state: string;
  branch: string;
  files: string[];
  output: string[];
  reviewGates: { gate: number; result: string; details?: string }[];
  mergeResult?: { success: boolean; error?: string; linesAdded?: number };
}

interface PipelineState {
  pipelineId: string | null;
  phase: "idle" | "planning" | "executing" | "complete";
  tasks: Record<string, TaskState>;
  plannerOutput: string[];
  setPipelineId: (id: string) => void;
  handleEvent: (event: { event: string; data: any }) => void;
}

export const useTaskStore = create<PipelineState>((set) => ({
  pipelineId: null,
  phase: "idle",
  tasks: {},
  plannerOutput: [],
  setPipelineId: (id) => set({ pipelineId: id }),
  handleEvent: (event) =>
    set((state) => {
      switch (event.event) {
        case "task:state_changed":
          return {
            tasks: {
              ...state.tasks,
              [event.data.taskId]: {
                ...state.tasks[event.data.taskId],
                state: event.data.newState,
              },
            },
          };
        case "task:agent_output":
          const task = state.tasks[event.data.taskId];
          return {
            tasks: {
              ...state.tasks,
              [event.data.taskId]: {
                ...task,
                output: [...(task?.output || []), event.data.line],
              },
            },
          };
        // ... handle other events
        default:
          return state;
      }
    }),
}));
```

**Step 2: Commit**

```bash
git add web/src/stores/taskStore.ts
git commit -m "feat: task store with WebSocket event handling"
```

### Task 8.3: AgentCard component

**Files:**
- Create: `web/src/components/task/AgentCard.tsx`

**Step 1: Implement AgentCard**

Card component showing:
- Task name + branch badge
- State badge (color-coded: blue=WORKING, yellow=IN_REVIEW, green=DONE, red=ERROR)
- File list
- Scrollable output area with auto-scroll (terminal-style, monospace)
- Review gate indicators (checkmarks/spinners/x)
- Progress estimation bar

**Step 2: Verify build**

Run: `cd web && npm run build`

**Step 3: Commit**

```bash
git add web/src/components/task/
git commit -m "feat: AgentCard component with live output streaming"
```

### Task 8.4: PipelineProgress component

**Files:**
- Create: `web/src/components/task/PipelineProgress.tsx`

**Step 1: Implement**

Horizontal progress bar: Plan > Execute > Review > Merge
Each step highlighted based on current phase. Active step has spinner.

**Step 2: Commit**

```bash
git add web/src/components/task/PipelineProgress.tsx
git commit -m "feat: pipeline progress bar component"
```

### Task 8.5: Task execution page

**Files:**
- Create: `web/src/app/tasks/[id]/page.tsx`

**Step 1: Implement**

Page that:
1. Connects WebSocket via `useWebSocket` hook
2. Routes events to `useTaskStore.handleEvent`
3. Renders PipelineProgress at top
4. Renders grid of AgentCards (one per task)
5. Shows CompletionSummary when all done

**Step 2: Verify build**

Run: `cd web && npm run build`

**Step 3: Commit**

```bash
git add web/src/app/tasks/
git commit -m "feat: task execution page with real-time agent monitoring"
```

### Task 8.6: CompletionSummary component

**Files:**
- Create: `web/src/components/task/CompletionSummary.tsx`

**Step 1: Implement**

Shows: all tasks with status, total lines, files changed, time taken, agent count. Buttons: View Full Diff, Push to GitHub.

**Step 2: Commit**

```bash
git add web/src/components/task/CompletionSummary.tsx
git commit -m "feat: completion summary with stats and actions"
```

---

## Phase 9: Task Creation Flow

### Task 9.1: New task page (multi-step form)

**Files:**
- Create: `web/src/app/tasks/new/page.tsx`
- Create: `web/src/components/task/ProjectSelector.tsx`
- Create: `web/src/components/task/TaskForm.tsx`
- Create: `web/src/components/task/ExecutionTargetSelector.tsx`

**Step 1: Implement ProjectSelector**

Step 1 of form: radio buttons for "Existing local repo", "Clone from GitHub", "Create new". Conditional inputs based on selection.

**Step 2: Implement TaskForm**

Step 2: Rich textarea for task description (markdown). Optional priority selector. Optional context text area.

**Step 3: Implement ExecutionTargetSelector**

Step 4: Toggle between Local and Remote. Remote shows SSH config fields (host, user, key path).

**Step 4: Wire into multi-step page**

`tasks/new/page.tsx` uses stepper pattern — steps 1-6 as defined in design doc. "Run Task" button calls `POST /tasks` and redirects to `/tasks/{pipeline_id}`.

**Step 5: Verify build**

Run: `cd web && npm run build`

**Step 6: Commit**

```bash
git add web/src/app/tasks/new/ web/src/components/task/
git commit -m "feat: multi-step task creation flow"
```

### Task 9.2: Task templates

**Files:**
- Create: `forge/api/services/template_service.py`
- Create: `forge/api/services/template_service_test.py`
- Create: `forge/api/routes/templates.py`
- Create: `web/src/components/task/TemplatePicker.tsx`

**Step 1: Write failing test**

```python
# forge/api/services/template_service_test.py
import pytest
import tempfile
from forge.api.services.template_service import TemplateService

def test_save_and_list_templates():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = TemplateService(templates_dir=tmpdir)
        svc.save("REST API", "Build a REST API with CRUD endpoints", "api")
        templates = svc.list_all()
        assert len(templates) == 1
        assert templates[0]["name"] == "REST API"
```

**Step 2: Implement backend**

**Step 3: Implement frontend TemplatePicker**

Shown at top of task creation form. Pre-fills description.

**Step 4: Commit**

```bash
git add forge/api/services/template_service.py forge/api/services/template_service_test.py forge/api/routes/templates.py web/src/components/task/TemplatePicker.tsx
git commit -m "feat: task templates (save, list, pick)"
```

### Task 9.3: Cost estimation

**Files:**
- Create: `forge/api/services/cost_estimator.py`
- Create: `forge/api/services/cost_estimator_test.py`

**Step 1: Write failing test**

```python
# forge/api/services/cost_estimator_test.py
from forge.api.services.cost_estimator import estimate_cost

def test_estimate_simple_task():
    est = estimate_cost(description="Build fibonacci function", complexity="low")
    assert est["sessions"] >= 3  # planner + agent + reviewer
    assert est["estimated_minutes"] > 0
```

**Step 2: Implement**

Based on heuristics: 1 planner + N agents + N reviewers. Complexity multiplier for time estimate.

**Step 3: Commit**

```bash
git add forge/api/services/cost_estimator.py forge/api/services/cost_estimator_test.py
git commit -m "feat: cost estimation for task creation"
```

---

## Phase 10: Additional Features

### Task 10.1: Diff viewer component

**Files:**
- Install: `npm install react-diff-viewer-continued` in `web/`
- Create: `web/src/components/diff/DiffViewer.tsx`
- Create: `forge/api/routes/diff.py` (endpoint to get diff for pipeline)

**Step 1: Create API endpoint**

`GET /tasks/{pipeline_id}/diff` — returns the combined diff of all merged branches.

**Step 2: Create DiffViewer component**

Side-by-side diff viewer using react-diff-viewer. Used in CompletionSummary and ReviewPanel.

**Step 3: Commit**

```bash
git add web/src/components/diff/ forge/api/routes/diff.py
git commit -m "feat: side-by-side diff viewer"
```

### Task 10.2: Task history page

**Files:**
- Create: `web/src/app/history/page.tsx`
- Create: `forge/api/routes/history.py`

**Step 1: Create API endpoint**

`GET /history` — returns list of past pipeline runs with summary stats.
`GET /history/{pipeline_id}` — returns full detail for a run.

**Step 2: Create history page**

Table of past runs: date, description, status, duration, tasks count. Click to see full detail with agent outputs, diffs, review results.

**Step 3: Commit**

```bash
git add web/src/app/history/ forge/api/routes/history.py
git commit -m "feat: task history page with run details"
```

### Task 10.3: GitHub integration

**Files:**
- Create: `forge/api/services/github_service.py`
- Create: `forge/api/services/github_service_test.py`
- Create: `forge/api/routes/github.py`

**Step 1: Write failing test**

```python
# forge/api/services/github_service_test.py
import pytest
from forge.api.services.github_service import build_pr_description

def test_build_pr_description():
    desc = build_pr_description(
        task="Build fibonacci function",
        subtasks=[{"title": "fib.py", "lines": 42}],
        review_results=[{"gate": 1, "result": "pass"}],
    )
    assert "fibonacci" in desc
    assert "fib.py" in desc
```

**Step 2: Implement**

Service that builds PR description and calls `gh pr create` via subprocess. Uses user's existing GitHub auth (gh CLI).

**Step 3: Add "Push to GitHub" / "Create PR" buttons to CompletionSummary**

**Step 4: Commit**

```bash
git add forge/api/services/github_service.py forge/api/services/github_service_test.py forge/api/routes/github.py
git commit -m "feat: GitHub PR creation from completed tasks"
```

### Task 10.4: Notification system

**Files:**
- Create: `forge/api/services/notification_service.py`
- Create: `web/src/hooks/useNotifications.ts`

**Step 1: Implement browser notifications hook**

```typescript
// web/src/hooks/useNotifications.ts
export function useNotifications() {
  const notify = (title: string, body: string) => {
    if (Notification.permission === "granted") {
      new Notification(title, { body });
    }
  };
  const requestPermission = () => Notification.requestPermission();
  return { notify, requestPermission };
}
```

**Step 2: Wire into task completion**

When `pipeline:complete` event received, trigger browser notification.

**Step 3: Implement backend webhook support**

NotificationService with `send_webhook(url, payload)` for Slack/Discord.

**Step 4: Add notification settings to Settings page**

**Step 5: Commit**

```bash
git add forge/api/services/notification_service.py web/src/hooks/useNotifications.ts
git commit -m "feat: browser notifications and webhook support"
```

### Task 10.5: Settings page

**Files:**
- Create: `web/src/app/settings/page.tsx`
- Create: `forge/api/routes/settings.py`

**Step 1: Create settings API**

`GET /settings` — returns user's settings.
`PUT /settings` — updates settings (max_agents, notification prefs, default execution target, etc.)

**Step 2: Create settings page**

Sections: General (max agents, timeout), Notifications (browser, webhooks), Security (change password), Claude (verify CLI status).

**Step 3: Commit**

```bash
git add web/src/app/settings/ forge/api/routes/settings.py
git commit -m "feat: user settings page"
```

---

## Phase 11: Integration & Polish

### Task 11.1: CLI entry point for web server

**Files:**
- Modify: `forge/cli/main.py` (add `forge serve` command)

**Step 1: Add serve command**

```python
@cli.command()
@click.option("--port", default=8000, help="API server port")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
def serve(port: int, host: str):
    """Start the Forge web server."""
    import uvicorn
    from forge.api.app import create_app
    app = create_app()
    uvicorn.run(app, host=host, port=port)
```

**Step 2: Verify**

Run: `forge serve` — should start FastAPI on port 8000

**Step 3: Commit**

```bash
git add forge/cli/main.py
git commit -m "feat: add 'forge serve' command for web server"
```

### Task 11.2: Run full test suite

**Step 1: Run all Python tests**

Run: `pytest forge/ -v`
Expected: ALL PASS

**Step 2: Run frontend build**

Run: `cd web && npm run build`
Expected: Build succeeds

**Step 3: Run frontend lint**

Run: `cd web && npm run lint`
Expected: No errors

### Task 11.3: Update README

**Files:**
- Modify: `README.md`

Add sections for:
- Web UI setup (`forge serve` + `cd web && npm run dev`)
- Auth setup
- Remote execution config
- Screenshots (placeholder)

**Step 1: Commit**

```bash
git add README.md
git commit -m "docs: update README with web UI setup instructions"
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1 | 2 | P0 bug fix — agent sandboxing |
| 2 | 7 | FastAPI backend (app, auth, rate limiting) |
| 3 | 4 | WebSocket streaming & events |
| 4 | 2 | Execution layer (local + remote) |
| 5 | 2 | Secret scanner & audit log |
| 6 | 2 | Task & project REST API |
| 7 | 3 | Next.js frontend scaffold |
| 8 | 6 | Real-time dashboard |
| 9 | 3 | Task creation flow |
| 10 | 5 | Additional features |
| 11 | 3 | Integration & polish |
| **Total** | **39** | |
