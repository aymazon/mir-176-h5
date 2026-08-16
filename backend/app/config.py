"""Typed application settings loaded from environment variables (see .env.example).

Defaults mirror ``.env.example`` so the app starts without a ``.env`` file.
"""

from pathlib import Path
from typing import ClassVar, Final

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE: Final[Path] = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Runtime configuration sourced from the environment or ``backend/.env``."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_file=ENV_FILE)

    database_url: str = "postgresql://mir176:mir176@127.0.0.1:5432/mir-176-h5"
    jwt_secret: str = "change-me"
    ws_token_ttl: int = 300
    server_port: int = 8000
    tick_rate_ms: int = 100
    snapshot_ms: int = 200
    ws_auth_timeout_s: float = 5.0
    ws_heartbeat_timeout_s: float = 90.0
    ws_reconnect_buffer_s: float = 5.0
    save_interval_s: int = 30
    xp_rate: float = 1.0
    gold_min: float = 0.8
    gold_max: float = 1.2
    sell_rate: float = 0.5
    log_level: str = "INFO"
    # D14 sensitive-word list; resolved against the server cwd (backend/), so
    # the default points at the repo file deploy/sensitive_words.txt.
    chat_sensitive_words_path: str = "../deploy/sensitive_words.txt"
