# CoinTrader

업비트(Upbit) 현물 자동매매 봇. 전략을 플러그인처럼 갈아끼우고, 같은 전략 코드로 **백테스트 → 모의매매(paper) → 실거래(live)** 를 단계적으로 진행한다.

## 설치

```bash
uv sync                      # Python 3.13 가상환경 + 의존성
cp .env.example .env         # API 키·텔레그램 설정 (백테스트만 할 땐 비워둬도 됨)
```

## 사용

```bash
uv run cointrader strategies                                  # 전략 목록
uv run cointrader fetch KRW-BTC -i day -n 1500                # 데이터 수집 (data/cache 에 parquet 캐시)
uv run cointrader backtest KRW-BTC -s volatility_breakout -p k=0.5 --stop-loss 0.03
uv run cointrader compare KRW-BTC -i minute60 -n 2000         # 전략 전부 비교
uv run cointrader optimize KRW-BTC -s rsi -p period=7,14,21 -p buy_below=25,30,35
uv run cointrader run -s volatility_breakout --mode paper     # 모의매매 (실시간 시세, 가상 잔고)
uv run cointrader run -s volatility_breakout --mode live      # 실거래 (확인 프롬프트 있음)
uv run cointrader paper-status / paper-reset                  # 모의 계좌 조회/초기화
uv run cointrader notify-test                                 # 텔레그램 설정 확인
uv run cointrader import-csv data.csv KRW-BTC -i day          # 외부 CSV 로 백테스트 (API 차단 환경)
```

`-i` 봉 간격: `minute1/3/5/10/15/30/60/240`, `day`, `week`

## 멀티봇 모의투자 (`bots.yaml`)

여러 봇을 한 프로세스에서 동시에 돌려 전략·조합·코인 구성을 비교한다. 봇마다 독립 가상계좌·거래기록·자산곡선(`data/bots/<name>/`).

```bash
uv run cointrader bots                 # bots.yaml 전체 구동 (Ctrl+C 로 중지, 상태는 파일에 유지)
uv run cointrader bots --once          # 한 사이클만 (설정 검증)
uv run cointrader bots-status          # 봇별 수익률·거래·보유 비교표 + reports/bots_equity.png
uv run cointrader bots-reset [이름]    # 기록 삭제 후 처음부터
uv run cointrader bots-why             # 봇별 현재 지표값 vs 진입 조건, 최근 24h 시그널 횟수 (왜 조용한지)
uv run cointrader simulate --days 365  # bots.yaml 봇 세트를 과거 N일로 시뮬레이션 (멀티코인·슬롯·손절 동일 규칙)
```

`enter_on_start`(기본 true): 봇 시작 시 전략이 이미 보유 구간이면 즉시 진입. 추세 전략은 크로스 봉에서만 시그널을 내므로 이게 없으면 다음 크로스까지 대기만 한다.

```yaml
bots:
  - name: solo_ma                       # 단일 전략 · 단일 코인
    strategy: ma_cross
    params: {fast: 5, slow: 20}
    tickers: KRW-BTC
  - name: combo                         # 전략 합성: all(전부 동의) | majority(과반) | any(하나라도)
    strategy: ensemble
    params: {strategies: "ma_cross,rsi", mode: all,
             sub_params: {ma_cross: {fast: 10}}}
    tickers: KRW-BTC
  - name: multi                         # 유니버스 스캔, 조건 맞는 코인에 최대 3개 분산
    strategy: ma_cross
    tickers: universe
    max_positions: 3
    stop_loss: 0.03
    interval: minute15                  # 봇별 봉 간격
```

서버 구동: `deploy/cointrader-paper.service` (systemd). 배포는 `SSHPASS=... ./scripts/deploy.sh`.
일일 리포트: `cointrader report` — 봇 순위·24h 변화·시장 대비를 출력하고 텔레그램 설정 시 전송 (`deploy/cointrader-report.cron` → 매일 09:05).

