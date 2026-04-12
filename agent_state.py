"""
Agent state management — save/load, ERC-8004 sync, reputation, on-chain intents.

Extracted from agent.py to keep main agent under 800 lines.
All functions are standalone helpers that operate on the agent instance (passed as `agent`).
"""
import fcntl
import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path

from erc8004 import (
    update_reputation, load_agent_card, save_agent_card,
    get_live_performance,
)
from hackathon_chain import (
    submit_trade_intent as _submit_intent,
    post_checkpoint as _post_checkpoint,
    get_agent_id as _get_onchain_agent_id,
)

logger = logging.getLogger(__name__)

# Re-use paths from agent.py (will be set by caller)
LOG_DIR = Path(__file__).parent / "logs"
TRADE_LOG_PATH = LOG_DIR / "trade_log.jsonl"
STATE_PATH = LOG_DIR / "agent_state.json"


def save_state(agent) -> None:
    """Save agent state to disk for crash recovery.

    Uses file-level locking (fcntl) to prevent race conditions
    between scan and monitor cron processes.
    """
    state = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scan_count": agent.scan_count,
        "last_full_scan": agent.last_full_scan,
        "open_positions": len(agent.executor.positions),
        "positions": {
            pair: {
                "cli_pair": pos.cli_pair,
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "volume": pos.volume,
                "sl_price": pos.sl_price,
                "tp1_price": pos.tp1_price,
                "tp2_price": pos.tp2_price,
                "tp3_price": pos.tp3_price,
                "tp1_hit": pos.tp1_hit,
                "tp2_hit": pos.tp2_hit,
                "remaining_pct": pos.remaining_pct,
                "source": pos.source,
                "opened_at": pos.opened_at.isoformat(),
            }
            for pair, pos in agent.executor.positions.items()
        },
        "risk": {
            "total_realized_pnl": agent.risk.total_realized_pnl,
            "peak_balance": agent.risk.peak_balance,
            "consecutive_losses": agent.risk.consecutive_losses,
            "position_scale": agent.risk.position_scale,
            "daily_date": agent.risk.daily_stats.date,
            "daily_trades": agent.risk.daily_stats.trades_count,
            "daily_pnl": agent.risk.daily_stats.realized_pnl,
            "daily_stopped": agent.risk.daily_stats.is_stopped,
            "daily_stop_reason": agent.risk.daily_stats.stop_reason,
            "pair_consecutive_losses": agent.risk.pair_consecutive_losses,
            "pair_cooldown": agent.risk.pair_cooldown,
        },
    }
    # Atomic write with file lock to prevent race conditions
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(state, f, indent=2, default=str)
        f.flush()
    tmp_path.rename(STATE_PATH)  # Atomic rename

    # Sync live performance to ERC-8004 Agent Card
    sync_agent_card(agent)


def sync_agent_card(agent) -> None:
    """Update ERC-8004 Agent Card with current performance data."""
    try:
        card = load_agent_card()
        perf = get_live_performance()
        if perf:
            perf["realizedPnl"] = agent.risk.total_realized_pnl
            realized_pnl_pct = (
                (agent.risk.total_realized_pnl / agent.risk.initial_capital) * 100
                if agent.risk.initial_capital > 0 else 0
            )
            perf["realizedPnlPct"] = realized_pnl_pct

            # Compute win rate + Sharpe from trade log
            closes = []
            if TRADE_LOG_PATH.exists():
                for line in TRADE_LOG_PATH.read_text().splitlines():
                    try:
                        t = json.loads(line)
                        if t.get("type") == "close" and abs(t.get("pnl", 0)) >= 0.01:
                            closes.append(t)
                    except json.JSONDecodeError:
                        pass
            win_count = sum(1 for t in closes if t["pnl"] > 0)
            total_count = len(closes)
            perf["winRate"] = round(win_count / total_count * 100, 1) if total_count else 0
            perf["totalTrades"] = total_count
            perf["wins"] = win_count
            perf["losses"] = total_count - win_count

            # Sharpe ratio (annualized, daily returns proxy)
            if total_count >= 2:
                pnls = [t["pnl"] for t in closes]
                mean_pnl = statistics.mean(pnls)
                std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 1
                perf["sharpeRatio"] = round(
                    (mean_pnl / std_pnl) * (252 ** 0.5) if std_pnl > 0 else 0, 2
                )

            # Max drawdown
            perf["maxDrawdown"] = round(
                abs(min(0, realized_pnl_pct)), 2
            )

            card["livePerformance"] = perf

        # Enrich validation artifact counts
        val_dir = Path(__file__).parent / "validation"
        for key, fname in [
            ("tradeIntentsCount", "trade_intents.json"),
            ("riskChecksCount", "risk_checks.json"),
            ("strategyCheckpointsCount", "strategy_checkpoints.json"),
        ]:
            fpath = val_dir / fname
            if fpath.exists():
                try:
                    data = json.loads(fpath.read_text())
                    count = len(data.get("records", data)) if isinstance(data, dict) else len(data)
                    card.setdefault("validationArtifacts", {})[key] = count
                except (json.JSONDecodeError, TypeError):
                    pass

        # Enrich reputation from local score file
        rep_path = Path(__file__).parent / "reputation_score.json"
        if rep_path.exists():
            try:
                rep_data = json.loads(rep_path.read_text())
                card["reputation"] = {
                    "score": rep_data.get("score", 0),
                    "winRate": rep_data.get("win_rate", 0),
                    "totalTrades": rep_data.get("total_trades", 0),
                    "lastUpdate": rep_data.get("timestamp", ""),
                    "checkpointCount": card.get("validationArtifacts", {}).get(
                        "strategyCheckpointsCount", 0
                    ),
                }
            except (json.JSONDecodeError, TypeError):
                pass

        save_agent_card(card)
    except Exception as e:
        logger.warning("Agent card sync skipped: %s", e)


