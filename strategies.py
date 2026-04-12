"""
StrategyEngine — Multi-strategy signal generator

Strategies:
  - WaveRider (EMA + RSI + Volume) — primary trend-following
  - BB Squeeze — Bollinger Band compression breakout
  - MACD Divergence — momentum divergence reversal
  - EMA Reaction — pullback-to-EMA hold/rejection pattern
  - MACD Consecutive Divergence — multi-bar histogram divergence reversal
  - Regime Detection — ADX-based market state classification

Features:
  - 36-cell regime grid routing with per-cell optimized params
  - 6 market regimes (trending up/down, ranging, high vol, exhaustion, breakout)
  - Walk-Forward Optimization (WFO) validated strategy parameters
"""
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config import STRATEGY_PARAMS, BLACKLIST, ACTIVE_PAIRS, REGIME_GRID
from indicators import (
    ema, sma, rsi, atr, macd, bollinger_bands, adx,
    find_swing_points, MarketRegime, RegimeResult, RegimeDetector,
)

logger = logging.getLogger(__name__)


# ── Data Classes ──────────────────────────────────────────────

@dataclass
class TradeSignal:
    pair: str             # Standard pair name, e.g. "BTC/USDT"
    direction: str        # "long" or "short"
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    confidence: float     # RSI value as proxy
    source: str           # "waverider", "bb_squeeze", "macd_div", "ema_reaction", "macd_divergence_consecutive", "multi"
    position_scale: float = 1.0  # 0.0-1.0, adjusted by regime/filters
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # AI-enhanced fields (populated by OpusAnalyst)
    ai_verdict: str = ""          # STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL
    ai_confidence: int = 0        # 0-100
    ai_reasoning: str = ""        # One-line AI judgment
    ensemble_score: float = 0.0   # Combined rule + AI score
    # Regime/grid context (populated by scan_pair)
    regime: str = ""              # e.g. "trending_up", "high_volatility"
    grid_cell: str = ""           # e.g. "WFO", "Q8"
    oos_wr: float = 0.0           # OOS win rate from grid cell
    indicators: dict = field(default_factory=dict)  # ADX, BB, EMA values at signal time

    def risk_reward_ratio(self) -> float:
        sl_dist = abs(self.entry_price - self.sl_price)
        tp_dist = abs(self.tp1_price - self.entry_price)
        return tp_dist / sl_dist if sl_dist > 0 else 0.0


# ── Strategy Engine ───────────────────────────────────────────

