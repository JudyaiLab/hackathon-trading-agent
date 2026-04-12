"""
AI prompt templates and market context builders for OpusAnalyst.

Extracted from opus_analyst.py for code quality (file size < 800 lines).
No logic changes — pure extraction.
"""
import json
from datetime import datetime, timezone

import pandas as pd


# ── Ming's Strategy Knowledge (System Prompt) ────────────────
ANALYST_SYSTEM_PROMPT = """You are a top-tier cryptocurrency trading analyst with the following expertise:

## Core Trading Framework (derived from 1006 trade analyses)

### Multi-Timeframe EMA System (Core)
- Four EMAs: 20 (short-term momentum) / 50 (mid-term trend) / 100 (medium-long direction) / 200 (long-term trend)
- **Bullish alignment**: EMA20 > EMA50 > EMA100 > EMA200 -> strong long bias
- **Bearish alignment**: EMA20 < EMA50 < EMA100 < EMA200 -> strong short bias
- **EMA convergence**: All four EMAs within 2% distance -> pause trading, wait for breakout
- **Slope matters more than position**: EMA slope determines trend strength, position determines direction

### Entry Logic
- **Trend-following entry**: Price pulls back to EMA50 while EMA20 stays above EMA50 -> go long
- **Breakout entry**: Price breaks above EMA200 + volume expansion -> trend reversal confirmed
- **Momentum confirmation**: MACD histogram direction aligned with trend + RSI not overbought/oversold
- **EMA resistance/support**:
  - Price touches EMA20/50 then closes on the other side = resistance signal (e.g., bounce hits H4 EMA50, closes bearish -> short)
  - Price touches EMA20/50 and holds = support confirmed (e.g., pullback to H4 EMA20, closes bullish -> continue long)

### Consecutive MACD Divergence (Momentum Exhaustion Signal)
- H4 shows 2-3 consecutive MACD divergences = severe momentum exhaustion, strong reversal signal
- Single divergence = warning; 2 consecutive = high alert; 3 = very likely reversal

### Risk Management Principles
- Stop-loss is mandatory, calculated dynamically with ATR
- **Break-Even Logic**:
  - After TP1 hit -> move SL to entry price (break-even), lock in zero risk
  - After TP2 hit -> move SL to TP1, lock in partial profit
- **Scale out at resistance levels**:
  - Reduce 5-20% at each EMA resistance / supply zone / previous high/low
- Increase position size on multi-strategy confluence, decrease on single strategy

### No-Trade Conditions (must return HOLD + position_adjustment=0)
- **EMA convergence**: All four EMAs within 2% + ADX < 20 -> no direction, no new positions
- **Pre-major data/options expiry**: Avoid new positions 1-2 days before monthly options expiry
- **Low volume + range-bound**: vol_ratio < 0.5 and price near BB middle band

### Market State Assessment
- ADX > 25 = clear trend, use trend strategies (WaveRider)
- ADX < 20 = ranging/consolidation, use mean-reversion (BB strategy)
- BB compression -> BB expansion = breakout signal
- MACD divergence = reversal warning

### Analysis Standards
1. **Trend alignment**: Are multiple timeframes (4H + daily + weekly) aligned?
2. **Momentum confirmation**: Do MACD/RSI support current direction? Watch for consecutive divergence.
3. **Volume validation**: Does volume support the price movement?
4. **Risk-reward**: At least 1.5:1, ideally 2:1 or higher
5. **Pattern recognition**: Any Head & Shoulders, Double Top/Bottom, Flag/Pennant?
6. **EMA interaction**: How does price interact with EMA20/50 resistance/support?
7. **No-trade check**: Does it match any no-trade conditions? If yes, must return HOLD.

## Output Format

You must return JSON (no markdown code blocks), in this format:
{
    "verdict": "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL",
    "ai_confidence": 0-100,
    "reasoning": "One-sentence core judgment",
    "details": {
        "trend_alignment": "Multi-timeframe trend analysis",
        "momentum": "Momentum indicator analysis",
        "volume": "Volume analysis",
        "risk_reward": "Risk-reward assessment",
        "pattern": "Pattern recognition result"
    },
    "position_adjustment": 0.0-1.5,
    "sl_suggestion": "Stop-loss suggestion (optional)",
    "warnings": ["Risk warning list"]
}

position_adjustment guide:
- 0.0 = do not open position (AI veto)
- 0.5 = half position size
- 1.0 = maintain original position size
- 1.5 = increase position size (multi-strategy confluence + high AI confidence)
"""


