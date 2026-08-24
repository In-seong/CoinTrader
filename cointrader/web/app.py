"""모의투자 대시보드 (읽기 전용).

  uv run uvicorn cointrader.web.app:app --host 127.0.0.1 --port 4300

data/bots/ 의 기록과 bots.yaml 을 읽어 JSON 으로 내보내고, / 에서 정적 대시보드를 서빙한다.
.env 에 DASHBOARD_USER / DASHBOARD_PASSWORD 를 두면 HTTP Basic 인증을 건다.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import pyupbit
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from cointrader.config import PROJECT_ROOT
from cointrader.data.upbit_client import INTERVALS
from cointrader.trading.multi_runner import DEFAULT_BOTS_FILE, load_bot_configs
from cointrader.trading.portfolio import BOT_DATA_DIR, BotConfig
from cointrader.trading.status import BotStatus, collect

STATIC = Path(__file__).parent / "static"
LOG_FILE = Path(os.environ.get("COINTRADER_LOG", "/var/log/cointrader.log"))
BOTS_FILE = Path(os.environ.get("COINTRADER_BOTS", DEFAULT_BOTS_FILE))

app = FastAPI(title="CoinTrader dashboard", docs_url=None, redoc_url=None)
_basic = HTTPBasic(auto_error=False)


def _auth(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    user, pw = os.environ.get("DASHBOARD_USER", ""), os.environ.get("DASHBOARD_PASSWORD", "")
    if not pw:
        return
    ok = creds is not None and secrets.compare_digest(creds.username, user) \
        and secrets.compare_digest(creds.password, pw)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})


# ---- 시세 캐시 -------------------------------------------------------------
_price_cache: dict[str, Any] = {"t": 0.0, "prices": {}}


def _prices(tickers: list[str]) -> dict[str, float]:
    now = time.time()
    if os.environ.get("COINTRADER_OFFLINE") or not tickers:  # 개발용: 거래소 호출 생략
        return _price_cache["prices"]
    if now - _price_cache["t"] < 10 and set(tickers) <= set(_price_cache["prices"]):
        return _price_cache["prices"]
    try:
        raw = pyupbit.get_current_price(tickers)
        prices = {tickers[0]: float(raw)} if isinstance(raw, (int, float)) else {k: float(v) for k, v in (raw or {}).items()}
        _price_cache.update(t=now, prices=prices)
    except Exception:
        prices = _price_cache["prices"]
    return prices


def _group(b: BotConfig) -> str:
    if INTERVALS.get(b.interval, 1440) < 60:
        return "단타"
    if b.strategy == "ensemble":
        return "전략 조합"
    if len(b.tickers) > 1:
        return "멀티코인"
    return "단독 전략"


def _configs() -> tuple[list[BotConfig], dict[str, Any]]:
    return load_bot_configs(BOTS_FILE)


def _bot_json(s: BotStatus, cfg: BotConfig | None) -> dict[str, Any]:
    return {
        "name": s.name,
        "group": _group(cfg) if cfg else "기타",
        "strategy": cfg.strategy if cfg else "?",
        "strategy_desc": repr(cfg.build_strategy()) if cfg else "",
        "params": cfg.params if cfg else {},
        "interval": cfg.interval if cfg else "",
        "tickers": cfg.tickers if cfg else [],
        "max_positions": cfg.max_positions if cfg else 1,
        "stop_loss": cfg.stop_loss if cfg else None,
        "take_profit": cfg.take_profit if cfg else None,
        "enabled": cfg.enabled if cfg else False,
        "initial_cash": s.initial_cash,
        "cash": s.cash,
        "equity": s.equity,
        "return": s.total_return,
        "realized_pnl": s.realized_pnl,
        "fees": s.fees,
        "n_trades": s.n_trades,
        "win_rate": s.win_rate,
        "positions": [{"ticker": tk, **p} for tk, p in s.positions.items()],
        "last_trade": s.last_trade,
        "started": s.started,
    }


def _read_trades(limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not BOT_DATA_DIR.exists():
        return rows
    for d in BOT_DATA_DIR.iterdir():
        f = d / "trades.jsonl"
        if f.exists():
            for line in f.read_text().splitlines()[-limit:]:
                if line.strip():
                    r = json.loads(line)
                    r["bot"] = d.name
                    rows.append(r)
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows[:limit]


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _read_log(lines: int) -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64_000))
            text = f.read().decode("utf-8", "ignore")
    except OSError:
        return []
    out = [re.sub(r"\s{2,}", " ", _ANSI.sub("", ln)).strip() for ln in text.splitlines()]
    return [ln for ln in out if ln][-lines:]


# ---- 라우트 ----------------------------------------------------------------
@app.get("/", dependencies=[Depends(_auth)])
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/overview", dependencies=[Depends(_auth)])
def overview(trades: int = Query(60, le=500), log: int = Query(40, le=400)):
    cfgs, meta = _configs()
    by_name = {c.name: c for c in cfgs}
    tickers = sorted({tk for c in cfgs for tk in c.tickers})
    prices = _prices(tickers)
    statuses = collect({c.name: c.cash for c in cfgs}, prices)
    bots = [_bot_json(s, by_name.get(s.name)) for s in statuses]
    # 기록이 아직 없는 봇도 목록에 보여준다
    seen = {b["name"] for b in bots}
    for c in cfgs:
        if c.name not in seen and c.enabled:
            bots.append(_bot_json(BotStatus(c.name, c.cash, c.cash, c.cash, {}, 0, 0, 0.0, 0.0, None, None), c))
    bots.sort(key=lambda b: b["return"], reverse=True)
    return {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "poll": meta.get("poll", 60),
        "universe": meta.get("universe", []),
        "prices": prices,
        "bots": bots,
        "trades": _read_trades(trades),
        "log": _read_log(log),
        "runner_alive": LOG_FILE.exists() and time.time() - LOG_FILE.stat().st_mtime < 600,
    }


@app.get("/api/equity", dependencies=[Depends(_auth)])
def equity(points: int = Query(400, le=5000)):
    cfgs, _ = _configs()
    statuses = collect({c.name: c.cash for c in cfgs}, {})
    series: dict[str, list[list[Any]]] = {}
    for s in statuses:
        c = s.equity_curve
        if c.empty:
            continue
        if len(c) > points:
            c = c.iloc[:: max(1, len(c) // points)]
        series[s.name] = [[t.isoformat(timespec="minutes"), round(v / s.initial_cash * 100 - 100, 3)]
                          for t, v in c.items()]
    return series


@app.get("/api/config", dependencies=[Depends(_auth)])
def config_text():
    return JSONResponse({"path": str(BOTS_FILE), "yaml": BOTS_FILE.read_text()})


@app.get("/api/health")
def health():
    return {"ok": True}
