"""
Configuration — pair mappings, strategy parameters, risk limits
"""
import os
from dataclasses import dataclass, field

# ── Pair Mapping ──────────────────────────────────────────────
# Standard pair name → Kraken CLI pair → Kraken API response key
PAIR_MAP = {
    "BTC/USDT": {"cli": "BTCUSD", "response_key": "XXBTZUSD"},
    "ETH/USDT": {"cli": "ETHUSD", "response_key": "XETHZUSD"},
    "SOL/USDT": {"cli": "SOLUSD", "response_key": "SOLUSD"},
    "XRP/USDT": {"cli": "XRPUSD", "response_key": "XXRPZUSD"},
    "DOGE/USDT": {"cli": "DOGEUSD", "response_key": "XDGUSD"},
    "BNB/USDT": {"cli": "BNBUSD", "response_key": "BNBUSD"},
    "LINK/USDT": {"cli": "LINKUSD", "response_key": "LINKUSD"},
}

# Reverse lookup: response key → standard pair name
RESPONSE_KEY_TO_PAIR = {v["response_key"]: k for k, v in PAIR_MAP.items()}
CLI_TO_PAIR = {v["cli"]: k for k, v in PAIR_MAP.items()}

# Active trading pairs for the hackathon
ACTIVE_PAIRS = list(PAIR_MAP.keys())

# ── Kraken OHLC Intervals ────────────────────────────────────
# Kraken CLI accepts interval in minutes
INTERVAL_MAP = {
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}

# ── Strategy Parameters (ALL 12 WFO-validated robust_configs) ──
# Full deployment for hackathon competition — 5 LONG + 7 SHORT
STRATEGY_PARAMS = {
    "long": {
        "BNB/USDT":  {"rsi_threshold": 52, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.5, "btc_filter": False},   # WFO 80.3% OOS, 100% consistency
        "ETH/USDT":  {"rsi_threshold": 55, "atr_sl_multiplier": 2.0, "vol_multiplier": 2.0, "btc_filter": False},   # WFO 93.3% OOS, 100% consistency
        "SOL/USDT":  {"rsi_threshold": 52, "atr_sl_multiplier": 2.0, "vol_multiplier": 3.0, "btc_filter": True},    # WFO 72.5% OOS, 87.5% consistency
        "LINK/USDT": {"rsi_threshold": 55, "atr_sl_multiplier": 2.0, "vol_multiplier": 3.0, "btc_filter": False},   # WFO 100% OOS, 100% consistency
        "XRP/USDT":  {"rsi_threshold": 55, "atr_sl_multiplier": 2.0, "vol_multiplier": 2.5, "btc_filter": True},    # WFO 78.7% OOS, 100% consistency
    },
    "short": {
        "BTC/USDT":  {"rsi_threshold": 48, "atr_sl_multiplier": 1.5, "vol_multiplier": 3.0, "btc_filter": False},   # WFO 75.4% OOS, 75% consistency
        "ETH/USDT":  {"rsi_threshold": 42, "atr_sl_multiplier": 2.0, "vol_multiplier": 4.5, "btc_filter": True},    # WFO 97.8% OOS, 100% consistency
        "SOL/USDT":  {"rsi_threshold": 42, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.5, "btc_filter": False},   # WFO 75.9% OOS, 87.5% consistency
        "XRP/USDT":  {"rsi_threshold": 42, "atr_sl_multiplier": 2.5, "vol_multiplier": 2.0, "btc_filter": False},   # WFO 75.5% OOS, 87.5% consistency
        "DOGE/USDT": {"rsi_threshold": 38, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.0, "btc_filter": False},   # WFO 87.8% OOS, 100% consistency
        "BNB/USDT":  {"rsi_threshold": 38, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.0, "btc_filter": False},   # WFO 76.7% OOS, 85.7% consistency
        "LINK/USDT": {"rsi_threshold": 38, "atr_sl_multiplier": 1.0, "vol_multiplier": 2.0, "btc_filter": False},   # WFO 86.1% OOS, 100% consistency
    },
}

