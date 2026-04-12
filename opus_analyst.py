"""
OpusAnalyst — AI-powered trading analyst with dual-AI comparison

Core responsibilities:
1. Review trade signals with deep market reasoning (Ming's strategy framework)
2. Multi-timeframe EMA analysis per professional trader methodology
3. Ensemble scoring: combine rule-based signal + AI confidence 4. Dual-AI comparison: MiniMax M2.7 + Ollama for cross-validation

Backends (dual comparison mode):
  - MiniMax M2.7 (cloud, strong reasoning with thinking)
  - Ollama qwen2.5:7b (local, independent perspective)
  - Agreement bonus: both agree → +15 confidence, disagree → -10

Fallback backends:
  - Claude Opus (premium, best reasoning + vision, requires API credits)
  - Groq Llama (free cloud fallback)

Integration: StrategyEngine.scan_all() → OpusAnalyst.review() → Executor
"""
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from ai_backends import (
    call_anthropic,
    call_claude_subscription,
    call_groq,
    call_minimax,
    call_minimax_position,
    call_ollama,
)
from ai_prompts import (
    ANALYST_SYSTEM_PROMPT,
    POSITION_REVIEW_PROMPT,
    build_market_context,
    build_position_context,
)

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────
OPUS_MODEL = "claude-opus-4-6"
SONNET_MODEL = "claude-sonnet-4-6"  # fallback for cost control
MINIMAX_MODEL = "MiniMax-M2.7"  # strong reasoning with thinking, no vision
OLLAMA_MODEL = "qwen2.5:7b"  # local model for dual-AI comparison
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MAX_TOKENS = 1024
ANALYSIS_TIMEOUT = 45  # seconds (increased — AI needs time for quality analysis)

# Cost control: max spend per scan cycle
MAX_COST_PER_SCAN_USD = 0.50  # ~5 signals * $0.10 each


# ── Data Classes ─────────────────────────────────────────────

@dataclass(frozen=True)
class AIAnalysis:
    """Immutable AI analysis result for a trade signal."""
    verdict: str              # STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL
    ai_confidence: int        # 0-100
    reasoning: str            # One-line core judgment
    position_adjustment: float  # 0.0-1.5
    details: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    chart_analysis: str = ""  # Visual chart analysis (if available)
    latency_ms: int = 0
    model_used: str = ""
    cost_usd: float = 0.0


@dataclass(frozen=True)
class PositionReview:
    """AI review of an open position."""
    pair: str
    action: str             # HOLD, TIGHTEN_SL, TAKE_PROFIT, EXIT_NOW, REDUCE
    new_sl: float | None    # Suggested SL (only if TIGHTEN_SL)
    urgency: int            # 0-100 (how urgent is the action)
    reasoning: str
    market_changed: bool    # Has market structure changed since entry?
    reduce_pct: float = 0.0 # Fraction to close (0.0-1.0), only if REDUCE
    model_used: str = ""
    latency_ms: int = 0


@dataclass(frozen=True)
class EnsembleScore:
    """Combined rule-based + AI score."""
    rule_score: float       # 0-100 from StrategyEngine
    ai_score: float         # 0-100 from OpusAnalyst
    ensemble_score: float   # Weighted combination
    final_scale: float      # Position scale after ensemble
    should_trade: bool      # Final go/no-go
    reasoning: str


# ── Opus Analyst ─────────────────────────────────────────────

