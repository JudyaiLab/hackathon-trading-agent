# JudyAI WaveRider — System Architecture

> Hackathon: AI Trading Agents with ERC-8004 | LabLab.ai | 2026-03-30 ~ 04-12

## 1. System Overview

```
                    ┌─────────────────────────────────────────────────┐
                    │           JudyAI WaveRider Agent                │
                    │                 (agent.py)                      │
                    │                                                 │
                    │  ┌───────────┐  ┌──────────┐  ┌─────────────┐  │
                    │  │ Strategy  │  │ Risk     │  │ Opus AI     │  │
                    │  │ Engine    │  │ Manager  │  │ Analyst     │  │
                    │  │ (3 strat) │  │ (7 layer)│  │ (dual-AI)   │  │
                    │  └─────┬─────┘  └────┬─────┘  └──────┬──────┘  │
                    │        │             │               │          │
                    │  ┌─────▼─────────────▼───────────────▼──────┐  │
                    │  │          KrakenExecutor                   │  │
                    │  │      (executor.py — paper trading)        │  │
                    │  └─────────────────┬────────────────────────┘  │
                    └───────────────────┤─────────────────────────────┘
                                        │
              ┌─────────────────────────┤─────────────────────────┐
              │                         │                         │
    ┌─────────▼─────────┐   ┌──────────▼──────────┐   ┌─────────▼─────────┐
    │   Kraken CLI       │   │  ERC-8004 Identity   │   │  Performance      │
    │   (data + trade)   │   │  (Sepolia on-chain)  │   │  Dashboard        │
    │   ─────────────    │   │  ─────────────────   │   │  ──────────────   │
    │   • OHLC/Ticker    │   │  • Agent NFT         │   │  • PnL chart      │
    │   • Paper buy/sell │   │  • Reputation tags    │   │  • Trade history  │
    │   • Balance/Status │   │  • Validation hooks   │   │  • Strategy stats │
    └────────────────────┘   └──────────────────────┘   └───────────────────┘
```

## 2. Component Architecture

### 2.1 Data Layer — `kraken_data.py`

```
KrakenDataAdapter
├── get_ohlc(pair, interval)     → DataFrame [open, high, low, close, volume]
├── get_ticker(pair)             → {bid, ask, last}
└── get_multi_ticker(pairs)      → {pair: ticker_data}

Mapping: Standard pair → Kraken CLI pair → API response key
  "BTC/USDT" → "BTCUSD" → "XXBTZUSD"
  "ETH/USDT" → "ETHUSD" → "XETHZUSD"
  "SOL/USDT" → "SOLUSD" → "SOLUSD"
  etc.

Execution: subprocess.run(["kraken", "ohlc", pair, "--interval", N, "-o json"])

MCP stdio mode (agent-to-agent):
  kraken mcp -s all --allow-dangerous
  Config: {"command": "kraken", "args": ["mcp", "-s", "all", "--allow-dangerous"]}

Rate limits: automatic retry with exponential backoff (up to 3 attempts)
```

### 2.2 Strategy Engine — `strategies.py`

Three OOS-validated strategies + regime detection:

```
StrategyEngine.scan_all(data_adapter)
  └── for each ACTIVE_PAIR (7 pairs):
      └── scan_pair(pair, df_4h)
          ├── RegimeDetector.detect(df) → RegimeResult
          │   ├── TRENDING_UP      → scale 1.0x
          │   ├── TRENDING_DOWN    → scale 1.0x
          │   ├── RANGING          → scale 0.75x
          │   ├── HIGH_VOLATILITY  → scale 0.5x
          │   ├── EMA_CONVERGENCE  → scale 0.3x
          │   ├── TREND_EXHAUSTION → scale 0.5x (ADX declining)
          │   └── BREAKOUT_FORMING → scale 0.75x (BB squeeze)
          │
          ├── WaveRider (primary)
          │   └── 4H EMA50 trend + RSI + volume surge
          │
          ├── BB Squeeze (secondary)
          │   └── BB width < 70% avg → compression → expansion breakout
          │
          └── MACD Divergence (secondary)
              └── Price HH + MACD LH = bearish; Price LL + MACD HL = bullish

Output: List[TradeSignal]
  TradeSignal(pair, direction, entry_price, sl_price, tp1/2/3, confidence, source, position_scale)
```