# Blacklisted combos (OOS-validated as unprofitable)
# NOTE: BTC/USDT_long is allowed in TREND_EXHAUSTION regime via REGIME_GRID (Q27 83.3%)
BLACKLIST = {
    "BTC/USDT_long",    # WFO OOS 40% — BTC only short (override: trend_exhaustion)
    "DOGE/USDT_long",   # 90d WR 30.3% — degraded (override: ranging via REGIME_GRID)
    "BNB/USDT_long_old", # only current params valid
    # combo_runner sweep (2026-04-07): 19 cells FAIL, removed 4 cells from REGIME_GRID
    "DOGE/USDT_long_high_volatility",   # WFO 0 OOS trades, low_sample
    "ETH/USDT_short_high_volatility",   # WFO max raw WR 52%, can't reach 55%
    "SOL/USDT_long_breakout_forming",   # WFO 1 OOS trade, degraded 62.5%
    "XRP/USDT_short_trending_down",     # WFO 4 OOS trades, Gap 33%, fail 40%
}


# ── 36-Cell Regime Strategy Grid ─────────────────────────────
# Key: "{pair}_{direction}_{regime}" → per-cell optimized params
# Source: WFO OOS validation + Q-series backtest (Phase 4)
# Cells not in grid → blocked (no validated strategy for that combo)
# "strategies" field: list of strategy functions to check for this cell
#   - "waverider": EMA+RSI+Volume trend-following
#   - "bb_squeeze": Bollinger Band compression breakout
#   - "macd_div": MACD histogram divergence reversal
REGIME_GRID: dict[str, dict] = {
    # ═══ TRENDING_UP — longs thrive, counter-trend shorts possible ═══
    "BNB/USDT_long_trending_up":  {
        "rsi_threshold": 52, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.5,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 80.3, "source": "WFO",
    },
    "ETH/USDT_long_trending_up":  {
        "rsi_threshold": 55, "atr_sl_multiplier": 2.0, "vol_multiplier": 2.0,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 93.3, "source": "WFO",
    },
    "SOL/USDT_long_trending_up":  {
        "rsi_threshold": 52, "atr_sl_multiplier": 2.0, "vol_multiplier": 3.0,
        "btc_filter": True, "strategies": ["waverider"],
        "oos_wr": 72.5, "source": "WFO",
    },
    "LINK/USDT_long_trending_up": {
        "rsi_threshold": 55, "atr_sl_multiplier": 2.0, "vol_multiplier": 3.0,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 100.0, "source": "WFO",
    },
    "XRP/USDT_long_trending_up":  {
        "rsi_threshold": 55, "atr_sl_multiplier": 2.0, "vol_multiplier": 2.5,
        "btc_filter": True, "strategies": ["waverider"],
        "oos_wr": 78.7, "source": "WFO",
    },
    "BTC/USDT_short_trending_up": {
        # Q8_HS_Volume: counter-trend short catches reversals in uptrend
        "rsi_threshold": 48, "atr_sl_multiplier": 1.5, "vol_multiplier": 3.0,
        "btc_filter": False, "strategies": ["macd_div", "waverider"],
        "oos_wr": 85.7, "source": "Q8",
    },

    # ═══ TRENDING_DOWN — shorts thrive, longs blocked ═══
    "BTC/USDT_short_trending_down":  {
        # Q3_MACD_ST: MACD divergence + trend confirmation
        "rsi_threshold": 48, "atr_sl_multiplier": 1.5, "vol_multiplier": 3.0,
        "btc_filter": False, "strategies": ["macd_div", "waverider"],
        "oos_wr": 75.4, "source": "WFO+Q3",
    },
    "ETH/USDT_short_trending_down":  {
        "rsi_threshold": 42, "atr_sl_multiplier": 2.0, "vol_multiplier": 4.5,
        "btc_filter": True, "strategies": ["waverider", "macd_div"],
        "oos_wr": 97.8, "source": "WFO",
    },
    "SOL/USDT_short_trending_down": {
        # Q8: 100% OOS in downtrend
        "rsi_threshold": 42, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.5,
        "btc_filter": False, "strategies": ["waverider", "macd_div"],
        "oos_wr": 100.0, "source": "WFO+Q8",
    },
    # REMOVED: XRP/USDT_short_trending_down — sweep FAIL (Gap 33%, 4 OOS trades)
    "DOGE/USDT_short_trending_down": {
        "rsi_threshold": 38, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.0,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 87.8, "source": "WFO",
    },
    "BNB/USDT_short_trending_down":  {
        "rsi_threshold": 38, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.0,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 76.7, "source": "WFO",
    },
    "LINK/USDT_short_trending_down": {
        "rsi_threshold": 38, "atr_sl_multiplier": 1.0, "vol_multiplier": 2.0,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 86.1, "source": "WFO",
    },

    # ═══ RANGING — mean reversion, selective entries only ═══
    "XRP/USDT_short_ranging": {
        # Q2: 90% OOS — ranging mean reversion short
        "rsi_threshold": 58, "atr_sl_multiplier": 2.0, "vol_multiplier": 1.5,
        "btc_filter": False, "strategies": ["waverider", "bb_squeeze"],
        "oos_wr": 90.0, "source": "Q2",
    },
    "DOGE/USDT_long_ranging": {
        # Q10: 100% OOS — ranging mean reversion long
        "rsi_threshold": 42, "atr_sl_multiplier": 2.0, "vol_multiplier": 1.5,
        "btc_filter": False, "strategies": ["waverider", "bb_squeeze"],
        "oos_wr": 100.0, "source": "Q10",
    },

    # ═══ HIGH_VOLATILITY — reduced size, wider SL ═══
    # Using validated trending params + wider ATR SL for high vol conditions
    # Regime already applies 50% position_size_mult
    # HIGH_VOL SHORT — for bearish HV conditions
    "BTC/USDT_short_high_volatility": {
        "rsi_threshold": 48, "atr_sl_multiplier": 2.5, "vol_multiplier": 2.5,
        "btc_filter": False, "strategies": ["macd_div", "waverider"],
        "oos_wr": 75.4, "source": "WFO+HV",
    },
    "SOL/USDT_short_high_volatility": {
        "rsi_threshold": 42, "atr_sl_multiplier": 2.5, "vol_multiplier": 2.0,
        "btc_filter": False, "strategies": ["waverider", "macd_div"],
        "oos_wr": 75.9, "source": "WFO+HV",
    },
    "XRP/USDT_short_high_volatility": {
        "rsi_threshold": 42, "atr_sl_multiplier": 3.0, "vol_multiplier": 1.5,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 75.5, "source": "WFO+HV",
    },
    "DOGE/USDT_short_high_volatility": {
        "rsi_threshold": 38, "atr_sl_multiplier": 2.0, "vol_multiplier": 2.0,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 87.8, "source": "WFO",
    },
    "BNB/USDT_short_high_volatility": {
        "rsi_threshold": 38, "atr_sl_multiplier": 2.5, "vol_multiplier": 1.5,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 76.7, "source": "WFO+HV",
    },
    "LINK/USDT_short_high_volatility": {
        "rsi_threshold": 38, "atr_sl_multiplier": 2.0, "vol_multiplier": 1.5,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 86.1, "source": "WFO+HV",
    },
    # HIGH_VOL LONG — based on trending_up validated params + wider SL
    "ETH/USDT_long_high_volatility": {
        "rsi_threshold": 55, "atr_sl_multiplier": 2.5, "vol_multiplier": 1.5,
        "btc_filter": False, "strategies": ["waverider", "bb_squeeze"],
        "oos_wr": 93.3, "source": "WFO+HV",
    },
    "SOL/USDT_long_high_volatility": {
        "rsi_threshold": 52, "atr_sl_multiplier": 2.5, "vol_multiplier": 1.5,
        "btc_filter": True, "strategies": ["waverider"],
        "oos_wr": 72.5, "source": "WFO+HV",
    },
    "XRP/USDT_long_high_volatility": {
        "rsi_threshold": 55, "atr_sl_multiplier": 3.0, "vol_multiplier": 1.5,
        "btc_filter": True, "strategies": ["waverider"],
        "oos_wr": 78.7, "source": "WFO+HV",
    },
    "LINK/USDT_long_high_volatility": {
        "rsi_threshold": 55, "atr_sl_multiplier": 2.5, "vol_multiplier": 1.5,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 100.0, "source": "WFO+HV",
    },
    "BNB/USDT_long_high_volatility": {
        "rsi_threshold": 52, "atr_sl_multiplier": 2.5, "vol_multiplier": 1.5,
        "btc_filter": False, "strategies": ["waverider"],
        "oos_wr": 80.3, "source": "WFO+HV",
    },

    # ═══ TREND_EXHAUSTION — ADX declining, reversal setups ═══
    "BTC/USDT_long_trend_exhaustion": {
        # Q27: 83.3% OOS — BTC long ONLY when trend exhausting
        # This overrides BTC/USDT_long blacklist for this specific regime
        "rsi_threshold": 52, "atr_sl_multiplier": 2.0, "vol_multiplier": 2.5,
        "btc_filter": False, "strategies": ["macd_div", "waverider"],
        "oos_wr": 83.3, "source": "Q27",
    },

    # ═══ BREAKOUT_FORMING — BB squeeze, wait for expansion ═══
    "ETH/USDT_long_breakout_forming": {
        "rsi_threshold": 55, "atr_sl_multiplier": 2.0, "vol_multiplier": 2.0,
        "btc_filter": False, "strategies": ["bb_squeeze", "waverider"],
        "oos_wr": 93.3, "source": "WFO+BB",
    },
    # REMOVED: SOL/USDT_long_breakout_forming — sweep FAIL (1 OOS trade, degraded)
    "BNB/USDT_long_breakout_forming": {
        "rsi_threshold": 52, "atr_sl_multiplier": 1.5, "vol_multiplier": 2.5,
        "btc_filter": False, "strategies": ["bb_squeeze", "waverider"],
        "oos_wr": 80.3, "source": "WFO+BB",
    },
}


