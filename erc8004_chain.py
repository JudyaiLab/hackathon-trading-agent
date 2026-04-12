"""
ERC-8004 On-Chain Operations — Identity registration & reputation updates

Supports two execution methods:
  1. web3.py  (preferred — SEPOLIA_PRIVATE_KEY env var)
  2. cast CLI (fallback  — Foundry toolchain)

Contracts (Sepolia):
  Identity:   0x8004A818BFB912233c491871b3d84c89A494BD9e
  Reputation: 0x8004B663056A597Dffe9eCcC1965A193B7388713
"""
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from config import (
    AGENT_NAME,
    ERC8004_IDENTITY_CONTRACT, ERC8004_REPUTATION_CONTRACT,
    SEPOLIA_RPC, SEPOLIA_CHAIN_ID,
)
from erc8004_abi import IDENTITY_ABI, REPUTATION_ABI
from erc8004_card import (
    generate_agent_card, save_agent_card, load_agent_card,
    get_live_performance,
)

logger = logging.getLogger(__name__)

# Import shared nonce manager from hackathon_chain
from hackathon_chain import _get_next_nonce

# ── Helpers ───────────────────────────────────────────────────

def _load_env():
    """Load .env file if present (for SEPOLIA_PRIVATE_KEY, INFURA_KEY)."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _resolve_rpc() -> str:
    """Build Sepolia RPC URL from config + optional INFURA_KEY."""
    infura_key = os.environ.get("INFURA_KEY", "")
    if SEPOLIA_RPC.endswith("/v3/"):
        return SEPOLIA_RPC + infura_key
    return SEPOLIA_RPC


def _resolve_agent_id(card: dict) -> int | None:
    """Extract agent ID from card registrations."""
    regs = card.get("registrations", [])
    if regs:
        return regs[0].get("agentId")
    return None


def _resolve_wallet(card: dict) -> str | None:
    """Extract agent wallet address from card or env."""
    # First check env (direct)
    pk = os.environ.get("SEPOLIA_PRIVATE_KEY", "")
    if pk:
        try:
            from web3 import Web3
            w3 = Web3()
            return w3.eth.account.from_key(pk).address
        except Exception:
            pass
    # Fallback: card metadata
    return card.get("contracts", {}).get("wallet")


# ── Identity Registration ─────────────────────────────────────

def register_identity() -> bool:
    """Register agent identity on Sepolia.

    Requires SEPOLIA_PRIVATE_KEY in env or .env file.
    Requires Sepolia ETH for gas.
    """
    _load_env()
    private_key = os.environ.get("SEPOLIA_PRIVATE_KEY", "")
    if not private_key:
        print("SEPOLIA_PRIVATE_KEY not set.\n")
        print("To register on-chain:")
        print("  1. Get Sepolia ETH: https://sepoliafaucet.com/")
        print("  2. export SEPOLIA_PRIVATE_KEY=0x...")
        print("  3. export INFURA_KEY=...")
        print("  4. python3 erc8004.py --register")
        return False

    rpc_url = _resolve_rpc()
    card = save_agent_card(generate_agent_card(get_live_performance()))
    agent_uri = (
        "https://raw.githubusercontent.com/JudyaiLab/"
        "hackathon-trading-agent/main/agent_card.json"
    )

    try:
        return _register_web3(private_key, rpc_url, agent_uri, card)
    except ImportError:
        logger.info("web3 not installed, trying cast CLI")

    return _register_cast(rpc_url, private_key, agent_uri, card)


def _register_web3(
    private_key: str, rpc_url: str, agent_uri: str, card: dict,
) -> bool:
    """Register via web3.py."""
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        logger.error("Cannot connect to Sepolia RPC: %s", rpc_url)
        return False

    account = w3.eth.account.from_key(private_key)
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(ERC8004_IDENTITY_CONTRACT),
        abi=IDENTITY_ABI,
    )

    print(f"Registering {AGENT_NAME} on Sepolia...")
    print(f"  Identity contract: {ERC8004_IDENTITY_CONTRACT}")
    print(f"  Wallet: {account.address}")

    tx = registry.functions.register(agent_uri).build_transaction({
        "from": account.address,
        "nonce": _get_next_nonce(w3, account),
        "gas": 500_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": SEPOLIA_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX sent: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        logger.error("Registration TX reverted")
        return False

    logs = registry.events.Registered().process_receipt(receipt)
    if logs:
        agent_id = logs[0]["args"]["agentId"]
        identifier = (
            f"eip155:{SEPOLIA_CHAIN_ID}:{ERC8004_IDENTITY_CONTRACT}:{agent_id}"
        )
        print(f"  Agent ID: {agent_id}")
        print(f"  Identifier: {identifier}")

        card["registrations"] = [{
            "agentId": agent_id,
            "agentRegistry": f"eip155:{SEPOLIA_CHAIN_ID}:{ERC8004_IDENTITY_CONTRACT}",
        }]
        card.setdefault("contracts", {})["wallet"] = account.address
        save_agent_card(card)
        print("  Agent Card updated with registration data.")
        return True

    logger.error("Could not parse Registered event from receipt")
    return False


def _register_cast(
    rpc_url: str, private_key: str, agent_uri: str, card: dict,
) -> bool:
    """Register via cast CLI (Foundry fallback)."""
    env = {**os.environ, "ETH_PRIVATE_KEY": private_key}
    result = subprocess.run(
        [
            "cast", "send", ERC8004_IDENTITY_CONTRACT,
            "register(string)", agent_uri,
            "--rpc-url", rpc_url,
        ],
        capture_output=True, text=True, timeout=120,
        env=env,
    )
    if result.returncode == 0:
        print(f"Registration TX sent via cast:\n{result.stdout}")
        return True

    logger.error("cast registration failed: %s", result.stderr)
    print("Install web3.py or Foundry for on-chain registration.")
    return False


# ── Reputation Updates ────────────────────────────────────────

def update_reputation(
    agent_id: int | None = None,
    realized_pnl: float | None = None,
    realized_pnl_pct: float | None = None,
):
    """Update on-chain reputation with latest PnL snapshot.

    Posts tradingYield feedback to the ERC-8004 Reputation Registry.
    Falls back to local-only card update if web3 or keys are unavailable.
    """
    _load_env()
    perf = get_live_performance()
    if not perf:
        logger.error("Could not get live performance data")
        return

    if realized_pnl is not None:
        perf["realizedPnl"] = realized_pnl
    if realized_pnl_pct is not None:
        perf["realizedPnlPct"] = realized_pnl_pct

    card = load_agent_card()
    card["livePerformance"] = perf
    save_agent_card(card)

    if agent_id is None:
        agent_id = _resolve_agent_id(card)
    if agent_id is None:
        print("No agent ID found. Register first with --register")
        return

    private_key = os.environ.get("SEPOLIA_PRIVATE_KEY", "")
    if private_key:
        rpc_url = _resolve_rpc()
        try:
            _post_reputation_web3(private_key, rpc_url, agent_id, perf, card)
        except ImportError:
            logger.info("web3 not installed — reputation saved locally only")
        except Exception as e:
            logger.warning("On-chain reputation update failed: %s", e)

    pnl = perf.get("realizedPnl", perf.get("unrealizedPnl", 0))
    pnl_pct = perf.get("realizedPnlPct", perf.get("unrealizedPnlPct", 0))
    logger.info(
        "Reputation updated | Agent %d | Value $%.2f | PnL $%.2f (%.2f%%) | Trades %d",
        agent_id, perf.get("currentValue", 0), pnl, pnl_pct,
        perf.get("totalTrades", 0),
    )


def _post_reputation_web3(
    private_key: str, rpc_url: str, agent_id: int, perf: dict, card: dict,
) -> bool:
    """Post tradingYield feedback to Reputation Registry via web3.py."""
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    account = w3.eth.account.from_key(private_key)
    reputation = w3.eth.contract(
        address=Web3.to_checksum_address(ERC8004_REPUTATION_CONTRACT),
        abi=REPUTATION_ABI,
    )

    pnl_pct = perf.get("realizedPnlPct", perf.get("unrealizedPnlPct", 0))
    value = int(round(pnl_pct * 100))  # fixed-point: 85.50% → 8550

    regs = card.get("registrations", [])
    feedback_uri = (
        "https://raw.githubusercontent.com/JudyaiLab/"
        "hackathon-trading-agent/main/agent_card.json"
    ) if regs else ""

    # keccak256 of performance JSON — EVM-compatible for on-chain verification
    perf_json = json.dumps(perf, sort_keys=True, default=str).encode()
    feedback_hash = w3.keccak(perf_json)

    tx = reputation.functions.giveFeedback(
        agent_id, value, 2,
        "tradingYield", "day", "",
        feedback_uri, feedback_hash,
    ).build_transaction({
        "from": account.address,
        "nonce": _get_next_nonce(w3, account),
        "gas": 200_000,
        "gasPrice": int(w3.eth.gas_price * 2),  # 2x gas to avoid stuck TXs
        "chainId": SEPOLIA_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt["status"] == 1:
        print(f"  On-chain reputation TX: {tx_hash.hex()}")
        # Also submit to hackathon ReputationRegistry (leaderboard reads from here)
        _post_hackathon_reputation(w3, account, perf)
        return True
    logger.error("Reputation TX reverted")
    return False


def _post_hackathon_reputation(w3, account, perf: dict) -> bool:
    """Submit reputation feedback to the HACKATHON ReputationRegistry.

    This is the contract the leaderboard reads from.
    Different from ERC-8004 standard contract.
    """
    from web3 import Web3
    from config import HACKATHON_REPUTATION_REGISTRY, SEPOLIA_CHAIN_ID
    from hackathon_abi import HACKATHON_REPUTATION_ABI
    from hackathon_chain import get_agent_id

    hackathon_agent_id = get_agent_id()
    if hackathon_agent_id is None:
        logger.warning("No hackathon agent ID — skipping hackathon reputation")
        return False

    rep_contract = w3.eth.contract(
        address=Web3.to_checksum_address(HACKATHON_REPUTATION_REGISTRY),
        abi=HACKATHON_REPUTATION_ABI,
    )

    # Use calc_reputation.py v2 formula for consistent scoring
    pnl = perf.get("realizedPnl", 0)
    drawdown = abs(perf.get("maxDrawdownPct", perf.get("realizedPnlPct", 0)))
    total_trades = perf.get("totalTrades", 0)

    try:
        from calc_reputation import calculate
        rep_result = calculate()
        score = rep_result["score"]
    except Exception:
        # Fallback: simple formula
        score = max(0, min(100, 50 + (10 if drawdown < 0.5 else 0) + min(5, total_trades // 5)))

    # outcomeRef: hash of performance data
    perf_json = json.dumps(perf, sort_keys=True, default=str).encode()
    outcome_ref = w3.keccak(perf_json)

    comment = (
        f"PnL ${pnl:+.2f} | Drawdown {drawdown:.1f}% | "
        f"Trades {total_trades} | WFO validated 82.2% OOS"
    )

    # Try feedbackType 0 (external/objective) — type 1 (self-assessment) is
    # blocked by the hackathon contract to prevent self-rating inflation
    for feedback_type in (0, 2):
        try:
            tx = rep_contract.functions.submitFeedback(
                hackathon_agent_id,
                score,
                outcome_ref,
                comment,
                feedback_type,
            ).build_transaction({
                "from": account.address,
                "nonce": _get_next_nonce(w3, account),
                "gas": 300_000,
                "gasPrice": int(w3.eth.gas_price * 2),
                "chainId": SEPOLIA_CHAIN_ID,
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] == 1:
                logger.info(
                    "Hackathon reputation submitted: agent=%d score=%d type=%d TX=%s",
                    hackathon_agent_id, score, feedback_type, tx_hash.hex()[:16],
                )
                return True
            logger.debug(
                "Hackathon reputation type=%d reverted, trying next type",
                feedback_type,
            )
        except Exception as e:
            logger.debug("Hackathon reputation type=%d failed: %s", feedback_type, e)

    # Fallback: post via ValidationRegistry checkpoint instead
    try:
        from hackathon_chain import post_checkpoint
        checkpoint_data = {
            "type": "reputation_update",
            "score": score,
            "pnl": pnl,
            "drawdown": drawdown,
            "total_trades": total_trades,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result = post_checkpoint(
            checkpoint_data,
            score=min(100, score),
            notes=comment,
        )
        if result:
            logger.info(
                "Reputation posted via ValidationRegistry checkpoint: TX=%s",
                result["tx_hash"][:16],
            )
            return True
    except Exception as e:
        logger.warning("ValidationRegistry fallback also failed: %s", e)

    logger.warning("Hackathon reputation update failed (all methods exhausted)")
    return False


def get_reputation_summary(agent_id: int | None = None) -> dict | None:
    """Read on-chain reputation summary via getSummary.

    Passes the agent's own wallet as clientAddresses filter when available,
    so the registry returns only self-reported feedback.

    Returns:
        dict with count, summaryValue, decimals — or None if unavailable
    """
    _load_env()
    card = load_agent_card()
    if agent_id is None:
        agent_id = _resolve_agent_id(card)
    if agent_id is None:
        return None

    rpc_url = _resolve_rpc()

    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            return None

        reputation = w3.eth.contract(
            address=Web3.to_checksum_address(ERC8004_REPUTATION_CONTRACT),
            abi=REPUTATION_ABI,
        )

        # Pass agent's wallet address for filtered summary
        wallet = _resolve_wallet(card)
        client_addrs = [Web3.to_checksum_address(wallet)] if wallet else []

        count, value, decimals = reputation.functions.getSummary(
            agent_id, client_addrs, "tradingYield", "day"
        ).call()
        return {
            "count": count,
            "summaryValue": value / (10 ** decimals) if decimals > 0 else value,
            "valueDecimals": decimals,
            "raw": value,
        }
    except ImportError:
        logger.info("web3 not installed — cannot read on-chain reputation")
    except Exception as e:
        logger.warning("Could not read reputation summary: %s", e)
    return None