**OOS Win Rates (Walk-Forward Optimization validated):**

| Pair | Direction | WaveRider | BB Squeeze | MACD Div |
|------|-----------|-----------|------------|----------|
| ETH/USDT | Long | 93.3% | — | — |
| SOL/USDT | Long | 72.5% | — | — |
| XRP/USDT | Long | 78.7% | — | 80.0% |
| BNB/USDT | Long | 80.3% | — | — |
| LINK/USDT | Long | 100% | — | — |
| BTC/USDT | Short | 75.4% | 90.9% | — |
| ETH/USDT | Short | 97.8% | — | — |
| SOL/USDT | Short | 75.9% | — | — |
| XRP/USDT | Short | 75.5% | — | — |
| DOGE/USDT | Short | 87.8% | — | — |
| BNB/USDT | Short | 76.7% | — | — |
| LINK/USDT | Short | 86.1% | — | — |

### 2.3 Execution Layer — `executor.py`

```
KrakenExecutor
├── execute_signal(signal, portfolio_value)
│   ├── Position sizing: risk_amount / sl_distance
│   ├── Cap: max 5% of portfolio
│   ├── Shorts: 0.7x of long size
│   └── CLI: kraken paper buy {pair} {volume} -o json
│
├── check_sl_tp(data_adapter)
│   ├── TP1 hit → move SL to breakeven
│   ├── TP2 hit → move SL to TP1
│   └── TP3 hit → close full position
│
├── check_short_exits(short_signals, data_adapter)
│   └── Short signal → close matching long position
│
├── close_all(data_adapter, reason)
│   └── Emergency close all positions
│
└── get_paper_status() → {balance, value, unrealized_pnl, trades}
    └── CLI: kraken paper status -o json

CLI command reference:
  ticker:  ["ticker", "BTCUSD"]
  balance: ["balance"]  (requires --allow-dangerous in MCP mode)
  order:   ["order", "buy", "BTCUSD", "0.001", "--type", "market"]
  paper:   ["paper", "buy", "BTCUSD", "0.001"]

Note: Kraken paper = SPOT ONLY
  - Long signals → BUY
  - Short signals → EXIT indicator only (close existing longs)
```

### 2.4 Risk Management — `risk_manager.py`

```
RiskManager
├── can_trade(pair, direction) → (bool, reason)
│   ├── Daily loss stop: 3% daily loss → pause
│   ├── Max trades: 10/day
│   ├── Max concurrent: 5 positions
│   ├── Consecutive losses: 5 → pause, 3 → 50% size
│   └── Emergency drawdown: 10% → close all
│
├── register_open(pair, direction)
├── register_close(pair, pnl, reason)
├── check_emergency() → bool
└── Daily reset at midnight UTC
```

### 2.5 ERC-8004 Identity — `erc8004.py`

```
Phase 2 (Apr 3-7):

1. Register Agent Identity NFT
   ├── Generate agent_card.json (spec-compliant registration-v1)
   │   ├── name: "JudyAI WaveRider"
   │   ├── services: [{web, MCP}]
   │   ├── capabilities, backtest performance, risk controls
   │   └── supportedTrust: ["reputation"]
   ├── Encode as base64 data URI (no IPFS dependency)
   └── Call Identity Registry.register(agentURI) on Sepolia
       Contract: 0x8004A818BFB912233c491871b3d84c89A494BD9e

2. Post Reputation Feedback
   ├── Daily: tradingYield / day → PnL percentage
   ├── Cumulative: successRate → win rate
   └── Call Reputation Registry.giveFeedback(agentId, value, ...)
       Contract: 0x8004B663056A597Dffe9eCcC1965A193B7388713

3. Validation (optional)
   └── Request re-execution validation for strategy performance

Agent Identifier: eip155:11155111:0x8004A818...:${agentId}
```

### 2.6 AI Trading Analyst — `opus_analyst.py` + `chart_analyzer.py`

