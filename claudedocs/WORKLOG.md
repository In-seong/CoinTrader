# CoinTrader WORKLOG

## 📌 현재 진행 중 / 다음 할 일

- [ ] **이 맥(기관망 168.131.x)에서는 거래소 API(업비트·빗썸·코인원·바이낸스) TLS가 SNI 차단됨** → 실데이터 수집/모의매매는 다른 네트워크(집·핫스팟) 또는 서버(주소는 CLAUDE.local.md)에서 실행해야 함. 서버에서 `curl https://api.upbit.com/v1/market/all` 로 먼저 확인.
- [ ] 실데이터로 `fetch` → `compare` → `optimize` 돌려 전략 후보 고르기
- [ ] `run --mode paper --once` 로 러너 1사이클 검증 후 paper 장기 구동
- [ ] 텔레그램 봇 토큰/chat_id 발급 → `.env` → `notify-test`
- [ ] 2단계: 웹 대시보드(수익률·포지션·로그·전략 on/off), 서버 배포(systemd)
- [ ] 아이디어: 멀티 코인 동시 운용, 업비트 웹소켓 실시간 시세, 워크포워드 검증

## 2026-08-20 — 프로젝트 생성 (1단계: 백테스트 엔진 + CLI + 실거래 골격)

**결정**
- 업비트 / Python 3.13 (uv) / 전략 플러그인 구조 / 백테스트 먼저 → paper → live 순
- 홈 디렉토리가 git repo라 CoinTrader 를 독립 repo 로 `git init` (아직 커밋 없음)
- 시그널 규약: `long_entry`/`long_exit`(종가 판단→다음 봉 시가 체결) + `entry_price`(봉 내 돌파 체결). 백테스트·실시간 러너가 같은 규약 공유
- 지표는 pandas 직접 구현 (TA-Lib 의존 없음)

**만든 것**
- `cointrader/` 패키지: config, data(업비트 수집+parquet 캐시), strategies(4종: volatility_breakout, rsi, ma_cross, bollinger), backtest(engine/metrics/report), trading(PaperBroker/UpbitBroker/LiveRunner), notify(telegram), cli(typer)
- CLI: strategies, fetch, import-csv, backtest, compare, optimize, run(paper|live), paper-status, paper-reset, notify-test
- tests 14개 통과 (체결가·손익 정확성, 손절 갭 처리, 돌파 체결, 전략 규약)
- 합성 CSV 로 CLI 전 경로 스모크 테스트 완료 (실데이터는 네트워크 차단으로 미확인)

**미검증**
- 실제 업비트 API 호출 경로(fetch, PaperBroker.get_price, UpbitBroker) — 네트워크 차단으로 이 세션에서 실행 못 함