def post_reputation(agent, reason: str = "trade_close") -> None:
    """Post realized PnL to ERC-8004 Reputation Registry + local persistence."""
    try:
        realized_pnl_pct = (
            (agent.risk.total_realized_pnl / agent.risk.initial_capital) * 100
            if agent.risk.initial_capital > 0 else 0
        )
        update_reputation(
            realized_pnl=agent.risk.total_realized_pnl,
            realized_pnl_pct=realized_pnl_pct,
        )
        # Persist reputation score locally for dashboard/validation
        rep_path = Path(__file__).parent / "reputation_score.json"
        closes = []
        if TRADE_LOG_PATH.exists():
            for line in TRADE_LOG_PATH.read_text().splitlines():
                try:
                    t = json.loads(line)
                    if t.get("type") == "close" and "pnl" in t:
                        closes.append(t)
                except json.JSONDecodeError:
                    pass
        # Exclude zero-PnL partial exits (AI_REDUCE_50pct with $0) from win rate
        meaningful = [t for t in closes if abs(t["pnl"]) >= 0.01]
        win_count = sum(1 for t in meaningful if t["pnl"] > 0)
        total_count = len(meaningful)
        win_rate = (win_count / total_count * 100) if total_count > 0 else 0
        # Multi-factor reputation score:
        # - Base: 60 (active agent with risk management)
        # - Win rate bonus: up to +12 (scaled from 30%-70% range)
        # - PnL bonus: up to +8 (capped, penalty for deep losses)
        # - Trade activity bonus: up to +6 (more trades = more data)
        # - Risk mgmt bonus: up to +7 (drawdown < 5% = disciplined)
        # - Validation artifacts bonus: up to +5 (complete audit trail)
        # - Uptime/consistency bonus: up to +5 (continuous operation)
        wr_score = min(12, max(0, (win_rate - 30) / 40 * 12))
        pnl_score = min(8, max(-3, realized_pnl_pct * 2))
        activity_score = min(6, total_count * 0.3)
        drawdown_pct = abs(min(0, realized_pnl_pct))
        risk_score = max(0, 7 - drawdown_pct * 2)
        # Validation artifacts quality bonus
        val_dir = Path(__file__).parent / "validation"
        artifact_count = 0
        for fname in ["trade_intents.json", "risk_checks.json", "strategy_checkpoints.json"]:
            fpath = val_dir / fname
            if fpath.exists():
                try:
                    data = json.loads(fpath.read_text())
                    artifact_count += len(data.get("records", data)) if isinstance(data, dict) else len(data)
                except (json.JSONDecodeError, TypeError):
                    pass
        artifact_score = min(5, artifact_count / 40)  # 200 records = max 5
        # Uptime bonus: agent has been running consistently (scan count)
        uptime_score = min(5, agent.scan_count / 20)  # 100 scans = max 5
        raw_score = 60 + wr_score + pnl_score + activity_score + risk_score + artifact_score + uptime_score

        rep_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "realized_pnl": round(agent.risk.total_realized_pnl, 2),
            "realized_pnl_pct": round(realized_pnl_pct, 4),
            "win_rate": round(win_rate, 1),
            "total_trades": total_count,
            "wins": win_count,
            "losses": total_count - win_count,
            "score": min(100, max(0, int(raw_score))),
        }
        rep_path.write_text(json.dumps(rep_data, indent=2) + "\n")
    except Exception as e:
        logger.warning("Reputation update skipped: %s", e)