# ── Risk Configuration ────────────────────────────────────────
@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 5.0        # Max 5% of portfolio per trade
    max_daily_loss_pct: float = 3.0      # Stop trading after 3% daily loss
    max_concurrent_positions: int = 5    # Max 5 open positions
    emergency_stop_pct: float = 10.0     # Emergency close all at 10% drawdown
    risk_per_trade_pct: float = 2.0      # Risk 2.0% per trade — competition sprint mode
    max_daily_trades: int = 10           # Max trades per day
    consecutive_loss_pause: int = 5      # Pause after 5 consecutive losses
    consecutive_loss_scale: float = 0.5  # Reduce position to 50% after 3 losses
    short_position_scale: float = 0.7    # Shorts = 70% of long size


RISK = RiskConfig()

# ── Agent Configuration ───────────────────────────────────────
INITIAL_BALANCE = 100_000.0  # Paper trading starting balance
SCAN_INTERVAL_SECONDS = 120  # 2 minutes between position checks (competition sprint)
FULL_SCAN_INTERVAL_HOURS = 0.5  # Full strategy scan every 30 min (final sprint — 3 days left)

# ── ERC-8004 (Sepolia Testnet) ────────────────────────────────
# Hackathon SHARED contract addresses (2026-04-06)
# Leaderboard uses shared contracts only
HACKATHON_AGENT_REGISTRY = "0x97b07dDc405B0c28B17559aFFE63BdB3632d0ca3"
HACKATHON_VAULT = "0x0E7CD8ef9743FEcf94f9103033a044caBD45fC90"
HACKATHON_RISK_ROUTER = "0xd6A6952545FF6E6E6681c2d15C59f9EB8F40FdBC"
HACKATHON_REPUTATION_REGISTRY = "0x423a9904e39537a9997fbaF0f220d79D7d545763"
HACKATHON_VALIDATION_REGISTRY = "0x92bF63E5C7Ac6980f237a7164Ab413BE226187F1"
# ERC-8004 standard singleton (for Agent Card metadata)
ERC8004_IDENTITY_CONTRACT = "0x8004A818BFB912233c491871b3d84c89A494BD9e"
ERC8004_REPUTATION_CONTRACT = "0x8004B663056A597Dffe9eCcC1965A193B7388713"
SEPOLIA_RPC = os.environ.get(
    "SEPOLIA_RPC", "https://ethereum-sepolia-rpc.publicnode.com"
)  # Override via .env; set SEPOLIA_RPC=https://sepolia.infura.io/v3/YOUR_KEY for Infura
SEPOLIA_CHAIN_ID = 11155111
AGENT_NAME = "JudyAI WaveRider"
AGENT_DESCRIPTION = (
    "AI trading agent with dual-model ensemble (MiniMax M2.7 + Qwen 2.5) "
    "and OOS-validated multi-strategy engine (82.2% win rate). Features: "
    "WaveRider EMA+RSI+Volume, BB Squeeze, MACD Divergence with "
    "regime-adaptive routing and 5-layer risk management."
)

# ── AI Analyst Configuration ──
AI_ENABLED = True                     # Master switch for AI analysis
AI_MODEL = "claude-sonnet"           # Primary: Claude Sonnet via subscription (claude -p)
AI_FALLBACK_MODEL = "qwen2.5:7b"    # Fallback: Ollama local
AI_BUDGET_PER_SCAN = 999.0            # MiniMax subscription; set high to avoid false budget blocks
AI_CHART_ENABLED = False             # MiniMax no vision, disable chart
AI_MIN_ENSEMBLE_SCORE = 50           # Minimum ensemble score to trade (lowered from 55 for competition)
AI_USE_ANTHROPIC = False             # Disable paid Claude API