```
OpusAnalyst (Dual-AI cross-validation + position management)

Signal Analysis (dual-AI mode):
├── analyze_signal(signal, df, regime, chart_b64)
│   ├── Build market context: EMA alignment, RSI, MACD, ATR, Volume, BB, Regime
│   ├── Dual-AI: MiniMax M2.7 + Ollama qwen2.5:7b analyze independently
│   ├── _dual_ai_merge() cross-validates:
│   │   ├── AGREE (same direction) → +15 confidence bonus
│   │   ├── DISAGREE (opposite) → -10 penalty, force HOLD
│   │   └── PARTIAL (one HOLD) → average, no bonus
│   │   └── Weighted: MiniMax 60% + Ollama 40%
│   ├── Optional: Render 4H chart → send to Opus vision (premium)
│   └── Return: AIAnalysis(verdict, confidence, position_adjustment, reasoning)
│
├── compute_ensemble(signal, ai_analysis, rule_confidence)
│   ├── Rule-based score: 40% (WFO-validated strategies)
│   ├── AI score: 40% (dual-AI cross-validated)
│   ├── Multi-strategy bonus: 20% (2+ strategies confirming)
│   └── Return: EnsembleScore(should_trade, final_scale, reasoning)
│
├── review_signals(signals, df_cache, regime_cache, chart_cache)
│   └── Batch analysis: AI review → ensemble → sort by score → return
│
Position Management (MiniMax only):
├── review_positions(positions, df_cache, regime_cache)
│   ├── For each open position: build context (PnL, time held, indicators)
│   ├── MiniMax M2.7 evaluates market structure changes
│   └── Return: List[PositionReview(action, urgency, reasoning)]
│       ├── HOLD — market structure unchanged
│       ├── TIGHTEN_SL — move SL closer (only tightens, never widens)
│       ├── TAKE_PROFIT — close position (momentum exhausted)
│       └── EXIT_NOW — urgent exit (trend reversal confirmed)
│
└── _build_position_context(position, df, regime)
    └── PnL%, hours held, EMA alignment, RSI, MACD, volume, SL/TP distance

ChartAnalyzer
├── render_chart_base64(df, pair, direction)
│   ├── 100-candle 4H chart with EMA 20/50/100/200 overlays
│   ├── Bollinger Bands (shaded), Volume bars, Signal arrow
│   └── Professional dark theme, 14×8 chart @ 120 DPI
│
└── render_charts_batch(df_cache, signals)
    └── Render charts for all signal pairs

AI Backends (priority order for signal analysis):
  1. Dual-AI: MiniMax M2.7 + Ollama qwen2.5:7b (cross-validated)
  2. Groq Llama 3.3 70B (free cloud fallback if Ollama unavailable)
  3. Claude Opus 4.6 (premium, best reasoning + vision)
  4. Pass-through — signals proceed as-is when AI unavailable

AI Backend for position management:
  - MiniMax M2.7 only (fast, reliable, free)

Knowledge Base:
  - Proprietary EMA multi-timeframe trading framework (1006 curated messages)
  - 273 validated historical trade setups with price levels
  - 63 strategy patterns from quantitative trading research
```

## 3. Data Flow

```
Every 2 hours (full scan):
┌──────────┐    OHLC     ┌──────────┐   signals   ┌──────────┐   AI-scored  ┌──────────┐
│ Kraken   │ ─────────── │ Strategy │ ──────────── │ Opus AI  │ ──────────── │ Risk     │
│ CLI API  │    4H       │ Engine   │  TradeSignal │ Analyst  │  Ensemble    │ Manager  │
└──────────┘   candles    └──────────┘              └────┬─────┘              └────┬─────┘
                                                         │ chart                   │ allowed?
                                                   ┌─────▼─────┐           ┌─────▼─────┐
                                                   │ Chart      │           │ Executor   │
                                                   │ Analyzer   │           │ paper buy  │
                                                   └───────────┘           └─────┬─────┘
                                                                                 │
                                                                           ┌─────▼─────┐
                                                                           │ Trade Log  │
                                                                           │ + State    │
                                                                           └───────────┘

Every 5 minutes (monitor):
┌──────────┐   ticker   ┌──────────┐   SL/TP    ┌──────────┐
│ Kraken   │ ────────── │ Executor │ ────────── │ Risk     │
│ ticker   │   price    │ positions│   events   │ Manager  │
└──────────┘            └──────────┘            └──────────┘

Daily (ERC-8004 reputation):
┌──────────┐   PnL     ┌──────────┐   tx       ┌──────────┐
│ Agent    │ ────────── │ ERC-8004 │ ────────── │ Sepolia  │
│ State    │   stats    │ Module   │   onchain  │ Network  │
└──────────┘            └──────────┘            └──────────┘
```

