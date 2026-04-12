"""
Agent signal processing — AI review, validation artifact writing.

Extracted from agent.py to keep main agent under 800 lines.
All functions are standalone helpers that operate on the agent instance (passed as `agent`).
"""
import logging
from dataclasses import replace

from config import AI_CHART_ENABLED, AI_MIN_ENSEMBLE_SCORE
from strategies import TradeSignal
from chart_analyzer import render_charts_batch
from validation_writer import (
    write_trade_intent, update_trade_intent_outcome,
    write_risk_check, write_strategy_checkpoint,
)

logger = logging.getLogger(__name__)


def ai_review_signals(agent, signals: list[TradeSignal]) -> list[TradeSignal]:
    """Run Opus AI analysis on signals and return AI-enhanced signals.

    Signals that fail the AI ensemble threshold are filtered out.
    Remaining signals get adjusted position_scale based on AI confidence.
    """
    if not agent.ai_enabled or not agent.opus_analyst or not signals:
        return signals

    logger.info(f"Running Opus AI analysis on {len(signals)} signals...")

    # Build data caches for AI analysis
    df_cache = {}
    regime_cache = {}
    for signal in signals:
        if signal.pair not in df_cache:
            try:
                df_4h = agent.data.get_ohlc(signal.pair, "4h")
                if not df_4h.empty and len(df_4h) >= 50:
                    df_computed = agent.strategy._compute_indicators(df_4h)
                    df_cache[signal.pair] = df_computed
                    regime_cache[signal.pair] = agent.strategy.regime_detector.detect(df_computed)
            except Exception as e:
                logger.warning(f"Failed to get data for AI analysis of {signal.pair}: {e}")

    # Render charts for vision analysis (if enabled)
    chart_cache = None
    if AI_CHART_ENABLED:
        try:
            chart_cache = render_charts_batch(df_cache, signals)
        except Exception as e:
            logger.warning(f"Chart rendering failed: {e}")

    # Run AI review with timeout protection
    import signal as _signal

    def _ai_timeout_handler(signum, frame):
        raise TimeoutError("AI review exceeded 90s timeout")

    old_handler = _signal.getsignal(_signal.SIGALRM)
    try:
        _signal.signal(_signal.SIGALRM, _ai_timeout_handler)
        _signal.alarm(90)  # 90s hard timeout for AI
        results = agent.opus_analyst.review_signals(
            signals, df_cache, regime_cache, chart_cache
        )
    except (Exception, TimeoutError) as e:
        logger.error(f"AI review failed ({type(e).__name__}): {e} — using rule-based signals")
        return signals  # Return original signals on AI failure
    finally:
        _signal.alarm(0)
        _signal.signal(_signal.SIGALRM, old_handler)

    # Apply AI results to signals
    enhanced_signals = []
    for signal, ai_analysis, ensemble in results:
        # Update signal with AI data
        enhanced = replace(
            signal,
            ai_verdict=ai_analysis.verdict,
            ai_confidence=ai_analysis.ai_confidence,
            ai_reasoning=ai_analysis.reasoning,
            ensemble_score=ensemble.ensemble_score,
            position_scale=ensemble.final_scale,
        )

        if ensemble.should_trade:
            enhanced_signals.append(enhanced)
            logger.info(
                f"AI PASS: {signal.pair} {signal.direction.upper()} "
                f"| {ai_analysis.verdict} ({ai_analysis.ai_confidence}) "
                f"| Ensemble={ensemble.ensemble_score:.0f} | Scale={ensemble.final_scale:.2f}"
            )
        else:
            logger.info(
                f"AI REJECT: {signal.pair} {signal.direction.upper()} "
                f"| {ai_analysis.verdict} ({ai_analysis.ai_confidence}) "
                f"| Ensemble={ensemble.ensemble_score:.0f} "
                f"| {ai_analysis.reasoning}"
            )

    logger.info(
        f"AI Review: {len(enhanced_signals)}/{len(signals)} signals passed "
        f"(${agent.opus_analyst.scan_cost:.4f} total cost)"
    )
    return enhanced_signals


