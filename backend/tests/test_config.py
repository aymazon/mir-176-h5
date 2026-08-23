"""BIRTH_MAP_ID 是 todo 3 建号默认值与 todo 13 最小数据集银杏村 id 的唯一来源，必须稳定。"""

from app.core.config import BIRTH_MAP_ID, Settings, settings


def test_birth_map_id_constant() -> None:
    assert BIRTH_MAP_ID == "yin-xing-cun"


def test_settings_shape() -> None:
    assert settings.ENV in ("dev", "prod")
    assert str(settings.DATABASE_URL).startswith("postgresql+asyncpg://")


def test_settings_env_validation() -> None:
    s = Settings(ENV="dev", DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db")  # pyright: ignore[reportArgumentType]
    assert s.ENV == "dev"
