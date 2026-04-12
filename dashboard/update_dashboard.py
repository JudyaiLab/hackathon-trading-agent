#!/usr/bin/env python3
"""
Dashboard data updater for JudyAI WaveRider hackathon trading agent.
Reads from agent logs, trade history, and on-chain state to produce
dashboard/data.json for the live status dashboard.

Run via cron every 5 minutes:
  */5 * * * * cd /path/to/hackathon-trading-agent && python3 dashboard/update_dashboard.py >> logs/dashboard_update.log 2>&1
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
DASHBOARD_DIR = BASE_DIR / "dashboard"
OUTPUT_FILE = DASHBOARD_DIR / "data.json"

AGENT_STATE_FILE = LOGS_DIR / "agent_state.json"
TRADE_LOG_FILE = LOGS_DIR / "trade_log.jsonl"
AGENT_CARD_FILE = BASE_DIR / "agent_card.json"
ONCHAIN_FILE = LOGS_DIR / "hackathon_onchain_state.json"
VALIDATION_DIR = BASE_DIR / "validation"


def load_json(path: Path) -> dict:
    """Load a JSON file, returning empty dict on failure."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[WARN] Could not load {path}: {e}", file=sys.stderr)
        return {}


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file, returning list of dicts."""
    entries = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        print(f"[WARN] Could not load {path}", file=sys.stderr)
    return entries


def get_kraken_paper_status() -> dict:
    """Try to get live paper trading status from kraken CLI."""
    try:
        result = subprocess.run(
            ["kraken", "paper", "status", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[WARN] Kraken CLI failed: {e}", file=sys.stderr)
    return {}


def build_portfolio(agent_state: dict, agent_card: dict, kraken: dict) -> dict:
    """Build portfolio overview — Kraken is the source of truth."""
    starting = 100000.0

    # Kraken paper status is the ONLY accurate source
    if kraken:
        current = kraken.get("current_value", starting)
        total_trades = kraken.get("total_trades", 0)
        # If no open positions, unrealized = 0
        positions = agent_state.get("positions", {})
        unrealized = kraken.get("unrealized_pnl", 0) if positions else 0.0
    else:
        live = agent_card.get("livePerformance", {})
        current = live.get("currentValue", starting)
        total_trades = live.get("totalTrades", 0)
        unrealized = 0.0

    # Total PnL = current_value - starting (includes fees)
    total_pnl = current - starting

    return {
        "starting_balance": starting,
        "current_value": current,
        "total_pnl": total_pnl,
        "total_pnl_pct": (total_pnl / starting * 100),
        "unrealized_pnl": unrealized,
        "unrealized_pnl_pct": (unrealized / starting * 100) if starting else 0,
        "total_trades": total_trades,
    }


def fetch_current_prices(pairs: list[str]) -> dict[str, float]:
    """Fetch current prices from Kraken for given pairs."""
    prices = {}
    pair_map = {
        "BTC/USDT": "XBTUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT",
        "BNB/USDT": "BNBUSDT", "ADA/USDT": "ADAUSDT", "DOT/USDT": "DOTUSDT",
        "AVAX/USDT": "AVAXUSDT", "LINK/USDT": "LINKUSDT",
    }
    kraken_pairs = [pair_map.get(p, p.replace("/", "")) for p in pairs]
    if not kraken_pairs:
        return prices
    try:
        import urllib.request
        url = f"https://api.kraken.com/0/public/Ticker?pair={','.join(kraken_pairs)}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data.get("result"):
            for orig_pair, kraken_pair in zip(pairs, kraken_pairs):
                for key, val in data["result"].items():
                    if kraken_pair.lower() in key.lower() or key.lower() in kraken_pair.lower():
                        prices[orig_pair] = float(val["c"][0])
                        break
    except Exception as e:
        print(f"[WARN] Price fetch failed: {e}", file=sys.stderr)
    return prices


def build_positions(agent_state: dict) -> list[dict]:
    """Build open positions list from agent state."""
    positions_raw = agent_state.get("positions", {})
    positions = []

    if not positions_raw:
        return positions

    # Fetch live prices for all open position pairs
    live_prices = fetch_current_prices(list(positions_raw.keys()))

    for pair, pos in positions_raw.items():
        if not isinstance(pos, dict):
            continue

        entry = pos.get("entry_price", 0)
        current = live_prices.get(pair, entry)
        volume = pos.get("volume", 0)
        direction = pos.get("direction", "unknown")

        # Calculate unrealized PnL
        if direction == "long":
            pnl = (current - entry) * volume
        elif direction == "short":
            pnl = (entry - current) * volume
        else:
            pnl = 0

        positions.append({
            "pair": pair,
            "direction": direction,
            "entry_price": entry,
            "current_price": current,
            "pnl": round(pnl, 2),
            "sl": pos.get("sl_price", 0),
            "tp1": pos.get("tp1_price", 0),
            "source": pos.get("source", ""),
        })

    return positions


def build_recent_trades(trade_log: list[dict], limit: int = 10) -> list[dict]:
    """Extract last N closed trades with PnL for chart display."""
    closed = [
        t for t in trade_log
        if t.get("type") == "close" and t.get("pnl") is not None and t.get("pnl") != 0
    ]
    recent = closed[-limit:]
    return [
        {
            "pair": t.get("pair", "???"),
            "pnl": t.get("pnl", 0),
            "reason": t.get("reason", ""),
        }
        for t in recent
    ]


def build_risk(agent_state: dict) -> dict:
    """Build risk manager status."""
    risk = agent_state.get("risk", {})
    starting = 100000.0
    peak = risk.get("peak_balance", starting)
    current_balance = peak + risk.get("total_realized_pnl", 0)
    drawdown = ((peak - current_balance) / peak * 100) if peak > 0 else 0

    return {
        "drawdown_pct": drawdown,
        "daily_pnl": risk.get("daily_pnl", 0),
        "consecutive_losses": risk.get("consecutive_losses", 0),
        "position_scale": risk.get("position_scale", 1.0),
        "daily_stopped": risk.get("daily_stopped", False),
        "daily_stop_reason": risk.get("daily_stop_reason", ""),
    }


def build_strategy(trade_log: list[dict], agent_card: dict) -> dict:
    """Build strategy engine statistics."""
    opens = [t for t in trade_log if t.get("type") == "open"]
    closes = [t for t in trade_log if t.get("type") == "close"]
    pnl_closes = [t for t in closes if t.get("pnl") is not None and abs(t.get("pnl", 0)) >= 0.01]
    wins = [t for t in pnl_closes if t["pnl"] > 0]

    executed = len(opens)
    # Count trades that went through AI filter
    ai_filtered = [t for t in opens if t.get("ai_verdict")]
    passed_ai = len(ai_filtered) if ai_filtered else executed
    # Estimate total raw signals (conservative: ~2 signals per scan on average)
    max_scan = max((t.get("scan", 0) for t in trade_log if t.get("scan")), default=0)
    total_signal_evals = max_scan * 2 if max_scan > 0 else executed * 3

    perf = agent_card.get("performance", {})
    caps = agent_card.get("capabilities", {}).get("trading", {})
    strategies = caps.get("strategies", [])
    short_strats = []
    for s in strategies:
        name = s.split("(")[0].strip() if "(" in s else s
        short_strats.append(name)

    win_rate = (len(wins) / len(pnl_closes) * 100) if pnl_closes else 0
    rejection_rate = ((total_signal_evals - executed) / total_signal_evals * 100) if total_signal_evals > 0 else 0

    return {
        "signals_generated": total_signal_evals,
        "signals_passed_ai": passed_ai,
        "signals_executed": executed,
        "rejection_rate": rejection_rate,
        "win_rate": win_rate,
        "backtest_win_rate": perf.get("oosWinRate", 0),
        "strategies": short_strats,
    }


def _query_onchain_scores(agent_id: int) -> dict:
    """Query on-chain reputation and validation scores via web3."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from hackathon_chain import _get_w3, _get_contract, _load_env
        from config import HACKATHON_REPUTATION_REGISTRY, HACKATHON_VALIDATION_REGISTRY
        from hackathon_abi import HACKATHON_REPUTATION_ABI, VALIDATION_REGISTRY_ABI
        _load_env()
        w3 = _get_w3()
        rep = _get_contract(w3, HACKATHON_REPUTATION_REGISTRY, HACKATHON_REPUTATION_ABI)
        val = _get_contract(w3, HACKATHON_VALIDATION_REGISTRY, VALIDATION_REGISTRY_ABI)
        rep_score = rep.functions.getAverageScore(agent_id).call()
        val_score = val.functions.getAverageValidationScore(agent_id).call()
        return {"reputation_score": rep_score, "validation_score": val_score}
    except Exception as e:
        print(f"[WARN] On-chain query failed: {e}", file=sys.stderr)
        return {}


