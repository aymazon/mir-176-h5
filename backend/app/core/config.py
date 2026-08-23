"""Application settings (pydantic-settings) and stable cross-layer constants."""

from typing import ClassVar, Final, Literal

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

# 出生城稳定 id（银杏村）。todo 3 建号默认值与 todo 13 最小数据集地图 id 的唯一来源。
# 故意不做成 Settings 字段：不允许被环境变量覆盖，保证跨层引用稳定。
BIRTH_MAP_ID: Final[str] = "yin-xing-cun"


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env", env_prefix="", extra="ignore"
    )

    ENV: Literal["dev", "prod"] = "dev"
    DATABASE_URL: PostgresDsn


# DATABASE_URL 由 .env / 环境变量注入；pyright 静态分析看不到，故忽略 reportCallIssue。
settings = Settings()  # pyright: ignore[reportCallIssue]
