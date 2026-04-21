from __future__ import annotations

import secrets

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlmodel import Session, select

from .config import settings
from .db import get_engine
from .models import User

security = HTTPBasic()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def _ensure_bootstrap_admin(name: str, env_pass_hash: str) -> User:
    """Create the env-configured admin user in the DB on first request.

    Idempotent. Subsequent calls return the existing row. The admin's password
    in the DB tracks the env hash on bootstrap; thereafter it is managed via
    /api/admin/users (so an admin password rotation does NOT require a redeploy).
    """
    with Session(get_engine()) as s:
        u = s.exec(select(User).where(User.name == name)).first()
        if u is None:
            u = User(name=name, pass_hash=env_pass_hash, is_admin=True)
            s.add(u)
            s.commit()
            s.refresh(u)
        elif not u.is_admin or not u.pass_hash:
            # Existing pre-multi-user row: promote and seed password from env.
            u.is_admin = True
            if not u.pass_hash:
                u.pass_hash = env_pass_hash
            s.add(u)
            s.commit()
            s.refresh(u)
        return u


def _lookup_user(name: str) -> User | None:
    with Session(get_engine()) as s:
        return s.exec(select(User).where(User.name == name)).first()


def require_user(creds: HTTPBasicCredentials = Depends(security)) -> str:
    """Authenticate via DB-stored bcrypt hash.

    Special case: if the request username matches the env-configured admin
    AND that user does not yet exist in the DB, bootstrap them with the env
    password hash. This keeps the legacy single-user setup working on first
    boot without requiring an out-of-band setup step.
    """
    name = creds.username
    user = _lookup_user(name)
    if user is None and secrets.compare_digest(name, settings.basic_auth_user):
        # Bootstrap path: validate against env hash, then persist as admin.
        if verify_password(creds.password, settings.basic_auth_pass_hash):
            user = _ensure_bootstrap_admin(name, settings.basic_auth_pass_hash)
        else:
            _unauthorized()
    if user is None or not verify_password(creds.password, user.pass_hash):
        _unauthorized()
    return user.name  # type: ignore[union-attr]


def require_admin(user: str = Depends(require_user)) -> str:
    u = _lookup_user(user)
    if u is None or not u.is_admin:
        raise HTTPException(403, "admin role required")
    return user


def _unauthorized() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Basic"},
    )