class StrategyEngine:
    """Multi-strategy signal generator with regime-adaptive routing."""

    def __init__(self):
        self.regime_detector = RegimeDetector()
        self.params = STRATEGY_PARAMS

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all needed indicators to a DataFrame."""
        df = df.copy()
        df["ema20"] = ema(df["close"], 20)
        df["ema50"] = ema(df["close"], 50)
        df["ema100"] = ema(df["close"], 100)
        df["ema200"] = ema(df["close"], 200)
        df["ema20_prev"] = df["ema20"].shift(1)
        df["ema50_prev"] = df["ema50"].shift(1)
        df["rsi"] = rsi(df["close"], 14)
        df["atr"] = atr(df, 14)
        df["vol_ma20"] = sma(df["volume"], 20)
        df["vol_ratio"] = (df["volume"] / df["vol_ma20"].replace(0, np.nan)).fillna(1.0)

        # EMA alignment score (0-3): how many EMA pairs are bullish-ordered
        df["ema_alignment"] = (
            (df["ema20"] > df["ema50"]).astype(int) +
            (df["ema50"] > df["ema100"]).astype(int) +
            (df["ema100"] > df["ema200"]).astype(int)
        )

        # EMA crosses
        df["golden_cross"] = (
            (df["ema20"] > df["ema50"]) & (df["ema20_prev"] <= df["ema50_prev"])
        )
        df["death_cross"] = (
            (df["ema20"] < df["ema50"]) & (df["ema20_prev"] >= df["ema50_prev"])
        )

        # MACD
        df["macd_line"], df["macd_signal"], df["macd_hist"] = macd(df["close"])

        # Bollinger Bands
        df["bb_upper"], df["bb_mid"], df["bb_lower"] = bollinger_bands(df["close"])
        df["bb_width"] = ((df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]) * 100

        return df

    def _check_waverider_with_params(self, df: pd.DataFrame, pair: str,
                                     direction: str, cell_params: dict,
                                     btc_trend: str = "neutral") -> TradeSignal | None:
        """WaveRider strategy using regime-grid cell params."""
        # BTC trend filter
        if cell_params.get("btc_filter", False):
            if direction == "long" and btc_trend not in ("up", "neutral"):
                return None
            if direction == "short" and btc_trend not in ("down", "neutral"):
                return None

        latest = df.iloc[-1]
        if pd.isna(latest["ema50"]) or pd.isna(latest["rsi"]):
            return None

        rsi_thresh = cell_params["rsi_threshold"]
        atr_mult = cell_params["atr_sl_multiplier"]
        vol_mult = cell_params["vol_multiplier"]

        return self._waverider_core(df, pair, direction, rsi_thresh, atr_mult, vol_mult, btc_trend)

    def _check_waverider(self, df: pd.DataFrame, pair: str,
                         direction: str,
                         btc_trend: str = "neutral") -> TradeSignal | None:
        """WaveRider strategy: EMA trend + RSI + volume confirmation."""
        key = f"{pair}_{direction}"
        if key in BLACKLIST:
            return None

        params = self.params.get(direction, {}).get(pair)
        if not params:
            return None

        # BTC trend filter: skip if BTC not aligned with direction
        if params.get("btc_filter", False):
            if direction == "long" and btc_trend not in ("up", "neutral"):
                logger.debug(f"{pair} long blocked by btc_filter (BTC trend={btc_trend})")
                return None
            if direction == "short" and btc_trend not in ("down", "neutral"):
                logger.debug(f"{pair} short blocked by btc_filter (BTC trend={btc_trend})")
                return None

        latest = df.iloc[-1]
        if pd.isna(latest["ema50"]) or pd.isna(latest["rsi"]):
            return None

        rsi_thresh = params["rsi_threshold"]
        atr_mult = params["atr_sl_multiplier"]
        vol_mult = params["vol_multiplier"]

        return self._waverider_core(df, pair, direction, rsi_thresh, atr_mult, vol_mult, btc_trend)

    def _waverider_core(self, df: pd.DataFrame, pair: str,
                        direction: str, rsi_thresh: float,
                        atr_mult: float, vol_mult: float,
                        btc_trend: str = "neutral") -> TradeSignal | None:
        """Core WaveRider logic shared by grid-cell and legacy paths."""
        latest = df.iloc[-1]

        # Layer 1: 4H trend confirmation
        if direction == "long":
            cond_price = latest["close"] > latest["ema50"]
            cond_slope = latest["ema50"] > latest["ema50_prev"]
            cond_rsi = latest["rsi"] >= rsi_thresh
        else:
            cond_price = latest["close"] < latest["ema50"]
            cond_slope = latest["ema50"] < latest["ema50_prev"]
            cond_rsi = latest["rsi"] <= rsi_thresh

        if not (cond_price and cond_slope and cond_rsi):
            return None

        # EMA alignment advisory: poor alignment → widen SL
        alignment = latest["ema_alignment"]
        recent = df.tail(5)
        if direction == "long":
            if not (recent["golden_cross"].any() or alignment >= 2):
                atr_mult *= 1.3
        else:
            if not (recent["death_cross"].any() or alignment <= 1):
                atr_mult *= 1.3

        # Volume shrinkage filter
        if not pd.isna(latest.get("vol_ma20", float("nan"))):
            recent_vol = df.tail(5)["volume"].mean()
            avg_vol = latest["vol_ma20"]
            if avg_vol > 0 and (recent_vol / avg_vol) < 0.5:
                return None

        # BB width filter: >10% blocks longs (extreme vol only), >6% widens SL
        current_bbw = latest["bb_width"] if not pd.isna(latest["bb_width"]) else 0
        if current_bbw > 10.0 and direction == "long":
            return None  # Extreme volatility — sit out
        elif current_bbw > 6.0 and direction == "long":
            atr_mult *= 1.5  # High vol — widen SL instead of blocking
        elif current_bbw > 6.0 and direction == "short":
            atr_mult *= 1.3

        # Layer 2: Volume confirmation on recent candles
        recent_20 = df.tail(20)
        if direction == "long":
            boom_mask = (recent_20["vol_ratio"] > vol_mult) & (recent_20["close"] > recent_20["open"])
        else:
            boom_mask = (recent_20["vol_ratio"] > vol_mult) & (recent_20["close"] < recent_20["open"])

        boom_bars = recent_20[boom_mask]
        if boom_bars.empty:
            return None

        # Calculate entry/SL/TP
        entry = round(float(boom_bars.iloc[-1]["close"]), 8)
        atr_val = float(latest["atr"])
        if atr_val <= 0:
            return None  # flat market, no edge

        if direction == "long":
            sl = round(entry - atr_mult * atr_val, 8)
            sl_dist = entry - sl
        else:
            sl = round(entry + atr_mult * atr_val, 8)
            sl_dist = sl - entry

        tp1 = round(entry + sl_dist if direction == "long" else entry - sl_dist, 8)
        tp2 = round(entry + 2 * sl_dist if direction == "long" else entry - 2 * sl_dist, 8)
        tp3 = round(entry + 3 * sl_dist if direction == "long" else entry - 3 * sl_dist, 8)

        return TradeSignal(
            pair=pair, direction=direction,
            entry_price=entry, sl_price=sl,
            tp1_price=tp1, tp2_price=tp2, tp3_price=tp3,
            confidence=round(latest["rsi"], 1),
            source="waverider",
        )

    def _check_ema_reaction(self, df: pd.DataFrame, pair: str,
                            direction: str) -> tuple[bool, float]:
        """EMA Rejection/Hold: price touches EMA 20/50 then reverses.

        Long (EMA Hold): low within 0.3% of EMA 20 or 50, closes above it,
            and price > EMA 200 (uptrend context).
        Short (EMA Rejection): high within 0.3% of EMA 20 or 50, closes below
            it, and price < EMA 200 (downtrend context).

        Returns:
            (triggered, confidence) where confidence 40-75 based on touch
            precision. Returns (False, 0) when no pattern detected.
        """
        if len(df) < 200:
            return False, 0.0

        latest = df.iloc[-1]
        close = latest["close"]
        low = latest["low"]
        high = latest["high"]
        ema200 = latest["ema200"]

        if pd.isna(ema200) or pd.isna(latest["ema20"]) or pd.isna(latest["ema50"]):
            return False, 0.0

        touch_threshold = 0.003  # 0.3%
        best_confidence = 0.0

        for ema_col in ("ema20", "ema50"):
            ema_val = latest[ema_col]
            if pd.isna(ema_val) or ema_val <= 0:
                continue

            if direction == "long":
                # Price dips to touch EMA from above, closes above it
                if close <= ema_val or close < ema200:
                    continue
                distance_pct = abs(low - ema_val) / ema_val
                if distance_pct > touch_threshold:
                    continue
                if close <= ema_val:
                    continue
                # Closer touch = higher confidence (40-75 range)
                precision = 1.0 - (distance_pct / touch_threshold)
                confidence = 40.0 + precision * 35.0
                best_confidence = max(best_confidence, confidence)

            else:  # short
                # Price rallies to touch EMA from below, closes below it
                if close > ema200:
                    continue
                distance_pct = abs(high - ema_val) / ema_val
                if distance_pct > touch_threshold:
                    continue
                if close >= ema_val:
                    continue
                precision = 1.0 - (distance_pct / touch_threshold)
                confidence = 40.0 + precision * 35.0
                best_confidence = max(best_confidence, confidence)

        return best_confidence > 0, round(best_confidence, 1)

    def _check_macd_divergence_consecutive(
        self, df: pd.DataFrame, pair: str, direction: str,
    ) -> tuple[bool, float]:
        """Detect 2-3 consecutive candles with MACD histogram divergence.

        Bullish: price makes consecutive lower lows but MACD histogram makes
            higher lows over a 5-10 bar window (reversal signal).
        Bearish: price makes consecutive higher highs but MACD histogram makes
            lower highs over a 5-10 bar window (reversal signal).

        Returns:
            (triggered, confidence) where confidence 40-60 for reversal
            signals. Returns (False, 0) when no pattern detected.
        """
        if len(df) < 30:
            return False, 0.0

        lookback = 10
        tail = df.tail(lookback)
        prices = tail["close"].values
        hist = tail["macd_hist"].values

        if len(prices) < 5 or np.any(np.isnan(hist[-5:])):
            return False, 0.0

        return self._scan_consecutive_divergence(prices, hist, direction)

    @staticmethod
    def _scan_consecutive_divergence(
        prices: np.ndarray, hist: np.ndarray, direction: str,
    ) -> tuple[bool, float]:
        """Scan last bars for 2-3 consecutive divergence candles.

        Isolated as a static method for testability.
        """
        # Use last 5 bars to find consecutive divergence sequences
        recent_prices = prices[-5:]
        recent_hist = hist[-5:]

        consecutive = 0
        max_consecutive = 0

        for i in range(1, len(recent_prices)):
            if direction == "long":
                # Bullish: price lower low, histogram higher low
                price_lower = recent_prices[i] < recent_prices[i - 1]
                hist_higher = recent_hist[i] > recent_hist[i - 1]
                diverging = price_lower and hist_higher
            else:
                # Bearish: price higher high, histogram lower high
                price_higher = recent_prices[i] > recent_prices[i - 1]
                hist_lower = recent_hist[i] < recent_hist[i - 1]
                diverging = price_higher and hist_lower

            if diverging:
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 0

        if max_consecutive < 2:
            return False, 0.0

        # 2 consecutive = 45, 3 consecutive = 55 (reversal = lower confidence)
        confidence = min(60.0, 35.0 + max_consecutive * 10.0)
        return True, round(confidence, 1)

    def _check_bb_squeeze(self, df: pd.DataFrame, pair: str,
                          direction: str) -> bool:
        """BB Squeeze: detect compression followed by expansion."""
        if len(df) < 30:
            return False

        bb_widths = df["bb_width"].dropna().tail(20)
        if len(bb_widths) < 10:
            return False

        # Squeeze = current BB width < 70% of 20-period average
        avg_bbw = bb_widths.mean()
        recent_bbw = bb_widths.iloc[-3:].mean()  # last 3 candles
        current_bbw = bb_widths.iloc[-1]

        is_squeezed = recent_bbw < avg_bbw * 0.7
        is_expanding = current_bbw > recent_bbw

        if not (is_squeezed and is_expanding):
            return False

        # Direction confirmation
        latest = df.iloc[-1]
        if direction == "long" and latest["close"] > latest["bb_mid"]:
            return True
        if direction == "short" and latest["close"] < latest["bb_mid"]:
            return True

        return False

    def _check_macd_divergence(self, df: pd.DataFrame, pair: str,
                               direction: str) -> bool:
        """MACD Divergence via swing-point comparison.

        Bullish: two consecutive price swing-lows where price L2 < L1
                 but MACD histogram L2 > L1  (hidden strength).
        Bearish: two consecutive price swing-highs where price H2 > H1
                 but MACD histogram H2 < H1  (hidden weakness).
        """
        if len(df) < 30:
            return False

        lookback = 30
        prices = df["close"].tail(lookback).reset_index(drop=True)
        macd_vals = df["macd_hist"].tail(lookback).reset_index(drop=True).dropna()
        if len(macd_vals) < 15:
            return False

        price_lows, price_highs = find_swing_points(prices, order=3)
        macd_lows, macd_highs = find_swing_points(macd_vals, order=2)

        if direction == "long":
            # Need at least 2 swing lows in both series
            if len(price_lows) < 2 or len(macd_lows) < 2:
                return False
            p1, p2 = price_lows[-2][1], price_lows[-1][1]
            m1, m2 = macd_lows[-2][1], macd_lows[-1][1]
            return p2 < p1 and m2 > m1
        else:
            if len(price_highs) < 2 or len(macd_highs) < 2:
                return False
            p1, p2 = price_highs[-2][1], price_highs[-1][1]
            m1, m2 = macd_highs[-2][1], macd_highs[-1][1]
            return p2 > p1 and m2 < m1

    def _check_regime_guard(self, regime: RegimeResult,
                            direction: str) -> tuple[bool, float]:
        """Check if regime allows trading in this direction.

        Returns:
            (allowed, atr_multiplier_adjustment)
        """
        r = regime.regime

        # EMA convergence: block all
        if r == MarketRegime.EMA_CONVERGENCE:
            return False, 1.0

        # Ranging: block when ADX very low (<15), reduce when moderate (15-20)
        # ADX < 15 = no directional edge; validated that low-ADX produces poor win rates.
        if r == MarketRegime.RANGING:
            if regime.adx < 15:
                return False, 1.0  # ADX<15 = no trend at all, block everything
            # ADX 15-20: allow shorts only with wider SL + reduced size
            if direction == "long":
                return False, 1.0  # No longs in ranging — gets stopped out
            return True, 1.8  # Shorts only, wider SL, reduced via pos_mult 0.75

        # High volatility + long: allow with small size + wider SL
        # (Spot-only = must go long. Blocking entirely = zero trades.)
        if r == MarketRegime.HIGH_VOLATILITY and direction == "long":
            return True, 1.8

        # Counter-trend: widen SL
        if r == MarketRegime.TRENDING_DOWN and direction == "long":
            if regime.adx > 25:
                return False, 1.0  # Strong downtrend blocks longs
            return True, 1.3

        if r == MarketRegime.TRENDING_UP and direction == "short":
            if regime.adx > 25:
                return False, 1.0  # Strong uptrend blocks shorts
            return True, 1.3

        # High vol short: widen SL
        if r == MarketRegime.HIGH_VOLATILITY and direction == "short":
            return True, 1.5

        return True, 1.0

    def _get_grid_cell(self, pair: str, direction: str,
                       regime: RegimeResult) -> dict | None:
        """Look up regime-grid cell for this (pair, direction, regime).

        Returns cell params dict or None if no validated strategy exists.
        """
        grid_key = f"{pair}_{direction}_{regime.regime.value}"
        return REGIME_GRID.get(grid_key)

    def scan_pair(self, pair: str, df_4h: pd.DataFrame,
                  btc_trend: str = "neutral") -> list[TradeSignal]:
        """Scan a single pair for signals using 36-cell regime grid routing.

        For each (pair, direction, regime), looks up the REGIME_GRID for
        validated strategy params. If no grid cell exists, the combo is
        blocked (no validated strategy for that market condition).

        Args:
            pair: Standard pair name e.g. "BTC/USDT"
            df_4h: 4H OHLCV data
            btc_trend: "up", "down", or "neutral" — used by btc_filter configs
        """
        if df_4h.empty or len(df_4h) < 50:
            return []

        df = self._compute_indicators(df_4h)
        regime = self.regime_detector.detect(df)
        signals = []

        for direction in ["long", "short"]:
            # Look up regime-grid cell
            cell = self._get_grid_cell(pair, direction, regime)

            if cell is None:
                # No validated strategy for this (pair, direction, regime)
                logger.debug(
                    f"{pair} {direction} blocked — no grid cell for "
                    f"regime={regime.regime.value} (ADX={regime.adx:.1f})"
                )
                continue

            # Grid cell found — blacklist override check
            # Grid cells can override blacklist (e.g., BTC long in TREND_EXHAUSTION)
            bl_key = f"{pair}_{direction}"
            if bl_key in BLACKLIST:
                # Only allow if grid cell explicitly exists for this regime
                # (the lookup above already confirmed it exists)
                logger.info(
                    f"{pair} {direction} blacklist overridden by grid cell "
                    f"regime={regime.regime.value} (OOS WR={cell.get('oos_wr', '?')}%)"
                )

            cell_strategies = cell.get("strategies", ["waverider"])
            sources = []

            # Check each strategy specified by the grid cell
            if "waverider" in cell_strategies:
                wr_signal = self._check_waverider_with_params(
                    df, pair, direction, cell, btc_trend,
                )
                if wr_signal:
                    sources.append("waverider")

            if "bb_squeeze" in cell_strategies:
                if self._check_bb_squeeze(df, pair, direction):
                    sources.append("bb_squeeze")

            if "macd_div" in cell_strategies:
                if self._check_macd_divergence(df, pair, direction):
                    sources.append("macd_div")

            if "ema_reaction" in cell_strategies:
                ema_hit, ema_conf = self._check_ema_reaction(df, pair, direction)
                if ema_hit:
                    sources.append("ema_reaction")

            if "macd_divergence_consecutive" in cell_strategies:
                mdc_hit, mdc_conf = self._check_macd_divergence_consecutive(
                    df, pair, direction,
                )
                if mdc_hit:
                    sources.append("macd_divergence_consecutive")

            if not sources:
                continue

            # Determine confidence level
            n = len(sources)
            if n >= 2:
                confidence_level = 3  # Multi-strategy confirmation
            elif "waverider" in sources:
                confidence_level = 2  # Primary strategy
            else:
                confidence_level = 1  # Secondary strategy only

            # Build signal
            if "waverider" in sources and wr_signal:
                signal = wr_signal
            else:
                # Generate signal from latest price (BB/MACD only)
                latest = df.iloc[-1]
                entry = round(float(latest["close"]), 8)
                atr_val = float(latest["atr"])
                mult = cell.get("atr_sl_multiplier", 1.5)

                if direction == "long":
                    sl = round(entry - mult * atr_val, 8)
                    sl_dist = entry - sl
                    tp1 = round(entry + sl_dist, 8)
                    tp2 = round(entry + 2 * sl_dist, 8)
                    tp3 = round(entry + 3 * sl_dist, 8)
                else:
                    sl = round(entry + mult * atr_val, 8)
                    sl_dist = sl - entry
                    tp1 = round(entry - sl_dist, 8)
                    tp2 = round(entry - 2 * sl_dist, 8)
                    tp3 = round(entry - 3 * sl_dist, 8)

                signal = TradeSignal(
                    pair=pair, direction=direction,
                    entry_price=entry, sl_price=sl,
                    tp1_price=tp1, tp2_price=tp2, tp3_price=tp3,
                    confidence=round(latest["rsi"], 1),
                    source="+".join(sources),
                )

            # Build indicator snapshot for validation artifacts
            latest = df.iloc[-1]
            indicator_snapshot = {
                "adx": round(float(regime.adx), 1),
                "bbWidth": round(float(latest.get("bb_width", 0)), 6),
                "rsi": round(float(latest.get("rsi", 0)), 1),
                "ema8": round(float(latest.get("ema8", 0)), 2),
                "ema21": round(float(latest.get("ema21", 0)), 2),
                "ema55": round(float(latest.get("ema55", 0)), 2),
                "atr": round(float(latest.get("atr", 0)), 6),
                "volume_ratio": round(float(latest.get("volume", 0) / df["volume"].rolling(20).mean().iloc[-1]) if df["volume"].rolling(20).mean().iloc[-1] > 0 else 0, 2),
            }

            cell_source = cell.get("source", "?")
            cell_oos_wr = float(cell.get("oos_wr", 0) or 0)

            # Apply regime position scaling (immutable — create new signal)
            signal = replace(
                signal,
                position_scale=regime.position_size_mult,
                source="+".join(sources),
                regime=regime.regime.value,
                grid_cell=cell_source,
                oos_wr=cell_oos_wr,
                indicators=indicator_snapshot,
            )

            logger.info(
                f"Signal: {pair} {direction.upper()} L{confidence_level} "
                f"| sources={sources} | entry={signal.entry_price} "
                f"| SL={signal.sl_price} | regime={regime.regime.value} "
                f"| grid={cell_source} OOS={cell.get('oos_wr', '?')}%"
            )
            signals.append(signal)

        return signals

    def _detect_btc_trend(self, data_adapter) -> str:
        """Detect BTC trend for btc_filter configs.

        Returns:
            "up" if BTC EMA50 > EMA200, "down" if EMA50 < EMA200, else "neutral"
        """
        try:
            df = data_adapter.get_ohlc("BTC/USDT", "4h")
            if df.empty or len(df) < 200:
                return "neutral"
            ema50 = ema(df["close"], 50).iloc[-1]
            ema200 = ema(df["close"], 200).iloc[-1]
            if pd.isna(ema50) or pd.isna(ema200):
                return "neutral"
            ema50_prev = ema(df["close"], 50).iloc[-2]
            slope_up = ema50 > ema50_prev
            if ema50 > ema200 and slope_up:
                return "up"
            elif ema50 < ema200 and not slope_up:
                return "down"
            return "neutral"
        except Exception as e:
            logger.warning(f"BTC trend detection failed: {e}")
            return "neutral"

    def scan_all(self, data_adapter) -> list[TradeSignal]:
        """Scan all active pairs using the data adapter.

        Args:
            data_adapter: KrakenDataAdapter instance

        Returns:
            List of TradeSignal sorted by confidence (high to low)
        """
        all_signals = []

        # Pre-fetch BTC trend for btc_filter configs
        btc_trend = self._detect_btc_trend(data_adapter)
        logger.info(f"Scanning {len(ACTIVE_PAIRS)} pairs... (BTC trend: {btc_trend})")

        for pair in ACTIVE_PAIRS:
            try:
                df_4h = data_adapter.get_ohlc(pair, "4h")
                signals = self.scan_pair(pair, df_4h, btc_trend)
                all_signals.extend(signals)
            except Exception as e:
                logger.error(f"Scan failed for {pair}: {e}")

        # Sort by number of sources (multi-strategy first)
        all_signals.sort(key=lambda s: len(s.source.split("+")), reverse=True)

        logger.info(f"Scan complete: {len(all_signals)} signals found")
        return all_signals
