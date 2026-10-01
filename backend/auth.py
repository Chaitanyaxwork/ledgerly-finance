"""
Authentication: password hashing (bcrypt, used directly — passlib is broken
against modern bcrypt), signed login tokens (JWT), the FastAPI dependency that
turns a token into the current user, and a small brute-force throttle.
"""

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import config
from .db_engine import get_db
from .models import User

bearer_scheme = HTTPBearer(auto_error=False)

# A valid bcrypt hash of a random value. Verifying against it when the email
# doesn't exist keeps login timing the same, so attackers can't use response
# time to discover which emails have accounts.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), (password_hash or _DUMMY_HASH).encode("utf-8")) and bool(password_hash)
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _unauthorized("Please sign in to continue.")
    try:
        payload = jwt.decode(
            credentials.credentials,
            config.SECRET_KEY,
            algorithms=[config.JWT_ALGORITHM],  # never trust the algorithm named in the token
            options={"require": ["exp", "sub"]},
        )
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _unauthorized("Your session has expired. Please sign in again.")
    user = session.get(User, user_id)
    if user is None:
        raise _unauthorized("Your session has expired. Please sign in again.")
    return user


# ----------------------------------------------------------------------
# Login throttle: max failed attempts per key inside a rolling window.
# In-memory, so it is per server process — good protection for a single
# instance; put a shared store (or a reverse-proxy rate limit) in front if you
# scale to several instances.
# ----------------------------------------------------------------------
MAX_FAILED_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60
_failures: dict[str, deque] = defaultdict(deque)


def _prune(key: str, now: float) -> deque:
    q = _failures[key]
    while q and now - q[0] > WINDOW_SECONDS:
        q.popleft()
    return q


def check_login_allowed(key: str) -> None:
    now = time.monotonic()
    q = _prune(key, now)
    if len(q) >= MAX_FAILED_ATTEMPTS:
        retry = int(WINDOW_SECONDS - (now - q[0])) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed sign-in attempts. Try again in {max(retry // 60, 1)} minute(s).",
            headers={"Retry-After": str(retry)},
        )


def record_login_failure(key: str) -> None:
    _failures[key].append(time.monotonic())


def clear_login_failures(key: str) -> None:
    _failures.pop(key, None)