POSITION_REVIEW_PROMPT = """You are a position management AI. Review open positions and decide on actions based on current market conditions.

## Decision Framework
1. **Market structure change**: Has the EMA alignment changed since entry? Has the trend reversed?
2. **Momentum exhaustion**: Has the MACD histogram flipped? Has RSI entered overbought/oversold?
3. **Volume decline**: Has volume dropped sharply? (trend losing momentum)
4. **Key support/resistance**: Has price reached an important EMA or previous high/low?
5. **Time in position**: Over 48H without hitting TP1 -> consider reducing
6. **Realized risk-reward**: Unrealized profit exceeds 2x ATR -> tighten stop-loss

## Output Format (JSON, no markdown code blocks)
{
    "action": "HOLD" | "TIGHTEN_SL" | "TAKE_PROFIT" | "EXIT_NOW" | "REDUCE",
    "urgency": 0-100,
    "new_sl": null or suggested new stop-loss price,
    "reduce_pct": 25-75 (only for REDUCE, suggested close percentage),
    "reasoning": "One-sentence core judgment",
    "market_changed": true/false
}

action guide:
- HOLD = maintain position, market structure unchanged
- TIGHTEN_SL = tighten stop-loss to suggested price (protect profit or reduce risk)
- REDUCE = partial close (EMA convergence, momentum exhaustion, unclear direction but no reversal), fill reduce_pct
- TAKE_PROFIT = close entire position for profit (market structure has changed)
- EXIT_NOW = emergency exit (confirmed trend reversal, major bearish event)

Important: When you judge "should reduce and observe", "unclear direction", or "EMA convergence", use REDUCE not HOLD. HOLD means absolutely no action needed.
"""


def build_market_context(signal, df: pd.DataFrame, regime) -> str:
    """Build structured market context string for AI analysis."""
    latest = df.iloc[-1]

    # EMA values
    ema_data = {}
    for period in [20, 50, 100, 200]:
        col = f"ema{period}"
        if col in df.columns and not pd.isna(latest.get(col, float("nan"))):
            ema_data[f"EMA{period}"] = round(float(latest[col]), 2)

    # Determine EMA alignment
    ema_vals = list(ema_data.values())
    if len(ema_vals) >= 4:
        if ema_vals[0] > ema_vals[1] > ema_vals[2] > ema_vals[3]:
            alignment = "Perfect bullish alignment (EMA20>50>100>200)"
        elif ema_vals[0] < ema_vals[1] < ema_vals[2] < ema_vals[3]:
            alignment = "Perfect bearish alignment (EMA20<50<100<200)"
        else:
            alignment = "Mixed alignment (no clear trend)"
    else:
        alignment = "Insufficient data"

    # RSI
    rsi_val = round(float(latest.get("rsi", 50)), 1) if not pd.isna(latest.get("rsi", float("nan"))) else "N/A"

    # MACD
    macd_hist = round(float(latest.get("macd_hist", 0)), 4) if not pd.isna(latest.get("macd_hist", float("nan"))) else "N/A"
    macd_line = round(float(latest.get("macd_line", 0)), 4) if not pd.isna(latest.get("macd_line", float("nan"))) else "N/A"

    # Volume
    vol_ratio = round(float(latest.get("vol_ratio", 1)), 2) if not pd.isna(latest.get("vol_ratio", float("nan"))) else "N/A"

    # BB
    bb_width = round(float(latest.get("bb_width", 0)), 2) if not pd.isna(latest.get("bb_width", float("nan"))) else "N/A"
    bb_upper = round(float(latest.get("bb_upper", 0)), 2) if not pd.isna(latest.get("bb_upper", float("nan"))) else "N/A"
    bb_lower = round(float(latest.get("bb_lower", 0)), 2) if not pd.isna(latest.get("bb_lower", float("nan"))) else "N/A"

    # ATR
    atr_val = round(float(latest.get("atr", 0)), 2) if not pd.isna(latest.get("atr", float("nan"))) else "N/A"

    # Price action (last 5 candles)
    recent_5 = df.tail(5)
    candles = []
    for _, row in recent_5.iterrows():
        candle_type = "Bullish" if row["close"] > row["open"] else "Bearish"
        body_pct = abs(row["close"] - row["open"]) / row["open"] * 100
        candles.append(f"{candle_type} {body_pct:.1f}%")

    # ADX
    adx_val = round(float(regime.adx), 1) if regime else "N/A"

    # Risk/Reward
    sl_dist = abs(signal.entry_price - signal.sl_price)
    tp1_dist = abs(signal.tp1_price - signal.entry_price)
    rr = round(tp1_dist / sl_dist, 2) if sl_dist > 0 else 0

    context = f"""## Trade Signal Analysis Request

**Pair**: {signal.pair}
**Direction**: {signal.direction.upper()}
**Strategy Source**: {signal.source}
**Signal Time**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

### Price Data
- Current Price: ${signal.entry_price:,.2f}
- Stop Loss: ${signal.sl_price:,.2f} (distance {sl_dist/signal.entry_price*100:.2f}%)
- TP1: ${signal.tp1_price:,.2f} | TP2: ${signal.tp2_price:,.2f} | TP3: ${signal.tp3_price:,.2f}
- Risk-Reward (TP1): {rr}:1

### Multi-Timeframe EMA Analysis (4H)
{json.dumps(ema_data, indent=2)}
- **Alignment**: {alignment}

### Technical Indicators
- RSI(14): {rsi_val}
- MACD Line: {macd_line} | MACD Histogram: {macd_hist}
- ATR(14): {atr_val}
- Volume Ratio (current/MA20): {vol_ratio}x
- BB Width: {bb_width}% | Upper: {bb_upper} | Lower: {bb_lower}

### Market Regime
- State: {regime.regime.value}
- Confidence: {regime.confidence}%
- ADX: {adx_val}
- Trend Strength: {regime.trend_strength}
- EMA Spread: {regime.ema_spread:.2f}%
- Position Multiplier: {regime.position_size_mult}x

### Recent 5 Candles
{' -> '.join(candles)}

### Signal Source
- Strategy: {signal.source}
- Rule Confidence: {signal.confidence}
- Position Scale: {signal.position_scale}

Please evaluate the quality of this {signal.direction.upper()} signal using the multi-timeframe EMA analysis framework.
"""
    return context


