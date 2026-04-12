#!/usr/bin/env python3
"""
KrakenTradingAgent — Main agent loop for hackathon paper trading

Architecture:
  - 4H scan cycle: run full strategy scan every 4 hours
  - 5min monitor: check SL/TP, risk limits, portfolio health
  - Uses Kraken CLI for all data + execution

Usage:
  python3 agent.py                  # Start main loop
  python3 agent.py --single-scan    # Run one scan cycle and exit
  python3 agent.py --status         # Show current status and exit
"""
import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path

from config import (
    INITIAL_BALANCE, SCAN_INTERVAL_SECONDS,
    FULL_SCAN_INTERVAL_HOURS, ACTIVE_PAIRS,
    AI_ENABLED, AI_MODEL, AI_BUDGET_PER_SCAN,
)
from kraken_data import KrakenDataAdapter
from strategies import StrategyEngine
from executor import KrakenExecutor
from risk_manager import RiskManager
from opus_analyst import OpusAnalyst, PositionReview

# Extracted modules
from agent_state import (
    save_state, load_state, post_reputation,
    post_onchain_intent, post_onchain_checkpoint,
)
from agent_signals import (
    ai_review_signals, write_validation_artifacts,
    write_scan_checkpoint, update_validation_outcome,
)

logger = logging.getLogger(__name__)

# Persist trade log to disk
LOG_DIR = Path(__file__).parent / "logs"
TRADE_LOG_PATH = LOG_DIR / "trade_log.jsonl"


