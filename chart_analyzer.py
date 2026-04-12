"""
ChartAnalyzer — Generate candlestick charts and send to Opus for visual analysis

Renders professional-grade 4H candlestick charts with EMA overlays,
then sends to Opus vision for pattern recognition (Head & Shoulders,
Double Top/Bottom, Flags, Wedges, Channels, etc.)
"""
import base64
import io
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def render_chart_base64(df: pd.DataFrame, pair: str, direction: str = "") -> str | None:
    """Render a 4H candlestick chart with EMA overlays as base64 PNG.

    Args:
        df: OHLCV DataFrame with columns: open, high, low, close, volume
            and optionally: ema20, ema50, ema100, ema200
        pair: Pair name for title (e.g. "BTC/USDT")
        direction: Signal direction for annotation ("long" or "short")

    Returns:
        Base64-encoded PNG string, or None on failure
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.patches import FancyArrowPatch
    except ImportError:
        logger.warning("matplotlib not available, skipping chart render")
        return None

    # Use last 100 candles for chart (400 hours ≈ 16 days of 4H data)
    chart_df = df.tail(100).copy()
    if len(chart_df) < 20:
        logger.warning(f"Not enough data to render chart for {pair}")
        return None

    # Create figure
    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1,
        figsize=(14, 8),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=True,
    )
    fig.patch.set_facecolor("#1a1a2e")

    # X-axis: use integer index
    x = np.arange(len(chart_df))

    # ── Candlestick rendering ────────────────────────────────
    opens = chart_df["open"].values
    highs = chart_df["high"].values
    lows = chart_df["low"].values
    closes = chart_df["close"].values

    colors_body = ["#26a69a" if c >= o else "#ef5350" for o, c in zip(opens, closes)]
    colors_wick = ["#26a69a" if c >= o else "#ef5350" for o, c in zip(opens, closes)]

    # Wicks
    for i in range(len(x)):
        ax_price.plot([x[i], x[i]], [lows[i], highs[i]],
                      color=colors_wick[i], linewidth=0.8)

    # Bodies
    body_width = 0.6
    for i in range(len(x)):
        bottom = min(opens[i], closes[i])
        height = abs(closes[i] - opens[i])
        if height < (highs[i] - lows[i]) * 0.01:
            height = (highs[i] - lows[i]) * 0.01  # Minimum body height for doji
        ax_price.bar(x[i], height, bottom=bottom, width=body_width,
                     color=colors_body[i], edgecolor=colors_body[i], linewidth=0.5)

    # ── EMA overlays ─────────────────────────────────────────
    ema_configs = [
        ("ema20", "#ffeb3b", 1.2, "EMA 20"),
        ("ema50", "#2196f3", 1.5, "EMA 50"),
        ("ema100", "#ff9800", 1.2, "EMA 100"),
        ("ema200", "#e91e63", 1.8, "EMA 200"),
    ]

    for col, color, width, label in ema_configs:
        if col in chart_df.columns:
            vals = chart_df[col].values
            mask = ~np.isnan(vals)
            if mask.any():
                ax_price.plot(x[mask], vals[mask], color=color,
                              linewidth=width, label=label, alpha=0.85)

    # ── Bollinger Bands (shaded) ─────────────────────────────
    if "bb_upper" in chart_df.columns and "bb_lower" in chart_df.columns:
        bb_up = chart_df["bb_upper"].values
        bb_lo = chart_df["bb_lower"].values
        mask = ~(np.isnan(bb_up) | np.isnan(bb_lo))
        if mask.any():
            ax_price.fill_between(x[mask], bb_lo[mask], bb_up[mask],
                                  alpha=0.08, color="#9e9e9e")

    # ── Volume bars ──────────────────────────────────────────
    volumes = chart_df["volume"].values
    vol_colors = ["#26a69a" if c >= o else "#ef5350" for o, c in zip(opens, closes)]
    ax_vol.bar(x, volumes, width=body_width, color=vol_colors, alpha=0.7)

    # Volume MA20
    if "vol_ma20" in chart_df.columns:
        vol_ma = chart_df["vol_ma20"].values
        mask = ~np.isnan(vol_ma)
        if mask.any():
            ax_vol.plot(x[mask], vol_ma[mask], color="#ffeb3b",
                        linewidth=1, alpha=0.7, label="Vol MA20")

    # ── Signal arrow annotation ──────────────────────────────
    if direction:
        last_x = x[-1]
        last_close = closes[-1]
        arrow_color = "#26a69a" if direction == "long" else "#ef5350"
        arrow_text = "▲ LONG" if direction == "long" else "▼ SHORT"
        y_offset = (highs.max() - lows.min()) * 0.05
        text_y = last_close + y_offset if direction == "long" else last_close - y_offset
        ax_price.annotate(
            arrow_text,
            xy=(last_x, last_close),
            xytext=(last_x - 3, text_y),
            fontsize=11,
            fontweight="bold",
            color=arrow_color,
            arrowprops=dict(arrowstyle="->", color=arrow_color, lw=2),
        )

    # ── Styling ──────────────────────────────────────────────
    for ax in [ax_price, ax_vol]:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="#9e9e9e")
        ax.grid(True, alpha=0.15, color="#9e9e9e")
        for spine in ax.spines.values():
            spine.set_color("#9e9e9e")
            spine.set_linewidth(0.5)

    ax_price.set_title(
        f"{pair} — 4H Chart | Opus AI Analysis",
        color="white", fontsize=14, fontweight="bold", pad=10,
    )
    ax_price.legend(loc="upper left", fontsize=8,
                    facecolor="#16213e", edgecolor="#9e9e9e",
                    labelcolor="white")
    ax_vol.set_ylabel("Volume", color="#9e9e9e", fontsize=10)
    ax_price.set_ylabel("Price (USD)", color="#9e9e9e", fontsize=10)

    # X-axis labels (show every 10th candle)
    if "timestamp" in chart_df.columns:
        tick_indices = list(range(0, len(x), 10))
        tick_labels = [chart_df.iloc[i]["timestamp"].strftime("%m/%d") if hasattr(chart_df.iloc[i].get("timestamp", ""), "strftime") else str(i) for i in tick_indices]
    else:
        tick_indices = list(range(0, len(x), 10))
        tick_labels = [str(i) for i in tick_indices]

    ax_vol.set_xticks(tick_indices)
    ax_vol.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)

    plt.tight_layout()

    # ── Export to base64 ─────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)

    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()

    logger.info(f"Chart rendered for {pair}: {len(b64) // 1024}KB")
    return b64


def render_charts_batch(df_cache: dict, signals: list) -> dict:
    """Render charts for all signal pairs.

    Args:
        df_cache: Dict mapping pair -> computed indicator DataFrame
        signals: List of TradeSignal

    Returns:
        Dict mapping pair -> base64 chart image
    """
    chart_cache = {}
    seen_pairs = set()

    for signal in signals:
        pair = signal.pair
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        df = df_cache.get(pair)
        if df is None or len(df) < 20:
            continue

        chart_b64 = render_chart_base64(df, pair, signal.direction)
        if chart_b64:
            chart_cache[pair] = chart_b64

    logger.info(f"Charts rendered: {len(chart_cache)}/{len(seen_pairs)} pairs")
    return chart_cache
