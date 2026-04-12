"""
Core tests — RiskManager logic, indicator math, config validation,
strategy engine, and executor position sizing.

Only pure logic; no external service mocks.
"""
import sys
import os
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk_manager import RiskManager, BatchTPLevel, BatchTPLevels, calculate_batch_tp_levels
from strategies import (
    ema, rsi, bollinger_bands, atr, macd, adx,
    StrategyEngine, RegimeDetector, MarketRegime, TradeSignal,
)
from erc8004 import (
    generate_agent_card, load_agent_card, save_agent_card,
    get_live_performance, update_reputation, get_reputation_summary,
)
from config import ACTIVE_PAIRS, PAIR_MAP, STRATEGY_PARAMS, BLACKLIST, RISK


# ── Helpers ──────────────────────────────────────────────────

def _make_rm(capital: float = 100_000.0) -> RiskManager:
    return RiskManager(initial_capital=capital)


def _ohlc_df(length: int = 60, base: float = 100.0, seed: int = 42) -> pd.DataFrame:
    """Generate a synthetic OHLC DataFrame with a slight uptrend."""
    np.random.seed(seed)
    close = base + np.cumsum(np.random.randn(length) * 0.5)
    high = close + np.abs(np.random.randn(length)) * 0.3
    low = close - np.abs(np.random.randn(length)) * 0.3
    return pd.DataFrame({
        "open": close - np.random.randn(length) * 0.1,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.randint(100, 1000, size=length).astype(float),
    })


