"""
KrakenExecutor — Paper trading execution via Kraken CLI

Kraken paper trading is SPOT-ONLY (no margin/short selling).
Strategy:
  - Long signals → BUY asset via Kraken CLI, SELL when SL/TP hits
  - Short signals → Internal paper tracking (no Kraken CLI call)
    Tracks entry/SL/TP internally, calculates PnL on close.
    This allows the agent to profit in bearish/ranging markets.

For hackathon PnL ranking:
  1. Executing high-confidence long signals (Kraken CLI)
  2. Executing short signals via internal paper tracking
  3. Both contribute to total PnL for ERC-8004 agent card
"""
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from config import PAIR_MAP, RISK
from kraken_cli import run_kraken
from strategies import TradeSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Position:
    """Immutable position record.  Trailing stop updates produce new instances
    via ``dataclasses.replace()`` so no hidden mutation occurs.

    Batch take-profit tracking:
      - ``remaining_pct``: fraction of original volume still open (1.0 → 0.0)
      - ``tp1_hit`` / ``tp2_hit``: whether each TP level has been reached
      - At TP1: close 25%, SL → entry (breakeven)
      - At TP2: close 25%, SL → TP1
      - At TP3: close remaining 50%
    """
    pair: str               # Standard pair name "BTC/USDT"
    cli_pair: str           # Kraken CLI pair "BTCUSD"
    direction: str          # "long" (Kraken spot) or "short" (internal paper)
    entry_price: float
    volume: float           # ORIGINAL position size in base currency (never changes)
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""
    tp1_hit: bool = False   # Partial TP tracking
    tp2_hit: bool = False   # TP2 partial close tracking
    remaining_pct: float = 1.0  # Fraction of original position still open

    @property
    def active_volume(self) -> float:
        """Volume currently held (original volume * remaining fraction)."""
        return self.volume * self.remaining_pct


