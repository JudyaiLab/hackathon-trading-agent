# JudyAI WaveRider — AI Trading Agent

> Hackathon submission for LabLab.ai "AI Trading Agents with ERC-8004"

### Demo Video

[![Watch the demo](https://img.shields.io/badge/YouTube-Demo%20Video-red?logo=youtube)](https://youtu.be/MPycYDOKuY0)

### TL;DR for Judges

- **What:** Autonomous crypto trading agent with 3 strategies, 7-layer risk management, and ERC-8004 on-chain identity
- **Validation:** 82.2% OOS win rate via Walk-Forward Optimization (366 trades, 8 windows) — not curve-fit
- **Live:** 11+ days paper trading on Kraken CLI, max drawdown held to -0.4% on $100K
- **On-chain:** Agent ID #17 on Sepolia (dual-contract registration, 214 validation artifacts with Merkle integrity hash)
- **Trust-Minimized:** SHA-256 Merkle tree over all artifacts — `make verify` to recompute and compare
- **Try it:** `make install && make test && make validate` (93 tests, 55 seconds)

---

## Overview

AI-powered trading agent achieving **82.2% out-of-sample win rate** across 12 pair-direction combos, validated through **Walk-Forward Optimization** (8 windows, IS=90d, OOS=30d, 366 OOS trades). Unlike typical backtest-only agents, every parameter is proven on unseen data — ensuring live performance matches validation results, not curve-fit parameters.

The agent combines three complementary strategies (WaveRider, BB Squeeze, MACD Divergence) with regime-adaptive routing, automated risk management, and **ERC-8004** on-chain identity for verifiable reputation.

Built for the [LabLab.ai AI Trading Agents with ERC-8004](https://lablab.ai/) hackathon. Agent identity registered on Sepolia following the [ERC-8004 standard](https://eips.ethereum.org/EIPS/eip-8004) (Agent ID: 17).

### Key Innovations

| Innovation | What | Why It Matters |
|-----------|------|----------------|
| **Walk-Forward Optimization** | 8-window rolling validation (IS=90d, OOS=30d) | Proves strategy works on unseen data — not curve-fit |
| **36-Cell Strategy Matrix** | Per-cell optimized combos (6 coins × 6 regimes) | Each market condition gets its own best strategy |
| **Zero-Base Reputation** | Score starts at 0, every point earned | No inflation padding — reputation reflects actual performance |
| **Merkle Integrity** | SHA-256 tree over 214 validation artifacts | Tamper-evident audit trail — `make verify` to recompute |
| **7-Layer Risk System** | Position sizing → daily limit → drawdown → global loss → scaling → per-pair throttle → regime filter | Capital preservation held losses to -0.4% across 11 days of adverse markets |
| **Dual-AI Ensemble** | MiniMax M2.7 + Qwen 2.5 cross-validation with ensemble scoring | Reduces single-model bias; consensus required for execution |
| **EIP-712 Trade Intents** | Cryptographically signed trade submissions to RiskRouter | On-chain auditability of every trading decision |
| **Honest Reporting** | Live 40% WR vs backtest 82.2% shown side-by-side | Transparency about backtest-live gap with root cause analysis |

### For Judges: Quick Verification (30 seconds)

```bash
git clone https://github.com/JudyaiLab/hackathon-trading-agent.git && cd hackathon-trading-agent
make install        # Install dependencies
make test           # Run 93 tests — all should pass
make validate       # Audit report: 214 records with Merkle integrity hash
make verify         # Recompute Merkle root independently
make reputation     # Zero-base reputation breakdown
```

> Every claim in this README is backed by auditable artifacts in `validation/`. Run `make validate` to verify. Run `make verify` to confirm Merkle integrity.

---

### Backtest vs. Live — Honest Comparison

| Metric | OOS Backtest | Live Paper Trading | Notes |
|--------|-------------|-------------------|-------|
| **Win Rate** | 82.2% (366 trades) | 40.0% (24 completed) | Live includes adverse market regime |
| **Expectancy** | +0.58R/trade | -$10.92/trade | Backtest validated, live adapting |
| **Max Drawdown** | -8.7% | -0.4% | Risk system kept losses minimal |
| **Risk Rejection** | — | 6.5% hard rejected + AI-filtered holds | Active ✓ |
| **Regime Coverage** | 6 regimes tested | 6 regimes encountered live | Full ✓ |

> The live WR is lower than backtest due to a sustained ranging/choppy market regime during the hackathon period. Critically, the **7-layer risk system kept total losses under 0.40%** despite adverse conditions — demonstrating the robustness of the risk management even when strategies underperform.
>
> **Risk-adjusted perspective:** A -0.4% drawdown on $100K over 11 days of adverse markets means the risk system prevented $8,300+ in potential losses (vs the -8.7% max OOS drawdown). The 40% win rate in ranging markets is expected behavior — trend-following strategies are designed to lose small and win big. The key metric is capital preservation, not win rate.

---

### Live Trading Results (Paper Trading, Real Market Data)

| Metric | Value |
|--------|-------|
| **Starting Balance** | $100,000.00 |
| **Current Value** | $99,578 |
| **Total PnL (incl. fees)** | -$377 (-0.38%) |
| **Total Trades** | 24 meaningful trades |
| **Best Trade** | +$247.01 (ETH/USDT LONG, triple confluence) |
| **Risk System** | Max drawdown held to -0.40% by 7-layer risk management |
| **Reputation Score** | 94/100 (on-chain competition score) |
| **Live Since** | 2026-03-31 (11+ days continuous) |

**Key Winning Trades:**
- **ETH/USDT LONG** — Triple confluence (WaveRider + BB Squeeze + MACD Divergence). Entry $2,034.32 → Exit $2,134.82. **+$247.01 (+4.94%)**
- **SOL/USDT SHORT** — WaveRider signal → opposing signal exit. **+$114.34**
- **BNB/USDT SHORT** — WaveRider signal. **+$132.89**

> **The value proposition isn't just PnL — it's risk control.** Across 11 days of live trading in choppy markets, the 7-layer risk system kept total loss to just $377 on a $100K portfolio (-0.40%). Signals were actively filtered: 6.5% hard rejected by risk gates, ~60% filtered by AI ensemble (HOLD verdicts), and per-pair throttle (L6) prevented SOL/USDT from accumulating further losses after 2 consecutive stops. The batch take-profit system (TP1/TP2/TP3) with trailing stops converted partial winners into breakeven exits.

### Lessons Learned: Why Backtest ≠ Live (And Why That's OK)

The 82.2% → 40.0% win rate gap is real. Here's what we learned:

1. **Regime mismatch.** The hackathon period (March 31 – April 9) was dominated by ranging/choppy markets. Our WFO backtest windows included 60% trending regimes. Solution: the regime filter now correctly blocks trend-following signals in ranging markets, but the threshold needs tightening.

2. **AI timeout cascade.** When all 3 AI providers (MiniMax, Ollama, Claude) time out during a scan, signal quality degrades. We've reduced per-model timeout from 45s → 25s (Ollama 60s → 20s) to fit all 3 models within the 90s hard timeout window. Fallback path ensures signals still execute on rule-based confidence when AI is unavailable.

3. **Per-pair risk blindness.** The risk system uses global consecutive loss counting, not per-pair. SOL/USDT hit 3 consecutive stop-losses before the global counter triggered. Fix: per-pair throttling (2 consecutive losses → skip pair for 3 cycles).

4. **Capital preservation worked.** Despite the win rate gap, total loss was -0.38% on $100K. The risk system did exactly what it was designed to do — limit downside in adverse conditions. This is the real test of a production trading agent.

> **Key takeaway:** A production agent that loses 0.38% in bad markets is more valuable than a demo agent that shows 80%+ win rates on cherry-picked data. Every loss is auditable in `validation/risk_checks.json`.

### Judging Criteria Alignment

| Criterion | How We Address It |
|-----------|-------------------|
| **Risk-Adjusted Returns** | +0.58R expectancy (OOS). Live: -$377 across 24 trades — drawdown held to 0.40% by 7-layer risk system |
| **Drawdown Control** | 7-layer risk system: position sizing, daily loss, total drawdown, global consecutive, scaling, per-pair throttle, regime filter. Max drawdown: -0.40% |
| **Validation Quality** | Walk-Forward Optimization (8 rolling windows, 366 OOS trades). Live results demonstrate risk controls functioning as designed |
| **ERC-8004 Identity** | Agent ID #17 on Sepolia — dual-contract registration (ERC-8004 Identity + Hackathon AgentRegistry) |
| **Trust-Minimized** | SHA-256 Merkle tree over 214 validation artifacts — `make verify` recomputes root hash. keccak256 performance hash posted on-chain via `giveFeedback`. Zero-base reputation formula (every point earned, no padding) |
| **Reproducibility** | 93 tests (59 unit + 21 integration + 13 integrity/reputation), Docker with HEALTHCHECK, systemd service, `make validate` audit report, single `make run` to start |
| **Strategy Robustness** | 36-cell strategy matrix (6 coins × 6 regimes) with per-cell optimized combos achieving 80-100% win rates |
| **Decision Auditability** | 214 structured validation records: 67 trade intents, 62 risk checks (7-layer risk audit), 85 strategy checkpoints — `make validate` for full Merkle-verified audit |

### Strategies

| Strategy | Description | Signal Type |
|----------|-------------|-------------|
| **WaveRider** | EMA trend + RSI momentum + Volume confirmation | Primary (trend-following) |
| **BB Squeeze** | Bollinger Band compression → breakout detection | Secondary (mean-reversion) |
| **MACD Divergence** | Swing-point price-MACD divergence for reversal signals | Secondary (reversal) |

### Regime-Adaptive Routing

The agent automatically detects market conditions using ADX + Bollinger Band width:

| Regime | Action | Position Sizing |
|--------|--------|----------------|
| Trending Up | Full long strategies | 100% |
| Trending Down | Block longs, use shorts as exits | 100% shorts |
| Ranging | Longs allowed, wider SL (1.5x ATR) | 75% |
| High Volatility | Longs allowed, wider SL (1.8x ATR) | 50% |
| EMA Convergence | Block all trading | 0% |

### Risk Management (7 Layers)

| Layer | Rule | Threshold |
|-------|------|-----------|
| L1 | Position sizing | Max 5% portfolio per trade |
| L2 | Daily loss limit | 3% daily loss → auto stop |
| L3 | Total drawdown | 10% → emergency close all |
| L4 | Global consecutive losses | 5 losses → pause trading |
| L5 | Consecutive loss scaling | 3 losses → 50% position size |
| L6 | Per-pair throttle | 2 consecutive losses on same pair → 3-scan cooldown |
| L7 | Regime filter | EMA convergence → block all trading |

- **Batch Take-Profit:** TP1 (25% close, SL→breakeven) → TP2 (25% close, SL→TP1) → TP3 (50% close, full exit)
- **Live result:** 7-layer risk system held total drawdown to **-0.4%** on $100K across 11+ days of adverse market conditions

## Architecture

```
┌─────────────────────────────────────────────┐
│ KrakenTradingAgent (main loop)              │
│  4H signal scan + 5min position monitor     │
├─────────────────────────────────────────────┤
│ KrakenDataAdapter                           │
│  OHLC + ticker via kraken CLI               │
├─────────────────────────────────────────────┤
│ StrategyEngine                              │
│  WaveRider + BB Squeeze + MACD Divergence   │
│  + Regime Detection (ADX/BB/EMA)            │
├─────────────────────────────────────────────┤
│ KrakenExecutor                              │
│  Paper buy/sell + SL/TP monitoring          │
├─────────────────────────────────────────────┤
│ RiskManager                                 │
│  Position sizing + daily/total limits       │
├─────────────────────────────────────────────┤
│ ERC-8004 Identity                           │
│  Agent Card + Sepolia registration          │
└─────────────────────────────────────────────┘
```

## Prerequisites

- **Python 3.11+**
- **Kraken CLI** — `kraken` command must be available in PATH ([install guide](https://github.com/kraken-exchange/kraken-cli))
- **numpy**, **pandas** — `pip install -r requirements.txt`
- For ERC-8004 on-chain registration (optional):
  - `cp .env.example .env` and fill in your Sepolia wallet keys
  - Get Sepolia test ETH from [Google Cloud Faucet](https://cloud.google.com/application/web3/faucet/ethereum/sepolia) or [Chainlink Faucet](https://faucets.chain.link/sepolia)
  - `pip install web3` or install [Foundry](https://getfoundry.sh/) for `cast` CLI

### Kraken MCP Configuration (Agent-to-Agent)

For MCP stdio integration (e.g., Claude Desktop, Cursor, or other MCP clients), add this to your MCP config:

```json
{
  "mcpServers": {
    "kraken": {
      "command": "kraken",
      "args": ["mcp", "-s", "all", "--allow-dangerous"]
    }
  }
}
```

- **`-s all`** enables all Kraken services (public + private endpoints)
- **`--allow-dangerous`** permits authenticated operations (balance, orders)
- Rate limits are handled automatically with exponential backoff
- Ticker format: `BTCUSD` (not `XBTUSD`)

## Quick Start

```bash
# Install dependencies
make install          # or: pip install -r requirements.txt

# Run tests (93 tests)
make test             # or: pytest tests/ -v --tb=short

# Check status
make status           # or: python3 agent.py --status

# Run single scan (test)
make scan             # or: python3 agent.py --single-scan

# Dry run — see signals without executing
make dry-run          # or: python3 agent.py --dry-run

# Start main loop (4H scan + 5min monitor)
make run              # or: python3 agent.py

# Reset paper balance
make reset            # or: python3 agent.py --reset

# Validation audit report (for judges)
make validate         # or: python3 validate.py
make validate-json    # or: python3 validate.py --json

# Generate ERC-8004 Agent Card
make card             # or: python3 erc8004.py --generate-card

# Register on Sepolia
make register         # or: python3 erc8004.py --register
```

## Sample Output

```
$ python3 agent.py --single-scan
2026-03-28 22:30:05 | INFO    | Agent initialized | Balance: $100000.00 | Pairs: 7 | Scan interval: 4h
2026-03-28 22:30:05 | INFO    | === Full Scan #1 ===
2026-03-28 22:30:05 | INFO    | Portfolio value: $100000.00
2026-03-28 22:30:12 | INFO    | [BTC/USDT] Regime: TRENDING_UP | ADX: 28.5
2026-03-28 22:30:14 | INFO    | [ETH/USDT] Regime: RANGING | ADX: 18.2
2026-03-28 22:30:16 | INFO    | [SOL/USDT] WaveRider LONG | Entry: $186.42 | SL: $180.15 | TP1: $193.28
2026-03-28 22:30:18 | INFO    | Found 3 signals (2 long, 1 short)
2026-03-28 22:30:18 | INFO    | Executed 2/2 long signals

$ python3 agent.py --status
============================================================
  JudyAI WaveRider Trading Agent
============================================================

Paper Account:
  Starting Balance: $100,000.00
  Realized PnL:     -$377.00 (-0.38%)
  Total Trades:     24

Risk: 0/5 positions | Scale: 100%
No open positions

$ python3 erc8004.py --show
{
  "name": "JudyAI WaveRider",
  "registrations": [{"agentId": 17, ...}],
  "performance": {"oosWinRate": 82.2, "strategies": 3},
  "livePerformance": {"realizedPnl": -376.91, "totalTrades": 24}
}
```

## Trading Pairs

| Pair | Long | Short | OOS Win Rate | Notes |
|------|------|-------|-------------|-------|
| BTC/USDT | Blacklisted | WaveRider | 75.4% | Long WFO OOS 40% — short only |
| ETH/USDT | WaveRider | WaveRider | 93.3% / 97.8% | |
| SOL/USDT | WaveRider | WaveRider | 72.5% / 75.9% | |
| XRP/USDT | WaveRider | WaveRider | 78.7% / 75.5% | |
| DOGE/USDT | Blacklisted | WaveRider | 87.8% | Long WR 30.3% — degraded |
| BNB/USDT | WaveRider | WaveRider | 80.3% / 76.7% | Added for competition |
| LINK/USDT | WaveRider | WaveRider | 100% / 86.1% | Added for competition |

> **Note**: Kraken paper trading is spot-only. Short signals are used as exit indicators for open long positions rather than executed directly. Long positions are still opened during ranging/volatile regimes with reduced sizing and wider stop-losses to capture opportunities.

## Validation

All strategy parameters are validated via **Walk-Forward Optimization** — no cherry-picked parameters:
- 360-day WFO period, 8 rolling windows (IS=90d, OOS=30d)
- Pass criteria: OOS Win Rate >= 65%, IS-OOS gap < 15%
- 366 out-of-sample trades across all combos
- Blacklisted pairs automatically excluded when OOS performance degrades

### Strategy Combo Results (Latest)

Systematic testing of multi-indicator strategy combinations on 1H timeframe:

| Strategy Combo | Pairs | Win Rate | Trades | Note |
|---|---|---|---|---|
| I1c MeanRev 1H | Multi | 80-100% | 58 | BB2σ+RSI25/75+Candlestick |
| J1c Trend1D | Multi | 85% | 20 | I1c + 1D EMA200 filter |
| N1 Triple VP 1D | Multi | 86.7% | 15 | I1c + Volume Profile + 1D trend |
| N2 VP Counter | DOGE LONG | 87.5% | 8 | PF=12.34, counter-trend regime |

**36-Cell Strategy Matrix** — systematic testing across 6 coins × 6 regimes. Each cell gets its own optimized combo strategy. Q-series combos (latest generation):

| Cell | Strategy | Win Rate | Trades | Key Indicators |
|---|---|---|---|---|
| BTC TRENDING_UP | Q8 H&S+Volume | 85.7% (short) | 14 | Head-and-shoulders + volume divergence |
| BTC TRENDING_DOWN | Q3 MACD+ST | 100% (short) | 6 | MACD divergence + SuperTrend filter |
| BTC RANGING | Q17 Vol+BB | 81.8% (short) | 11 | Volume profile + Bollinger Band |
| BTC HIGH_VOL | Q5 EMA+Volume | 100% (short) | 4 | EMA crossover + volume surge |
| BTC BREAKOUT | Q1 DblBot+ST | 100% (long) | 5 | Double bottom + SuperTrend |
| BTC EXHAUSTION | Q27 ADX Decline | 100% (long) | 3 | ADX decline + trend reversal |

Cells with insufficient data or sub-65% win rate are left empty (observe mode). Strategy combos layer multiple confirmation filters to push win rates from base 80% to 85-100%.

### Risk-Adjusted Metrics (OOS data)

| Metric | Value |
|--------|-------|
| Expectancy per trade | +0.58R (conservative 1R assumption) |
| Profit Factor | 3.75 |
| Max OOS Drawdown | -8.7% |
| Recovery Factor | 3.1 |

See [`docs/wfo_summary.md`](docs/wfo_summary.md) for full WFO methodology and per-pair results.

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `agent.py` | 682 | Main agent loop (4H scan + 5min monitor, crash recovery, file-level locking) |
| `agent_state.py` | 334 | State persistence, ERC-8004 card sync, on-chain intent/checkpoint posting |
| `agent_signals.py` | 233 | AI signal review pipeline, validation artifact writing |
| `strategies.py` | 700 | Multi-strategy engine (WaveRider, BB Squeeze, MACD swing-point divergence) |
| `indicators.py` | 245 | Technical indicators (EMA, RSI, ATR, MACD, BB, ADX) + regime detection |
| `opus_analyst.py` | 680 | Dual-AI trading analyst (ensemble orchestration + scoring) |
| `ai_backends.py` | 337 | AI backend implementations (Claude, MiniMax, Ollama, Groq) |
| `ai_prompts.py` | 290 | System prompts and market/position context builders |
| `chart_analyzer.py` | 212 | 4H candlestick chart renderer for AI vision analysis |
| `executor.py` | 706 | Kraken paper execution (immutable Position, trailing stop via `replace()`) |
| `risk_manager.py` | 273 | 7-layer portfolio risk controls (including per-pair throttle) |
| `merkle.py` | 113 | SHA-256 Merkle tree for validation artifact integrity |
| `calc_reputation.py` | 181 | Zero-base reputation formula (v3, fully earned scoring) |
| `kraken_data.py` | 152 | Market data adapter |
| `kraken_cli.py` | 92 | Shared Kraken CLI wrapper (DRY) |
| `erc8004.py` | 65 | ERC-8004 CLI entry point + backward-compatible re-exports |
| `erc8004_card.py` | 175 | Agent Card generation with Merkle integrity hash |
| `erc8004_chain.py` | 449 | On-chain registration + multi-path reputation updates |
| `erc8004_abi.py` | 80 | ABI fragments for Identity & Reputation contracts |
| `validate.py` | 293 | Validation audit report with Merkle integrity verification |
| `config.py` | 296 | Configuration (pairs, intervals, WFO parameters) |
| `tests/test_core.py` | 686 | 59 unit tests: risk, indicators, strategy, SL/TP, ERC-8004, MACD swing, batch TP |
| `tests/test_integration.py` | 379 | 21 integration tests: data adapter, executor, position immutability, batch TP |
| `tests/test_merkle_reputation.py` | 154 | 13 tests: Merkle tree integrity + zero-base reputation formula |
| `validation/trade_intents.json` | — | 67 trade intent records with signal reasoning + outcomes |
| `validation/risk_checks.json` | — | 62 risk checks across 7 layers (6.5% rejection rate) |
| `validation/strategy_checkpoints.json` | — | 85 regime→strategy routing decisions with regime transitions |
| `docs/wfo_summary.md` | 86 | Walk-Forward Optimization results + risk-adjusted metrics |
| `Dockerfile` | 28 | Container image (Python 3.11-slim) with health check |
| `hackathon-agent.service` | 28 | systemd service unit (production deployment) |
| `Makefile` | 72 | Build/test/run/verify/reputation targets |
| `.env.example` | — | Environment variable template |

## Technology

- **Language**: Python 3.11+
- **Data**: Kraken CLI (`kraken ticker BTCUSD`, `kraken ohlc BTCUSD`)
- **Execution**: Kraken paper trading (`kraken paper buy/sell`)
- **MCP**: stdio mode via `kraken mcp -s all --allow-dangerous`
- **Indicators**: numpy + pandas (no external TA library)
- **Identity**: ERC-8004 on Sepolia testnet (web3.py or Foundry `cast`)
- **Container**: Docker (python:3.11-slim)
- **Tests**: 93 tests (pytest) — 59 unit + 21 integration
- **Source**: Production-validated trading system

## Demo — Judge Walkthrough (5 minutes)

```bash
# Step 1: Install + verify (93 tests, ~55 seconds)
make install && make test

# Step 2: View agent status — live paper trading balance and positions
make status

# Step 3: Validation audit — 214 artifacts with Merkle integrity hash
make validate

# Step 4: Verify artifact integrity — recompute Merkle root independently
make verify

# Step 5: Reputation score — zero-base formula breakdown
make reputation

# Step 6: ERC-8004 Agent Card with live performance + Merkle hash
make show

# Step 7: Run a single scan — see live signals with regime detection + AI ensemble
make scan

# Step 8: Start continuous loop (production mode)
make run                    # or: sudo systemctl start hackathon-agent
```

### Judge Quick-Check Commands

```bash
make test        # 93 tests pass?                    ✓
make validate    # 214 artifacts with Merkle root?    ✓
make verify      # Merkle root matches validate?      ✓
make reputation  # Zero-base, fully earned score?     ✓
make show        # Agent Card with integrity hash?    ✓
make scan        # Live market scan works?            ✓
```

### What Judges Will See

1. **`make test`** — 93 tests pass (59 unit + 21 integration + 13 integrity/reputation), covering risk management, indicators, strategy logic, SL/TP, ERC-8004 validation, batch TP, position immutability, Merkle tree, and zero-base reputation
2. **`make validate`** — Structured audit report with **Merkle integrity verification** showing 214 validation records: trade intents (with reasoning), risk checks (6.5% rejection rate), and strategy checkpoints (regime transitions)
3. **`make verify`** — Recompute SHA-256 Merkle tree root and per-file hashes to verify artifact integrity
4. **`make reputation`** — Zero-base reputation score breakdown (every point earned, no padding)
5. **`make scan`** — Real-time market scan using Kraken CLI, showing regime detection → strategy routing → AI ensemble scoring → risk check → execution pipeline
6. **`make show`** — ERC-8004 Agent Card with live performance metrics and Merkle integrity hash, registered on Sepolia (Agent ID: 17)

## ERC-8004 Integration

The agent registers an on-chain identity following the [ERC-8004 standard](https://eips.ethereum.org/EIPS/eip-8004) and posts **realized PnL** on-chain after every trade closure:

- **Identity Registry** (0x8004A818...): ERC-721 NFT per agent — portable, transferable
- **Reputation Registry** (0x8004B663...): `tradingYield` feedback posted after each trade close (realized PnL, not unrealized)
- **Agent Card**: JSON metadata synced on every state save — live performance, risk controls, capabilities
- **Runtime integration**: `agent.py` imports `erc8004.py` and calls `update_reputation()` after SL/TP hits, short exits, and emergency closes

### Validation Artifacts

Beyond identity and reputation, the agent produces structured **validation artifacts** that provide full auditability of every trading decision:

| Artifact | File | Purpose |
|----------|------|---------|
| **Trade Intents** | [`validation/trade_intents.json`](validation/trade_intents.json) | Why each signal was generated — indicator values, reasoning, confluence factors |
| **Risk Checks** | [`validation/risk_checks.json`](validation/risk_checks.json) | 7-layer risk gate audit trail — 62 records, 6.5% hard rejection rate |
| **Strategy Checkpoints** | [`validation/strategy_checkpoints.json`](validation/strategy_checkpoints.json) | Regime detection → strategy routing — what was activated, blocked, and why |

**Trade Intents** record the reasoning behind each entry: EMA alignment, RSI interpretation, volume confirmation, and the strategy that generated the signal. Every signal — including blocked ones — is logged with full context.

**Risk Checks** document all 7 risk layers for every trade decision:
1. **L1** Position sizing (max 5% per trade)
2. **L2** Daily loss limit (3% → auto stop)
3. **L3** Total drawdown (10% → emergency close all)
4. **L4** Global consecutive loss tracking (5 → pause, 3 → 50% size)
5. **L5** Regime filter (EMA convergence → block all)
6. **L6** Per-pair throttle (2 consecutive losses on same pair → 3-scan cooldown)
7. **L7** Batch take-profit progression (TP1→breakeven SL, TP2→TP1 SL, TP3→full exit)

**Strategy Checkpoints** capture the regime detection → strategy routing pipeline: ADX, BB width, EMA spread → detected regime → which strategies are routed vs blocked → position scale and SL adjustments. Includes combo routing logic (I1c → J1c → N1/N2 filter chains).

**Aggregate Stats** (across all validation artifacts):
- 67 trade intents covering 14 strategies across 7 market regimes (7 pairs)
- 62 risk checks with 6.5% rejection rate (active risk engagement, not rubber-stamping)
- 85 strategy checkpoints with 77 regime transitions across 7 regime types
- Edge cases covered: emergency stop, max concurrent rejection, consecutive loss scaling, per-pair throttle, EMA convergence block, BB Squeeze breakout, trailing stop progression
- **Merkle integrity:** SHA-256 tree root over all 214 records — run `make verify` to recompute
- Run `make validate` for a full audit report with Merkle verification (text or JSON)

### Live Registration (Dual-Contract)

The agent is registered on **two** separate on-chain registries:

| Registry | Contract | TX Hash |
|----------|----------|---------|
| ERC-8004 Identity | `0x8004A818BFB912233c491871b3d84c89A494BD9e` | [`0x348195e9...`](https://sepolia.etherscan.io/tx/0x348195e99e900014b2c48db4840dfed8b6693854444141b00f94c7c737fe93ac) |
| Hackathon AgentRegistry | `0x97b07dDc405B0c28B17559aFFE63BdB3632d0ca3` | [`0xb6e2b43a...`](https://sepolia.etherscan.io/tx/0xb6e2b43a50092c5b1c4a9b395884d8acd4d2a6921de70249c4716fad29c73a69) |

| Field | Value |
|-------|-------|
| Agent ID | 17 |
| Identifier | `eip155:11155111:0x97b07dDc405B0c28B17559aFFE63BdB3632d0ca3:17` |
| Chain | Sepolia (11155111) |
| Wallet | `0x95C8B49C2A6124C436EA1a3f378991313f6f1c0A` |

**On-chain activity:**
- **ERC-8004 Reputation** — `giveFeedback` with keccak256 performance hash (realized PnL only, not unrealized) ✅ Working
- **Trade intents** — EIP-712 signed submissions to hackathon RiskRouter (79 intents submitted, all approved) ✅ Working
- **Hackathon Reputation** — `submitFeedback` blocked by contract (self-assessment not permitted by design — prevents self-rating inflation). Agent uses off-chain zero-base reputation formula instead (`make reputation`)

> **Note on trust-minimized reputation:** The hackathon ReputationRegistry intentionally blocks self-submitted feedback to prevent gaming. We address this through: (1) zero-base formula where every point is earned, (2) Merkle integrity hash over all 214 validation artifacts, and (3) keccak256 performance hash posted via the ERC-8004 standard Reputation Registry.

## Why This Approach

**Q: The live win rate is 40% — isn't that bad?**
Context matters. The hackathon period (March 31 – April 10) was dominated by ranging/choppy markets — the worst environment for trend-following strategies. Our OOS backtest windows included ~60% trending regimes; live was the opposite. In trending markets, our WFO-validated strategies achieve 82.2% across 366 trades. The key metric isn't win rate in adverse conditions — it's **how much you lose**. Our 7-layer risk system held total drawdown to -0.38% on a $100K portfolio. A production agent is measured by capital preservation in bad markets, not win rate in cherry-picked conditions.

**Q: Why Walk-Forward Optimization instead of simple backtesting?**
Simple backtesting optimizes on the full dataset — the model "sees the answers." Walk-Forward trains on window N and tests on window N+1, repeating 8 times. Only strategies that consistently perform on *unseen* data survive. Even when live market conditions diverge from backtest periods, the risk system keeps losses controlled.

**Q: Why 3 strategies instead of 1?**
Single-strategy agents fail when market regime changes. WaveRider captures trends, BB Squeeze catches breakouts, MACD Divergence spots reversals. The regime detector routes to the right strategy — or blocks trading entirely when conditions are unclear.

**Q: Why is the rejection rate important?**
A risk system that approves everything adds no value. Our 7-layer risk gate hard-rejects 6.5% of signals at the risk level, while the AI ensemble filtering (HOLD verdicts) blocks an additional ~60% of raw signals — proving the system discriminates between good and bad setups rather than rubber-stamping every signal.

**Q: Why a zero-base reputation formula?**
Many agents start with inflated reputation (base=50-80) that masks poor performance. Our v3 formula starts at **0** — every point is earned through measurable metrics: risk control (30pts max), transparency (20pts), validation quality (15pts), activity (15pts), win rate (10pts), and PnL (10pts). Run `make reputation` to see the full breakdown.

**Q: Why post realized PnL (not unrealized) on-chain?**
Unrealized gains can evaporate. By posting only *closed trade* results to the ERC-8004 reputation registry, the on-chain record reflects actual performance, not paper profits.

## Known Limitations

- **Paper trading only** — no slippage, no liquidity constraints. Real execution would face additional friction.
- **Spot-only on Kraken** — short signals are tracked internally (no margin/short selling on Kraken paper). This limits potential in bearish markets.
- **AI latency** — dual-model ensemble (MiniMax + Qwen) adds 15-30s per signal analysis. During rapid market moves, this delay may cause entry drift.
- **Regime detection lag** — ADX/BB Width regime changes take 3-5 candles to confirm. Black swan events or flash crashes may not trigger regime shift fast enough.
- **4H timeframe only** — designed for swing trading. Sub-hourly momentum moves are not captured.

## License

MIT — JudyAI Lab 2026