## 4. Trading Pairs & Rules

| Pair | Long | Short | Notes |
|------|------|-------|-------|
| BTC/USDT | BLACKLISTED | Active (75.4% WR) | Short-only via exit signals |
| ETH/USDT | Active (93.3% WR) | Active (97.8% WR) | Best performer |
| SOL/USDT | Active (72.5% WR) | Active (75.9% WR) | |
| XRP/USDT | Active (78.7% WR) | Active (75.5% WR) | |
| DOGE/USDT | BLACKLISTED | Active (87.8% WR) | Short-only |
| BNB/USDT | Active (80.3% WR) | Active (76.7% WR) | Added for competition |
| LINK/USDT | Active (100% WR) | Active (86.1% WR) | Added for competition |

**Kraken paper trading limitation:** Spot-only, no margin shorts.
- Long signals: Execute as BUY
- Short signals: Used as EXIT indicators to close existing longs

## 5. PnL Maximization Strategy

### 5.1 Competition Edge

The hackathon ranks by **net PnL**. Our advantages:

1. **AI-Enhanced Signals** — Dual-AI (MiniMax M2.7 + Ollama) analyzes each signal with professional trader knowledge (proprietary EMA framework + 273 validated trade setups)
2. **Visual Chart Analysis** — Opus vision analyzes 4H candlestick charts for pattern recognition (H&S, Double Top/Bottom, Flags, Wedges)
3. **Ensemble Scoring** — Rule-based (40%) + AI reasoning (40%) + multi-strategy bonus (20%) = single decision score
4. **OOS-validated strategies** — 82.2% aggregate win rate from Walk-Forward Optimization
5. **Multi-strategy confirmation** — Level 3 confidence when 2+ strategies agree
6. **Regime-adaptive sizing** — Smaller positions in ranging/volatile markets
7. **Risk-first** — 3% daily loss stop + 10% emergency drawdown prevents blowups

### 5.2 Optimization Levers (Phase 3)

| Lever | Current | Tuned | Impact |
|-------|---------|-------|--------|
| Scan interval | 4H | 1H (monitor) + 4H (signals) | Catch more opportunities |
| Risk per trade | 1.5% | 2.0% (trending) / 1.0% (ranging) | +33% position size in trends |
| Max concurrent | 5 | 7 | More capital deployed |
| TP targets | 1:1, 2:1, 3:1 | Dynamic by regime | Better exit timing |
| SL trailing | Manual breakeven | ATR-based trailing | Capture more upside |

### 5.3 Paper Trading Initial State

- Starting balance: $100,000 USD
- Fee: 0.26% per trade (Kraken paper default)
- Pairs: 7 active (BTC, ETH, SOL, XRP, DOGE, BNB, LINK)

## 6. File Structure