def write_validation_artifacts(
    agent, signal: TradeSignal, portfolio_value: float, pos_value: float, allowed: bool,
) -> None:
    """Write trade intent + risk check + strategy checkpoint for a live trade."""
    try:
        regime = getattr(signal, 'regime', '') or ''
        grid_cell = getattr(signal, 'grid_cell', '') or ''
        oos_wr = getattr(signal, 'oos_wr', 0.0) or 0.0
        indicators = getattr(signal, 'indicators', {}) or {}

        # Build detailed reasoning from signal data
        ai_reasoning = getattr(signal, 'ai_reasoning', '') or ''
        ind = indicators
        reasoning_parts = []
        if ind.get('ema8') and ind.get('ema21') and ind.get('ema55'):
            if ind['ema8'] > ind['ema21'] > ind['ema55']:
                reasoning_parts.append(f"EMA bullish stack ({ind['ema8']}>{ind['ema21']}>{ind['ema55']})")
            elif ind['ema8'] < ind['ema21'] < ind['ema55']:
                reasoning_parts.append(f"EMA bearish stack ({ind['ema8']}<{ind['ema21']}<{ind['ema55']})")
            else:
                reasoning_parts.append(f"EMA mixed ({ind['ema8']}/{ind['ema21']}/{ind['ema55']})")
        if ind.get('rsi'):
            reasoning_parts.append(f"RSI={ind['rsi']}")
        if ind.get('adx'):
            reasoning_parts.append(f"ADX={ind['adx']}")
        if ind.get('volume_ratio'):
            reasoning_parts.append(f"Vol ratio={ind['volume_ratio']}x")
        if ai_reasoning:
            reasoning_parts.append(f"AI: {ai_reasoning}")
        detailed_reasoning = " | ".join(reasoning_parts) if reasoning_parts else f"Signal from {signal.source} in {regime} regime"

        write_trade_intent(
            pair=signal.pair,
            direction=signal.direction,
            strategy_source=signal.source or '',
            regime=regime,
            entry_price=signal.entry_price,
            sl_price=signal.sl_price,
            tp1_price=signal.tp1_price,
            ai_verdict=getattr(signal, 'ai_verdict', '') or '',
            ai_confidence=getattr(signal, 'ai_confidence', 0) or 0,
            ensemble_score=getattr(signal, 'ensemble_score', 0.0) or 0.0,
            reasoning=detailed_reasoning,
            grid_cell=grid_cell,
            oos_wr=oos_wr,
        )

        write_risk_check(
            pair=signal.pair,
            direction=signal.direction,
            trade_intent_id="",
            portfolio_value=portfolio_value,
            position_size_usd=pos_value,
            daily_pnl=agent.risk.daily_stats.realized_pnl,
            total_pnl=agent.risk.total_realized_pnl,
            peak_balance=agent.risk.peak_balance,
            consecutive_losses=agent.risk.consecutive_losses,
            position_scale=agent.risk.position_scale,
            regime=regime,
            allowed=allowed,
        )

        write_strategy_checkpoint(
            pair=signal.pair,
            regime=regime,
            previous_regime="",
            routed_strategies=[signal.source] if signal.source else [],
            blocked_strategies=[],
            position_scale=getattr(signal, 'position_scale', 1.0),
            sl_multiplier=getattr(signal, 'atr_sl_multiplier', 1.0) if hasattr(signal, 'atr_sl_multiplier') else 1.0,
            grid_cell=grid_cell,
            oos_wr=oos_wr,
            indicators=indicators,
        )
    except Exception as e:
        logger.warning("Validation artifact write failed (non-fatal): %s", e)


def write_scan_checkpoint(outcome: str, signal_count: int, signals: list) -> None:
    """Write a strategy checkpoint for every scan, even without trades.

    This shows continuous risk management activity to ERC-8004 validators.
    """
    try:
        for sig in signals[:3]:  # Cap at 3 to avoid bloat
            regime = getattr(sig, 'regime', '') or ''
            grid_cell = getattr(sig, 'grid_cell', '') or ''
            oos_wr = getattr(sig, 'oos_wr', 0.0) or 0.0
            write_strategy_checkpoint(
                pair=sig.pair,
                regime=regime,
                previous_regime="",
                routed_strategies=[sig.source] if sig.source else [],
                blocked_strategies=[],
                position_scale=getattr(sig, 'position_scale', 1.0),
                sl_multiplier=getattr(sig, 'atr_sl_multiplier', 1.0) if hasattr(sig, 'atr_sl_multiplier') else 1.0,
                grid_cell=grid_cell,
                oos_wr=oos_wr,
                indicators=getattr(sig, 'indicators', {}) or {},
            )
        if not signals:
            # Write a "market scan" checkpoint even with no signals
            write_strategy_checkpoint(
                pair="PORTFOLIO",
                regime="SCANNING",
                previous_regime="",
                routed_strategies=[],
                blocked_strategies=[],
                position_scale=1.0,
                sl_multiplier=1.0,
                grid_cell="",
                oos_wr=0.0,
                indicators={},
            )
    except Exception as e:
        logger.warning("Scan checkpoint write failed: %s", e)


def update_validation_outcome(agent, pair: str, reason: str, pnl: float) -> None:
    """Update validation trade intent with outcome."""
    try:
        pnl_pct = (pnl / agent.risk.initial_capital) * 100 if agent.risk.initial_capital > 0 else 0
        update_trade_intent_outcome(pair, reason, pnl_pct)
    except Exception as e:
        logger.warning("Validation outcome update failed (non-fatal): %s", e)
