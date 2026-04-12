"""
Hackathon Shared Contract Integration — Sepolia Testnet

On-chain operations for the 5 shared hackathon contracts:
  1. AgentRegistry  — register agent, get agentId
  2. RiskRouter     — submit EIP-712 signed trade intents
  3. ValidationRegistry — post reasoning checkpoints
  4. ReputationRegistry — submit/query reputation
  5. HackathonVault — claim allocation (optional)

Usage:
  python3 hackathon_chain.py register     # Register agent on shared registry
  python3 hackathon_chain.py status       # Check registration + scores
  python3 hackathon_chain.py simulate     # Dry-run a test trade intent
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from config import (
    AGENT_NAME, AGENT_DESCRIPTION,
    HACKATHON_AGENT_REGISTRY, HACKATHON_VAULT,
    HACKATHON_RISK_ROUTER, HACKATHON_REPUTATION_REGISTRY,
    HACKATHON_VALIDATION_REGISTRY,
    SEPOLIA_RPC, SEPOLIA_CHAIN_ID,
)
from hackathon_abi import (
    AGENT_REGISTRY_ABI, HACKATHON_VAULT_ABI,
    RISK_ROUTER_ABI, VALIDATION_REGISTRY_ABI,
    HACKATHON_REPUTATION_ABI,
    EIP712_DOMAIN, EIP712_TRADE_INTENT_TYPES,
)

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────
STATE_FILE = Path(__file__).parent / "logs" / "hackathon_onchain_state.json"
CARD_PATH = Path(__file__).parent / "agent_card.json"


# ── State Management ─────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


# ── Helpers ──────────────────────────────────────────────────

def _load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _get_w3():
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(SEPOLIA_RPC))
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to Sepolia RPC: {SEPOLIA_RPC}")
    return w3


def _get_account(w3):
    pk = os.environ.get("SEPOLIA_PRIVATE_KEY", "")
    if not pk:
        raise ValueError(
            "SEPOLIA_PRIVATE_KEY not set. Add it to .env or export it."
        )
    return w3.eth.account.from_key(pk)


def _get_contract(w3, address: str, abi: list):
    from web3 import Web3
    return w3.eth.contract(
        address=Web3.to_checksum_address(address),
        abi=abi,
    )


# ── Nonce Manager (prevents nonce collisions) ──────────────

import threading

_nonce_lock = threading.Lock()
_last_nonce: int | None = None


def _get_next_nonce(w3, account) -> int:
    """Thread-safe nonce manager: ensures sequential nonces across all TX."""
    global _last_nonce
    with _nonce_lock:
        chain_nonce = w3.eth.get_transaction_count(account.address, "pending")
        if _last_nonce is None or chain_nonce > _last_nonce:
            _last_nonce = chain_nonce
        else:
            _last_nonce += 1
        return _last_nonce


def get_agent_id() -> int | None:
    """Get stored agent ID from on-chain state."""
    state = _load_state()
    return state.get("agent_id")


# ── 1. Agent Registration ───────────────────────────────────

def register_agent() -> int | None:
    """Register agent on the shared AgentRegistry.

    Returns:
        agentId if successful, None otherwise
    """
    _load_env()
    w3 = _get_w3()
    account = _get_account(w3)
    registry = _get_contract(w3, HACKATHON_AGENT_REGISTRY, AGENT_REGISTRY_ABI)

    capabilities = [
        "trading",
        "risk-management",
        "multi-strategy",
        "regime-detection",
        "ai-analysis",
    ]
    agent_uri = (
        "https://raw.githubusercontent.com/JudyaiLab/"
        "hackathon-trading-agent/main/agent_card.json"
    )

    print(f"Registering {AGENT_NAME} on shared AgentRegistry...")
    print(f"  Registry:  {HACKATHON_AGENT_REGISTRY}")
    print(f"  Wallet:    {account.address}")

    tx = registry.functions.register(
        account.address,
        AGENT_NAME,
        AGENT_DESCRIPTION,
        capabilities,
        agent_uri,
    ).build_transaction({
        "from": account.address,
        "nonce": _get_next_nonce(w3, account),
        "gas": 800_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": SEPOLIA_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX sent: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        logger.error("Registration TX reverted")
        print("  Registration FAILED (TX reverted)")
        return None

    # Parse AgentRegistered event
    logs = registry.events.AgentRegistered().process_receipt(receipt)
    agent_id = None
    if logs:
        agent_id = logs[0]["args"]["agentId"]
    else:
        # Fallback: try to extract from return value or logs
        logger.warning("Could not parse AgentRegistered event, checking state...")

    if agent_id is not None:
        print(f"  Agent ID: {agent_id}")
        state = _load_state()
        state["agent_id"] = agent_id
        state["wallet"] = account.address
        state["registered_at"] = datetime.now(timezone.utc).isoformat()
        state["tx_hash"] = tx_hash.hex()
        _save_state(state)

        # Update agent card with shared registration
        _update_card_registration(agent_id, account.address)
        print("  Registration SUCCESS")
        return agent_id

    print("  Registration TX succeeded but could not parse agentId")
    return None


def _update_card_registration(agent_id: int, wallet: str) -> None:
    """Update agent_card.json with shared registry registration."""
    if CARD_PATH.exists():
        card = json.loads(CARD_PATH.read_text())
    else:
        card = {}

    card["registrations"] = [{
        "agentId": agent_id,
        "agentRegistry": f"eip155:{SEPOLIA_CHAIN_ID}:{HACKATHON_AGENT_REGISTRY}",
        "type": "hackathon_shared",
    }]
    card.setdefault("contracts", {})
    card["contracts"]["hackathon"] = {
        "agentRegistry": HACKATHON_AGENT_REGISTRY,
        "riskRouter": HACKATHON_RISK_ROUTER,
        "validationRegistry": HACKATHON_VALIDATION_REGISTRY,
        "reputationRegistry": HACKATHON_REPUTATION_REGISTRY,
        "vault": HACKATHON_VAULT,
        "network": "sepolia",
        "chainId": SEPOLIA_CHAIN_ID,
    }
    card["contracts"]["wallet"] = wallet

    CARD_PATH.write_text(json.dumps(card, indent=2, default=str))
    logger.info("Agent card updated with shared registry (agentId=%d)", agent_id)


# ── 2. RiskRouter — Trade Intent Submission ──────────────────

def submit_trade_intent(
    pair: str,
    action: str,
    amount_usd: float,
    max_slippage_bps: int = 50,
) -> dict:
    """Submit a signed trade intent to the RiskRouter.

    Args:
        pair: Trading pair (e.g. "BTCUSD")
        action: "BUY" or "SELL"
        amount_usd: Trade amount in USD (capped at $950 for RiskRouter default limit)
        max_slippage_bps: Max slippage in basis points (default 50 = 0.5%)

    Returns:
        dict with tx_hash, approved status, etc.
    """
    _load_env()
    agent_id = get_agent_id()
    if agent_id is None:
        logger.error("Agent not registered. Call register_agent() first.")
        return {"error": "not_registered"}

    # RiskRouter has $1000 default cap — clamp to $950 for safety
    amount_usd = min(amount_usd, 950.0)

    w3 = _get_w3()
    account = _get_account(w3)
    router = _get_contract(w3, HACKATHON_RISK_ROUTER, RISK_ROUTER_ABI)

    # Get nonce from contract
    nonce = router.functions.getIntentNonce(agent_id).call()
    deadline = int(time.time()) + 300  # 5 minute deadline

    # Build intent struct
    intent = {
        "agentId": agent_id,
        "agentWallet": account.address,
        "pair": pair,
        "action": action,
        "amountUsdScaled": int(amount_usd * 100),  # USD × 100
        "maxSlippageBps": max_slippage_bps,
        "nonce": nonce,
        "deadline": deadline,
    }

    # EIP-712 sign
    signature = _sign_trade_intent(w3, account, intent)

    # Build intent tuple for contract call
    intent_tuple = (
        intent["agentId"],
        intent["agentWallet"],
        intent["pair"],
        intent["action"],
        intent["amountUsdScaled"],
        intent["maxSlippageBps"],
        intent["nonce"],
        intent["deadline"],
    )

    logger.info(
        "Submitting trade intent: %s %s $%.2f (nonce=%d)",
        action, pair, amount_usd, nonce,
    )

    tx = router.functions.submitTradeIntent(
        intent_tuple, signature,
    ).build_transaction({
        "from": account.address,
        "nonce": _get_next_nonce(w3, account),
        "gas": 500_000,
        "gasPrice": int(w3.eth.gas_price * 2),
        "chainId": SEPOLIA_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    approved = receipt["status"] == 1
    result = {
        "tx_hash": tx_hash.hex(),
        "approved": approved,
        "pair": pair,
        "action": action,
        "amount_usd": amount_usd,
        "nonce": nonce,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Check raw event logs for rejection text (ABI event sigs may differ)
    if approved:
        for log_entry in receipt.get("logs", []):
            try:
                raw = log_entry["data"]
                if isinstance(raw, bytes):
                    text = raw.decode("utf-8", errors="replace")
                else:
                    text = bytes.fromhex(raw.replace("0x", "")).decode(
                        "utf-8", errors="replace"
                    )
                if "reject" in text.lower() or "exceeds" in text.lower():
                    result["approved"] = False
                    readable = "".join(
                        c if c.isprintable() else "" for c in text
                    ).strip()
                    result["rejection_reason"] = readable
                    break
            except Exception:
                pass

    logger.info("Trade intent result: %s", "APPROVED" if result["approved"] else "REJECTED")
    return result


def simulate_trade_intent(
    pair: str,
    action: str,
    amount_usd: float,
    max_slippage_bps: int = 50,
) -> dict:
    """Dry-run a trade intent to check if it would pass.

    Returns:
        dict with valid (bool) and reason (str)
    """
    _load_env()
    agent_id = get_agent_id()
    if agent_id is None:
        return {"valid": False, "reason": "not_registered"}

    w3 = _get_w3()
    account = _get_account(w3)
    router = _get_contract(w3, HACKATHON_RISK_ROUTER, RISK_ROUTER_ABI)

    nonce = router.functions.getIntentNonce(agent_id).call()
    deadline = int(time.time()) + 300

    intent_tuple = (
        agent_id,
        account.address,
        pair,
        action,
        int(amount_usd * 100),
        max_slippage_bps,
        nonce,
        deadline,
    )

    valid, reason = router.functions.simulateIntent(intent_tuple).call()
    return {"valid": valid, "reason": reason}


def _sign_trade_intent(w3, account, intent: dict) -> bytes:
    """Sign a TradeIntent using EIP-712 structured data."""
    from web3 import Web3

    # Build EIP-712 message
    message = {
        "agentId": intent["agentId"],
        "agentWallet": Web3.to_checksum_address(intent["agentWallet"]),
        "pair": intent["pair"],
        "action": intent["action"],
        "amountUsdScaled": intent["amountUsdScaled"],
        "maxSlippageBps": intent["maxSlippageBps"],
        "nonce": intent["nonce"],
        "deadline": intent["deadline"],
    }

    # Use eth_account's encode_typed_data for EIP-712
    from eth_account.messages import encode_typed_data

    signable = encode_typed_data(
        domain_data=EIP712_DOMAIN,
        message_types=EIP712_TRADE_INTENT_TYPES,
        message_data=message,
    )
    signed = account.sign_message(signable)
    return signed.signature


# ── 3. ValidationRegistry — Checkpoint Posting ───────────────

def post_checkpoint(
    checkpoint_data: dict,
    score: int = 80,
    notes: str = "",
) -> dict | None:
    """Post a reasoning checkpoint to the ValidationRegistry.

    Args:
        checkpoint_data: Dict with decision context (will be hashed)
        score: Self-assessment score 0-100
        notes: Human-readable reasoning notes

    Returns:
        dict with tx_hash or None on failure
    """
    _load_env()
    agent_id = get_agent_id()
    if agent_id is None:
        logger.error("Agent not registered")
        return None

    w3 = _get_w3()
    account = _get_account(w3)
    registry = _get_contract(
        w3, HACKATHON_VALIDATION_REGISTRY, VALIDATION_REGISTRY_ABI
    )

    # Hash checkpoint data
    checkpoint_json = json.dumps(checkpoint_data, sort_keys=True, default=str)
    checkpoint_hash = w3.keccak(text=checkpoint_json)

    # Clamp score to uint8 range
    score = max(0, min(255, score))

    # Truncate notes for gas efficiency
    if len(notes) > 256:
        notes = notes[:253] + "..."

    logger.info(
        "Posting checkpoint: agent=%d score=%d hash=%s",
        agent_id, score, checkpoint_hash.hex()[:16],
    )

    tx = registry.functions.postEIP712Attestation(
        agent_id, checkpoint_hash, score, notes,
    ).build_transaction({
        "from": account.address,
        "nonce": _get_next_nonce(w3, account),
        "gas": 500_000,
        "gasPrice": int(w3.eth.gas_price * 2),
        "chainId": SEPOLIA_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt["status"] != 1:
        logger.debug("Checkpoint TX reverted (not an authorized validator)")
        return None

    return {
        "tx_hash": tx_hash.hex(),
        "agent_id": agent_id,
        "score": score,
        "checkpoint_hash": checkpoint_hash.hex(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── 4. ReputationRegistry — Query Scores ─────────────────────

def get_reputation_score() -> int | None:
    """Query average reputation score from shared ReputationRegistry."""
    _load_env()
    agent_id = get_agent_id()
    if agent_id is None:
        return None

    w3 = _get_w3()
    registry = _get_contract(
        w3, HACKATHON_REPUTATION_REGISTRY, HACKATHON_REPUTATION_ABI
    )

    try:
        score = registry.functions.getAverageScore(agent_id).call()
        return score
    except Exception as e:
        logger.warning("Failed to get reputation score: %s", e)
        return None


def get_validation_score() -> int | None:
    """Query average validation score from ValidationRegistry."""
    _load_env()
    agent_id = get_agent_id()
    if agent_id is None:
        return None

    w3 = _get_w3()
    registry = _get_contract(
        w3, HACKATHON_VALIDATION_REGISTRY, VALIDATION_REGISTRY_ABI
    )

    try:
        score = registry.functions.getAverageValidationScore(agent_id).call()
        return score
    except Exception as e:
        logger.warning("Failed to get validation score: %s", e)
        return None


# ── 5. Status Summary ────────────────────────────────────────

def get_onchain_status() -> dict:
    """Get comprehensive on-chain status."""
    _load_env()
    state = _load_state()
    agent_id = state.get("agent_id")

    status = {
        "registered": agent_id is not None,
        "agent_id": agent_id,
        "wallet": state.get("wallet"),
        "registered_at": state.get("registered_at"),
    }

    if agent_id is not None:
        try:
            w3 = _get_w3()

            # Check registration is still active
            registry = _get_contract(
                w3, HACKATHON_AGENT_REGISTRY, AGENT_REGISTRY_ABI
            )
            is_registered = registry.functions.isRegistered(agent_id).call()
            status["on_chain_active"] = is_registered

            # Reputation score
            rep_score = get_reputation_score()
            status["reputation_score"] = rep_score

            # Validation score
            val_score = get_validation_score()
            status["validation_score"] = val_score

            # Vault status
            vault = _get_contract(w3, HACKATHON_VAULT, HACKATHON_VAULT_ABI)
            has_claimed = vault.functions.hasClaimed(agent_id).call()
            status["vault_claimed"] = has_claimed

            # Wallet balance
            wallet_addr = state.get("wallet")
            if wallet_addr:
                from web3 import Web3
                bal = w3.eth.get_balance(
                    Web3.to_checksum_address(wallet_addr)
                )
                status["wallet_eth"] = float(w3.from_wei(bal, "ether"))

        except Exception as e:
            status["error"] = str(e)

    return status


# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "register":
        agent_id = register_agent()
        if agent_id:
            print(f"\nAgent registered with ID: {agent_id}")
        else:
            print("\nRegistration failed")
            sys.exit(1)

    elif cmd == "status":
        status = get_onchain_status()
        print(json.dumps(status, indent=2, default=str))

    elif cmd == "simulate":
        result = simulate_trade_intent("BTCUSD", "BUY", 500.0)
        print(json.dumps(result, indent=2))

    elif cmd == "checkpoint":
        result = post_checkpoint(
            {"type": "test", "message": "Manual checkpoint test"},
            score=80,
            notes="CLI test checkpoint",
        )
        if result:
            print(f"Checkpoint posted: {result['tx_hash']}")
        else:
            print("Checkpoint failed")

    else:
        print("Usage: hackathon_chain.py [register|status|simulate|checkpoint]")
