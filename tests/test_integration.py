"""
Integration tests — mock subprocess to test data adapter, executor, and
the full scan-and-trade flow without requiring a live Kraken CLI.
"""
import json
import sys
import os
from dataclasses import replace
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kraken_data import KrakenDataAdapter
from executor import KrakenExecutor, Position
from strategies import StrategyEngine, TradeSignal


# ── Fixtures ─────────────────────────────────────────────────

MOCK_TICKER_RESPONSE = {
    "XETHZUSD": {
        "a": ["2000.00", "1", "1.000"],
        "b": ["1999.50", "1", "1.000"],
        "c": ["2000.00", "0.5"],
        "v": ["1000.0", "5000.0"],
    }
}

MOCK_OHLC_RESPONSE = {
    "XETHZUSD": [
        [1711900800 + i * 14400, str(1900 + i * 2), str(1910 + i * 2),
         str(1895 + i * 2), str(1905 + i * 2), "0", str(100 + i * 10), 50]
        for i in range(100)
    ],
    "last": 1711900800 + 99 * 14400,
}

MOCK_PAPER_STATUS = {
    "starting_balance": 100000,
    "current_value": 100500,
    "unrealized_pnl": 500,
    "unrealized_pnl_pct": 0.5,
    "total_trades": 3,
}

MOCK_BUY_RESULT = {"action": "buy", "pair": "ETHUSD", "volume": "0.50000000"}
MOCK_SELL_RESULT = {"action": "sell", "pair": "ETHUSD", "volume": "0.50000000"}


MOCK_BALANCE_RESPONSE = {
    "balances": {
        "USD": {"total": 95000},
        "ETH": {"total": 2.5},
    }
}


def _mock_subprocess_run(cmd, **kwargs):
    """Route mocked subprocess.run calls based on command args."""
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""

    args = cmd
    if "ticker" in args:
        result.stdout = json.dumps(MOCK_TICKER_RESPONSE)
    elif "ohlc" in args:
        result.stdout = json.dumps(MOCK_OHLC_RESPONSE)
    elif "status" in args:
        result.stdout = json.dumps(MOCK_PAPER_STATUS)
    elif "balance" in args:
        result.stdout = json.dumps(MOCK_BALANCE_RESPONSE)
    elif "buy" in args:
        result.stdout = json.dumps(MOCK_BUY_RESULT)
    elif "sell" in args:
        result.stdout = json.dumps(MOCK_SELL_RESULT)
    else:
        result.stdout = json.dumps({})

    return result


# ── KrakenDataAdapter Tests ──────────────────────────────────

class TestKrakenDataAdapterIntegration:

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_get_ticker(self, mock_run):
        adapter = KrakenDataAdapter()
        ticker = adapter.get_ticker("ETH/USDT")
        assert ticker["last"] == 2000.00
        assert ticker["bid"] == 1999.50
        assert ticker["ask"] == 2000.00
        assert ticker["pair"] == "ETH/USDT"

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_get_ohlc(self, mock_run):
        adapter = KrakenDataAdapter()
        df = adapter.get_ohlc("ETH/USDT", "4h")
        assert not df.empty
        assert len(df) == 100
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
        assert df["close"].dtype == float

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_get_multi_ticker(self, mock_run):
        adapter = KrakenDataAdapter()
        tickers = adapter.get_multi_ticker(["ETH/USDT"])
        assert "ETH/USDT" in tickers
        assert tickers["ETH/USDT"]["last"] == 2000.00

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_unknown_pair_raises(self, mock_run):
        adapter = KrakenDataAdapter()
        with pytest.raises(ValueError, match="Unknown pair"):
            adapter.get_ohlc("FAKE/USDT", "4h")


# ── KrakenExecutor Tests ─────────────────────────────────────