def post_onchain_intent(pair: str, action: str, amount_usd: float) -> None:
    """Submit trade intent to shared RiskRouter (non-blocking)."""
    if _get_onchain_agent_id() is None:
        return  # Not registered yet
    try:
        result = _submit_intent(pair, action, amount_usd)
        if result.get("error"):
            logger.warning("On-chain intent skipped: %s", result["error"])
        elif result.get("approved"):
            logger.info("On-chain intent APPROVED: %s %s $%.0f", action, pair, amount_usd)
        else:
            logger.warning("On-chain intent REJECTED: %s", result.get("rejection_reason", ""))
    except Exception as e:
        logger.warning("On-chain intent failed (non-fatal): %s", e)


def post_onchain_checkpoint(data: dict, score: int, notes: str) -> None:
    """Post reasoning checkpoint to shared ValidationRegistry (non-blocking)."""
    if _get_onchain_agent_id() is None:
        return
    try:
        result = _post_checkpoint(data, score=score, notes=notes)
        if result:
            logger.info("Checkpoint posted: %s", result["tx_hash"][:16])
    except Exception as e:
        logger.warning("Checkpoint failed (non-fatal): %s", e)


def load_state(agent) -> None:
    """Restore agent state from disk after restart."""
    if not STATE_PATH.exists():
        return

    try:
        with open(STATE_PATH) as f:
            fcntl.flock(f, fcntl.LOCK_SH)  # Shared lock for reads
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load state: {e}")
        return

    # Restore scan tracking
    agent.scan_count = state.get("scan_count", 0)
    agent.last_full_scan = state.get("last_full_scan", 0)

    # Restore positions
    from executor import Position
    positions_data = state.get("positions", {})
    for pair, pdata in positions_data.items():
        opened_at = datetime.fromisoformat(pdata["opened_at"]) if pdata.get("opened_at") else datetime.now(timezone.utc)
        pos = Position(
            pair=pair,
            cli_pair=pdata["cli_pair"],
            direction=pdata["direction"],
            entry_price=pdata["entry_price"],
            volume=pdata["volume"],
            sl_price=pdata["sl_price"],
            tp1_price=pdata["tp1_price"],
            tp2_price=pdata.get("tp2_price", 0),
            tp3_price=pdata.get("tp3_price", 0),
            opened_at=opened_at,
            source=pdata.get("source", ""),
            tp1_hit=pdata.get("tp1_hit", False),
            tp2_hit=pdata.get("tp2_hit", False),
            remaining_pct=pdata.get("remaining_pct", 1.0),
        )
        agent.executor.positions[pair] = pos

    # Restore risk state
    risk_data = state.get("risk", {})
    agent.risk.total_realized_pnl = risk_data.get("total_realized_pnl", 0)
    agent.risk.peak_balance = risk_data.get("peak_balance", agent.risk.initial_capital)
    agent.risk.consecutive_losses = risk_data.get("consecutive_losses", 0)
    agent.risk.position_scale = risk_data.get("position_scale", 1.0)
    agent.risk.open_position_count = len(positions_data)

    # Restore per-pair risk state
    agent.risk.pair_consecutive_losses = risk_data.get("pair_consecutive_losses", {})
    agent.risk.pair_cooldown = risk_data.get("pair_cooldown", {})

    # Restore daily stats if same day
    if risk_data.get("daily_date") == agent.risk._today():
        agent.risk.daily_stats.trades_count = risk_data.get("daily_trades", 0)
        agent.risk.daily_stats.realized_pnl = risk_data.get("daily_pnl", 0)
        agent.risk.daily_stats.is_stopped = risk_data.get("daily_stopped", False)
        agent.risk.daily_stats.stop_reason = risk_data.get("daily_stop_reason", "")

    restored = len(positions_data)
    if restored > 0:
        logger.info(f"State restored: {restored} positions, total PnL ${agent.risk.total_realized_pnl:+.2f}")
    else:
        logger.info("State loaded (no open positions)")