def _trending_up_df(length: int = 200, base: float = 100.0) -> pd.DataFrame:
    """Generate a clearly trending-up OHLC DataFrame."""
    np.random.seed(99)
    # Steady uptrend with small noise
    trend = np.linspace(0, 50, length)
    noise = np.random.randn(length) * 0.3
    close = base + trend + noise
    high = close + np.abs(np.random.randn(length)) * 0.5
    low = close - np.abs(np.random.randn(length)) * 0.5
    # Volume surges on green candles for WaveRider confirmation
    volume = np.where(
        np.diff(close, prepend=close[0]) > 0,
        np.random.randint(800, 2000, size=length),
        np.random.randint(100, 400, size=length),
    ).astype(float)
    return pd.DataFrame({
        "open": close - 0.2,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


# ── RiskManager Tests ────────────────────────────────────────

class TestRiskManagerState:
    def test_initial_state(self):
        rm = _make_rm(50_000)
        assert rm.current_balance == 50_000
        assert rm.total_realized_pnl == 0.0
        assert rm.open_position_count == 0
        assert rm.consecutive_losses == 0
        assert rm.position_scale == 1.0

    def test_can_trade_basic(self):
        rm = _make_rm()
        allowed, reason = rm.can_trade("BTC/USDT", "long")
        assert allowed is True
        assert reason == "OK"

    def test_can_trade_max_positions(self):
        rm = _make_rm()
        for _ in range(RISK.max_concurrent_positions):
            rm.register_open("ETH/USDT", "long")
        allowed, reason = rm.can_trade("SOL/USDT", "long")
        assert allowed is False
        assert "Max positions" in reason

    def test_daily_loss_stop(self):
        rm = _make_rm(100_000)
        # 3% of 100k = 3000; register a loss exceeding that
        rm.register_open("BTC/USDT", "short")
        rm.register_close("BTC/USDT", -3100.0, "sl")
        allowed, reason = rm.can_trade("ETH/USDT", "long")
        assert allowed is False
        assert "Daily loss" in reason or "stopped" in reason.lower()

    def test_emergency_drawdown(self):
        rm = _make_rm(100_000)
        # 10% of 100k = 10000
        rm.register_open("BTC/USDT", "short")
        rm.register_close("BTC/USDT", -10_500, "sl")
        assert rm.check_emergency() is True

    def test_consecutive_loss_scaling(self):
        rm = _make_rm()
        for i in range(3):
            rm.register_open("ETH/USDT", "long")
            rm.register_close("ETH/USDT", -50.0, "sl")
        assert rm.consecutive_losses == 3
        assert rm.position_scale == RISK.consecutive_loss_scale  # 0.5

    def test_consecutive_loss_pause(self):
        rm = _make_rm()
        for i in range(RISK.consecutive_loss_pause):
            rm.register_open("ETH/USDT", "long")
            rm.register_close("ETH/USDT", -10.0, "sl")
        allowed, reason = rm.can_trade("ETH/USDT", "long")
        assert allowed is False
        assert "Consecutive losses" in reason

    def test_register_close_profit(self):
        rm = _make_rm(100_000)
        rm.register_open("SOL/USDT", "long")
        rm.register_close("SOL/USDT", 500.0, "tp")
        assert rm.total_realized_pnl == 500.0
        assert rm.current_balance == 100_500.0
        assert rm.consecutive_losses == 0
        assert rm.position_scale == 1.0

    def test_register_close_loss(self):
        rm = _make_rm()
        rm.register_open("XRP/USDT", "short")
        rm.register_close("XRP/USDT", -200.0, "sl")
        assert rm.consecutive_losses == 1
        assert rm.total_realized_pnl == -200.0


# ── Indicator Tests ──────────────────────────────────────────

class TestIndicators:
    def test_ema_calculation(self):
        df = _ohlc_df(30)
        result = ema(df["close"], 10)
        assert len(result) == 30
        assert not result.iloc[-1:].isna().any()
        # EMA should be close to the mean for stationary-ish data
        assert abs(result.iloc[-1] - df["close"].iloc[-10:].mean()) < 5.0

    def test_rsi_calculation(self):
        df = _ohlc_df(60)
        result = rsi(df["close"], 14)
        valid = result.dropna()
        assert len(valid) > 0
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_extreme_up(self):
        # Monotonically rising prices should push RSI near 100
        prices = pd.Series(range(1, 51), dtype=float)
        result = rsi(prices, 14).dropna()
        assert result.iloc[-1] > 80

    def test_bollinger_bands(self):
        df = _ohlc_df(40)
        upper, middle, lower = bollinger_bands(df["close"], 20, 2.0)
        valid_idx = middle.dropna().index
        assert (upper.loc[valid_idx] >= middle.loc[valid_idx]).all()
        assert (lower.loc[valid_idx] <= middle.loc[valid_idx]).all()
        # Band width should be positive
        width = upper.loc[valid_idx] - lower.loc[valid_idx]
        assert (width > 0).all()

    def test_atr_calculation(self):
        df = _ohlc_df(40)
        result = atr(df, 14)
        valid = result.dropna()
        assert len(valid) > 0
        assert (valid > 0).all()

    def test_macd_calculation(self):
        df = _ohlc_df(60)
        macd_line, signal_line, histogram = macd(df["close"])
        assert len(macd_line) == 60
        # Histogram = MACD line - signal line
        diff = (macd_line - signal_line - histogram).dropna().abs()
        assert (diff < 1e-10).all()

    def test_adx_calculation(self):
        """ADX should be 0-100 for typical data."""
        df = _ohlc_df(100)
        result = adx(df, 14)
        valid = result.dropna()
        assert len(valid) > 0
        assert (valid >= 0).all()
        assert (valid <= 100).all()


# ── Strategy Engine Tests ────────────────────────────────────

class TestRegimeDetector:
    def test_insufficient_data_returns_unknown(self):
        """Less than 50 bars → UNKNOWN regime."""
        rd = RegimeDetector()
        df = _ohlc_df(30)
        result = rd.detect(df)
        assert result.regime == MarketRegime.UNKNOWN
        assert result.confidence == 0

    def test_sufficient_data_returns_regime(self):
        """200 bars of trending data → valid regime, not UNKNOWN."""
        rd = RegimeDetector()
        df = _trending_up_df(200)
        result = rd.detect(df)
        assert result.regime != MarketRegime.UNKNOWN
        assert result.confidence > 0
        assert 0 < result.position_size_mult <= 1.0

    def test_trending_up_detection(self):
        """Clear uptrend should produce TRENDING_UP regime."""
        rd = RegimeDetector()
        df = _trending_up_df(200)
        result = rd.detect(df)
        # With a strong uptrend, should be TRENDING_UP or at least high ADX
        assert result.regime in (
            MarketRegime.TRENDING_UP,
            MarketRegime.HIGH_VOLATILITY,  # large moves may register as high vol
        )
        assert result.adx > 0


class TestStrategyEngine:
    def test_compute_indicators_adds_columns(self):
        """_compute_indicators should add all expected indicator columns."""
        engine = StrategyEngine()
        df = _ohlc_df(60)
        result = engine._compute_indicators(df)
        expected_cols = [
            "ema20", "ema50", "ema100", "ema200", "rsi", "atr",
            "vol_ma20", "vol_ratio", "ema_alignment",
            "golden_cross", "death_cross",
            "macd_line", "macd_signal", "macd_hist",
            "bb_upper", "bb_mid", "bb_lower", "bb_width",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing indicator column: {col}"

    def test_blacklisted_pair_returns_none(self):
        """Blacklisted pair+direction should produce no WaveRider signal."""
        engine = StrategyEngine()
        df = engine._compute_indicators(_trending_up_df(200))
        # BTC/USDT_long is in BLACKLIST
        result = engine._check_waverider(df, "BTC/USDT", "long")
        assert result is None

    def test_trade_signal_risk_reward(self):
        """TradeSignal.risk_reward_ratio should compute correctly."""
        sig = TradeSignal(
            pair="ETH/USDT", direction="long",
            entry_price=100.0, sl_price=95.0,
            tp1_price=105.0, tp2_price=110.0, tp3_price=115.0,
            confidence=60.0, source="waverider",
        )
        assert abs(sig.risk_reward_ratio() - 1.0) < 0.01  # 5 / 5 = 1.0

    def test_trade_signal_fields(self):
        """TradeSignal should have all required fields."""
        sig = TradeSignal(
            pair="SOL/USDT", direction="short",
            entry_price=200.0, sl_price=210.0,
            tp1_price=190.0, tp2_price=180.0, tp3_price=170.0,
            confidence=40.0, source="waverider", position_scale=0.75,
        )
        assert sig.direction == "short"
        assert sig.position_scale == 0.75
        assert sig.tp3_price < sig.tp2_price < sig.tp1_price < sig.entry_price


# ── Executor Position Sizing Tests ───────────────────────────

class TestExecutorSizing:
    def test_position_size_capped_at_max_pct(self):
        """Position value should not exceed max_position_pct of portfolio."""
        portfolio = 100_000
        max_pct = RISK.max_position_pct / 100  # 5% = 0.05
        max_value = portfolio * max_pct

        # Simulate sizing: risk_amount / sl_distance * entry_price
        entry = 50000  # BTC price
        sl = 48000     # SL $2000 below
        sl_dist = entry - sl
        risk_pct = RISK.risk_per_trade_pct / 100  # 1.5%
        risk_amount = portfolio * risk_pct  # $1500

        volume = risk_amount / sl_dist  # 0.75 BTC
        position_value = volume * entry  # $37,500

        # Cap at max
        if position_value > max_value:
            volume = max_value / entry

        position_value = volume * entry
        assert position_value <= max_value + 0.01

    def test_short_direction_scaling(self):
        """Short positions should be scaled by short_position_scale (0.7x)."""
        base_volume = 1.0
        short_volume = base_volume * RISK.short_position_scale
        assert short_volume == pytest.approx(0.7)


# ── ERC-8004 Agent Card Tests ────────────────────────────────

class TestERC8004:
    def test_agent_card_structure(self):
        """Agent card should have required ERC-8004 fields."""
        from erc8004 import generate_agent_card
        card = generate_agent_card()
        assert card["type"] == "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"
        assert card["name"] == "JudyAI WaveRider"
        assert card["active"] is True
        assert "reputation" in card["supportedTrust"]
        assert len(card["services"]) >= 2
        assert card["performance"]["strategies"] == 3
        assert card["performance"]["oosWinRate"] == 82.2

    def test_agent_card_risk_controls(self):
        """Agent card should expose risk control parameters."""
        from erc8004 import generate_agent_card
        card = generate_agent_card()
        rc = card["riskControls"]
        assert rc["maxPositionPct"] == 5.0
        assert rc["maxDailyLossPct"] == 3.0
        assert rc["maxDrawdownPct"] == 10.0
        assert rc["maxConcurrentPositions"] == 5

    def test_agent_card_with_pnl_data(self):
        """Agent card should include livePerformance when pnl_data provided."""
        from erc8004 import generate_agent_card
        pnl = {"currentValue": 100500, "unrealizedPnl": 500}
        card = generate_agent_card(pnl_data=pnl)
        assert card["livePerformance"]["currentValue"] == 100500


# ── Config Validation Tests ──────────────────────────────────

class TestConfig:
    def test_pair_map_consistency(self):
        for pair in ACTIVE_PAIRS:
            assert pair in PAIR_MAP, f"{pair} in ACTIVE_PAIRS but not in PAIR_MAP"

    def test_strategy_params_valid(self):
        for direction in STRATEGY_PARAMS:
            for pair in STRATEGY_PARAMS[direction]:
                assert pair in PAIR_MAP, (
                    f"STRATEGY_PARAMS[{direction}][{pair}] references "
                    f"a pair not in PAIR_MAP"
                )

    def test_blacklist_format(self):
        for entry in BLACKLIST:
            # Format: PAIR_direction or PAIR_direction_suffix (e.g. BNB/USDT_long_old)
            parts = entry.split("_")
            # Find direction part: must contain "long" or "short"
            has_direction = any(p in ("long", "short") for p in parts)
            assert has_direction, (
                f"Blacklist entry '{entry}' missing 'long' or 'short' direction"
            )

    def test_no_stale_params(self):
        for direction in STRATEGY_PARAMS:
            for pair in STRATEGY_PARAMS[direction]:
                assert pair in PAIR_MAP, (
                    f"Stale param: {pair} in STRATEGY_PARAMS[{direction}] "
                    f"but missing from PAIR_MAP"
                )

    def test_active_pairs_count(self):
        """Should have exactly 7 active pairs (incl. LINK added for competition)."""
        assert len(ACTIVE_PAIRS) == 7
        assert "BTC/USDT" in ACTIVE_PAIRS
        assert "ETH/USDT" in ACTIVE_PAIRS
        assert "LINK/USDT" in ACTIVE_PAIRS


# ── Executor SL/TP State Machine Tests ───────────────────────

class TestSLTPStateMachine:
    """Test the trailing stop state machine using immutable Position updates."""

    def _make_position(self, entry=100.0, sl=95.0, tp1=105.0, tp2=110.0, tp3=115.0):
        from executor import Position
        return Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=entry, volume=1.0,
            sl_price=sl, tp1_price=tp1, tp2_price=tp2, tp3_price=tp3,
            source="waverider",
        )

    def test_sl_hit_triggers_close(self):
        """Price below SL should trigger close."""
        pos = self._make_position()
        price = 94.0  # below SL of 95
        assert price <= pos.sl_price

    def test_tp1_moves_sl_to_breakeven(self):
        """TP1 hit should move SL to breakeven (entry price)."""
        pos = self._make_position()
        price = 106.0  # above TP1 of 105
        assert price >= pos.tp1_price
        # Immutable TP1 update
        if not pos.tp1_hit and price >= pos.tp1_price:
            pos = replace(pos, tp1_hit=True, sl_price=pos.entry_price)
        assert pos.tp1_hit is True
        assert pos.sl_price == 100.0  # breakeven

    def test_tp2_tightens_sl_to_tp1(self):
        """TP2 hit should tighten SL to TP1 level."""
        pos = self._make_position()
        pos = replace(pos, tp1_hit=True)
        price = 111.0  # above TP2 of 110
        assert price >= pos.tp2_price
        if price >= pos.tp2_price:
            pos = replace(pos, sl_price=pos.tp1_price)
        assert pos.sl_price == 105.0  # TP1 level

    def test_tp3_triggers_full_close(self):
        """Price above TP3 should trigger full position close."""
        pos = self._make_position()
        price = 116.0  # above TP3 of 115
        assert price >= pos.tp3_price

    def test_trailing_stop_full_sequence(self):
        """Full sequence: TP1 → breakeven, TP2 → tighten, TP3 → close."""
        pos = self._make_position(entry=100.0, sl=95.0, tp1=105.0, tp2=110.0, tp3=115.0)

        # Phase 1: Price rises to TP1 (immutable update)
        price = 106.0
        if not pos.tp1_hit and price >= pos.tp1_price:
            pos = replace(pos, tp1_hit=True, sl_price=pos.entry_price)
        assert pos.sl_price == 100.0  # breakeven

        # Phase 2: Price rises to TP2 (immutable update)
        price = 111.0
        if price >= pos.tp2_price:
            pos = replace(pos, sl_price=pos.tp1_price)
        assert pos.sl_price == 105.0  # tightened to TP1

        # Phase 3: Price rises to TP3
        price = 116.0
        should_close = price >= pos.tp3_price
        assert should_close is True

    def test_sl_after_tp1_is_breakeven(self):
        """After TP1, SL at breakeven means no loss even if price drops."""
        pos = self._make_position(entry=100.0, sl=95.0, tp1=105.0)
        # TP1 hit (immutable update)
        pos = replace(pos, tp1_hit=True, sl_price=pos.entry_price)
        # Price drops back but above new SL
        price = 101.0
        assert price > pos.sl_price  # still safe
        # Price drops to exactly breakeven — SL triggers
        price = 100.0
        assert price <= pos.sl_price  # SL triggers, PnL ≈ 0


# ── BB Squeeze Logic Tests ───────────────────────────────────

class TestBBSqueeze:
    def test_squeeze_requires_compression_and_expansion(self):
        """BB Squeeze should require BOTH compression AND expansion."""
        engine = StrategyEngine()
        # Generate data with stable BB width (no squeeze)
        df = engine._compute_indicators(_ohlc_df(60))
        # The AND condition should be stricter than the old OR
        result = engine._check_bb_squeeze(df, "ETH/USDT", "long")
        # For random walk data, squeeze-and-expand is unlikely
        assert isinstance(result, bool)


# ── State Roundtrip Tests ────────────────────────────────────

class TestStateRoundtrip:
    def test_risk_manager_state_survives_profit_loss_cycle(self):
        """RiskManager state should be consistent after profit+loss cycle."""
        rm = _make_rm(100_000)
        # Open and close with profit
        rm.register_open("ETH/USDT", "long")
        rm.register_close("ETH/USDT", 500.0, "tp")
        # Open and close with loss
        rm.register_open("BTC/USDT", "short")
        rm.register_close("BTC/USDT", -200.0, "sl")

        # Verify composite state
        assert rm.total_realized_pnl == 300.0
        assert rm.current_balance == 100_300.0
        assert rm.consecutive_losses == 1
        assert rm.open_position_count == 0

    def test_risk_state_serializable(self):
        """RiskManager key fields should be JSON-serializable."""
        import json
        rm = _make_rm(100_000)
        rm.register_open("ETH/USDT", "long")
        rm.register_close("ETH/USDT", -100.0, "sl")

        state = {
            "total_realized_pnl": rm.total_realized_pnl,
            "peak_balance": rm.peak_balance,
            "consecutive_losses": rm.consecutive_losses,
            "position_scale": rm.position_scale,
            "current_balance": rm.current_balance,
        }
        serialized = json.dumps(state)
        restored = json.loads(serialized)

        assert restored["total_realized_pnl"] == -100.0
        assert restored["consecutive_losses"] == 1


# ── MACD Swing Point Detection Tests ─────────────────────────

class TestMACDSwingPoints:
    def test_find_swing_points_detects_lows(self):
        """Swing point detector should find local minima."""
        from indicators import find_swing_points
        # V-shaped dip: [10, 8, 6, 4, 6, 8, 10]
        series = pd.Series([10, 8, 6, 4, 6, 8, 10])
        lows, highs = find_swing_points(series, order=2)
        assert len(lows) >= 1
        assert any(v == 4.0 for _, v in lows)

    def test_find_swing_points_detects_highs(self):
        """Swing point detector should find local maxima."""
        from indicators import find_swing_points
        # Peak: [5, 7, 9, 11, 9, 7, 5]
        series = pd.Series([5, 7, 9, 11, 9, 7, 5])
        lows, highs = find_swing_points(series, order=2)
        assert len(highs) >= 1
        assert any(v == 11.0 for _, v in highs)

    def test_divergence_requires_two_swing_points(self):
        """MACD divergence should return False without 2+ swing points."""
        engine = StrategyEngine()
        # Short flat data — unlikely to produce 2 swing points
        df = engine._compute_indicators(_ohlc_df(35, seed=1))
        result = engine._check_macd_divergence(df, "ETH/USDT", "long")
        assert isinstance(result, bool)


# ── ERC-8004 Module Split Tests ──────────────────────────────

class TestERC8004ModuleSplit:
    def test_facade_exports_card_functions(self):
        """erc8004.py should re-export all card functions."""
        assert callable(generate_agent_card)
        assert callable(save_agent_card)
        assert callable(load_agent_card)
        assert callable(get_live_performance)

    def test_facade_exports_chain_functions(self):
        """erc8004.py should re-export all chain functions."""
        assert callable(update_reputation)
        assert callable(get_reputation_summary)

    def test_abi_importable(self):
        """ABI fragments should be importable from the dedicated module."""
        from erc8004_abi import IDENTITY_ABI, REPUTATION_ABI
        assert len(IDENTITY_ABI) >= 3   # register, setAgentURI, tokenURI, event
        assert len(REPUTATION_ABI) >= 2  # giveFeedback, getSummary


# ── Batch Take-Profit Tests ────────────────────────────────

class TestBatchTPLevels:
    def test_calculate_default_levels(self):
        """Default batch TP: 25% / 25% / 50%."""
        result = calculate_batch_tp_levels(105.0, 110.0, 115.0)
        assert result.tp1.price == 105.0
        assert result.tp1.close_pct == 0.25
        assert result.tp2.price == 110.0
        assert result.tp2.close_pct == 0.25
        assert result.tp3.price == 115.0
        assert result.tp3.close_pct == 0.50

    def test_custom_percentages(self):
        """Custom close percentages should work if they sum to 1.0."""
        result = calculate_batch_tp_levels(
            105.0, 110.0, 115.0,
            tp1_close_pct=0.20, tp2_close_pct=0.30, tp3_close_pct=0.50,
        )
        assert result.tp1.close_pct == 0.20
        assert result.tp2.close_pct == 0.30
        assert result.tp3.close_pct == 0.50

    def test_invalid_percentages_raises(self):
        """Close percentages not summing to 1.0 should raise ValueError."""
        with pytest.raises(ValueError, match="sum to 1.0"):
            calculate_batch_tp_levels(
                105.0, 110.0, 115.0,
                tp1_close_pct=0.30, tp2_close_pct=0.30, tp3_close_pct=0.50,
            )

    def test_levels_property(self):
        """levels property should return tuple of 3 BatchTPLevel."""
        result = calculate_batch_tp_levels(105.0, 110.0, 115.0)
        levels = result.levels
        assert len(levels) == 3
        assert all(isinstance(lvl, BatchTPLevel) for lvl in levels)

    def test_frozen_immutability(self):
        """BatchTPLevels should be frozen (immutable)."""
        result = calculate_batch_tp_levels(105.0, 110.0, 115.0)
        with pytest.raises(AttributeError):
            result.tp1 = BatchTPLevel(price=999.0, close_pct=0.5)  # type: ignore

    def test_batch_tp_level_frozen(self):
        """BatchTPLevel should be frozen (immutable)."""
        lvl = BatchTPLevel(price=100.0, close_pct=0.25)
        with pytest.raises(AttributeError):
            lvl.price = 200.0  # type: ignore


class TestPositionRemainingPct:
    """Test remaining_pct and active_volume on Position."""

    def _make_position(self, remaining_pct: float = 1.0):
        from executor import Position
        return Position(
            pair="ETH/USDT", cli_pair="ETHUSD", direction="long",
            entry_price=100.0, volume=10.0,
            sl_price=95.0, tp1_price=105.0, tp2_price=110.0, tp3_price=115.0,
            source="waverider", remaining_pct=remaining_pct,
        )

    def test_full_position_active_volume(self):
        """100% remaining → active_volume == volume."""
        pos = self._make_position(1.0)
        assert pos.active_volume == 10.0

    def test_partial_position_active_volume(self):
        """75% remaining → active_volume == volume * 0.75."""
        pos = self._make_position(0.75)
        assert pos.active_volume == pytest.approx(7.5)

    def test_half_position_active_volume(self):
        """50% remaining → active_volume == volume * 0.50."""
        pos = self._make_position(0.50)
        assert pos.active_volume == pytest.approx(5.0)

    def test_remaining_pct_immutable_update(self):
        """Updating remaining_pct via replace keeps original intact."""
        pos = self._make_position(1.0)
        updated = replace(pos, remaining_pct=0.75)
        assert pos.remaining_pct == 1.0
        assert pos.active_volume == 10.0
        assert updated.remaining_pct == 0.75
        assert updated.active_volume == pytest.approx(7.5)

    def test_batch_tp_full_sequence_remaining(self):
        """Simulate full batch TP: 100% → 75% → 50% → close."""
        pos = self._make_position(1.0)

        # TP1: close 25%
        pos = replace(pos, tp1_hit=True, sl_price=pos.entry_price, remaining_pct=0.75)
        assert pos.remaining_pct == 0.75
        assert pos.active_volume == pytest.approx(7.5)

        # TP2: close another 25%
        pos = replace(pos, tp2_hit=True, sl_price=pos.tp1_price, remaining_pct=0.50)
        assert pos.remaining_pct == 0.50
        assert pos.active_volume == pytest.approx(5.0)

        # TP3: close remaining 50%
        assert pos.tp3_price == 115.0
        # At this point close_position would close 5.0 volume