class TestKrakenExecutorIntegration:

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_get_paper_status(self, mock_run):
        executor = KrakenExecutor()
        status = executor.get_paper_status()
        assert status["starting_balance"] == 100000
        assert status["current_value"] == 100500

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_execute_buy(self, mock_run):
        executor = KrakenExecutor()
        result = executor.execute_buy("ETHUSD", 0.5)
        assert result["action"] == "buy"

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_execute_sell(self, mock_run):
        executor = KrakenExecutor()
        result = executor.execute_sell("ETHUSD", 0.5)
        assert result["action"] == "sell"

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_execute_signal_long(self, mock_run):
        executor = KrakenExecutor()
        signal = TradeSignal(
            pair="ETH/USDT", direction="long",
            entry_price=2000.0, sl_price=1950.0,
            tp1_price=2050.0, tp2_price=2100.0, tp3_price=2150.0,
            confidence=60.0, source="waverider",
        )
        success = executor.execute_signal(signal, 100000)
        assert success is True
        assert "ETH/USDT" in executor.positions
        pos = executor.positions["ETH/USDT"]
        assert pos.direction == "long"
        assert pos.entry_price == 2000.0

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_execute_signal_short_paper_tracked(self, mock_run):
        executor = KrakenExecutor()
        signal = TradeSignal(
            pair="ETH/USDT", direction="short",
            entry_price=2000.0, sl_price=2050.0,
            tp1_price=1950.0, tp2_price=1900.0, tp3_price=1850.0,
            confidence=40.0, source="waverider",
        )
        success = executor.execute_signal(signal, 100000)
        assert success is True  # Shorts are paper-tracked internally
        assert "ETH/USDT" in executor.positions
        assert executor.positions["ETH/USDT"].direction == "short"

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_close_position_short(self, mock_run):
        """Close an internal paper short — no Kraken CLI needed."""
        executor = KrakenExecutor()
        executor.positions["ETH/USDT"] = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="short",
            entry_price=2000.0, volume=0.5,
            sl_price=2050.0, tp1_price=1950.0, tp2_price=1900.0, tp3_price=1850.0,
        )
        pnl = executor.close_position("ETH/USDT", 1940.0, "TP1_HIT")
        assert pnl == pytest.approx(30.0)  # (2000 - 1940) * 0.5
        assert "ETH/USDT" not in executor.positions


# ── Position Immutability Tests ──────────────────────────────

class TestPositionImmutability:

    def test_position_is_frozen(self):
        pos = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=100.0, volume=1.0,
            sl_price=95.0, tp1_price=105.0, tp2_price=110.0, tp3_price=115.0,
        )
        with pytest.raises(AttributeError):
            pos.sl_price = 99.0  # type: ignore

    def test_position_replace_creates_copy(self):
        pos = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=100.0, volume=1.0,
            sl_price=95.0, tp1_price=105.0, tp2_price=110.0, tp3_price=115.0,
        )
        updated = replace(pos, tp1_hit=True, sl_price=100.0)
        assert updated.tp1_hit is True
        assert updated.sl_price == 100.0
        assert pos.tp1_hit is False      # original unchanged
        assert pos.sl_price == 95.0       # original unchanged

    def test_trailing_stop_immutable_sequence(self):
        """Full trailing stop flow using immutable updates."""
        pos = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=100.0, volume=1.0,
            sl_price=95.0, tp1_price=105.0, tp2_price=110.0, tp3_price=115.0,
        )
        # TP1 hit: SL → breakeven
        pos = replace(pos, tp1_hit=True, sl_price=pos.entry_price)
        assert pos.sl_price == 100.0

        # TP2 hit: SL → TP1
        pos = replace(pos, sl_price=pos.tp1_price)
        assert pos.sl_price == 105.0

        # TP3: should trigger close
        assert 116.0 >= pos.tp3_price


# ── Scan-and-Trade Flow Test ─────────────────────────────────

class TestScanAndTradeFlow:

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_full_scan_produces_no_crash(self, mock_run):
        """Full scan-and-trade cycle should complete without errors."""
        engine = StrategyEngine()
        adapter = KrakenDataAdapter()
        executor = KrakenExecutor()

        # Scan all pairs (mocked data)
        signals = engine.scan_all(adapter)
        # Signals may or may not fire on synthetic data — that's fine.
        # The important thing is no exceptions.
        assert isinstance(signals, list)

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_executor_respects_duplicate_position(self, mock_run):
        """Should not open a second position for the same pair."""
        executor = KrakenExecutor()
        signal = TradeSignal(
            pair="ETH/USDT", direction="long",
            entry_price=2000.0, sl_price=1950.0,
            tp1_price=2050.0, tp2_price=2100.0, tp3_price=2150.0,
            confidence=60.0, source="waverider",
        )
        assert executor.execute_signal(signal, 100000) is True
        assert executor.execute_signal(signal, 100000) is False  # duplicate blocked


# ── Batch Take-Profit Integration Tests ────────────────────