class KrakenTradingAgent:
    """Main trading agent orchestrating data -> strategy -> execution -> risk."""

    def __init__(self):
        self.data = KrakenDataAdapter()
        self.strategy = StrategyEngine()
        self.executor = KrakenExecutor()

        # Get actual starting balance from Kraken paper account
        starting = self.executor.get_starting_balance()
        self.risk = RiskManager(initial_capital=starting or INITIAL_BALANCE)

        # Opus AI Analyst
        self.ai_enabled = AI_ENABLED
        self.opus_analyst = OpusAnalyst(
            model=AI_MODEL,
            budget_per_scan=AI_BUDGET_PER_SCAN,
        ) if AI_ENABLED else None

        self.last_full_scan: float = 0
        self.scan_count: int = 0

        # Ensure log directory exists
        LOG_DIR.mkdir(exist_ok=True)

        # Restore state from previous run (crash recovery)
        load_state(self)

        ai_status = f"AI: {AI_MODEL}" if AI_ENABLED else "AI: disabled"
        logger.info(
            f"Agent initialized | Balance: ${self.risk.initial_capital:.2f} | "
            f"Pairs: {len(ACTIVE_PAIRS)} | Scan interval: {FULL_SCAN_INTERVAL_HOURS}h | {ai_status}"
        )

    def _should_scan(self) -> bool:
        """Check if it's time for a full strategy scan."""
        elapsed = time.time() - self.last_full_scan
        return elapsed >= FULL_SCAN_INTERVAL_HOURS * 3600

    def _log_trade(self, event: dict):
        """Append trade event to JSONL log."""
        with open(TRADE_LOG_PATH, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def _save_state(self):
        save_state(self)

    def _ai_review_signals(self, signals):
        return ai_review_signals(self, signals)

    def _write_validation_artifacts(self, signal, portfolio_value, pos_value, allowed):
        write_validation_artifacts(self, signal, portfolio_value, pos_value, allowed)

    def _write_scan_checkpoint(self, outcome, signal_count, signals):
        write_scan_checkpoint(outcome, signal_count, signals)

    def _update_validation_outcome(self, pair, reason, pnl):
        update_validation_outcome(self, pair, reason, pnl)

    def _post_onchain_intent(self, pair, action, amount_usd):
        post_onchain_intent(pair, action, amount_usd)

    def _post_onchain_checkpoint(self, data, score, notes):
        post_onchain_checkpoint(data, score, notes)

    def _post_reputation(self, reason):
        post_reputation(self, reason)

    def scan_and_trade(self):
        """Run a full strategy scan and execute qualifying signals.

        Flow: StrategyEngine -> OpusAnalyst AI Review -> RiskManager -> Executor

        Kraken paper = spot-only:
          - Long signals -> execute as BUY
          - Short signals -> use as exit indicators for existing longs
        """
        self.scan_count += 1
        logger.info(f"=== Full Scan #{self.scan_count} ===")

        # Decrement per-pair cooldowns
        for p in list(self.risk.pair_cooldown):
            self.risk.pair_cooldown[p] -= 1
            if self.risk.pair_cooldown[p] <= 0:
                del self.risk.pair_cooldown[p]
                logger.info(f"Pair {p} cooldown expired")

        # Get current portfolio value
        portfolio_value = self.executor.get_current_value()
        if portfolio_value <= 0:
            portfolio_value = self.risk.current_balance
        logger.info(f"Portfolio value: ${portfolio_value:.2f}")

        # Step 1: Rule-based scan for signals
        signals = self.strategy.scan_all(self.data)

        if not signals:
            logger.info("No signals found")
            self._write_scan_checkpoint("no_signals", 0, [])
            self.last_full_scan = time.time()
            return

        raw_signals = list(signals)  # preserve for checkpoint logging
        logger.info(f"Rule-based scan: {len(signals)} raw signals")

        # Step 2: AI analysis and ensemble scoring (if enabled)
        signals = self._ai_review_signals(signals)

        if not signals:
            logger.info("All signals rejected by AI analysis")
            self._write_scan_checkpoint("ai_rejected_all", len(raw_signals), raw_signals)
            self.last_full_scan = time.time()
            return

        # Separate long and short signals
        long_signals = [s for s in signals if s.direction == "long"]
        short_signals = [s for s in signals if s.direction == "short"]
        short_pairs = {s.pair for s in short_signals}

        logger.info(f"After AI: {len(signals)} signals ({len(long_signals)} long, {len(short_signals)} short)")

        # Filter out conflicting signals: if both long AND short exist for same pair,
        # only use the short as exit (don't re-open a long right after closing)
        conflicting = {s.pair for s in long_signals} & short_pairs
        if conflicting:
            logger.info(f"Conflicting signals for {conflicting} — short exits only, no new longs")

        # Use short signals to exit existing long positions
        exit_events = []
        if short_signals:
            exit_events = self.executor.check_short_exits(short_signals, self.data)
            for event in exit_events:
                self.risk.register_close(event["pair"], event["pnl"], event["reason"])
                self._update_validation_outcome(event["pair"], event["reason"], event["pnl"])
                self._post_onchain_intent(event["pair"], "SELL", abs(event.get("pnl", 0)))
                self._log_trade({
                    "type": "close",
                    "pair": event["pair"],
                    "price": event["price"],
                    "reason": event["reason"],
                    "pnl": event["pnl"],
                    "scan": self.scan_count,
                })
            if exit_events:
                self._save_state()  # Immediately persist after position changes
                self._post_reputation("short_exit")

        # Execute long signals (skip pairs with conflicting shorts)
        executed = 0
        for signal in long_signals:
            if signal.pair in conflicting:
                logger.info(f"Skipping {signal.pair} long — conflicting short signal")
                continue
            allowed, reason = self.risk.can_trade(signal.pair, signal.direction)
            if not allowed:
                logger.info(f"Risk blocked {signal.pair} {signal.direction}: {reason}")
                continue
            signal.position_scale *= self.risk.get_position_scale(signal.direction)
            success = self.executor.execute_signal(signal, portfolio_value)
            if success:
                self.risk.register_open(signal.pair, signal.direction)
                pos_value = signal.entry_price * self.executor.positions.get(signal.pair, signal).volume if signal.pair in self.executor.positions else 0
                self._post_onchain_intent(signal.pair, "BUY", pos_value)
                self._log_trade({
                    "type": "open",
                    "pair": signal.pair,
                    "direction": "long",
                    "entry_price": signal.entry_price,
                    "sl": signal.sl_price,
                    "tp1": signal.tp1_price,
                    "source": signal.source,
                    "ai_verdict": signal.ai_verdict,
                    "ai_confidence": signal.ai_confidence,
                    "ensemble_score": signal.ensemble_score,
                    "scan": self.scan_count,
                })
                # Write validation artifacts for live trade
                self._write_validation_artifacts(
                    signal, portfolio_value, pos_value, allowed=True,
                )
                executed += 1

        # Execute short signals as internal paper positions
        short_executed = 0
        exited_pairs = {e["pair"] for e in exit_events}
        for signal in short_signals:
            if signal.pair in exited_pairs:
                continue  # Already used as exit signal
            if signal.pair in self.executor.positions:
                continue  # Already in position
            allowed, reason = self.risk.can_trade(signal.pair, signal.direction)
            if not allowed:
                logger.info(f"Risk blocked {signal.pair} {signal.direction}: {reason}")
                continue
            signal.position_scale *= self.risk.get_position_scale(signal.direction)
            success = self.executor.execute_signal(signal, portfolio_value)
            if success:
                self.risk.register_open(signal.pair, signal.direction)
                pos_value = signal.entry_price * self.executor.positions.get(signal.pair, signal).volume if signal.pair in self.executor.positions else 0
                self._post_onchain_intent(signal.pair, "SELL", pos_value)
                self._log_trade({
                    "type": "open",
                    "pair": signal.pair,
                    "direction": "short",
                    "entry_price": signal.entry_price,
                    "sl": signal.sl_price,
                    "tp1": signal.tp1_price,
                    "source": signal.source,
                    "ai_verdict": signal.ai_verdict,
                    "ai_confidence": signal.ai_confidence,
                    "ensemble_score": signal.ensemble_score,
                    "scan": self.scan_count,
                })
                # Write validation artifacts for live short trade
                self._write_validation_artifacts(
                    signal, portfolio_value, pos_value, allowed=True,
                )
                short_executed += 1

        logger.info(f"Executed {executed}/{len(long_signals)} long, {short_executed}/{len(short_signals)} short signals")
        if executed > 0 or short_executed > 0:
            self._save_state()  # Persist new positions immediately
            # Post scan checkpoint to ValidationRegistry
            self._post_onchain_checkpoint(
                {"scan": self.scan_count, "long": executed, "short": short_executed,
                 "signals": len(signals), "portfolio": portfolio_value},
                score=min(95, 60 + executed * 10 + short_executed * 5),
                notes=f"Scan #{self.scan_count}: {executed}L+{short_executed}S of {len(signals)} signals",
            )
        self.last_full_scan = time.time()

    def _ai_review_positions(self) -> list[PositionReview]:
        """AI position review using MiniMax only.

        Reviews open positions for market structure changes and suggests
        SL tightening, early exit, or hold.
        """
        if not self.ai_enabled or not self.opus_analyst:
            return []
        if not self.executor.positions:
            return []

        # Build data caches
        df_cache = {}
        regime_cache = {}
        for pair in self.executor.positions:
            try:
                df_4h = self.data.get_ohlc(pair, "4h")
                if not df_4h.empty and len(df_4h) >= 50:
                    df_computed = self.strategy._compute_indicators(df_4h)
                    df_cache[pair] = df_computed
                    regime_cache[pair] = self.strategy.regime_detector.detect(df_computed)
            except Exception as e:
                logger.warning(f"Failed to get data for position review of {pair}: {e}")

        try:
            return self.opus_analyst.review_positions(
                self.executor.positions, df_cache, regime_cache
            )
        except Exception as e:
            logger.error(f"AI position review failed: {e}")
            return []

    def _apply_position_reviews(self, reviews: list[PositionReview]) -> list[dict]:
        """Apply AI position review actions to actual positions.

        Returns list of close events from AI-triggered exits.
        """
        from dataclasses import replace as dc_replace
        events = []

        for review in reviews:
            pair = review.pair
            pos = self.executor.positions.get(pair)
            if not pos:
                continue

            if review.action == "HOLD":
                continue

            elif review.action == "TIGHTEN_SL" and review.new_sl is not None:
                # Only tighten (move SL closer to current price), never widen
                if pos.direction == "long" and review.new_sl > pos.sl_price:
                    updated = dc_replace(pos, sl_price=review.new_sl)
                    self.executor.positions[pair] = updated
                    logger.info(
                        f"AI TIGHTEN: {pair} SL ${pos.sl_price:.2f} -> ${review.new_sl:.2f} | "
                        f"{review.reasoning}"
                    )
                elif pos.direction == "short" and review.new_sl < pos.sl_price:
                    updated = dc_replace(pos, sl_price=review.new_sl)
                    self.executor.positions[pair] = updated
                    logger.info(
                        f"AI TIGHTEN: {pair} SHORT SL ${pos.sl_price:.2f} -> ${review.new_sl:.2f} | "
                        f"{review.reasoning}"
                    )

            elif review.action == "REDUCE" and review.reduce_pct > 0:
                # Guard: skip if already partially closed or remaining too low
                if pos.remaining_pct <= 0.25:
                    logger.info(
                        f"AI REDUCE skipped: {pair} remaining={pos.remaining_pct:.0%} too low"
                    )
                    continue
                # Guard: only reduce if urgency >= 80 (avoid over-eager AI exits)
                if review.urgency < 80:
                    logger.info(
                        f"AI REDUCE skipped: {pair} urgency={review.urgency} < 70"
                    )
                    continue
                # Partial close -- AI decides what % to exit
                try:
                    tickers = self.data.get_multi_ticker([pair])
                    price = tickers.get(pair, {}).get("last", pos.entry_price)
                    reason = f"AI_REDUCE_{int(review.reduce_pct * 100)}pct"
                    pnl = self.executor.partial_close(
                        pair, price, review.reduce_pct, reason
                    )
                    events.append({
                        "pair": pair, "price": price,
                        "reason": reason, "pnl": pnl,
                        "close_pct": review.reduce_pct,
                    })
                    logger.info(
                        f"AI REDUCE: {pair} closed {review.reduce_pct:.0%} at ${price:.2f} | "
                        f"PnL ${pnl:+.2f} | {review.reasoning}"
                    )
                except Exception as e:
                    logger.error(f"Failed to reduce {pair}: {e}")

            elif review.action in ("TAKE_PROFIT", "EXIT_NOW"):
                # Guard: EXIT_NOW needs urgency >= 80, TAKE_PROFIT >= 60
                min_urgency = 80 if review.action == "EXIT_NOW" else 60
                if review.urgency < min_urgency:
                    logger.info(
                        f"AI {review.action} skipped: {pair} urgency={review.urgency} < {min_urgency}"
                    )
                    continue
                # Get current price and close
                try:
                    tickers = self.data.get_multi_ticker([pair])
                    price = tickers.get(pair, {}).get("last", pos.entry_price)
                    reason = f"AI_{review.action}"
                    pnl = self.executor.close_position(pair, price, reason)
                    if pair not in self.executor.positions:
                        events.append({
                            "pair": pair, "price": price,
                            "reason": reason, "pnl": pnl,
                        })
                        logger.info(
                            f"AI {review.action}: {pair} closed at ${price:.2f} | "
                            f"PnL ${pnl:+.2f} | {review.reasoning}"
                        )
                except Exception as e:
                    logger.error(f"Failed to close {pair} on AI {review.action}: {e}")

        return events

    def monitor_positions(self):
        """Check all open positions for SL/TP hits + AI position review."""
        if not self.executor.positions:
            return

        # Standard SL/TP checks
        events = self.executor.check_sl_tp(self.data)

        for event in events:
            is_partial = "close_pct" in event and event.get("close_pct", 1.0) < 1.0
            if not is_partial:
                # Only register full closes in risk manager (affects consecutive loss tracking)
                self.risk.register_close(event["pair"], event["pnl"], event["reason"])
                self._update_validation_outcome(event["pair"], event["reason"], event["pnl"])
                # Submit close intent to RiskRouter (judge bot reads these)
                self._post_onchain_intent(event["pair"], "SELL", abs(event.get("pnl", 0)))
            self._log_trade({
                "type": "partial_close" if is_partial else "close",
                "pair": event["pair"],
                "price": event["price"],
                "reason": event["reason"],
                "pnl": event["pnl"],
                "close_pct": event.get("close_pct"),
            })

        # AI position review (MiniMax only, runs every monitor cycle)
        if self.executor.positions:
            ai_reviews = self._ai_review_positions()
            ai_events = self._apply_position_reviews(ai_reviews)
            if ai_events:
                # CRITICAL: Save state IMMEDIATELY after AI closes positions.
                # On-chain calls can be slow and cron timeout may kill process.
                for event in ai_events:
                    self.risk.register_close(event["pair"], event["pnl"], event["reason"])
                    self._update_validation_outcome(event["pair"], event["reason"], event["pnl"])
                self._save_state()
                # Non-critical: on-chain and logging (best-effort after state is safe)
                for event in ai_events:
                    self._post_onchain_intent(event["pair"], "SELL", abs(event.get("pnl", 0)))
                    self._log_trade({
                        "type": "close",
                        "pair": event["pair"],
                        "price": event["price"],
                        "reason": event["reason"],
                        "pnl": event["pnl"],
                        "ai_action": True,
                    })
            events.extend(ai_events)

        # Persist state and post reputation after closures
        if events:
            self._save_state()
            self._post_reputation("sl_tp_close")
            self._post_onchain_checkpoint(
                {"monitor": True, "events": len(events),
                 "positions_remaining": len(self.executor.positions)},
                score=min(90, 50 + len(events) * 10),
                notes=f"Monitor: {len(events)} position events",
            )

        # Emergency check
        if self.risk.check_emergency():
            logger.warning("EMERGENCY: Drawdown limit reached, closing all positions")
            close_events = self.executor.close_all(self.data, "EMERGENCY_DRAWDOWN")
            for event in close_events:
                self.risk.register_close(event["pair"], event["pnl"], event["reason"])
                self._log_trade({
                    "type": "close",
                    "pair": event["pair"],
                    "price": event["price"],
                    "reason": event["reason"],
                    "pnl": event["pnl"],
                })
            if close_events:
                self._post_reputation("emergency_close")

    async def run_loop(self) -> None:
        """Main agent loop: scan every 4h, monitor every 5min.

        Supports graceful shutdown via SIGTERM/SIGINT — saves state
        before exiting so no position data is lost.
        """
        self._shutdown_requested = False

        def _handle_shutdown(signum: int, frame: object) -> None:
            sig_name = signal.Signals(signum).name
            logger.info("Received %s — initiating graceful shutdown...", sig_name)
            self._shutdown_requested = True

        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)

        logger.info("Agent loop starting (graceful shutdown enabled)...")

        while not self._shutdown_requested:
            try:
                # Full scan if due
                if self._should_scan():
                    self.scan_and_trade()

                # Monitor positions
                self.monitor_positions()

                # Save state
                self._save_state()

                # Status log
                status = self.executor.get_paper_status()
                pnl = status.get("unrealized_pnl", 0)
                pnl_pct = status.get("unrealized_pnl_pct", 0)
                logger.info(
                    f"Monitor | Positions: {len(self.executor.positions)} | "
                    f"Unrealized: ${pnl:+.2f} ({pnl_pct:+.2f}%) | "
                    f"Daily PnL: ${self.risk.daily_stats.realized_pnl:+.2f}"
                )

            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt — shutting down...")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)

            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

        # Graceful shutdown: save final state
        logger.info("Saving final state before exit...")
        self._save_state()
        logger.info("Agent shutdown complete.")

    def show_status(self):
        """Print current agent status."""
        status = self.executor.get_paper_status()
        print(f"\n{'='*60}")
        print(f"  JudyAI WaveRider Trading Agent")
        print(f"{'='*60}")
        print(f"\nPaper Account:")
        print(f"  Starting Balance: ${status.get('starting_balance', 0):.2f}")
        print(f"  Current Value:    ${status.get('current_value', 0):.2f}")
        print(f"  Unrealized PnL:   ${status.get('unrealized_pnl', 0):+.2f} "
              f"({status.get('unrealized_pnl_pct', 0):+.2f}%)")
        print(f"  Total Trades:     {status.get('total_trades', 0)}")
        print(f"\n{self.risk.summary()}")
        print(f"\n{self.executor.summary()}")
        print()