**실거래(live) 전환**: `.env` 에 업비트 키(자산조회+주문만, 출금 X, IP 허용) → bots.yaml 봇에 `mode: live` (반드시 새 이름, `cash`=실투입 예산) → 재시작. 실계좌 하나를 봇별 가상 서브계좌로 나눠 쓰며(자기 예산·자기 수량만 관리), live 봇 예산 합이 실잔고를 넘으면 시작 시 경고.

## 내장 전략

| 이름 | 유형 | 요약 |
|---|---|---|
| `volatility_breakout` | 돌파 | 시가 + 전봉변동폭×k 돌파 매수, 다음 봉 시가 매도 |
| `vb_plus` | 돌파 | 노이즈 기반 k, 추세·거래량 필터, 이평 위 보유 연장 |
| `ma_cross` | 추세 | 단기/장기 이평 골든·데드크로스 |
| `ma_atr` | 추세 | EMA 크로스 + ATR 트레일링 스탑 |
| `rsi` | 역추세 | RSI 과매도 매수, 과매수 매도 |
| `bollinger` | 역추세 | 하단 밴드 이탈 매수, 중심선 회복 매도 |
| `momentum` | 코인 간 비교 | 최근 수익률 상위 N개 로테이션, 전부 약세면 현금 (`cross_sectional`) |
| `ensemble` | 합성 | 여러 전략 보유 상태를 all/majority/any 로 합성 |

## 전략 추가

`cointrader/strategies/` 에 파일을 만들고 `@register` 를 붙이면 끝. CLI·백테스트·실시간 러너에 자동 등록된다.

```python
from . import register
from .base import Strategy

@register
class MyStrategy(Strategy):
    name = "my_strategy"
    description = "설명"
    params = {"period": 20}          # 기본값. CLI 에서 -p period=30 으로 덮어씀

    @property
    def warmup(self) -> int:         # 시그널이 유효해지는 데 필요한 봉 수
        return self.p["period"] + 1

    def generate_signals(self, df):  # df: open/high/low/close/volume/value
        return pd.DataFrame({
            "long_entry": ...,       # bool: 이 봉 종가에 매수 결정 → 다음 봉 시가 체결
            "long_exit": ...,        # bool: 이 봉 종가에 매도 결정 → 다음 봉 시가 체결
            "entry_price": ...,      # (선택) float: 봉 안에서 이 가격 닿으면 즉시 매수
        }, index=df.index)
```

## 구조

```
cointrader/
  config.py              .env 설정 (pydantic-settings)
  cli.py                 typer CLI
  data/upbit_client.py   OHLCV 수집 + parquet 캐시 (200개 페이지네이션)
  strategies/            base.py(인터페이스) · indicators.py · 전략 4종
  backtest/              engine.py(봉 단위 이벤트 루프) · metrics.py · report.py(표·차트)
  trading/broker.py      PaperBroker(JSON 상태) · UpbitBroker(pyupbit 시장가)
  trading/runner.py      실시간 루프 (백테스트와 동일 시그널 규약)
  notify/telegram.py     텔레그램 알림
tests/                   pytest (엔진 체결가·손익 정확성, 전략 규약)
```

## 체결 규칙 (백테스트 = 실시간 동일)

- `long_entry`/`long_exit` 는 봉 **종가에 판단, 다음 봉 시가에 체결** (미래 참조 방지)
- `entry_price` 는 해당 봉 고가가 목표가 이상이면 목표가에 체결 (시가가 이미 위면 시가)
- 손절/익절(`--stop-loss`, `--take-profit`)은 봉 내 저가/고가 기준
- 수수료 0.05% + 슬리피지 0.05% 양방향 기본 적용 (`.env` 에서 조정)

## 주의

- 실거래 전 반드시 `--mode paper` 로 충분히 검증할 것. 그리드 서치 결과는 과최적화되기 쉽다.
- 업비트 API 키는 **출금 권한 없이**, IP 허용 목록을 설정해서 발급할 것.
