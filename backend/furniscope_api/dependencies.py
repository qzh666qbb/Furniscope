"""Infrastructure dependencies shared by future route modules."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import PaginationParams


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in request.app.state.database.session():
        request.state.db_session = session
        yield session


DatabaseSession = Annotated[AsyncSession, Depends(database_session)]


def pagination_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


Pagination = Annotated[PaginationParams, Depends(pagination_params)]
