"""GitHub integration endpoints: create pull requests."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from forge.api.security.dependencies import get_current_user
from forge.api.services.github_service import build_pr_description, create_pr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])


class CreatePrRequest(BaseModel):
    """Request body for creating a GitHub PR."""

    repo_path: str
    branch: str
    title: str
    task_description: str = ""
    subtasks: list[dict] = []
    review_results: dict | None = None


@router.post("/pr")
async def create_pull_request(
    body: CreatePrRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Create a GitHub pull request via the gh CLI.

    Builds a PR description from task info and calls ``gh pr create``.
    """
    pr_body = build_pr_description(
        task={"description": body.task_description},
        subtasks=body.subtasks,
        review_results=body.review_results,
    )

    try:
        result = await create_pr(
            repo_path=body.repo_path,
            branch=body.branch,
            title=body.title,
            body=pr_body,
        )
    except RuntimeError as exc:
        logger.error("GitHub PR creation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="GitHub operation failed")

    return result
