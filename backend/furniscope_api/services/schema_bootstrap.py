"""Apply runtime DDL once at process start, never on request handlers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.workspace_repository import WorkspaceRepository
from .authorized_signals import AuthorizedSignalService
from .competitor_tracking import CompetitorTrackingService
from .notifications import NotificationService
from .password_reset_service import PasswordResetService


async def bootstrap_runtime_schema(session: AsyncSession) -> None:
    await WorkspaceRepository().ensure_schema(session)
    await CompetitorTrackingService().ensure_schema(session)
    await AuthorizedSignalService().ensure_schema(session)
    await NotificationService().ensure_schema(session)
    await PasswordResetService().ensure_schema(session)
