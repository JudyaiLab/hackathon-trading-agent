"""
ValidationWriter — Appends live trade records to validation artifact files.

Writes to:
  - validation/trade_intents.json (trade reasoning)
  - validation/risk_checks.json (5-layer risk audit)
  - validation/strategy_checkpoints.json (regime routing)

Thread-safe via fcntl file locking.
"""
import fcntl
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

VALIDATION_DIR = Path(__file__).parent / "validation"


def _atomic_append_record(filepath: Path, record: dict) -> bool:
    """Append a record to a validation JSON file atomically."""
    try:
        with open(filepath, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            data = json.load(f)
            records = data.get("records", [])
            records.append(record)
            data["records"] = records
            f.seek(0)
            json.dump(data, f, indent=2, default=str)
            f.truncate()
        return True
    except Exception as e:
        logger.warning("Failed to write validation record to %s: %s", filepath, e)
        return False


def _next_id(filepath: Path, prefix: str) -> str:
    """Get the next sequential ID for a validation file."""
    try:
        with open(filepath) as f:
            data = json.load(f)
        records = data.get("records", [])
        if records:
            last_id = records[-1].get("id", f"{prefix}-000")
            num = int(last_id.split("-")[1]) + 1
        else:
            num = 1
        return f"{prefix}-{num:03d}"
    except Exception:
        return f"{prefix}-100"


def write_trade_intent(
    pair: str,
    direction: str,
    strategy_source: str,
    regime: str,
    entry_price: float,
    sl_price: float,
    tp1_price: float,
    ai_verdict: str = "",
    ai_confidence: int = 0,
    ensemble_score: float = 0.0,
    reasoning: str = "",
    grid_cell: str = "",
    oos_wr: float = 0.0,
) -> None:
    """Record a live trade intent with full reasoning."""
    filepath = VALIDATION_DIR / "trade_intents.json"
    record_id = _next_id(filepath, "TI")

    risk_reward = 0.0
    if sl_price and entry_price and tp1_price:
        risk = abs(entry_price - sl_price)
        if risk > 0:
            risk_reward = round(abs(tp1_price - entry_price) / risk, 2)

    record = {
        "id": record_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": pair,
        "direction": direction.upper(),
        "strategy": strategy_source,
        "regime": regime.upper() if regime else "UNKNOWN",
        "reasoning": {
            "ai_verdict": ai_verdict,
            "ai_confidence": ai_confidence,
            "ensemble_score": ensemble_score,
            "strategy_signals": strategy_source,
            "detailed_reasoning": reasoning or f"Signal from {strategy_source} in {regime} regime",
            "grid_cell": grid_cell,
            "oos_win_rate": oos_wr,
        },
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp1_price": tp1_price,
        "risk_reward": risk_reward,
        "outcome": "PENDING",
        "pnl_pct": 0.0,
        "source": "LIVE_TRADE",
    }

    _atomic_append_record(filepath, record)
    logger.info("Validation: trade intent %s recorded for %s %s", record_id, pair, direction)


def update_trade_intent_outcome(
    pair: str,
    outcome: str,
    pnl_pct: float,
) -> None:
    """Update the most recent PENDING intent for a pair with outcome."""
    filepath = VALIDATION_DIR / "trade_intents.json"
    try:
        with open(filepath, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            data = json.load(f)
            records = data.get("records", [])
            for rec in reversed(records):
                if rec.get("pair") == pair and rec.get("outcome") == "PENDING":
                    rec["outcome"] = outcome
                    rec["pnl_pct"] = round(pnl_pct, 4)
                    break
            f.seek(0)
            json.dump(data, f, indent=2, default=str)
            f.truncate()
    except Exception as e:
        logger.warning("Failed to update trade intent outcome: %s", e)


def write_risk_check(
    pair: str,
    direction: str,
    trade_intent_id: str,
    portfolio_value: float,
    position_size_usd: float,
    daily_pnl: float,
    total_pnl: float,
    peak_balance: float,
    consecutive_losses: int,
    position_scale: float,
    regime: str,
    allowed: bool,
    block_reason: str = "",
) -> None:
    """Record a 5-layer risk check audit trail."""
    filepath = VALIDATION_DIR / "risk_checks.json"
    record_id = _next_id(filepath, "RC")

    initial_capital = 100000.0
    drawdown_pct = round((peak_balance - portfolio_value) / peak_balance * 100, 2) if peak_balance > 0 else 0
    daily_pnl_pct = round(daily_pnl / initial_capital * 100, 2) if initial_capital > 0 else 0
    size_pct = round(position_size_usd / portfolio_value * 100, 2) if portfolio_value > 0 else 0

    record = {
        "id": record_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tradeIntentId": trade_intent_id,
        "pair": pair,
        "checks": {
            "L1_positionSize": {
                "passed": size_pct <= 5.0,
                "portfolioValue": round(portfolio_value, 2),
                "maxRiskAmount": round(portfolio_value * 0.05, 2),
                "calculatedSize": round(position_size_usd, 2),
                "sizePct": size_pct,
                "note": f"Position sized at {size_pct}% — {'within' if size_pct <= 5.0 else 'exceeds'} 5% limit",
            },
            "L2_dailyLoss": {
                "passed": daily_pnl_pct > -3.0,
                "dailyPnl": round(daily_pnl, 2),
                "dailyPnlPct": daily_pnl_pct,
                "limit": -3.0,
                "note": f"Daily loss {abs(daily_pnl_pct)}% — {'within' if daily_pnl_pct > -3.0 else 'exceeds'} 3% limit",
            },
            "L3_totalDrawdown": {
                "passed": drawdown_pct < 10.0,
                "peakValue": round(peak_balance, 2),
                "currentValue": round(portfolio_value, 2),
                "drawdownPct": drawdown_pct,
                "limit": -10.0,
                "note": f"Drawdown {drawdown_pct}% from peak — {'within' if drawdown_pct < 10.0 else 'exceeds'} 10% limit",
            },
            "L4_consecutiveLoss": {
                "passed": consecutive_losses < 5,
                "consecutiveLosses": consecutive_losses,
                "pauseThreshold": 5,
                "sizeReductionThreshold": 3,
                "sizeScale": position_scale,
                "note": f"{consecutive_losses} consecutive losses — {'normal' if consecutive_losses < 3 else 'reduced'} sizing",
            },
            "L5_regimeFilter": {
                "passed": regime.upper() != "EMA_CONVERGENCE",
                "detectedRegime": regime.upper() if regime else "UNKNOWN",
                "allowedRegimes": ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY", "BREAKOUT_FORMING", "TREND_EXHAUSTION"],
                "note": f"Regime {regime} — {'trading allowed' if regime.upper() != 'EMA_CONVERGENCE' else 'BLOCKED'}",
            },
        },
        "allPassed": allowed,
        "blockReason": block_reason,
        "source": "LIVE_TRADE",
    }

    _atomic_append_record(filepath, record)
    logger.info("Validation: risk check %s recorded for %s", record_id, pair)


def write_strategy_checkpoint(
    pair: str,
    regime: str,
    previous_regime: str,
    routed_strategies: list[str],
    blocked_strategies: list[str],
    position_scale: float,
    sl_multiplier: float,
    grid_cell: str = "",
    oos_wr: float = 0.0,
    indicators: dict | None = None,
) -> None:
    """Record a regime detection to strategy routing decision."""
    filepath = VALIDATION_DIR / "strategy_checkpoints.json"
    record_id = _next_id(filepath, "SC")

    record = {
        "id": record_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": pair,
        "indicators": indicators or {},
        "detectedRegime": regime.upper() if regime else "UNKNOWN",
        "previousRegime": previous_regime.upper() if previous_regime else "UNKNOWN",
        "regimeChanged": regime != previous_regime,
        "routedStrategies": routed_strategies,
        "blockedStrategies": blocked_strategies,
        "positionScale": position_scale,
        "slMultiplier": sl_multiplier,
        "gridCell": grid_cell,
        "oosWinRate": oos_wr,
        "note": f"{'Grid cell ' + grid_cell + ' — ' if grid_cell else ''}{regime} regime, {len(routed_strategies)} strategies active",
        "source": "LIVE_SCAN",
    }

    _atomic_append_record(filepath, record)
    logger.info("Validation: checkpoint %s recorded for %s (%s)", record_id, pair, regime)