```
hackathon-trading-agent/
├── agent.py              # Main agent loop + AI pipeline + crash recovery (792 lines)
├── strategies.py         # Multi-strategy signal engine (WaveRider/BB/MACD) (905 lines)
├── opus_analyst.py       # Dual-AI trading analyst + ensemble scoring (1212 lines)
├── chart_analyzer.py     # 4H candlestick chart renderer for AI vision (212 lines)
├── executor.py           # Kraken CLI paper trading (699 lines)
├── risk_manager.py       # Portfolio risk controls (251 lines)
├── kraken_data.py        # Market data adapter (152 lines)
├── kraken_cli.py         # Shared Kraken CLI wrapper (92 lines)
├── erc8004.py            # ERC-8004 CLI entry point + re-exports (65 lines)
├── erc8004_card.py       # Agent Card generation, save/load (146 lines)
├── erc8004_chain.py      # On-chain registration + reputation (337 lines)
├── erc8004_abi.py        # ABI fragments for contracts (80 lines)
├── validate.py           # Validation audit report generator (266 lines)
├── config.py             # Configuration + REGIME_GRID (244 lines)
├── requirements.txt      # Python dependencies
├── agent_card.json       # ERC-8004 agent metadata
├── Dockerfile            # Container image (Python 3.11-slim)
├── Makefile              # Build/test/run targets (58 lines)
├── .env.example          # Environment variable template
├── logs/
│   ├── trade_log.jsonl   # Trade event log
│   └── agent_state.json  # Current state snapshot
├── tests/
│   ├── test_core.py      # 48 unit tests (risk, indicators, strategy, ERC-8004)
│   └── test_integration.py # 15 integration tests (data, executor, immutability)
├── validation/           # Auditable decision records (trade intents, risk checks, strategy checkpoints)
└── docs/
    ├── architecture.md   # This file
    ├── wfo_summary.md    # Walk-Forward Optimization results
    ├── demo_script.md    # Demo video script
    ├── erc8004_research.md   # ERC-8004 integration research
    ├── recording_guide.md    # Demo recording guide
    ├── lablab_submission.md  # LabLab.ai submission fields
    └── surge_submission.md   # early.surge.xyz submission
```

## 7. Dependencies

```
Python 3.11+
├── pandas              # OHLC data processing
├── numpy               # Indicator calculations
├── web3                # ERC-8004 contract interaction (Phase 2)
└── (stdlib only)       # subprocess, asyncio, json, logging

External:
├── Kraken CLI v0.2.3   # /usr/local/bin/kraken (aarch64-linux)
│   ├── Direct CLI mode: kraken ticker BTCUSD -o json
│   └── MCP stdio mode:  kraken mcp -s all --allow-dangerous
└── Foundry cast        # ERC-8004 fallback registration (optional)
```

## 8. Deployment Plan

### Phase 1: Core Trading (3/30 - 4/3)
```bash
# Start agent (cron or systemd)
cd hackathon-trading-agent/
python3 agent.py  # Main loop: 4H scan + 5min monitor

# Or single scan for testing
python3 agent.py --single-scan
python3 agent.py --status
```

### Phase 2: ERC-8004 ✅ COMPLETE (Agent ID: 17)
```bash
# ERC-8004 Identity: TX 0x348195e99e900014b2c48db4840dfed8b6693854...
# Hackathon AgentRegistry: TX 0xb6e2b43a50092c5b1c4a9b395884d8acd4d2a6921de70249...
# Agent ID: 17
# Wallet: 0x95C8B49C2A6124C436EA1a3f378991313f6f1c0A

# Show agent card
python3 erc8004.py --show

# Update reputation after trades
python3 erc8004.py --update-reputation
```

### Phase 3: Polish + Submission (4/7 - 4/12)
- README enhanced with judges' quick-start, backtest-vs-live table, FAQ
- Demo video recording (2-3 min)
- LabLab submission + early.surge.xyz

## 9. Competition Submission Checklist

- [x] GitHub repo (public) with README
- [x] Architecture documentation (this file)
- [x] Judges' Quick Start section in README
- [x] Backtest vs Live comparison table
- [x] "Why This Approach" FAQ for judges
- [ ] Demo video (2-3 min) — script ready in `docs/demo_script.md`
- [ ] early.surge.xyz project page
- [ ] LabLab team page
- [x] ERC-8004 agent identity (Sepolia) — Agent ID: 17
- [x] PnL performance data (trade log)
- [x] Agent card with on-chain reputation
- [x] Validation artifacts (36 records: 14 trade intents, 11 risk checks, 11 strategy checkpoints)
- [x] 80 automated tests (48 unit + 15 integration + 17 batch TP)
- [x] 36-cell strategy matrix with Q-series combos
- [x] Walk-Forward Optimization documentation

## 10. Security Constraints

- Paper trading only (no real funds)
- No private keys in code (env vars only)
- Strategy parameters are competition-specific
- No internal paths in public-facing code
- SEPOLIA_PRIVATE_KEY via env var only
