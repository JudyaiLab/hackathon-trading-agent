"""
Technical Indicators & Regime Detection

Pure mathematical indicator functions (EMA, RSI, BB, MACD, ATR, ADX)
plus regime classification built on those indicators. No trading logic.
"""
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index."""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_val = atr(df, period)
    plus_di = 100 * ema(plus_dm, period) / atr_val
    minus_di = 100 * ema(minus_dm, period) / atr_val
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return ema(dx, period)


def find_swing_points(
    series: pd.Series, order: int = 3,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Find swing highs and lows using local extrema detection.

    A swing low at index *i* means series[i] is the minimum in
    the window [i-order, i+order].  Same logic inverted for highs.

    Args:
        series: Price or indicator series (NaN-dropped recommended)
        order: Number of bars on each side to compare

    Returns:
        (swing_lows, swing_highs) — each a list of (position, value)
    """
    vals = series.values
    n = len(vals)
    lows: list[tuple[int, float]] = []
    highs: list[tuple[int, float]] = []
    for i in range(order, n - order):
        window = vals[i - order : i + order + 1]
        if vals[i] == window.min():
            lows.append((i, float(vals[i])))
        if vals[i] == window.max():
            highs.append((i, float(vals[i])))
    return lows, highs


# ── Market Regime ────────────────────────────────────────────

class MarketRegime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    EMA_CONVERGENCE = "ema_convergence"
    TREND_EXHAUSTION = "trend_exhaustion"
    BREAKOUT_FORMING = "breakout_forming"
    UNKNOWN = "unknown"


@dataclass
class RegimeResult:
    regime: MarketRegime
    confidence: float       # 0-100
    adx: float
    bb_width: float         # BB width as % of price
    ema_spread: float       # Max EMA distance as % of price
    trend_strength: float   # 0-100
    position_size_mult: float = 1.0


class RegimeDetector:
    """Simplified regime detection using ADX + BB width + EMA spread."""

    def detect(self, df: pd.DataFrame) -> RegimeResult:
        if len(df) < 50:
            return RegimeResult(
                regime=MarketRegime.UNKNOWN, confidence=0,
                adx=0, bb_width=0, ema_spread=0, trend_strength=0,
            )

        df = df.copy()
        df["adx"] = adx(df)
        df["ema20"] = ema(df["close"], 20)
        df["ema50"] = ema(df["close"], 50)
        df["ema100"] = ema(df["close"], 100)
        df["ema200"] = ema(df["close"], 200)
        df["rsi14"] = rsi(df["close"], 14)

        latest = df.iloc[-1]
        price = latest["close"]
        adx_val = latest["adx"] if not pd.isna(latest["adx"]) else 15.0

        # BB width
        bb_sma = df["close"].rolling(20).mean()
        bb_std = df["close"].rolling(20).std()
        bb_width_pct = ((4 * bb_std) / bb_sma) * 100
        bb_w = bb_width_pct.iloc[-1] if not pd.isna(bb_width_pct.iloc[-1]) else 3.0

        # BB width percentile (for squeeze detection)
        bb_width_series = bb_width_pct.dropna().tail(100)
        bb_percentile = 50.0
        if len(bb_width_series) >= 20:
            bb_percentile = (bb_width_series < bb_w).sum() / len(bb_width_series) * 100

        # EMA spread (max distance between EMAs as % of price)
        ema_vals = [latest.get(f"ema{p}", price) for p in [20, 50, 100, 200]]
        ema_vals = [v for v in ema_vals if not pd.isna(v)]
        ema_spread_pct = ((max(ema_vals) - min(ema_vals)) / price * 100) if ema_vals else 0

        # EMA slope direction
        ema50_slope = latest["ema50"] - df["ema50"].iloc[-2] if len(df) > 1 else 0

        # ADX slope (declining = trend exhaustion)
        adx_series = df["adx"].dropna().tail(10)
        adx_declining = False
        adx_peak = 0.0
        if len(adx_series) >= 5:
            adx_peak = adx_series.max()
            adx_declining = (
                adx_peak > 25
                and adx_val < adx_peak * 0.85  # ADX dropped 15%+ from peak
                and adx_series.iloc[-1] < adx_series.iloc[-3]  # still falling
            )

        # RSI divergence check (price trending but RSI diverging)
        rsi_val = latest["rsi14"] if not pd.isna(latest.get("rsi14")) else 50.0
        rsi_divergence = False
        if len(df) >= 10:
            price_5 = df["close"].tail(5)
            rsi_5 = df["rsi14"].tail(5).dropna()
            if len(rsi_5) >= 5:
                price_down = price_5.iloc[-1] < price_5.iloc[0]
                rsi_up = rsi_5.iloc[-1] > rsi_5.iloc[0]
                price_up = price_5.iloc[-1] > price_5.iloc[0]
                rsi_down = rsi_5.iloc[-1] < rsi_5.iloc[0]
                rsi_divergence = (price_down and rsi_up) or (price_up and rsi_down)

        # Trend strength (composite)
        trend_strength = min(100, adx_val * 2)

        # Classify regime (order matters — most specific first)
        # 1. EMA convergence (flat market, no edge)
        if ema_spread_pct < 0.5 and adx_val < 20:
            regime = MarketRegime.EMA_CONVERGENCE
            pos_mult = 0.3
            confidence = 70
        # 2. Trend exhaustion (ADX was high but declining + RSI divergence)
        elif adx_declining and rsi_divergence:
            regime = MarketRegime.TREND_EXHAUSTION
            pos_mult = 0.7
            confidence = 75
        # 3. Breakout forming (BB squeeze — compression before expansion)
        elif bb_percentile < 30 and adx_val < 20:
            regime = MarketRegime.BREAKOUT_FORMING
            pos_mult = 0.8
            confidence = 70
        # 4. High volatility (BB very wide)
        elif bb_w > 8.0:
            regime = MarketRegime.HIGH_VOLATILITY
            pos_mult = 0.5
            confidence = 65
        # 5. Ranging (low ADX, no clear trend)
        elif adx_val < 20:
            regime = MarketRegime.RANGING
            pos_mult = 0.75
            confidence = 60
        # 6-7. Trending (ADX >= 20 with direction)
        elif ema50_slope > 0:
            regime = MarketRegime.TRENDING_UP
            pos_mult = 1.0
            confidence = min(90, 50 + adx_val)
        else:
            regime = MarketRegime.TRENDING_DOWN
            pos_mult = 1.0
            confidence = min(90, 50 + adx_val)

        return RegimeResult(
            regime=regime,
            confidence=confidence,
            adx=adx_val,
            bb_width=bb_w,
            ema_spread=ema_spread_pct,
            trend_strength=trend_strength,
            position_size_mult=pos_mult,
        )
