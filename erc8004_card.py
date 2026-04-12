"""
ERC-8004 Agent Card — Generation, persistence, and live performance sync

The Agent Card is the off-chain metadata document linked from the on-chain
Identity Registry via agentURI.  Format follows:
  https://eips.ethereum.org/EIPS/eip-8004#registration-file
"""
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from config import (
    AGENT_NAME, AGENT_DESCRIPTION,
    ERC8004_IDENTITY_CONTRACT, ERC8004_REPUTATION_CONTRACT,
    HACKATHON_AGENT_REGISTRY, HACKATHON_RISK_ROUTER,
    HACKATHON_VALIDATION_REGISTRY, HACKATHON_REPUTATION_REGISTRY,
    HACKATHON_VAULT,
    SEPOLIA_CHAIN_ID, ACTIVE_PAIRS,
)

logger = logging.getLogger(__name__)

CARD_PATH = Path(__file__).parent / "agent_card.json"


def generate_agent_card(pnl_data: dict | None = None) -> dict:
    """Generate ERC-8004 spec-compliant Agent Card (registration-v1 format)."""
    card = {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": AGENT_NAME,
        "description": AGENT_DESCRIPTION,
        "image": "https://judyailab.com/img/logo.png",
        "services": [
            {"name": "web", "endpoint": "https://judyailab.com/"},
            {
                "name": "MCP",
                "endpoint": "stdio://hackathon-trading-agent",
                "version": "2025-06-18",
            },
        ],
        "active": True,
        "registrations": [],
        "supportedTrust": ["reputation"],
        "capabilities": {
            "trading": {
                "strategies": [
                    "WaveRider (EMA + RSI + Volume trend-following)",
                    "BB Squeeze (Bollinger Band compression breakout)",
                    "MACD Divergence (swing-point momentum divergence)",
                ],
                "regimeDetection": True,
                "riskManagement": True,
                "pairs": [p for p in ACTIVE_PAIRS if p != "ADA/USDT"],
                "timeframes": ["4H"],
                "execution": "Kraken paper trading via CLI",
            },
        },
        "performance": {
            "backtestWinRate": 82.2,
            "backtestPeriod": "360 days",
            "validation": "Walk-Forward Optimization (8 windows, IS=90d, OOS=30d)",
            "oosWinRate": 82.2,
            "oosWinRateRange": "80-87%",
            "strategies": 3,
            "strategyCombos": {
                "I1c_MeanRev_1H": {"winRate": "80-100%", "trades": 58, "note": "BB2σ+RSI25/75+Candlestick"},
                "J1c_Trend1D": {"winRate": "85%", "trades": 20, "note": "I1c + 1D EMA200 filter"},
                "N1_TripleVP_1D": {"winRate": "86.7%", "trades": 15, "note": "I1c + Volume Profile + 1D trend"},
                "N2_VP_Counter": {"winRate": "87.5%", "trades": 8, "note": "PF=12.34, counter-trend regime"},
            },
            "strategyComboPeriod": "36-cell matrix (6 coins × 6 regimes)",
        },
        "validationArtifacts": {
            "tradeIntents": "validation/trade_intents.json",
            "riskChecks": "validation/risk_checks.json",
            "strategyCheckpoints": "validation/strategy_checkpoints.json",
        },
        "riskControls": {
            "maxPositionPct": 5.0,
            "maxDailyLossPct": 3.0,
            "maxDrawdownPct": 10.0,
            "maxConcurrentPositions": 5,
            "consecutiveLossPause": 5,
        },
        "contracts": {
            "identity": ERC8004_IDENTITY_CONTRACT,
            "reputation": ERC8004_REPUTATION_CONTRACT,
            "network": "sepolia",
            "chainId": SEPOLIA_CHAIN_ID,
            "hackathon": {
                "agentRegistry": HACKATHON_AGENT_REGISTRY,
                "riskRouter": HACKATHON_RISK_ROUTER,
                "validationRegistry": HACKATHON_VALIDATION_REGISTRY,
                "reputationRegistry": HACKATHON_REPUTATION_REGISTRY,
                "vault": HACKATHON_VAULT,
            },
        },
        "created": datetime.now(timezone.utc).isoformat(),
    }

    if pnl_data:
        card["livePerformance"] = pnl_data

    # Add Merkle integrity hash for trust-minimized verification
    try:
        from merkle import compute_artifact_merkle
        merkle = compute_artifact_merkle()
        card["integrity"] = {
            "merkleRoot": merkle["merkle_root"],
            "algorithm": merkle["algorithm"],
            "totalRecords": merkle["total_records"],
            "verifyCommand": "python3 merkle.py",
        }
    except Exception:
        pass

    return card


def save_agent_card(card: dict | None = None) -> dict:
    """Save Agent Card to agent_card.json with live performance data."""
    if card is None:
        perf = get_live_performance()
        card = generate_agent_card(pnl_data=perf)
        # Add on-chain registration data
        try:
            state_path = CARD_PATH.parent / "logs" / "hackathon_onchain_state.json"
            if state_path.exists():
                state = json.loads(state_path.read_text())
                card["registrations"] = [{
                    "agentId": state["agent_id"],
                    "agentRegistry": "eip155:11155111:0x97b07dDc405B0c28B17559aFFE63BdB3632d0ca3",
                    "type": "hackathon_shared",
                }]
                card.setdefault("contracts", {})["wallet"] = state["wallet"]
        except Exception:
            pass
    with open(CARD_PATH, "w") as f:
        json.dump(card, f, indent=2, default=str)
    logger.info("Agent Card saved to %s", CARD_PATH)
    return card


def load_agent_card() -> dict:
    """Load existing Agent Card, or generate a fresh one."""
    if CARD_PATH.exists():
        with open(CARD_PATH) as f:
            return json.load(f)
    return generate_agent_card()


def get_live_performance() -> dict:
    """Get live trading performance from Kraken paper status."""
    try:
        result = subprocess.run(
            ["kraken", "paper", "status", "-o", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            status = json.loads(result.stdout)
            return {
                "startingBalance": status.get("starting_balance", 100000),
                "currentValue": status.get("current_value", 100000),
                "unrealizedPnl": status.get("unrealized_pnl", 0),
                "unrealizedPnlPct": status.get("unrealized_pnl_pct", 0),
                "totalTrades": status.get("total_trades", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as e:
        logger.error("Failed to get live performance: %s", e)
    return {}
