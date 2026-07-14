from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select

from app.dependencies import CurrentUser, DatabaseSession
from app.models import User
from app.rate_limit import login_rate_limiter
from app.schemas import LoginRequest, UserRead
from app.security import (
    clear_auth_cookies,
    create_session_token,
    hash_password,
    new_csrf_token,
    require_csrf,
    set_auth_cookies,
    set_csrf_cookie,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
_DUMMY_PASSWORD_HASH = hash_password("not-the-password-for-any-real-account")


@router.get("/csrf")
async def csrf(response: Response) -> dict[str, str]:
    token = new_csrf_token()
    set_csrf_cookie(response, token)
    return {"csrf_token": token}


@router.post("/login", response_model=UserRead)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DatabaseSession,
    _: Annotated[None, Depends(require_csrf)],
) -> User:
    client_host = request.client.host if request.client else "unknown"
    limit_key = f"login:{client_host}:{payload.username}"
    await login_rate_limiter.ensure_allowed(limit_key)

    user = await session.scalar(
        select(User).where(func.lower(User.username) == payload.username.lower())
    )
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    valid_password = verify_password(password_hash, payload.password)
    if user is None or not valid_password or not user.is_enabled:
        await login_rate_limiter.record_failure(limit_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    await login_rate_limiter.clear(limit_key)
    csrf_token = new_csrf_token()
    set_auth_cookies(
        response,
        create_session_token(user.id, user.session_version),
        csrf_token,
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUser) -> User:
    return current_user
