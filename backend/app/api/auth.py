"""REST authentication and public config endpoints.

Register/login mint an HS256 JWT (7-day expiry); ``POST /ws_token`` exchanges
a valid JWT for a one-time WebSocket credential. The credential is stored
only as a sha256 digest in ``account_session`` with a 5-minute TTL (design
2.6, 3.5; decisions D12, D22). ``verify_ws_token`` is the seam the WS
gateway (W1T3) consumes for one-time validation.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, ClassVar, Final

import asyncpg
import bcrypt
import jwt
from anyio.to_thread import run_sync
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings
from app.db.pool import get_pool

JWT_ALGORITHM: Final[str] = "HS256"
JWT_TTL: Final[timedelta] = timedelta(days=7)
BCRYPT_ROUNDS: Final[int] = 12
_BEARER_PREFIX: Final[str] = "Bearer "
_BCRYPT_MAX_BYTES: Final[int] = 72
APP_VERSION: Final[str] = "0.1.0"
NOTICE: Final[str] = "Welcome to the mir-176-h5 MVP."


def _reject_oversized_password(value: str) -> str:
    """Reject passwords whose UTF-8 encoding exceeds bcrypt's 72-byte limit."""
    if len(value.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise ValueError(f"password exceeds the {_BCRYPT_MAX_BYTES}-byte bcrypt limit")
    return value


class CredentialsRequest(BaseModel):
    """Shared credential payload for register and login (D22 bounds)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=64)

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        return _reject_oversized_password(value)


class RegisterResponse(BaseModel):
    """Created account identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: int
    username: str


class LoginResponse(BaseModel):
    """Bearer JWT issued on successful login."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    token: str
    token_type: str = "bearer"


class WsTokenResponse(BaseModel):
    """One-time WebSocket credential and its TTL in seconds."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    ws_token: str
    expires_in: int


class PublicConfig(BaseModel):
    """Client-facing build info (design 3.5: version, notice, tick rate)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    version: str
    notice: str
    tick_ms: int


@dataclass(frozen=True, slots=True)
class JwtClaims:
    """Trusted, decoded account identity carried by a valid JWT."""

    account_id: int
    username: str


def _hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt cost 12 (blocking; run in a worker)."""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def _verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a bcrypt hash (blocking; run in a worker)."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))


def _issue_jwt(account_id: int, username: str, secret: str) -> str:
    """Mint an HS256 account JWT valid for seven days."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(account_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + JWT_TTL).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


async def require_account(
    authorization: Annotated[str | None, Header()] = None,
) -> JwtClaims:
    """FastAPI dependency: validate the Bearer JWT and return its account."""
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix(_BEARER_PREFIX).strip()
    try:
        payload: dict[str, object] = jwt.decode(
            token, Settings().jwt_secret, algorithms=[JWT_ALGORITHM]
        )
        raw_account_id: object = payload["sub"]
        raw_username: object = payload["username"]
        if not isinstance(raw_account_id, str) or not isinstance(raw_username, str):
            raise HTTPException(status_code=401, detail="invalid or expired token")
        return JwtClaims(account_id=int(raw_account_id), username=raw_username)
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="invalid or expired token") from None


async def verify_ws_token(pool: asyncpg.Pool, ws_token: str) -> int | None:
    """Return the account id if the one-time ws_token is unspent and unexpired.

    The WS gateway (W1T3) consumes this on connect; a token whose
    ``account_session`` row is missing or past ``expires_at`` yields ``None``.
    """
    token_hash = hashlib.sha256(ws_token.encode("ascii")).hexdigest()
    row = await pool.fetchrow(
        "SELECT account_id, expires_at FROM account_session WHERE ws_token_hash = $1",
        token_hash,
    )
    if row is None or row["expires_at"] <= datetime.now(UTC):
        return None
    return int(row["account_id"])


router = APIRouter()


@router.post("/auth/register", status_code=201, response_model=RegisterResponse)
async def register(body: CredentialsRequest) -> RegisterResponse:
    """Create an account; 409 when the username is already taken."""
    pool = get_pool()
    password_hash = await run_sync(_hash_password, body.password)
    try:
        row = await pool.fetchrow(
            "INSERT INTO account (username, password_hash) VALUES ($1, $2) RETURNING id, username",
            body.username,
            password_hash,
        )
    except asyncpg.UniqueViolationError as error:
        raise HTTPException(status_code=409, detail="username already taken") from error
    if row is None:
        raise HTTPException(status_code=500, detail="register insert returned no row")
    return RegisterResponse(id=int(row["id"]), username=row["username"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: CredentialsRequest) -> LoginResponse:
    """Verify credentials and issue a seven-day JWT; 401 on bad credentials."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, username, password_hash FROM account WHERE username = $1",
        body.username,
    )
    if row is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    verified = await run_sync(_verify_password, body.password, row["password_hash"])
    if not verified:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = _issue_jwt(int(row["id"]), row["username"], Settings().jwt_secret)
    return LoginResponse(token=token)


@router.post("/auth/ws_token", response_model=WsTokenResponse)
async def issue_ws_token(
    claims: Annotated[JwtClaims, Depends(require_account)],
) -> WsTokenResponse:
    """Exchange a valid JWT for a one-time ws_token (TTL from settings)."""
    settings = Settings()
    pool = get_pool()
    now = datetime.now(UTC)
    # Lazy cleanup of expired one-time credentials (design 2.6).
    await pool.execute("DELETE FROM account_session WHERE expires_at < $1", now)
    ws_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(ws_token.encode("ascii")).hexdigest()
    await pool.execute(
        "INSERT INTO account_session (ws_token_hash, account_id, expires_at) VALUES ($1, $2, $3)",
        token_hash,
        claims.account_id,
        now + timedelta(seconds=settings.ws_token_ttl),
    )
    return WsTokenResponse(ws_token=ws_token, expires_in=settings.ws_token_ttl)


@router.get("/config", response_model=PublicConfig)
async def public_config() -> PublicConfig:
    """Public client config: build version, notice text, and tick rate."""
    return PublicConfig(version=APP_VERSION, notice=NOTICE, tick_ms=Settings().tick_rate_ms)
