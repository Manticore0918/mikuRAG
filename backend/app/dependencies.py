import uuid
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import KnowledgeBaseAccess, User
from app.security import SESSION_COOKIE, decode_session_token

DatabaseSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: DatabaseSession,
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    if not session_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    claims = decode_session_token(session_cookie)
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    user = await session.get(User, claims.user_id)
    if (
        user is None
        or not user.is_enabled
        or user.session_version != claims.session_version
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_administrator(current_user: CurrentUser) -> User:
    if not current_user.is_administrator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return current_user


Administrator = Annotated[User, Depends(require_administrator)]


async def ensure_knowledge_base_access(
    session: AsyncSession,
    current_user: User,
    knowledge_base_id: uuid.UUID,
) -> None:
    if current_user.is_administrator:
        return
    granted = await session.scalar(
        select(
            exists().where(
                KnowledgeBaseAccess.user_id == current_user.id,
                KnowledgeBaseAccess.knowledge_base_id == knowledge_base_id,
            )
        )
    )
    if not granted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge Base not found"
        )
