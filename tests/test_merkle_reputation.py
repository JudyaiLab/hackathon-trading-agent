"""Tests for merkle.py and calc_reputation.py"""
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from merkle import _hash_leaf, _hash_pair, build_merkle_tree, compute_artifact_merkle, verify_record
from calc_reputation import calculate


# ---------------------------------------------------------------------------
# merkle.py tests
# ---------------------------------------------------------------------------


class TestHashLeaf:
    def test_hash_leaf_deterministic(self) -> None:
        """Same input must always produce the same hash."""
        record = {"action": "buy", "pair": "BTC/USD", "price": 60000}
        assert _hash_leaf(record) == _hash_leaf(record)

    def test_hash_leaf_different_inputs(self) -> None:
        """Different inputs must produce different hashes."""
        r1 = {"action": "buy", "pair": "BTC/USD"}
        r2 = {"action": "sell", "pair": "ETH/USD"}
        assert _hash_leaf(r1) != _hash_leaf(r2)


class TestBuildMerkleTree:
    def test_build_merkle_tree_single_leaf(self) -> None:
        """A single leaf tree should have that leaf as root and depth 0."""
        leaf = _hash_leaf({"x": 1})
        tree = build_merkle_tree([leaf])
        assert tree["root"] == leaf
        assert tree["leaf_count"] == 1
        assert tree["depth"] == 0

    def test_build_merkle_tree_even_leaves(self) -> None:
        """Even number of leaves should pair perfectly."""
        leaves = [_hash_leaf({"i": i}) for i in range(4)]
        tree = build_merkle_tree(leaves)
        assert tree["leaf_count"] == 4
        assert tree["depth"] == 2
        assert len(tree["root"]) == 64  # SHA-256 hex

    def test_build_merkle_tree_odd_leaves(self) -> None:
        """Odd number of leaves should promote the unpaired node."""
        leaves = [_hash_leaf({"i": i}) for i in range(3)]
        tree = build_merkle_tree(leaves)
        assert tree["leaf_count"] == 3
        assert tree["depth"] == 2
        assert len(tree["root"]) == 64

    def test_build_merkle_tree_empty(self) -> None:
        """Empty leaves should return a deterministic 'empty' root."""
        tree = build_merkle_tree([])
        expected_root = hashlib.sha256(b"empty").hexdigest()
        assert tree["root"] == expected_root
        assert tree["leaf_count"] == 0
        assert tree["depth"] == 0


class TestComputeArtifactMerkle:
    def test_compute_artifact_merkle_returns_root(self) -> None:
        """Integration: compute_artifact_merkle should return a valid root from real files."""
        result = compute_artifact_merkle()
        assert "merkle_root" in result
        assert len(result["merkle_root"]) == 64
        assert result["total_records"] >= 0
        assert "files" in result
        assert result["algorithm"] == "SHA-256 sorted-pair Merkle tree"


class TestVerifyRecord:
    def test_verify_record(self) -> None:
        """verify_record should return True for matching hash, False otherwise."""
        record = {"action": "buy", "pair": "SOL/USD", "ts": "2026-04-10"}
        correct_hash = _hash_leaf(record)
        assert verify_record(record, correct_hash) is True
        assert verify_record(record, "0" * 64) is False


# ---------------------------------------------------------------------------
# calc_reputation.py tests
# ---------------------------------------------------------------------------


def _mock_calculate(
    closes: list | None = None,
    state: dict | None = None,
    artifact_count: int = 0,
    ai_reviewed: int = 0,
) -> dict:
    """Run calculate() with mocked data sources."""
    if closes is None:
        closes = []
    if state is None:
        state = {}
    with (
        patch("calc_reputation.load_trades", return_value=closes),
        patch("calc_reputation.load_state", return_value=state),
        patch("calc_reputation.count_artifacts", return_value=artifact_count),
        patch("calc_reputation._count_ai_reviewed_intents", return_value=ai_reviewed),
    ):
        return calculate()


class TestCalcReputation:
    def test_zero_base_formula(self) -> None:
        """With zero trades and zero state the base component must be 0 (points are earned)."""
        result = _mock_calculate()
        assert result["breakdown"]["base"] == 0
        # Score is not 0 because risk_control awards 30 for low drawdown,
        # but the base padding itself must be zero.
        assert result["score"] >= 0

    def test_max_risk_score_low_drawdown(self) -> None:
        """Drawdown < 0.5% should earn the full 30 risk points."""
        state = {"risk": {"total_realized_pnl": 100.0, "peak_balance": 100100.0}}
        result = _mock_calculate(state=state)
        assert result["breakdown"]["risk_control"] == 30.0

    def test_negative_pnl_reduces_score(self) -> None:
        """Negative PnL should produce a negative pnl component."""
        closes = [{"type": "close", "pnl": -500.0}]
        state = {"risk": {"total_realized_pnl": -500.0, "peak_balance": 100000.0}}
        result = _mock_calculate(closes=closes, state=state)
        assert result["breakdown"]["pnl"] < 0

    def test_score_capped_at_100(self) -> None:
        """Even with extreme positive values the score must not exceed 100."""
        closes = [{"type": "close", "pnl": 50000.0}] * 100
        state = {
            "risk": {"total_realized_pnl": 50000.0, "peak_balance": 150000.0},
            "scan_count": 9999,
        }
        result = _mock_calculate(
            closes=closes,
            state=state,
            artifact_count=500,
            ai_reviewed=100,
        )
        assert result["score"] <= 100

    def test_formula_version_v3(self) -> None:
        """The formula version string must be v3_zero_base."""
        result = _mock_calculate()
        assert result["formula_version"] == "v3_zero_base"
