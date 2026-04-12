"""
ERC-8004 Agent Identity — CLI entry point + backward-compatible re-exports

Split into focused modules:
  erc8004_abi.py   — ABI fragments for Identity & Reputation contracts
  erc8004_card.py  — Agent Card generation, save/load, live performance
  erc8004_chain.py — On-chain registration & reputation updates

Usage:
  python3 erc8004.py --generate-card      # Generate agent_card.json
  python3 erc8004.py --register           # Register on Sepolia testnet
  python3 erc8004.py --update-reputation  # Post PnL snapshot on-chain
  python3 erc8004.py --show               # Display current card + on-chain reputation
"""
import argparse
import json
import logging

# ── Re-exports (backward compatibility with agent.py imports) ──
from erc8004_card import (                    # noqa: F401
    generate_agent_card,
    save_agent_card,
    load_agent_card,
    get_live_performance,
    CARD_PATH,
)
from erc8004_chain import (                   # noqa: F401
    register_identity,
    update_reputation,
    get_reputation_summary,
)
from erc8004_abi import IDENTITY_ABI, REPUTATION_ABI  # noqa: F401


def main():
    parser = argparse.ArgumentParser(description="ERC-8004 Agent Identity Manager")
    parser.add_argument("--generate-card", action="store_true", help="Generate agent_card.json")
    parser.add_argument("--register", action="store_true", help="Register on Sepolia testnet")
    parser.add_argument("--update-reputation", action="store_true", help="Update PnL reputation")
    parser.add_argument("--show", action="store_true", help="Show current agent card")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

    if args.generate_card:
        card = save_agent_card(generate_agent_card(get_live_performance()))
        print(json.dumps(card, indent=2))
    elif args.register:
        register_identity()
    elif args.update_reputation:
        update_reputation()
    elif args.show:
        card = load_agent_card()
        print(json.dumps(card, indent=2))
        summary = get_reputation_summary()
        if summary:
            print(f"\nOn-chain Reputation (tradingYield/day):")
            print(f"  Feedback count: {summary['count']}")
            print(f"  Summary value:  {summary['summaryValue']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
