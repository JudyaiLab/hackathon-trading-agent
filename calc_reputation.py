"""
Calculate and update reputation score dynamically.

Formula v3 — fully earned, zero base padding:
  risk_control(0-30) + transparency(0-20) + validation(0-15) +
  activity(0-15) + win_rate(0-10) + pnl(-5..10)

Total range: -5 to 100. Every point is earned through measurable performance.
Risk control is weighted highest (30%) because capital preservation is the
#1 requirement for a production trading agent.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
VALIDATION_DIR = BASE_DIR / "validation"
STATE_FILE = LOGS_DIR / "agent_state.json"
TRADE_LOG = LOGS_DIR / "trade_log.jsonl"
OUTPUT_FILE = BASE_DIR / "reputation_score.json"


def load_trades() -> list[dict]:
    closes = []
    if not TRADE_LOG.exists():
        print(f"  ⚠️  {TRADE_LOG} not found — using empty trade log (clone without live data)")
        return closes
    for line in TRADE_LOG.read_text().splitlines():
        try:
            t = json.loads(line)
            if t.get("type") == "close" and abs(t.get("pnl", 0)) >= 0.01:
                closes.append(t)
        except json.JSONDecodeError:
            pass
    return closes


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def count_artifacts() -> int:
    total = 0
    for fname in ["trade_intents.json", "risk_checks.json", "strategy_checkpoints.json"]:
        fpath = VALIDATION_DIR / fname
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text())
                records = data.get("records", data) if isinstance(data, dict) else data
                total += len(records)
            except (json.JSONDecodeError, TypeError):
                pass
    return total


def _count_ai_reviewed_intents() -> int:
    """Count trade intents that have a non-empty ai_verdict (evidence of AI review)."""
    fpath = VALIDATION_DIR / "trade_intents.json"
    if not fpath.exists():
        return 0
    try:
        data = json.loads(fpath.read_text())
        records = data.get("records", [])
        return sum(
            1 for r in records
            if r.get("reasoning", {}).get("ai_verdict")
            and r["reasoning"].get("ensemble_score", 0) > 0
        )
    except (json.JSONDecodeError, TypeError):
        return 0


def calculate() -> dict:
    closes = load_trades()
    state = load_state()
    risk = state.get("risk", {})

    win_count = sum(1 for t in closes if t["pnl"] > 0)
    total_count = len(closes)
    win_rate = win_count / total_count * 100 if total_count else 0

    realized_pnl = risk.get("total_realized_pnl", 0.0)
    realized_pnl_pct = realized_pnl / 100000 * 100

    peak = risk.get("peak_balance", 100000.0)
    drawdown_pct = abs(min(0, realized_pnl_pct))

    artifact_count = count_artifacts()
    artifacts_per_trade = artifact_count / max(1, total_count)

    # --- Component scores (v3: zero-base, fully earned) ---

    # Risk control: 0-30 (HIGHEST weight — capital preservation is #1)
    # drawdown < 0.5% = 30, < 1% = 25, < 2% = 20, < 5% = 10, >= 5% = 0
    if drawdown_pct < 0.5:
        risk_score = 30.0
    elif drawdown_pct < 1.0:
        risk_score = 25.0 + 5.0 * (1.0 - drawdown_pct) / 0.5
    elif drawdown_pct < 2.0:
        risk_score = 20.0 + 5.0 * (2.0 - drawdown_pct)
    elif drawdown_pct < 5.0:
        risk_score = max(0, 20.0 * (5.0 - drawdown_pct) / 3.0)
    else:
        risk_score = 0.0

    # Transparency: 0-20 (artifacts per trade × quality)
    density_score = min(15.0, artifacts_per_trade * 2.0)
    ai_reviewed_count = _count_ai_reviewed_intents()
    ai_quality = min(5.0, ai_reviewed_count / 3.0)
    transparency_score = density_score + ai_quality

    # Validation quality: 0-15 (WFO methodology + live validation)
    # Based on having structured backtest + live data
    wfo_score = 10.0 if artifact_count >= 50 else artifact_count / 5.0
    live_validation = min(5.0, total_count / 5.0)
    validation_score = wfo_score + live_validation

    # Activity: 0-15 (continuous operation evidence)
    scan_count = state.get("scan_count", 0)
    scan_score = min(8.0, scan_count / 12.0)
    trade_activity = min(7.0, total_count * 0.28)
    activity_score = scan_score + trade_activity

    # Win rate: 0-10 (scaled 30%-80% range)
    wr_score = min(10.0, max(0, (win_rate - 30) / 50 * 10))

    # PnL: -5 to 10 (rewards profit, penalizes loss)
    pnl_score = min(10.0, max(-5.0, realized_pnl_pct * 5))

    base = 0  # v3: zero base — every point is earned
    raw = base + risk_score + transparency_score + validation_score + activity_score + wr_score + pnl_score
    score = min(100, max(0, int(raw)))

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": "sprint_update",
        "formula_version": "v3_zero_base",
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": round(realized_pnl_pct, 4),
        "win_rate": round(win_rate, 1),
        "total_trades": total_count,
        "wins": win_count,
        "losses": total_count - win_count,
        "score": score,
        "breakdown": {
            "base": base,
            "risk_control": round(risk_score, 2),
            "transparency": round(transparency_score, 2),
            "validation": round(validation_score, 2),
            "activity": round(activity_score, 2),
            "win_rate": round(wr_score, 2),
            "pnl": round(pnl_score, 2),
            "raw": round(raw, 2),
            "weights": "risk(30)+transparency(20)+validation(15)+activity(15)+wr(10)+pnl(10)=100",
        },
        "artifacts": artifact_count,
        "drawdown_pct": round(drawdown_pct, 4),
    }

    return result


def main() -> None:
    result = calculate()
    OUTPUT_FILE.write_text(json.dumps(result, indent=2) + "\n")

    b = result["breakdown"]
    print(f"Win rate: {result['win_rate']:.1f}% ({result['wins']}W/{result['losses']}L from {result['total_trades']} trades)")
    print(f"PnL: ${result['realized_pnl']:.2f} ({result['realized_pnl_pct']:.2f}%) | Drawdown: {result['drawdown_pct']:.2f}%")
    print(f"Artifacts: {result['artifacts']} ({result['artifacts']/max(1,result['total_trades']):.1f}/trade)")
    print(f"risk={b['risk_control']:.1f}/30 transparency={b['transparency']:.1f}/20 validation={b['validation']:.1f}/15 activity={b['activity']:.1f}/15 wr={b['win_rate']:.1f}/10 pnl={b['pnl']:.1f}/10")
    print(f"Score: {b['raw']:.1f} -> {result['score']} (v3 zero-base, fully earned)")


if __name__ == "__main__":
    main()