def _count_validation_records() -> dict:
    """Count records in each validation artifact file."""
    counts = {}
    for name in ["trade_intents.json", "risk_checks.json", "strategy_checkpoints.json"]:
        path = VALIDATION_DIR / name
        try:
            with open(path) as f:
                data = json.load(f)
            records = data.get("records", data) if isinstance(data, dict) else data
            counts[name.replace(".json", "")] = len(records)
        except Exception:
            counts[name.replace(".json", "")] = 0
    return counts


def build_onchain(onchain_state: dict, agent_id: int = 17) -> dict:
    """Build on-chain statistics with live queries."""
    scores = _query_onchain_scores(agent_id)
    artifact_counts = _count_validation_records()
    total_artifacts = sum(artifact_counts.values())

    return {
        "agent_id": agent_id,
        "wallet": onchain_state.get("wallet"),
        "reputation_score": scores.get("reputation_score", onchain_state.get("hackathon_reputation_score")),
        "validation_score": scores.get("validation_score"),
        "total_trade_intents": artifact_counts.get("trade_intents", 0),
        "total_risk_checks": artifact_counts.get("risk_checks", 0),
        "total_strategy_checkpoints": artifact_counts.get("strategy_checkpoints", 0),
        "total_validation_artifacts": total_artifacts,
        "vault_claimed": onchain_state.get("vault_claimed", False),
        "wallet_eth": onchain_state.get("wallet_eth", 0),
        "last_activity": onchain_state.get("last_activity"),
    }


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Updating dashboard data...")

    # Load all data sources
    agent_state = load_json(AGENT_STATE_FILE)
    agent_card = load_json(AGENT_CARD_FILE)
    trade_log = load_jsonl(TRADE_LOG_FILE)
    onchain_state = load_json(ONCHAIN_FILE)
    kraken = get_kraken_paper_status()

    # Build dashboard data
    onchain = build_onchain(onchain_state, agent_id=17)
    strategy = build_strategy(trade_log, agent_card)
    dashboard = {
        "agent_name": agent_card.get("name", "JudyAI WaveRider"),
        "agent_id": 17,
        "status": "ACTIVE" if agent_card.get("active", True) else "INACTIVE",
        "portfolio": build_portfolio(agent_state, agent_card, kraken),
        "positions": build_positions(agent_state),
        "recent_trades": build_recent_trades(trade_log),
        "risk": build_risk(agent_state),
        "strategy": strategy,
        "reputation": {
            "score": onchain.get("reputation_score"),
            "validation_score": onchain.get("validation_score"),
        },
        "performance": {
            "win_rate": strategy.get("win_rate", 0),
            "total_trades": len([t for t in trade_log if t.get("type") == "open"]),
            "total_pnl": (kraken.get("current_value", 100000) - 100000) if kraken else agent_card.get("livePerformance", {}).get("realizedPnl", 0),
        },
        "onchain": onchain,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    # Write atomically (write to temp, then rename)
    tmp_file = OUTPUT_FILE.with_suffix(".tmp")
    with open(tmp_file, "w") as f:
        json.dump(dashboard, f, indent=2)
    tmp_file.rename(OUTPUT_FILE)

    print(f"[OK] Dashboard data written to {OUTPUT_FILE}")
    print(f"  Portfolio: ${dashboard['portfolio']['current_value']:.2f}")
    print(f"  Positions: {len(dashboard['positions'])} open")
    print(f"  Trades: {len(dashboard['recent_trades'])} recent")
    print(f"  Reputation: {dashboard['onchain'].get('reputation_score', 'N/A')}")


if __name__ == "__main__":
    main()
