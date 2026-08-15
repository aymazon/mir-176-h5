"""Skeleton tests: health endpoint and settings defaults."""

from typing import ClassVar, Final

import pytest
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict

from app.config import Settings
from app.main import app

_ENV_KEYS: Final[tuple[str, ...]] = (
    "DATABASE_URL",
    "JWT_SECRET",
    "WS_TOKEN_TTL",
    "SERVER_PORT",
    "TICK_RATE_MS",
    "SNAPSHOT_MS",
    "SAVE_INTERVAL_S",
    "XP_RATE",
    "GOLD_MIN",
    "GOLD_MAX",
    "SELL_RATE",
    "LOG_LEVEL",
)


class _DefaultsSettings(Settings):
    """Settings variant with env-file loading disabled to test pure defaults."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_file=None)


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_settings_load_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    settings = _DefaultsSettings()
    assert settings.database_url == "postgresql://mir176:mir176@127.0.0.1:5432/mir-176-h5"
    assert settings.jwt_secret == "change-me"
    assert settings.ws_token_ttl == 300
    assert settings.server_port == 8000
    assert settings.tick_rate_ms == 100
    assert settings.snapshot_ms == 200
    assert settings.save_interval_s == 30
    assert settings.xp_rate == 1.0
    assert settings.gold_min == 0.8
    assert settings.gold_max == 1.2
    assert settings.sell_rate == 0.5
    assert settings.log_level == "INFO"
