import uuid

import pytest
from fastapi import HTTPException, Request

from app.security import (
    SessionClaims,
    create_session_token,
    decode_session_token,
    hash_password,
    require_csrf,
    verify_password,
)


def test_passwords_use_argon2_and_verify() -> None:
    password_hash = hash_password("correct horse battery staple")
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, "correct horse battery staple")
    assert not verify_password(password_hash, "wrong password")


def test_signed_session_round_trip() -> None:
    user_id = uuid.uuid4()
    token = create_session_token(user_id, 7)
    assert decode_session_token(token) == SessionClaims(user_id=user_id, session_version=7)
    assert decode_session_token(f"{token}tampered") is None


def test_csrf_requires_matching_cookie_and_header() -> None:
    matching = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"cookie", b"mikurag_csrf=token"), (b"x-csrf-token", b"token")],
        }
    )
    require_csrf(matching)

    mismatched = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"cookie", b"mikurag_csrf=token"), (b"x-csrf-token", b"other")],
        }
    )
    with pytest.raises(HTTPException) as error:
        require_csrf(mismatched)
    assert error.value.status_code == 403

