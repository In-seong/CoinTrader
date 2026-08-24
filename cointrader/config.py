"""환경설정. .env 파일 또는 환경변수에서 로드한다."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
REPORT_DIR = PROJECT_ROOT / "reports"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    upbit_access_key: str = ""
    upbit_secret_key: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    default_ticker: str = "KRW-BTC"
    default_interval: str = "day"
    fee_rate: float = 0.0005  # 업비트 KRW 마켓 수수료 0.05%
    slippage_rate: float = 0.0005

    @property
    def has_upbit_keys(self) -> bool:
        return bool(self.upbit_access_key and self.upbit_secret_key)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


settings = Settings()
