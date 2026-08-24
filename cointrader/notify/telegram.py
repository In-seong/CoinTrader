"""텔레그램 알림. 토큰/chat_id 미설정 시 콘솔 출력만 한다."""

from __future__ import annotations

import logging

import requests

from cointrader.config import settings

log = logging.getLogger(__name__)


def notify(text: str, silent: bool = False) -> bool:
    """메시지 전송. 성공 시 True. 설정이 없으면 False (예외 없음)."""
    if not settings.has_telegram:
        log.info("[telegram 미설정] %s", text)
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning("telegram send failed: %s", e)
        return False
