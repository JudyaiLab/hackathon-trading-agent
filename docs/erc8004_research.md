# ERC-8004 Research Notes

## Overview
ERC-8004 (Draft, Aug 2025) — "Trustless Agents" by Marco De Rossi (MetaMask), Davide Crapis (EF), Jordan Ellis (Google), Erik Reppel (Coinbase).

Three on-chain singleton registries for AI agent discovery, trust, and interaction.

## Contract Addresses (Sepolia Testnet)
| Registry | Address |
|----------|---------|
| Identity | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Reputation | `0x8004B663056A597Dffe9eCcC1965A193B7388713` |

Also deployed on 30+ chains (Ethereum, Base, Arbitrum, Polygon, Optimism, etc.)

## Agent Identifier Format
```
eip155:{chainId}:{identityRegistry}:{agentId}
```

## Identity Registry (ERC-721)
- `register(string agentURI)` → returns `uint256 agentId`
- `register(string agentURI, MetadataEntry[] metadata)` — with key-value pairs
- `setAgentURI(uint256 agentId, string newURI)` — update metadata
- `setAgentWallet(agentId, wallet, deadline, signature)` — requires EIP-712/ERC-1271 sig
- Agent wallet auto-clears on NFT transfer
- Metadata key `agentWallet` is reserved

## Reputation Registry
Standard tags for trading:
| Tag | Description |
|-----|-------------|
| `tradingYield` | Percentage (tag2: day/week/month/year) |
| `successRate` | Percentage |
| `starred` | Quality 0-100 |
| `uptime` | Percentage |
| `responseTime` | Milliseconds |
| `revenues` | Cumulative |

- `giveFeedback(agentId, value, valueDecimals, tag1, tag2, endpoint, feedbackURI, feedbackHash)`
- `getSummary(agentId, clientAddresses[], tag1, tag2)` — clientAddresses MUST be non-empty (Sybil resistance)
- `revokeFeedback(agentId, feedbackIndex)`

## Validation Registry
- `validationRequest(validatorAddress, agentId, requestURI, requestHash)`
- `validationResponse(requestHash, response 0-100, responseURI, responseHash, tag)`
- Methods: stake-backed re-execution, zkML proofs, TEE attestation, human judges

## Agent Card (registration-v1)
```json
{
  "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
  "name": "...",
  "description": "...",
  "services": [
    {"name": "web", "endpoint": "https://..."},
    {"name": "MCP", "endpoint": "stdio://...", "version": "2025-06-18"}
  ],
  "active": true,
  "registrations": [
    {"agentId": 42, "agentRegistry": "eip155:11155111:0x8004A818..."}
  ],
  "supportedTrust": ["reputation"]
}
```

Storage: IPFS, HTTPS, or base64 data URI.
Domain verification: `/.well-known/agent-registration.json`

## SDK Ecosystem
| Package | Purpose |
|---------|---------|
| `npx create-8004-agent` | Scaffold agent project |
| `@azeth/sdk` | Smart account + registry ops |
| `web3.py` + ABI | Direct contract interaction |
| `cast` (Foundry) | CLI registration fallback |

## Quickest Registration Path
```bash
# Option A: npx wizard
npx create-8004-agent

# Option B: Direct web3.py (what we use)
python3 erc8004.py --register

# Option C: cast CLI
cast send 0x8004A818BFB912233c491871b3d84c89A494BD9e \
  "register(string)" "data:application/json;base64,..." \
  --rpc-url $RPC --private-key $KEY
```

## Requirements for Hackathon
1. Sepolia ETH (gas) — faucet: https://sepoliafaucet.com/
2. SEPOLIA_PRIVATE_KEY env var
3. INFURA_KEY env var (or other Sepolia RPC)
4. web3.py (`pip install web3`) or Foundry cast

## Phase 2 Plan (Apr 3-7)
1. Register identity NFT on Sepolia
2. Post trading performance as reputation feedback (tradingYield tag)
3. Optionally request validation from a validator address
4. Display agent identity on performance dashboard
