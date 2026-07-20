import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import Administrator, DatabaseSession
from app.models import (
    Document,
    KnowledgeBase,
    KnowledgeBaseAccess,
    UploadSession,
    UploadSessionStatus,
    User,
)
from app.schemas import (
    AccessGrantRead,
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
    PasswordReset,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.security import hash_password, require_csrf

router = APIRouter(prefix="/admin", tags=["administration"])
CsrfCheck = Annotated[None, Depends(require_csrf)]


async def commit_or_conflict(session: DatabaseSession, detail: str) -> None:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from error


@router.get("/users", response_model=list[UserRead])
async def list_users(_: Administrator, session: DatabaseSession) -> list[User]:
    result = await session.scalars(select(User).order_by(User.username))
    return list(result)


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> User:
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        is_administrator=payload.is_administrator,
        is_enabled=True,
    )
    session.add(user)
    await commit_or_conflict(session, "Username already exists")
    await session.refresh(user)
    return user


async def require_user(session: DatabaseSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def protect_last_administrator(
    session: DatabaseSession,
    user: User,
    update: UserUpdate,
) -> None:
    removes_admin = user.is_administrator and (
        update.is_administrator is False or update.is_enabled is False
    )
    if not removes_admin:
        return
    enabled_admins = await session.scalar(
        select(func.count()).select_from(User).where(
            User.is_administrator.is_(True), User.is_enabled.is_(True)
        )
    )
    if enabled_admins is not None and enabled_admins <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last enabled Administrator cannot be disabled or demoted",
        )


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> User:
    user = await require_user(session, user_id)
    await protect_last_administrator(session, user, payload)
    invalidates_session = False
    if payload.is_enabled is not None and payload.is_enabled != user.is_enabled:
        user.is_enabled = payload.is_enabled
        invalidates_session = not payload.is_enabled
    if (
        payload.is_administrator is not None
        and payload.is_administrator != user.is_administrator
    ):
        user.is_administrator = payload.is_administrator
        invalidates_session = True
    if invalidates_session:
        user.session_version += 1
    await session.commit()
    await session.refresh(user)
    return user


@router.put("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: uuid.UUID,
    payload: PasswordReset,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> Response:
    user = await require_user(session, user_id)
    user.password_hash = hash_password(payload.password)
    user.session_version += 1
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseRead])
async def list_knowledge_bases(
    _: Administrator,
    session: DatabaseSession,
) -> list[KnowledgeBase]:
    result = await session.scalars(select(KnowledgeBase).order_by(KnowledgeBase.name))
    return list(result)


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> KnowledgeBase:
    knowledge_base = KnowledgeBase(name=payload.name, description=payload.description)
    session.add(knowledge_base)
    await commit_or_conflict(session, "Knowledge Base name already exists")
    await session.refresh(knowledge_base)
    return knowledge_base


async def require_knowledge_base(
    session: DatabaseSession, knowledge_base_id: uuid.UUID
) -> KnowledgeBase:
    knowledge_base = await session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge Base not found"
        )
    return knowledge_base


@router.patch("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseRead)
async def update_knowledge_base(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseUpdate,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> KnowledgeBase:
    knowledge_base = await require_knowledge_base(session, knowledge_base_id)
    if payload.name is not None:
        knowledge_base.name = payload.name
    if "description" in payload.model_fields_set:
        knowledge_base.description = payload.description
    await commit_or_conflict(session, "Knowledge Base name already exists")
    await session.refresh(knowledge_base)
    return knowledge_base


@router.delete("/knowledge-bases/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    knowledge_base_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> Response:
    knowledge_base = await require_knowledge_base(session, knowledge_base_id)
    document_count = await session.scalar(
        select(func.count()).select_from(Document).where(
            Document.knowledge_base_id == knowledge_base_id
        )
    )
    if document_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Delete every Document in this Knowledge Base before deleting it",
        )
    upload_count = await session.scalar(
        select(func.count()).select_from(UploadSession).where(
            UploadSession.knowledge_base_id == knowledge_base_id,
            UploadSession.status == UploadSessionStatus.OPEN,
            UploadSession.expires_at > datetime.now(UTC),
        )
    )
    if upload_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancel every active Upload Session before deleting this Knowledge Base",
        )
    await session.delete(knowledge_base)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/knowledge-bases/{knowledge_base_id}/access", response_model=list[AccessGrantRead])
async def list_access_grants(
    knowledge_base_id: uuid.UUID,
    _: Administrator,
    session: DatabaseSession,
) -> list[KnowledgeBaseAccess]:
    await require_knowledge_base(session, knowledge_base_id)
    result = await session.scalars(
        select(KnowledgeBaseAccess).where(
            KnowledgeBaseAccess.knowledge_base_id == knowledge_base_id
        )
    )
    return list(result)


@router.put(
    "/knowledge-bases/{knowledge_base_id}/access/{user_id}",
    response_model=AccessGrantRead,
)
async def grant_access(
    knowledge_base_id: uuid.UUID,
    user_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> KnowledgeBaseAccess:
    await require_knowledge_base(session, knowledge_base_id)
    await require_user(session, user_id)
    grant = await session.get(
        KnowledgeBaseAccess,
        {"user_id": user_id, "knowledge_base_id": knowledge_base_id},
    )
    if grant is None:
        grant = KnowledgeBaseAccess(user_id=user_id, knowledge_base_id=knowledge_base_id)
        session.add(grant)
        await session.commit()
        await session.refresh(grant)
    return grant


@router.delete(
    "/knowledge-bases/{knowledge_base_id}/access/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_access(
    knowledge_base_id: uuid.UUID,
    user_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> Response:
    await session.execute(
        delete(KnowledgeBaseAccess).where(
            KnowledgeBaseAccess.knowledge_base_id == knowledge_base_id,
            KnowledgeBaseAccess.user_id == user_id,
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