def main():
    parser = argparse.ArgumentParser(description="JudyAI WaveRider Trading Agent")
    parser.add_argument("--single-scan", action="store_true", help="Run one scan and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show signals and risk decisions without executing")
    parser.add_argument("--status", action="store_true", help="Show status and exit")
    parser.add_argument("--monitor", action="store_true", help="Monitor positions only")
    parser.add_argument("--review-positions", action="store_true", help="AI review of open positions (dry-run)")
    parser.add_argument("--close-all", action="store_true", help="Close all positions")
    parser.add_argument("--reset", action="store_true", help="Reset paper balance")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "agent.log" if LOG_DIR.exists() else "/tmp/agent.log"),
        ],
    )

    agent = KrakenTradingAgent()

    if args.status:
        agent.show_status()
        return

    if args.close_all:
        events = agent.executor.close_all(agent.data, "MANUAL_CLOSE")
        for e in events:
            agent.risk.register_close(e["pair"], e["pnl"], e["reason"])
            print(f"Closed {e['pair']}: PnL ${e['pnl']:+.2f}")
        agent._save_state()
        return

    if args.reset:
        import subprocess
        subprocess.run(["kraken", "paper", "reset", "--balance", "100000", "--yes"])
        print("Paper balance reset to $100,000")
        return

    if args.dry_run:
        # Show what the agent would do without executing
        print(f"\n{'='*60}")
        print(f"  DRY RUN — Signals & Risk Decisions (no execution)")
        ai_tag = f"AI: {AI_MODEL}" if AI_ENABLED else "AI: disabled"
        print(f"  {ai_tag}")
        print(f"{'='*60}\n")
        signals = agent.strategy.scan_all(agent.data)
        if not signals:
            print("No signals found.")
        else:
            # AI review if enabled
            if agent.ai_enabled:
                print(f"  Running Opus AI analysis on {len(signals)} signals...\n")
                signals = agent._ai_review_signals(signals)
                if not signals:
                    print("  All signals rejected by AI analysis.")
                else:
                    for s in signals:
                        allowed, reason = agent.risk.can_trade(s.pair, s.direction)
                        scale = agent.risk.get_position_scale(s.direction) * s.position_scale
                        status = "WOULD EXECUTE" if allowed else f"BLOCKED: {reason}"
                        ai_info = f"AI:{s.ai_verdict}({s.ai_confidence})" if s.ai_verdict else "no-AI"
                        print(f"  {s.pair:12s} {s.direction:5s} | {s.source:18s} | "
                              f"Entry: ${s.entry_price:.2f} | SL: ${s.sl_price:.2f} | "
                              f"TP1: ${s.tp1_price:.2f} | Scale: {scale:.0%} | "
                              f"Ensemble: {s.ensemble_score:.0f} | {ai_info} | {status}")
                        if s.ai_reasoning:
                            print(f"  {'':12s}       | AI: {s.ai_reasoning}")
            else:
                for s in signals:
                    allowed, reason = agent.risk.can_trade(s.pair, s.direction)
                    scale = agent.risk.get_position_scale(s.direction) * s.position_scale
                    status = "WOULD EXECUTE" if allowed else f"BLOCKED: {reason}"
                    print(f"  {s.pair:12s} {s.direction:5s} | {s.source:18s} | "
                          f"Entry: ${s.entry_price:.2f} | SL: ${s.sl_price:.2f} | "
                          f"TP1: ${s.tp1_price:.2f} | Scale: {scale:.0%} | {status}")
        print(f"\n  Open positions: {len(agent.executor.positions)}")
        print(f"  Risk: {agent.risk.summary()}")
        return

    if args.review_positions:
        # AI position review dry-run
        print(f"\n{'='*60}")
        print(f"  AI Position Review (MiniMax only)")
        print(f"{'='*60}\n")
        if not agent.executor.positions:
            print("  No open positions to review.")
        else:
            reviews = agent._ai_review_positions()
            if not reviews:
                print("  AI review returned no results.")
            else:
                for r in reviews:
                    pos = agent.executor.positions.get(r.pair)
                    if not pos:
                        continue
                    icon = {"HOLD": "B", "TIGHTEN_SL": "Y", "TAKE_PROFIT": "G", "EXIT_NOW": "R"}.get(r.action, "?")
                    sl_info = f" -> new SL: ${r.new_sl:.2f}" if r.new_sl else ""
                    print(f"  [{icon}] {r.pair:12s} {pos.direction:5s} | {r.action:12s} | "
                          f"Urgency: {r.urgency}/100{sl_info}")
                    print(f"    {'':12s}         | {r.reasoning}")
                    if r.market_changed:
                        print(f"    {'':12s}         | Market structure changed")
        print(f"\n  Open positions: {len(agent.executor.positions)}")
        return

    if args.single_scan:
        agent.scan_and_trade()
        agent.monitor_positions()
        agent._save_state()
        agent.show_status()
        return

    if args.monitor:
        agent.monitor_positions()
        agent._save_state()
        agent.show_status()
        return

    # Main loop
    asyncio.run(agent.run_loop())


if __name__ == "__main__":
    main()
