"""The notification bell.

NO "ALL NOTIFICATIONS" VIEW, and that is the whole design. Every listing intersects
the caller against the audience a notification named when it was written — a person,
or the role they act as. An unaddressed list would hand a Developer the Organization
Admin's queue, and a request title says which unit is over budget.

The permission floor is `artifact:view`: everyone signed in has a bell. What differs
between two people is not whether they may read notifications but which ones exist for
them, and that is answered by the address on the row rather than by a permission.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.authz.effective_role import effective_platform_role
from shared.db import get_db_session
from shared.services import notifications as service

logger = logging.getLogger(__name__)

notifications_router = APIRouter(
    prefix="/notifications",
    dependencies=[Depends(require_permission("artifact:view"))],
)


def _user_id(request: Request) -> str:
    uid = getattr(request.state, "user_id", "") or ""
    if not uid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return uid


@notifications_router.get("")
async def list_notifications(
    request: Request,
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return await service.list_for(
        db,
        user_id=_user_id(request),
        role=await effective_platform_role(db, request),
        unread_only=unread_only,
    )


@notifications_router.post("/read")
async def mark_all_read(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict[str, int]:
    """Mark everything currently addressed to this viewer as read.

    Bounded to the same set the listing returns, so this cannot clear a queue the
    caller cannot see.
    """
    count = await service.mark_read(
        db,
        user_id=_user_id(request),
        role=await effective_platform_role(db, request),
    )
    return {"marked": count}
