import hmac
import secrets
import uuid
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

SESSION_COOKIE = "mikurag_session"
CSRF_COOKIE = "mikurag_csrf"

password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


@dataclass(frozen=True)
class SessionClaims:
    user_id: uuid.UUID
    session_version: int


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="mikurag-session-v1")


def create_session_token(user_id: uuid.UUID, session_version: int) -> str:
    return _serializer().dumps({"uid": str(user_id), "sv": session_version})


def decode_session_token(token: str) -> SessionClaims | None:
    try:
        payload = _serializer().loads(token, max_age=get_settings().session_max_age_seconds)
        return SessionClaims(
            user_id=uuid.UUID(payload["uid"]),
            session_version=int(payload["sv"]),
        )
    except (BadSignature, KeyError, TypeError, ValueError, SignatureExpired):
        return None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def require_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("X-CSRF-Token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")


def set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    settings = get_settings()
    secure = settings.environment == "production"
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.session_max_age_seconds,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


def set_csrf_cookie(response: Response, csrf_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.session_max_age_seconds,
        httponly=False,
        secure=settings.environment == "production",
        samesite="strict",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", samesite="strict")
