"""FastAPI routers for the Forge REST API."""

from forge.api.routes.auth import router as auth_router
from forge.api.routes.diff import router as diff_router
from forge.api.routes.followup import router as followup_router
from forge.api.routes.github import router as github_router
from forge.api.routes.history import router as history_router
from forge.api.routes.settings import router as settings_router
from forge.api.routes.tasks import router as tasks_router
from forge.api.routes.templates import router as templates_router
from forge.api.routes.webhooks import router as webhooks_router

__all__ = [
    "auth_router",
    "diff_router",
    "followup_router",
    "github_router",
    "history_router",
    "settings_router",
    "tasks_router",
    "templates_router",
    "webhooks_router",
]