def build_position_context(position, df: pd.DataFrame, regime) -> str:
    """Build context for position review."""
    latest = df.iloc[-1]
    current_price = float(latest["close"])

    # PnL calculation
    if position.direction == "long":
        unrealized_pnl = (current_price - position.entry_price) * position.volume
        pnl_pct = (current_price - position.entry_price) / position.entry_price * 100
    else:
        unrealized_pnl = (position.entry_price - current_price) * position.volume
        pnl_pct = (position.entry_price - current_price) / position.entry_price * 100

    # Time in position
    now = datetime.now(timezone.utc)
    hours_held = (now - position.opened_at).total_seconds() / 3600

    # EMA data
    ema_data = {}
    for period in [20, 50, 100, 200]:
        col = f"ema{period}"
        if col in df.columns and not pd.isna(latest.get(col, float("nan"))):
            ema_data[f"EMA{period}"] = round(float(latest[col]), 2)

    # RSI / MACD
    rsi_val = round(float(latest.get("rsi", 50)), 1) if not pd.isna(latest.get("rsi", float("nan"))) else "N/A"
    macd_hist = round(float(latest.get("macd_hist", 0)), 4) if not pd.isna(latest.get("macd_hist", float("nan"))) else "N/A"
    vol_ratio = round(float(latest.get("vol_ratio", 1)), 2) if not pd.isna(latest.get("vol_ratio", float("nan"))) else "N/A"
    atr_val = round(float(latest.get("atr", 0)), 4) if not pd.isna(latest.get("atr", float("nan"))) else "N/A"

    # Distance to SL/TP
    sl_dist_pct = abs(current_price - position.sl_price) / current_price * 100
    tp1_dist_pct = abs(position.tp1_price - current_price) / current_price * 100

    return f"""## Position Review

**Pair**: {position.pair}
**Direction**: {position.direction.upper()}
**Strategy Source**: {position.source}

### Position Status
- Entry Price: ${position.entry_price:,.2f}
- Current Price: ${current_price:,.2f}
- Unrealized PnL: ${unrealized_pnl:+,.2f} ({pnl_pct:+.2f}%)
- Time Held: {hours_held:.1f} hours
- Current SL: ${position.sl_price:,.2f} (distance {sl_dist_pct:.2f}%)
- TP1: ${position.tp1_price:,.2f} (distance {tp1_dist_pct:.2f}%)
- TP1 Hit: {'Yes' if position.tp1_hit else 'No'}

### EMA Alignment (4H)
{json.dumps(ema_data, indent=2)}

### Technical Indicators
- RSI(14): {rsi_val}
- MACD Histogram: {macd_hist}
- Volume Ratio: {vol_ratio}x
- ATR(14): {atr_val}

### Market Regime
- State: {regime.regime.value}
- ADX: {round(float(regime.adx), 1)}
- Trend Strength: {regime.trend_strength}

Please evaluate how this {position.direction.upper()} position should be managed.
"""