class TestBatchTPIntegration:
    """Test batch take-profit partial close flow end-to-end."""

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_partial_close_long_pnl(self, mock_run):
        """Partial close of a long should return correct PnL for closed portion."""
        executor = KrakenExecutor()
        executor.positions["ETH/USDT"] = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=2000.0, volume=1.0,
            sl_price=1950.0, tp1_price=2050.0, tp2_price=2100.0, tp3_price=2150.0,
        )
        # Close 25% at TP1 (price=2050)
        pnl = executor.partial_close("ETH/USDT", 2050.0, 0.25, "TP1_PARTIAL")
        # PnL = (2050 - 2000) * (1.0 * 0.25) = 50 * 0.25 = 12.5
        assert pnl == pytest.approx(12.5)
        assert "ETH/USDT" in executor.positions
        assert executor.positions["ETH/USDT"].remaining_pct == pytest.approx(0.75)

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_partial_close_short_pnl(self, mock_run):
        """Partial close of a short should return correct PnL for closed portion."""
        executor = KrakenExecutor()
        executor.positions["ETH/USDT"] = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="short",
            entry_price=2000.0, volume=1.0,
            sl_price=2050.0, tp1_price=1950.0, tp2_price=1900.0, tp3_price=1850.0,
        )
        # Close 25% at TP1 (price=1950)
        pnl = executor.partial_close("ETH/USDT", 1950.0, 0.25, "TP1_PARTIAL")
        # PnL = (2000 - 1950) * (1.0 * 0.25) = 50 * 0.25 = 12.5
        assert pnl == pytest.approx(12.5)
        assert executor.positions["ETH/USDT"].remaining_pct == pytest.approx(0.75)

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_full_batch_tp_sequence_long(self, mock_run):
        """Full batch TP sequence: TP1 (25%) → TP2 (25%) → full close (50%)."""
        executor = KrakenExecutor()
        executor.positions["ETH/USDT"] = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=2000.0, volume=1.0,
            sl_price=1950.0, tp1_price=2050.0, tp2_price=2100.0, tp3_price=2150.0,
        )

        # TP1: close 25%
        pnl1 = executor.partial_close("ETH/USDT", 2050.0, 0.25, "TP1_PARTIAL")
        pos = executor.positions["ETH/USDT"]
        pos = replace(pos, tp1_hit=True, sl_price=pos.entry_price)
        executor.positions["ETH/USDT"] = pos
        assert pnl1 == pytest.approx(12.5)
        assert pos.remaining_pct == pytest.approx(0.75)
        assert pos.sl_price == 2000.0  # breakeven

        # TP2: close another 25%
        pnl2 = executor.partial_close("ETH/USDT", 2100.0, 0.25, "TP2_PARTIAL")
        pos = executor.positions["ETH/USDT"]
        pos = replace(pos, tp2_hit=True, sl_price=pos.tp1_price)
        executor.positions["ETH/USDT"] = pos
        assert pnl2 == pytest.approx(25.0)  # (2100-2000)*0.25
        assert pos.remaining_pct == pytest.approx(0.50)
        assert pos.sl_price == 2050.0  # SL at TP1

        # TP3: close remaining 50%
        pnl3 = executor.close_position("ETH/USDT", 2150.0, "TP3_HIT")
        # active_volume = 1.0 * 0.50 = 0.5; PnL = (2150-2000)*0.5 = 75
        assert pnl3 == pytest.approx(75.0)
        assert "ETH/USDT" not in executor.positions

        total_pnl = pnl1 + pnl2 + pnl3
        assert total_pnl == pytest.approx(112.5)

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_sl_after_partial_close_uses_active_volume(self, mock_run):
        """SL after TP1 partial close should only close remaining volume."""
        executor = KrakenExecutor()
        executor.positions["ETH/USDT"] = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=2000.0, volume=1.0,
            sl_price=2000.0, tp1_price=2050.0, tp2_price=2100.0, tp3_price=2150.0,
            tp1_hit=True, remaining_pct=0.75,
        )
        # SL hit at breakeven — close remaining 75%
        pnl = executor.close_position("ETH/USDT", 2000.0, "SL_HIT")
        # active_volume = 1.0 * 0.75 = 0.75; PnL = (2000-2000)*0.75 = 0
        assert pnl == pytest.approx(0.0)
        assert "ETH/USDT" not in executor.positions

    @patch("kraken_cli.subprocess.run", side_effect=_mock_subprocess_run)
    def test_partial_close_logs_in_trade_log(self, mock_run):
        """Partial close should create a trade_log entry with action=partial_close."""
        executor = KrakenExecutor()
        executor.positions["ETH/USDT"] = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=2000.0, volume=1.0,
            sl_price=1950.0, tp1_price=2050.0, tp2_price=2100.0, tp3_price=2150.0,
        )
        executor.partial_close("ETH/USDT", 2050.0, 0.25, "TP1_PARTIAL")
        partial_logs = [e for e in executor.trade_log if e["action"] == "partial_close"]
        assert len(partial_logs) == 1
        assert partial_logs[0]["close_pct"] == 0.25
        assert partial_logs[0]["remaining_pct"] == pytest.approx(0.75)

    def test_position_tp2_hit_field(self):
        """Position should have tp2_hit field."""
        pos = Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=100.0, volume=1.0,
            sl_price=95.0, tp1_price=105.0, tp2_price=110.0, tp3_price=115.0,
        )
        assert pos.tp2_hit is False
        updated = replace(pos, tp2_hit=True)
        assert updated.tp2_hit is True
        assert pos.tp2_hit is False  # original unchanged