class OpusAnalyst:
    """AI trading analyst with dual-AI comparison (MiniMax + Ollama).

    Provides deep market reasoning as an overlay on rule-based signals.
    Uses Ming's EMA multi-timeframe methodology as analytical framework.

    Dual-AI mode (default, free):
      - MiniMax M2.7 (cloud, strong reasoning + thinking)
      - Ollama qwen2.5:7b (local, independent perspective)
      - Cross-validation: agreement → bonus, disagreement → caution

    Premium mode (requires API credits):
      - Claude Opus (best reasoning + vision)

    Fallback: Groq Llama 3.3 70B
    """

    def __init__(self, model: str = OPUS_MODEL, budget_per_scan: float = MAX_COST_PER_SCAN_USD):
        from config import AI_USE_ANTHROPIC
        self.model = model
        self.budget_per_scan = budget_per_scan
        self.scan_cost = 0.0

        # Claude subscription via `claude -p` (primary — no API credits needed)
        self.claude_subscription = False
        try:
            result = subprocess.run(
                ["claude", "--version"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                self.claude_subscription = True
                logger.info("Claude Opus subscription ready (claude -p)")
        except Exception:
            logger.info("claude CLI unavailable, skipping subscription")

        # Anthropic API (fallback — disabled by default to save credits)
        self.anthropic_client = None
        if AI_USE_ANTHROPIC:
            try:
                import anthropic
                self.anthropic_client = anthropic.Anthropic()
                logger.info(f"Anthropic client initialized (model: {model})")
            except Exception as e:
                logger.warning(f"Anthropic client init failed: {e}")
        else:
            logger.info("Anthropic API disabled by config")

        # MiniMax M2.7 via Anthropic-compatible API (dual-AI primary)
        self.minimax_client = None
        minimax_key = os.environ.get("MINIMAX_API_KEY", "")
        minimax_host = os.environ.get("MINIMAX_API_HOST", "https://api.minimax.io")
        if minimax_key:
            try:
                import anthropic as _anthropic
                self.minimax_client = _anthropic.Anthropic(
                    api_key=minimax_key,
                    base_url=f"{minimax_host}/anthropic",
                    max_retries=1,  # Don't retry — fall back to claude-only faster
                )
                logger.info("MiniMax M2.7 client initialized (dual-AI primary)")
            except Exception as e:
                logger.warning(f"MiniMax client init failed: {e}")

        # Ollama local model (dual-AI secondary)
        self.ollama_available = False
        try:
            import requests
            resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                if any(OLLAMA_MODEL.split(":")[0] in m for m in models):
                    self.ollama_available = True
                    logger.info(f"Ollama {OLLAMA_MODEL} available (dual-AI secondary)")
                else:
                    logger.warning(f"Ollama running but {OLLAMA_MODEL} not found. Available: {models}")
        except Exception as e:
            logger.warning(f"Ollama not reachable: {e}")

        # Groq as final fallback (set GROQ_API_KEY in .env)
        self.groq_api_key = os.environ.get("GROQ_API_KEY", "")

        self._cost_per_1k_input = 0.015 if "opus" in model else 0.003
        self._cost_per_1k_output = 0.075 if "opus" in model else 0.015

    def reset_scan_budget(self):
        """Reset cost tracker for new scan cycle."""
        self.scan_cost = 0.0

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate API cost in USD."""
        return (input_tokens / 1000 * self._cost_per_1k_input +
                output_tokens / 1000 * self._cost_per_1k_output)

    def _build_market_context(self, signal, df: pd.DataFrame, regime) -> str:
        """Build structured market context string for AI analysis."""
        return build_market_context(signal, df, regime)

    def analyze_signal(self, signal, df: pd.DataFrame, regime,
                       chart_image_b64: str | None = None) -> AIAnalysis:
        """Analyze a single trade signal using dual-AI comparison.

        Dual-AI mode: MiniMax M2.7 + Ollama analyze independently,
        then cross-validate. Agreement boosts confidence, disagreement reduces it.

        Falls back to single-AI if only one backend is available.

        Args:
            signal: TradeSignal from StrategyEngine
            df: 4H OHLCV DataFrame with computed indicators
            regime: RegimeResult from RegimeDetector
            chart_image_b64: Optional base64-encoded chart image (Opus only)

        Returns:
            AIAnalysis with verdict, confidence, and position adjustment
        """
        # Budget check (only matters for paid APIs)
        estimated_cost = self._estimate_cost(2000, 500)
        if self.scan_cost + estimated_cost > self.budget_per_scan:
            logger.warning(
                f"Budget exceeded (${self.scan_cost:.3f}/{self.budget_per_scan:.2f}), "
                f"skipping AI analysis for {signal.pair}"
            )
            return AIAnalysis(
                verdict="HOLD", ai_confidence=50,
                reasoning="Budget limit, skipping AI analysis",
                position_adjustment=1.0, model_used="budget_skip",
            )

        context = self._build_market_context(signal, df, regime)
        start_time = time.time()

        # ── Dual-AI: Claude Opus(subscription) + MiniMax M2.7 ──
        opus_result = self._call_claude_subscription(context, signal.pair)
        minimax_result = self._call_minimax(context, signal.pair)

        if opus_result and minimax_result:
            return self._dual_ai_merge(
                opus_result, minimax_result, signal, start_time
            )

        # ── Single-AI fallback chain ────────────────────────────
        result = opus_result or minimax_result
        if result is None:
            result = self._call_ollama(context, signal.pair)
        if result is None:
            result = self._call_groq(context, signal.pair)
        if result is None:
            result = self._call_anthropic(context, signal.pair, chart_image_b64)
        if result is None:
            return self._fallback_analysis("all AI backends unavailable")

        raw_text, model_used, input_tokens, output_tokens = result
        latency_ms = int((time.time() - start_time) * 1000)

        cost = self._estimate_cost(input_tokens, output_tokens) if "claude" in model_used else 0.0
        self.scan_cost += cost

        analysis = self._parse_response(raw_text)

        return AIAnalysis(
            verdict=analysis.get("verdict", "HOLD"),
            ai_confidence=min(100, max(0, int(analysis.get("ai_confidence", 50)))),
            reasoning=analysis.get("reasoning", "unable to parse AI response"),
            position_adjustment=min(1.5, max(0.0, float(analysis.get("position_adjustment", 1.0)))),
            details=analysis.get("details", {}),
            warnings=analysis.get("warnings", []),
            chart_analysis=analysis.get("details", {}).get("pattern", ""),
            latency_ms=latency_ms,
            model_used=model_used,
            cost_usd=cost,
        )

    def _dual_ai_merge(self, minimax_result: tuple, ollama_result: tuple,
                       signal, start_time: float) -> AIAnalysis:
        """Merge two AI analyses with cross-validation scoring.

        Agreement logic:
          - Same verdict direction → +15 confidence bonus
          - Opposite verdicts → -10 penalty, take conservative side
          - Mixed (one HOLD) → average, no bonus
        """
        mm_text, mm_model, mm_in, mm_out = minimax_result
        ol_text, ol_model, ol_in, ol_out = ollama_result
        latency_ms = int((time.time() - start_time) * 1000)

        mm_parsed = self._parse_response(mm_text)
        ol_parsed = self._parse_response(ol_text)

        mm_verdict = mm_parsed.get("verdict", "HOLD")
        ol_verdict = ol_parsed.get("verdict", "HOLD")
        mm_conf = min(100, max(0, int(mm_parsed.get("ai_confidence", 50))))
        ol_conf = min(100, max(0, int(ol_parsed.get("ai_confidence", 50))))

        # Classify verdicts into directions
        bullish = {"STRONG_BUY", "BUY"}
        bearish = {"STRONG_SELL", "SELL"}

        mm_bull = mm_verdict in bullish
        mm_bear = mm_verdict in bearish
        ol_bull = ol_verdict in bullish
        ol_bear = ol_verdict in bearish

        # Cross-validation
        if (mm_bull and ol_bull) or (mm_bear and ol_bear):
            # Both agree on direction → confidence bonus
            agreement = "AGREE"
            conf_adj = 15
            # Take the stronger verdict
            verdict = mm_verdict if mm_conf >= ol_conf else ol_verdict
        elif (mm_bull and ol_bear) or (mm_bear and ol_bull):
            # Opposite directions → penalty, conservative
            agreement = "DISAGREE"
            conf_adj = -10
            verdict = "HOLD"
        else:
            # One HOLD, one directional → moderate
            agreement = "PARTIAL"
            conf_adj = 0
            verdict = mm_verdict if mm_verdict != "HOLD" else ol_verdict

        # Weighted average confidence (MiniMax 60%, Ollama 40%)
        # MiniMax gets higher weight due to stronger reasoning
        avg_conf = int(mm_conf * 0.6 + ol_conf * 0.4 + conf_adj)
        avg_conf = min(100, max(0, avg_conf))

        # Position adjustment: average both
        mm_adj = min(1.5, max(0.0, float(mm_parsed.get("position_adjustment", 1.0))))
        ol_adj = min(1.5, max(0.0, float(ol_parsed.get("position_adjustment", 1.0))))
        avg_adj = (mm_adj * 0.6 + ol_adj * 0.4)

        # If disagree, reduce position
        if agreement == "DISAGREE":
            avg_adj = min(avg_adj, 0.5)

        # Build merged reasoning
        mm_reason = mm_parsed.get("reasoning", "")[:80]
        ol_reason = ol_parsed.get("reasoning", "")[:80]
        reasoning = (
            f"[{agreement}] MM:{mm_verdict}({mm_conf}) + OL:{ol_verdict}({ol_conf}) → "
            f"{mm_reason}"
        )

        # Merge details from MiniMax (higher quality)
        details = mm_parsed.get("details", {})
        details["ollama_verdict"] = f"{ol_verdict}({ol_conf})"
        details["agreement"] = agreement

        # Merge warnings
        warnings = mm_parsed.get("warnings", []) + ol_parsed.get("warnings", [])

        logger.info(
            f"Dual-AI {signal.pair}: {agreement} | "
            f"MM={mm_verdict}({mm_conf}) OL={ol_verdict}({ol_conf}) → "
            f"{verdict}({avg_conf}) adj={avg_adj:.2f}"
        )

        return AIAnalysis(
            verdict=verdict,
            ai_confidence=avg_conf,
            reasoning=reasoning,
            position_adjustment=round(avg_adj, 2),
            details=details,
            warnings=warnings,
            chart_analysis=details.get("pattern", ""),
            latency_ms=latency_ms,
            model_used=f"dual:{mm_model}+{ol_model}",
            cost_usd=0.0,  # Both free
        )

    # ── Backend Delegators ────────────────────────────────────
    # Thin wrappers that delegate to ai_backends module functions,
    # passing instance state as parameters.

    def _call_claude_subscription(self, context: str, pair: str) -> tuple | None:
        return call_claude_subscription(context, pair, self.claude_subscription)

    def _call_anthropic(self, context: str, pair: str,
                        chart_image_b64: str | None = None) -> tuple | None:
        result = call_anthropic(context, pair, self.anthropic_client,
                                self.model, chart_image_b64)
        if result is None and self.anthropic_client:
            # Credits exhausted — disable for rest of session
            self.anthropic_client = None
        return result

    def _call_minimax(self, context: str, pair: str) -> tuple | None:
        return call_minimax(context, pair, self.minimax_client)

    def _call_ollama(self, context: str, pair: str) -> tuple | None:
        return call_ollama(context, pair, self.ollama_available)

    def _call_groq(self, context: str, pair: str) -> tuple | None:
        return call_groq(context, pair, self.groq_api_key)

    def _call_minimax_position(self, context: str, pair: str) -> tuple | None:
        return call_minimax_position(context, pair, self.minimax_client)

    def _parse_response(self, raw_text: str) -> dict:
        """Parse Opus JSON response, handling common formatting issues."""
        text = raw_text.strip()

        # Remove markdown code blocks if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (```json and ```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            import re
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass

            logger.warning(f"Failed to parse AI response as JSON: {text[:200]}")
            return {
                "verdict": "HOLD",
                "ai_confidence": 50,
                "reasoning": text[:200],
                "position_adjustment": 1.0,
            }

    def _fallback_analysis(self, reason: str) -> AIAnalysis:
        """Trust rule-based signals when ALL AI backends are unavailable.

        WFO-validated signals are proven (82% OOS win rate).
        When AI can't review, pass through with neutral verdict and
        reduced position size (50%) as a safety margin.
        """
        logger.warning("All AI backends unavailable (%s) — trusting rule-based signal at 50%% size", reason)
        return AIAnalysis(
            verdict="BUY",  # Neutral-positive: trust WFO-validated rules
            ai_confidence=55,  # Moderate confidence from rules alone
            reasoning=f"AI unavailable ({reason}) — trusting WFO-validated rules at 50%% size",
            position_adjustment=0.5,  # Half position size for safety
            model_used="rule_fallback",
        )

    def compute_ensemble(self, signal, ai_analysis: AIAnalysis,
                         rule_confidence: float = 50.0) -> EnsembleScore:
        """Compute ensemble score combining rule-based and AI signals.

        Weights:
          - Rule-based signal: 40% (proven via WFO backtesting)
          - AI analysis: 40% (Opus deep reasoning)
          - Multi-strategy bonus: 20% (number of confirming strategies)

        Args:
            signal: TradeSignal from StrategyEngine
            ai_analysis: AIAnalysis from OpusAnalyst
            rule_confidence: Rule-based confidence (0-100)

        Returns:
            EnsembleScore with final trading decision
        """
        # Rule score (0-100)
        rule_score = min(100, max(0, rule_confidence))

        # AI score (0-100)
        ai_score = ai_analysis.ai_confidence

        # Multi-strategy bonus
        n_sources = len(signal.source.split("+"))
        multi_bonus = min(20, (n_sources - 1) * 10)  # 0 for single, 10 for double, 20 for triple

        # Weighted ensemble
        ensemble = rule_score * 0.40 + ai_score * 0.40 + multi_bonus

        # AI verdict adjustment
        verdict_map = {
            "STRONG_BUY": 1.3,
            "BUY": 1.1,
            "HOLD": 0.7,
            "SELL": 0.3,
            "STRONG_SELL": 0.0,
        }

        # For short signals, invert the verdict
        if signal.direction == "short":
            verdict_map = {
                "STRONG_SELL": 1.3,
                "SELL": 1.1,
                "HOLD": 0.7,
                "BUY": 0.3,
                "STRONG_BUY": 0.0,
            }

        verdict_mult = verdict_map.get(ai_analysis.verdict, 1.0)

        # Final position scale
        ai_adj = ai_analysis.position_adjustment
        final_scale = signal.position_scale * ai_adj * verdict_mult

        # Decision thresholds
        from config import AI_MIN_ENSEMBLE_SCORE
        # High-conviction override: triple confluence (multi_bonus >= 20) with
        # strong rule score bypasses AI threshold — these are our best setups
        # (e.g., the +$247 ETH trade was triple confluence)
        high_conviction = rule_score >= 75 and multi_bonus >= 10
        min_score = AI_MIN_ENSEMBLE_SCORE * 0.7 if high_conviction else AI_MIN_ENSEMBLE_SCORE

        should_trade = (
            ensemble >= min_score and      # Config-driven threshold (reduced for high conviction)
            final_scale >= 0.3 and         # Minimum position size
            ai_analysis.verdict not in ("STRONG_SELL" if signal.direction == "long" else "STRONG_BUY",)
        )

        reasoning = (
            f"Ensemble {ensemble:.0f} = Rule({rule_score:.0f})*40% + "
            f"AI({ai_score})*40% + Multi({multi_bonus}) | "
            f"AI: {ai_analysis.verdict} → scale {final_scale:.2f} | "
            f"{ai_analysis.reasoning}"
        )

        return EnsembleScore(
            rule_score=rule_score,
            ai_score=ai_score,
            ensemble_score=ensemble,
            final_scale=min(1.5, max(0.0, final_scale)),
            should_trade=should_trade,
            reasoning=reasoning,
        )

    def review_signals(self, signals: list, df_cache: dict,
                       regime_cache: dict,
                       chart_cache: dict | None = None) -> list[tuple]:
        """Review a batch of signals and return ensemble-scored results.

        Args:
            signals: List of TradeSignal from StrategyEngine
            df_cache: Dict mapping pair -> computed indicator DataFrame
            regime_cache: Dict mapping pair -> RegimeResult
            chart_cache: Optional dict mapping pair -> base64 chart image

        Returns:
            List of (signal, ai_analysis, ensemble_score) tuples,
            sorted by ensemble_score descending
        """
        self.reset_scan_budget()
        results = []

        for signal in signals:
            pair = signal.pair
            df = df_cache.get(pair)
            regime = regime_cache.get(pair)

            if df is None or regime is None:
                logger.warning(f"No data for {pair}, skipping AI review")
                fallback = self._fallback_analysis("no market data")
                ensemble = self.compute_ensemble(signal, fallback, signal.confidence)
                results.append((signal, fallback, ensemble))
                continue

            chart_b64 = chart_cache.get(pair) if chart_cache else None
            ai_analysis = self.analyze_signal(signal, df, regime, chart_b64)

            # Compute rule confidence from signal properties
            # Multi-source signals get higher rule confidence
            n_sources = len(signal.source.split("+"))
            rule_conf = 50 + n_sources * 15  # 65 for single, 80 for double, 95 for triple

            ensemble = self.compute_ensemble(signal, ai_analysis, rule_conf)

            logger.info(
                f"AI Review: {pair} {signal.direction.upper()} | "
                f"Verdict={ai_analysis.verdict} Conf={ai_analysis.ai_confidence} | "
                f"Ensemble={ensemble.ensemble_score:.0f} Trade={ensemble.should_trade} | "
                f"${ai_analysis.cost_usd:.4f} {ai_analysis.latency_ms}ms"
            )

            results.append((signal, ai_analysis, ensemble))

        # Sort by ensemble score descending
        results.sort(key=lambda x: x[2].ensemble_score, reverse=True)

        total_cost = sum(r[1].cost_usd for r in results)
        logger.info(f"AI Review complete: {len(results)} signals, total cost ${total_cost:.4f}")

        return results

    # ── Position Management ──────────────────────────────────

    def _build_position_context(self, position, df: pd.DataFrame, regime) -> str:
        """Build context for position review."""
        return build_position_context(position, df, regime)

    def review_positions(self, positions: dict, df_cache: dict,
                         regime_cache: dict) -> list[PositionReview]:
        """Review all open positions using Claude subscription + MiniMax.

        Args:
            positions: Dict mapping pair -> Position
            df_cache: Dict mapping pair -> DataFrame with indicators
            regime_cache: Dict mapping pair -> RegimeResult

        Returns:
            List of PositionReview with recommended actions
        """
        if not positions:
            return []

        if not self.claude_subscription and not self.minimax_client:
            logger.info("No AI backend for position review — positions managed by SL/TP only")
            return []

        reviews = []

        for pair, position in positions.items():
            df = df_cache.get(pair)
            regime = regime_cache.get(pair)

            if df is None or regime is None:
                logger.warning(f"No data for {pair}, skipping position review")
                continue

            context = self._build_position_context(position, df, regime)
            start_time = time.time()

            # MiniMax only for position review (claude -p consistently times out,
            # wasting 30s per position and causing state save to miss cron timeout)
            result = self._call_minimax_position(context, pair)
            latency_ms = int((time.time() - start_time) * 1000)

            if result is None:
                logger.warning(f"Position review failed for {pair}")
                continue

            raw_text, model_used, _, _ = result
            parsed = self._parse_response(raw_text)

            action = parsed.get("action", "HOLD")
            if action not in ("HOLD", "TIGHTEN_SL", "TAKE_PROFIT", "EXIT_NOW", "REDUCE"):
                action = "HOLD"

            new_sl = parsed.get("new_sl")
            if new_sl is not None:
                try:
                    new_sl = float(new_sl)
                except (TypeError, ValueError):
                    new_sl = None

            # Parse reduce_pct (AI gives 25-75 as integer percentage)
            reduce_pct = 0.0
            if action == "REDUCE":
                try:
                    raw_pct = float(parsed.get("reduce_pct", 50))
                    reduce_pct = max(0.1, min(0.75, raw_pct / 100.0))
                except (TypeError, ValueError):
                    reduce_pct = 0.5  # Default 50% if parse fails

            review = PositionReview(
                pair=pair,
                action=action,
                new_sl=new_sl,
                urgency=min(100, max(0, int(parsed.get("urgency", 0)))),
                reasoning=parsed.get("reasoning", ""),
                market_changed=bool(parsed.get("market_changed", False)),
                reduce_pct=reduce_pct,
                model_used=model_used,
                latency_ms=latency_ms,
            )

            reviews.append(review)

            logger.info(
                f"Position Review: {pair} {position.direction.upper()} | "
                f"Action={review.action} Urgency={review.urgency} | "
                f"Changed={review.market_changed} | {review.reasoning[:60]}"
            )

        return reviews
