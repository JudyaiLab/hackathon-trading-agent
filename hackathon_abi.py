"""
Hackathon Shared Contract ABI Fragments — Sepolia Testnet

Five shared contracts deployed for the ERC-8004 AI Trading Agent Hackathon.
All teams use these same contracts; the leaderboard reads from shared addresses.

Ref: https://github.com/Stephen-Kimoi/ai-trading-agent-template/blob/main/SHARED_CONTRACTS.md
"""

# ── AgentRegistry (register agent, get agentId) ──────────────
AGENT_REGISTRY_ABI = [
    {
        "name": "register",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "agentWallet", "type": "address"},
            {"name": "name", "type": "string"},
            {"name": "description", "type": "string"},
            {"name": "capabilities", "type": "string[]"},
            {"name": "agentURI", "type": "string"},
        ],
        "outputs": [{"name": "agentId", "type": "uint256"}],
    },
    {
        "name": "getAgent",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [
            {"name": "wallet", "type": "address"},
            {"name": "name", "type": "string"},
            {"name": "description", "type": "string"},
            {"name": "capabilities", "type": "string[]"},
            {"name": "agentURI", "type": "string"},
            {"name": "isActive", "type": "bool"},
        ],
    },
    {
        "name": "isRegistered",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getSigningNonce",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        # Actual on-chain event sig: cc66f27f... (3 indexed topics)
        "name": "AgentRegistered",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "agentId", "type": "uint256", "indexed": True},
            {"name": "wallet", "type": "address", "indexed": True},
            {"name": "operator", "type": "address", "indexed": True},
            {"name": "name", "type": "string", "indexed": False},
        ],
    },
]

# ── HackathonVault (claim 0.05 ETH allocation — optional) ────
HACKATHON_VAULT_ABI = [
    {
        "name": "claimAllocation",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [],
    },
    {
        "name": "getBalance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "hasClaimed",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "allocationPerTeam",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# ── RiskRouter (submit EIP-712 signed trade intents) ─────────
RISK_ROUTER_ABI = [
    {
        "name": "submitTradeIntent",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "intent",
                "type": "tuple",
                "components": [
                    {"name": "agentId", "type": "uint256"},
                    {"name": "agentWallet", "type": "address"},
                    {"name": "pair", "type": "string"},
                    {"name": "action", "type": "string"},
                    {"name": "amountUsdScaled", "type": "uint256"},
                    {"name": "maxSlippageBps", "type": "uint256"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                ],
            },
            {"name": "signature", "type": "bytes"},
        ],
        "outputs": [],
    },
    {
        "name": "simulateIntent",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {
                "name": "intent",
                "type": "tuple",
                "components": [
                    {"name": "agentId", "type": "uint256"},
                    {"name": "agentWallet", "type": "address"},
                    {"name": "pair", "type": "string"},
                    {"name": "action", "type": "string"},
                    {"name": "amountUsdScaled", "type": "uint256"},
                    {"name": "maxSlippageBps", "type": "uint256"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                ],
            },
        ],
        "outputs": [
            {"name": "valid", "type": "bool"},
            {"name": "reason", "type": "string"},
        ],
    },
    {
        "name": "getIntentNonce",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "TradeApproved",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "agentId", "type": "uint256", "indexed": True},
            {"name": "pair", "type": "string", "indexed": False},
            {"name": "action", "type": "string", "indexed": False},
        ],
    },
    {
        "name": "TradeRejected",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "agentId", "type": "uint256", "indexed": True},
            {"name": "reason", "type": "string", "indexed": False},
        ],
    },
]

# ── ValidationRegistry (post reasoning checkpoints) ──────────
VALIDATION_REGISTRY_ABI = [
    {
        "name": "postEIP712Attestation",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "agentId", "type": "uint256"},
            {"name": "checkpointHash", "type": "bytes32"},
            {"name": "score", "type": "uint8"},
            {"name": "notes", "type": "string"},
        ],
        "outputs": [],
    },
    {
        "name": "getAverageValidationScore",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# ── ReputationRegistry (submit feedback, query score) ────────
HACKATHON_REPUTATION_ABI = [
    {
        "name": "submitFeedback",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "agentId", "type": "uint256"},
            {"name": "score", "type": "uint8"},
            {"name": "outcomeRef", "type": "bytes32"},
            {"name": "comment", "type": "string"},
            {"name": "feedbackType", "type": "uint8"},
        ],
        "outputs": [],
    },
    {
        "name": "getAverageScore",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# ── EIP-712 Domain for RiskRouter ────────────────────────────
EIP712_DOMAIN = {
    "name": "RiskRouter",
    "version": "1",
    "chainId": 11155111,
    "verifyingContract": "0xd6A6952545FF6E6E6681c2d15C59f9EB8F40FdBC",
}

EIP712_TRADE_INTENT_TYPES = {
    "TradeIntent": [
        {"name": "agentId", "type": "uint256"},
        {"name": "agentWallet", "type": "address"},
        {"name": "pair", "type": "string"},
        {"name": "action", "type": "string"},
        {"name": "amountUsdScaled", "type": "uint256"},
        {"name": "maxSlippageBps", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
    ],
}