class KrakenExecutor:
    """Execute paper trades and monitor positions via Kraken CLI.

    Note: Kraken paper trading is spot-only. All positions are long
    (buy asset, sell later). Short signals from the strategy engine
    are used as exit indicators, not as trade entries.
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.positions: dict[str, Position] = {}  # pair -> Position
        self.trade_log: list[dict] = []

    def _run_kraken(self, args: list[str]) -> dict:
        """Execute kraken CLI command, return parsed JSON."""
        return run_kraken(args, timeout=self.timeout)

    def get_paper_status(self) -> dict:
        """Get paper trading account status."""
        return self._run_kraken(["paper", "status"])

    def get_paper_balance(self) -> dict:
        """Get paper trading balance (all holdings)."""
        return self._run_kraken(["paper", "balance"])

    def get_current_value(self) -> float:
        """Get current portfolio value."""
        status = self.get_paper_status()
        return float(status.get("current_value", 0))

    def get_starting_balance(self) -> float:
        """Get starting balance."""
        status = self.get_paper_status()
        return float(status.get("starting_balance", 100000))

    def get_unrealized_pnl(self) -> float:
        """Get unrealized PnL."""
        status = self.get_paper_status()
        return float(status.get("unrealized_pnl", 0))

    def execute_buy(self, cli_pair: str, volume: float) -> dict:
        """Execute paper buy order."""
        vol_str = f"{volume:.8f}"
        result = self._run_kraken(["paper", "buy", cli_pair, vol_str])
        if result:
            logger.info(f"BUY executed: {cli_pair} vol={vol_str} | {result.get('action', '')}")
        return result

    def execute_sell(self, cli_pair: str, volume: float) -> dict:
        """Execute paper sell order (must own the asset)."""
        vol_str = f"{volume:.8f}"
        result = self._run_kraken(["paper", "sell", cli_pair, vol_str])
        if result:
            logger.info(f"SELL executed: {cli_pair} vol={vol_str} | {result.get('action', '')}")
        return result

    def execute_signal(self, signal: TradeSignal, portfolio_value: float) -> bool:
        """Execute a trade signal.

        For spot-only paper trading:
          - Long signals: BUY the asset
          - Short signals: SKIP (used as exit indicators only)

        Args:
            signal: The trade signal to execute
            portfolio_value: Current portfolio value for sizing

        Returns:
            True if trade was placed successfully
        """
        # Short signals: internal paper tracking (no Kraken CLI call)
        if signal.direction == "short":
            # If we have an existing long, let check_short_exits() handle it
            if signal.pair in self.positions:
                pos = self.positions[signal.pair]
                if pos.direction == "long":
                    logger.info(f"Short signal → will close existing long for {signal.pair}")
                    return False  # Handled by check_short_exits()
                else:
                    logger.info(f"Already in short position for {signal.pair}, skipping")
                    return False

            return self._execute_internal_short(signal, portfolio_value)

        if signal.pair in self.positions:
            logger.info(f"Already in position for {signal.pair}, skipping")
            return False

        pair_info = PAIR_MAP.get(signal.pair)
        if not pair_info:
            logger.error(f"Unknown pair: {signal.pair}")
            return False

        cli_pair = pair_info["cli"]

        # Position sizing: risk-based
        sl_distance = abs(signal.entry_price - signal.sl_price)
        if sl_distance == 0:
            logger.warning(f"SL distance is 0 for {signal.pair}, skipping")
            return False

        # Risk amount = portfolio * risk_per_trade_pct * position_scale
        risk_amount = portfolio_value * (RISK.risk_per_trade_pct / 100) * signal.position_scale

        # Volume = risk_amount / SL_distance
        volume = risk_amount / sl_distance

        # Cap at max_position_pct of portfolio
        max_position_value = portfolio_value * (RISK.max_position_pct / 100)
        position_value = volume * signal.entry_price
        if position_value > max_position_value:
            volume = max_position_value / signal.entry_price
            logger.info(f"Position capped at {RISK.max_position_pct}% of portfolio")

        if volume <= 0:
            logger.warning(f"Calculated volume <= 0 for {signal.pair}")
            return False

        # Execute BUY
        result = self.execute_buy(cli_pair, volume)
        if not result:
            logger.error(f"Buy execution failed for {signal.pair}")
            return False

        # Track position (always long for spot)
        position = Position(
            pair=signal.pair,
            cli_pair=cli_pair,
            direction="long",
            entry_price=signal.entry_price,
            volume=volume,
            sl_price=signal.sl_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            tp3_price=signal.tp3_price,
            source=signal.source,
        )
        self.positions[signal.pair] = position

        self.trade_log.append({
            "action": "open",
            "pair": signal.pair,
            "direction": "long",
            "entry_price": signal.entry_price,
            "volume": volume,
            "sl": signal.sl_price,
            "tp1": signal.tp1_price,
            "source": signal.source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"Position opened: {signal.pair} LONG "
            f"vol={volume:.6f} entry={signal.entry_price} "
            f"SL={signal.sl_price} TP1={signal.tp1_price}"
        )
        return True

    def _execute_internal_short(self, signal: TradeSignal, portfolio_value: float) -> bool:
        """Execute a SHORT signal via internal paper tracking (no Kraken CLI).

        Position sizing uses the same risk-based logic as longs.
        PnL is calculated internally: profit when price drops, loss when price rises.
        """
        pair_info = PAIR_MAP.get(signal.pair)
        if not pair_info:
            logger.error(f"Unknown pair: {signal.pair}")
            return False

        # Position sizing (same as long)
        sl_distance = abs(signal.sl_price - signal.entry_price)
        if sl_distance == 0:
            logger.warning(f"SL distance is 0 for {signal.pair} SHORT, skipping")
            return False

        risk_amount = portfolio_value * (RISK.risk_per_trade_pct / 100) * signal.position_scale
        volume = risk_amount / sl_distance

        max_position_value = portfolio_value * (RISK.max_position_pct / 100)
        position_value = volume * signal.entry_price
        if position_value > max_position_value:
            volume = max_position_value / signal.entry_price

        if volume <= 0:
            return False

        # Track as internal paper short (no Kraken CLI call)
        position = Position(
            pair=signal.pair,
            cli_pair=pair_info["cli"],
            direction="short",
            entry_price=signal.entry_price,
            volume=volume,
            sl_price=signal.sl_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            tp3_price=signal.tp3_price,
            source=signal.source,
        )
        self.positions[signal.pair] = position

        self.trade_log.append({
            "action": "open",
            "pair": signal.pair,
            "direction": "short",
            "entry_price": signal.entry_price,
            "volume": volume,
            "sl": signal.sl_price,
            "tp1": signal.tp1_price,
            "source": signal.source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"Position opened: {signal.pair} SHORT (internal paper) "
            f"vol={volume:.6f} entry={signal.entry_price} "
            f"SL={signal.sl_price} TP1={signal.tp1_price}"
        )
        return True

    def _close_internal_short(self, pair: str, current_price: float, reason: str) -> float:
        """Close an internal paper short position (remaining portion).

        PnL = (entry - exit) * active_volume (profit when price drops).
        """
        pos = self.positions.get(pair)
        if not pos or pos.direction != "short":
            return 0.0

        close_volume = pos.active_volume
        pnl = (pos.entry_price - current_price) * close_volume
        del self.positions[pair]

        self.trade_log.append({
            "action": "close",
            "pair": pair,
            "direction": "short",
            "entry_price": pos.entry_price,
            "exit_price": current_price,
            "volume": close_volume,
            "remaining_pct_before": pos.remaining_pct,
            "pnl": pnl,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"Position closed: {pair} SHORT (internal paper) "
            f"entry={pos.entry_price} exit={current_price} "
            f"vol={close_volume:.6f} (was {pos.remaining_pct:.0%} remaining) "
            f"PnL=${pnl:+.2f} ({reason})"
        )
        return pnl

    def _partial_close_long(
        self, pair: str, current_price: float, close_pct: float, reason: str,
    ) -> float:
        """Partially close a LONG position via Kraken sell.

        Args:
            pair: Standard pair name.
            current_price: Current market price.
            close_pct: Fraction of ORIGINAL volume to close (e.g. 0.25).
            reason: Reason for partial close.

        Returns:
            Realized PnL for the closed portion.
        """
        pos = self.positions.get(pair)
        if not pos or pos.direction != "long":
            return 0.0

        close_volume = pos.volume * close_pct
        if close_volume <= 0:
            return 0.0

        # Verify Kraken holds enough
        balance = self.get_paper_balance()
        holdings = balance.get("balances", {})
        base_currency = pair.split("/")[0]
        held_amount = 0.0
        for key, val in holdings.items():
            if key.upper().startswith(base_currency.upper()) or base_currency.upper() in key.upper():
                held_amount = float(val.get("total", 0))
                break

        if held_amount < close_volume * 0.01:
            logger.warning(
                f"Partial close prevented: {pair} — need {close_volume:.6f} "
                f"but Kraken holds {held_amount:.6f}"
            )
            return 0.0

        result = self.execute_sell(pos.cli_pair, close_volume)
        if not result:
            logger.error(f"Partial sell failed: {pair} vol={close_volume:.6f}")
            return 0.0

        pnl = (current_price - pos.entry_price) * close_volume

        self.trade_log.append({
            "action": "partial_close",
            "pair": pair,
            "direction": "long",
            "entry_price": pos.entry_price,
            "exit_price": current_price,
            "volume": close_volume,
            "close_pct": close_pct,
            "remaining_pct": pos.remaining_pct - close_pct,
            "pnl": pnl,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"Partial close: {pair} LONG {close_pct:.0%} "
            f"vol={close_volume:.6f} at {current_price} "
            f"PnL=${pnl:+.2f} ({reason}) | "
            f"remaining={pos.remaining_pct - close_pct:.0%}"
        )
        return pnl

    def _partial_close_short(
        self, pair: str, current_price: float, close_pct: float, reason: str,
    ) -> float:
        """Partially close an internal paper SHORT position.

        Args:
            pair: Standard pair name.
            current_price: Current market price.
            close_pct: Fraction of ORIGINAL volume to close (e.g. 0.25).
            reason: Reason for partial close.

        Returns:
            Realized PnL for the closed portion.
        """
        pos = self.positions.get(pair)
        if not pos or pos.direction != "short":
            return 0.0

        close_volume = pos.volume * close_pct
        if close_volume <= 0:
            return 0.0

        pnl = (pos.entry_price - current_price) * close_volume

        self.trade_log.append({
            "action": "partial_close",
            "pair": pair,
            "direction": "short",
            "entry_price": pos.entry_price,
            "exit_price": current_price,
            "volume": close_volume,
            "close_pct": close_pct,
            "remaining_pct": pos.remaining_pct - close_pct,
            "pnl": pnl,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"Partial close: {pair} SHORT {close_pct:.0%} "
            f"vol={close_volume:.6f} at {current_price} "
            f"PnL=${pnl:+.2f} ({reason}) | "
            f"remaining={pos.remaining_pct - close_pct:.0%}"
        )
        return pnl

    def partial_close(
        self, pair: str, current_price: float, close_pct: float, reason: str,
    ) -> float:
        """Partially close a position (long or short).

        Updates remaining_pct on the Position via immutable replace().

        Returns:
            Realized PnL for the closed portion.
        """
        pos = self.positions.get(pair)
        if not pos:
            return 0.0

        if pos.direction == "long":
            pnl = self._partial_close_long(pair, current_price, close_pct, reason)
        else:
            pnl = self._partial_close_short(pair, current_price, close_pct, reason)

        # Update remaining percentage (immutable)
        if pair in self.positions:
            new_remaining = pos.remaining_pct - close_pct
            self.positions[pair] = replace(pos, remaining_pct=max(0.0, new_remaining))

        return pnl

    def close_position(self, pair: str, current_price: float, reason: str) -> float:
        """Close a position (long via Kraken sell, short via internal paper).

        Returns:
            Realized PnL in USD (0.0 if failed)
        """
        pos = self.positions.get(pair)
        if not pos:
            logger.warning(f"No position found for {pair}")
            return 0.0

        # Short positions: close via internal paper tracking
        if pos.direction == "short":
            return self._close_internal_short(pair, current_price, reason)

        # Long positions: sell via Kraken CLI (use active_volume for remaining)
        sell_volume = pos.active_volume
        if sell_volume <= 0:
            logger.warning(
                f"Zero volume for {pair} (remaining_pct={pos.remaining_pct}). "
                f"Removing stale position."
            )
            del self.positions[pair]
            return 0.0
        balance = self.get_paper_balance()
        holdings = balance.get("balances", {})
        base_currency = pair.split("/")[0]
        held_amount = 0.0
        for key, val in holdings.items():
            if key.upper().startswith(base_currency.upper()) or base_currency.upper() in key.upper():
                held_amount = float(val.get("total", 0))
                break
        if held_amount < sell_volume * 0.01:
            logger.warning(
                f"Phantom close prevented: {pair} — expected vol={sell_volume:.6f} "
                f"but Kraken holds {held_amount:.6f}. Removing stale position."
            )
            del self.positions[pair]
            return 0.0

        result = self.execute_sell(pos.cli_pair, sell_volume)
        if not result:
            logger.error(f"Failed to sell {pair} vol={sell_volume} — position kept, will retry")
            return 0.0

        pnl = (current_price - pos.entry_price) * sell_volume
        del self.positions[pair]

        self.trade_log.append({
            "action": "close",
            "pair": pair,
            "direction": "long",
            "entry_price": pos.entry_price,
            "exit_price": current_price,
            "volume": sell_volume,
            "remaining_pct_before": pos.remaining_pct,
            "pnl": pnl,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"Position closed: {pair} LONG "
            f"entry={pos.entry_price} exit={current_price} "
            f"vol={sell_volume:.6f} (was {pos.remaining_pct:.0%} remaining) "
            f"PnL=${pnl:+.2f} ({reason})"
        )
        return pnl

    def check_sl_tp(self, data_adapter) -> list[dict]:
        """Check all positions for SL/TP hits with batch take-profit.

        Batch TP (Batch Take-Profit):
          - TP1: partial close 25%, SL → entry (breakeven)
          - TP2: partial close 25%, SL → TP1
          - TP3: close remaining 50%

        SL logic is PRESERVED exactly:
          - TP1 hit → SL moves to entry (breakeven)
          - TP2 hit → SL moves to TP1 price

        Uses immutable Position updates — trailing stop state changes
        produce new Position instances via ``replace()``.

        Args:
            data_adapter: KrakenDataAdapter for current prices

        Returns:
            List of close/partial-close events
        """
        if not self.positions:
            return []

        events = []
        pairs_to_close: list[tuple[str, float, str]] = []
        pairs_to_partial: list[tuple[str, float, float, str]] = []  # pair, price, pct, reason
        updates: dict[str, Position] = {}

        tickers = data_adapter.get_multi_ticker(list(self.positions.keys()))

        for pair, pos in self.positions.items():
            ticker = tickers.get(pair)
            if not ticker:
                continue

            price = ticker["last"]
            updated = pos  # start from current (immutable) state

            if pos.direction == "long":
                # LONG: SL when price drops, TP when price rises
                if price <= updated.sl_price:
                    pairs_to_close.append((pair, price, "SL_HIT"))
                    continue

                # TP1: partial close 25%, SL → breakeven
                if not updated.tp1_hit and price >= updated.tp1_price:
                    updated = replace(updated, tp1_hit=True, sl_price=updated.entry_price)
                    pairs_to_partial.append((pair, price, 0.25, "TP1_PARTIAL"))
                    logger.info(
                        f"{pair} TP1 hit! Partial close 25%, "
                        f"SL moved to breakeven {updated.entry_price}"
                    )

                # TP2: partial close 25%, SL → TP1
                if updated.tp1_hit and not updated.tp2_hit and price >= updated.tp2_price:
                    updated = replace(updated, tp2_hit=True, sl_price=updated.tp1_price)
                    pairs_to_partial.append((pair, price, 0.25, "TP2_PARTIAL"))
                    logger.info(
                        f"{pair} TP2 hit! Partial close 25%, "
                        f"SL tightened to {updated.sl_price}"
                    )

                # TP3: close remaining position
                if price >= updated.tp3_price:
                    pairs_to_close.append((pair, price, "TP3_HIT"))
                    continue

            else:
                # SHORT (internal paper): SL when price rises, TP when price drops
                if price >= updated.sl_price:
                    pairs_to_close.append((pair, price, "SL_HIT"))
                    continue

                # TP1: partial close 25%, SL → breakeven
                if not updated.tp1_hit and price <= updated.tp1_price:
                    updated = replace(updated, tp1_hit=True, sl_price=updated.entry_price)
                    pairs_to_partial.append((pair, price, 0.25, "TP1_PARTIAL"))
                    logger.info(
                        f"{pair} SHORT TP1 hit! Partial close 25%, "
                        f"SL moved to breakeven {updated.entry_price}"
                    )

                # TP2: partial close 25%, SL → TP1
                if updated.tp1_hit and not updated.tp2_hit and price <= updated.tp2_price:
                    updated = replace(updated, tp2_hit=True, sl_price=updated.tp1_price)
                    pairs_to_partial.append((pair, price, 0.25, "TP2_PARTIAL"))
                    logger.info(
                        f"{pair} SHORT TP2 hit! Partial close 25%, "
                        f"SL tightened to {updated.sl_price}"
                    )

                # TP3: close remaining position
                if price <= updated.tp3_price:
                    pairs_to_close.append((pair, price, "TP3_HIT"))
                    continue

            if updated is not pos:
                updates[pair] = updated

        # Apply immutable position updates first (before partial closes modify remaining_pct)
        self.positions.update(updates)

        # Execute partial closes
        for pair, price, close_pct, reason in pairs_to_partial:
            pnl = self.partial_close(pair, price, close_pct, reason)
            events.append({
                "pair": pair, "price": price, "reason": reason,
                "pnl": pnl, "close_pct": close_pct,
            })

        # Full closes (SL or TP3 — closes whatever remains)
        for pair, price, reason in pairs_to_close:
            pnl = self.close_position(pair, price, reason)
            if pair not in self.positions:  # Only log if actually closed
                events.append({"pair": pair, "price": price, "reason": reason, "pnl": pnl})

        return events

    def check_short_exits(self, short_signals: list[TradeSignal], data_adapter) -> list[dict]:
        """Use short signals to exit existing long positions.

        When the strategy generates a short signal for a pair we're long on,
        close the long position (the market is turning bearish).

        Returns:
            List of close events
        """
        events = []
        tickers = data_adapter.get_multi_ticker(list(self.positions.keys()))

        for signal in short_signals:
            if signal.direction != "short":
                continue
            if signal.pair not in self.positions:
                continue
            # Only close LONG positions — don't close existing shorts
            if self.positions[signal.pair].direction != "long":
                continue

            ticker = tickers.get(signal.pair)
            if not ticker:
                continue

            price = ticker["last"]
            logger.info(f"Short signal exit: closing {signal.pair} long at {price}")
            pnl = self.close_position(signal.pair, price, "SHORT_SIGNAL_EXIT")
            if signal.pair not in self.positions:  # Only log if actually closed
                events.append({"pair": signal.pair, "price": price, "reason": "SHORT_SIGNAL_EXIT", "pnl": pnl})

        return events

    def close_all(self, data_adapter, reason: str = "EMERGENCY") -> list[dict]:
        """Close all positions (emergency or EOD)."""
        events = []
        tickers = data_adapter.get_multi_ticker(list(self.positions.keys()))

        pairs = list(self.positions.keys())
        for pair in pairs:
            ticker = tickers.get(pair, {})
            price = ticker.get("last", self.positions[pair].entry_price)
            pnl = self.close_position(pair, price, reason)
            events.append({"pair": pair, "price": price, "reason": reason, "pnl": pnl})

        return events

    def summary(self) -> str:
        """Position summary string."""
        if not self.positions:
            return "No open positions"

        lines = [f"Open positions: {len(self.positions)}"]
        for pair, pos in self.positions.items():
            tp_status = "TP2+" if pos.tp2_hit else ("TP1+" if pos.tp1_hit else "")
            dir_label = pos.direction.upper()
            paper_tag = " (paper)" if pos.direction == "short" else ""
            remaining_tag = f" [{pos.remaining_pct:.0%}]" if pos.remaining_pct < 1.0 else ""
            lines.append(
                f"  {pair} {dir_label}{paper_tag} vol={pos.active_volume:.6f} "
                f"entry={pos.entry_price} SL={pos.sl_price} "
                f"{tp_status}{remaining_tag}"
            )
        return "\n".join(lines)
