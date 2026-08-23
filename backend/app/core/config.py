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

    # SSE 通道参数（todo 5）：ring buffer 容量 = 每 tick 事件上限 × 目标重放窗口
    # （默认 1200 ≈ 5min × 60s × 4 事件/tick）；ping/send_timeout 直通 sse-starlette；
    # 宽限期 = SSE 断开后会话存活窗口（超时由 todo 15 取消 tick task）；
    # SSE_MOCK_INTERVAL 仅 dev 模拟日志源的产出间隔（测试可调快）。
    SSE_BUFFER_SIZE: int = 1200
    SSE_PING_INTERVAL: float = 15.0
    SSE_SEND_TIMEOUT: float = 30.0
    SSE_GRACE_PERIOD_SECONDS: float = 60.0
    SSE_MOCK_INTERVAL: float = 1.0


# DATABASE_URL 由 .env / 环境变量注入；pyright 静态分析看不到，故忽略 reportCallIssue。
settings = Settings()  # pyright: ignore[reportCallIssue]
