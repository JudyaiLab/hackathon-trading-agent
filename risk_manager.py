"""
RiskManager — Portfolio-level risk controls

5-layer risk control system for portfolio management.
Focused on spot trading via Kraken.

Rules:
  - Max 5% per trade position
  - 3% daily loss → stop trading
  - 10% total drawdown → emergency close all
  - 5 consecutive losses → pause
  - 3 consecutive losses → reduce size to 50%

Batch Take-Profit (Batch Take-Profit):
  - TP1: close 25% of position, SL → entry (breakeven)
  - TP2: close 25% of position, SL → TP1 price
  - TP3: close remaining 50% (full exit)
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import RISK

logger = logging.getLogger(__name__)


# ── Batch Take-Profit (Batch Take-Profit) ─────────────────────────────

@dataclass(frozen=True)
class BatchTPLevel:
    """Single take-profit level with close percentage."""
    price: float
    close_pct: float  # 0.0–1.0, fraction of ORIGINAL position to close


@dataclass(frozen=True)
class BatchTPLevels:
    """Immutable batch take-profit plan.

    TP1: close 25%, SL → entry (breakeven)
    TP2: close 25%, SL → TP1
    TP3: close remaining 50% (full exit)
    """
    tp1: BatchTPLevel
    tp2: BatchTPLevel
    tp3: BatchTPLevel

    @property
    def levels(self) -> tuple[BatchTPLevel, ...]:
        return (self.tp1, self.tp2, self.tp3)


def calculate_batch_tp_levels(
    tp1_price: float,
    tp2_price: float,
    tp3_price: float,
    tp1_close_pct: float = 0.25,
    tp2_close_pct: float = 0.25,
    tp3_close_pct: float = 0.50,
) -> BatchTPLevels:
    """Calculate batch take-profit levels with close percentages.

    Args:
        tp1_price: First take-profit price level.
        tp2_price: Second take-profit price level.
        tp3_price: Third take-profit price level.
        tp1_close_pct: Fraction of original position to close at TP1 (default 25%).
        tp2_close_pct: Fraction of original position to close at TP2 (default 25%).
        tp3_close_pct: Fraction of original position to close at TP3 (default 50%).

    Returns:
        Frozen BatchTPLevels dataclass.

    Raises:
        ValueError: If close percentages don't sum to 1.0.
    """
    total = tp1_close_pct + tp2_close_pct + tp3_close_pct
    if abs(total - 1.0) > 1e-9:
        raise ValueError(
            f"Close percentages must sum to 1.0, got {total:.4f} "
            f"({tp1_close_pct} + {tp2_close_pct} + {tp3_close_pct})"
        )

    return BatchTPLevels(
        tp1=BatchTPLevel(price=tp1_price, close_pct=tp1_close_pct),
        tp2=BatchTPLevel(price=tp2_price, close_pct=tp2_close_pct),
        tp3=BatchTPLevel(price=tp3_price, close_pct=tp3_close_pct),
    )


@dataclass
class DailyStats:
    date: str = ""
    trades_count: int = 0
    realized_pnl: float = 0.0
    is_stopped: bool = False
    stop_reason: str = ""


class RiskManager:
    def __init__(self, initial_capital: float) -> None:
        self.initial_capital = initial_capital
        self.daily_stats = DailyStats(date=self._today())
        self.total_realized_pnl: float = 0.0
        self.open_position_count: int = 0
        self.consecutive_losses: int = 0
        self.position_scale: float = 1.0
        self.peak_balance: float = initial_capital
        self.pair_consecutive_losses: dict[str, int] = {}
        self.pair_cooldown: dict[str, int] = {}  # pair -> scans remaining

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_new_day(self) -> None:
        today = self._today()
        if self.daily_stats.date != today:
            logger.info(f"Day reset: {self.daily_stats.date} -> {today}")
            self.daily_stats = DailyStats(date=today)

    @property
    def current_balance(self) -> float:
        return self.initial_capital + self.total_realized_pnl

    @property
    def drawdown_pct(self) -> float:
        if self.peak_balance <= 0:
            return 0.0
        return max(0, (self.peak_balance - self.current_balance) / self.peak_balance * 100)

    def can_trade(self, pair: str, direction: str) -> tuple[bool, str]:
        """Check if a new trade is allowed.

        Returns:
            (allowed, reason)
        """
        self._check_new_day()

        # Daily stop
        if self.daily_stats.is_stopped:
            return False, f"Trading stopped today: {self.daily_stats.stop_reason}"

        # Max daily trades
        if self.daily_stats.trades_count >= RISK.max_daily_trades:
            return False, f"Daily trade limit reached ({RISK.max_daily_trades})"

        # Max concurrent positions
        if self.open_position_count >= RISK.max_concurrent_positions:
            return False, f"Max positions reached ({RISK.max_concurrent_positions})"

        # Daily loss limit
        max_daily_loss = self.initial_capital * (RISK.max_daily_loss_pct / 100)
        if self.daily_stats.realized_pnl < -max_daily_loss:
            self.daily_stats.is_stopped = True
            self.daily_stats.stop_reason = (
                f"Daily loss ${abs(self.daily_stats.realized_pnl):.2f} "
                f"> limit ${max_daily_loss:.2f}"
            )
            return False, self.daily_stats.stop_reason

        # Total drawdown
        max_total_loss = self.initial_capital * (RISK.emergency_stop_pct / 100)
        if self.total_realized_pnl < -max_total_loss:
            return False, (
                f"Total drawdown ${abs(self.total_realized_pnl):.2f} "
                f"> emergency limit ${max_total_loss:.2f}"
            )

        # Consecutive loss pause (global)
        if self.consecutive_losses >= RISK.consecutive_loss_pause:
            return False, (
                f"Consecutive losses ({self.consecutive_losses}) "
                f">= pause threshold ({RISK.consecutive_loss_pause})"
            )

        # Per-pair cooldown (L7: pair-level risk throttling)
        if pair in self.pair_cooldown and self.pair_cooldown[pair] > 0:
            return False, (
                f"Pair {pair} in cooldown ({self.pair_cooldown[pair]} scans remaining) "
                f"after {self.pair_consecutive_losses.get(pair, 0)} consecutive losses"
            )

        return True, "OK"

    def register_open(self, pair: str, direction: str) -> None:
        """Record that a position was opened."""
        self.daily_stats.trades_count += 1
        self.open_position_count += 1
        logger.info(
            f"Position opened: {pair} {direction} | "
            f"Daily trades: {self.daily_stats.trades_count} | "
            f"Open positions: {self.open_position_count}"
        )

    def register_close(self, pair: str, pnl: float, reason: str) -> None:
        """Record that a position was closed."""
        self.open_position_count = max(0, self.open_position_count - 1)
        self.daily_stats.realized_pnl += pnl
        self.total_realized_pnl += pnl

        # Update peak balance
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance

        # Consecutive loss tracking (global)
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 3:
                self.position_scale = RISK.consecutive_loss_scale
                logger.warning(
                    f"Consecutive losses: {self.consecutive_losses}, "
                    f"position scale reduced to {self.position_scale:.0%}"
                )
        else:
            if self.consecutive_losses > 0:
                logger.info(f"Losing streak ended at {self.consecutive_losses}")
            self.consecutive_losses = 0
            self.position_scale = 1.0

        # Per-pair consecutive loss tracking (L7)
        if pnl < 0:
            self.pair_consecutive_losses[pair] = self.pair_consecutive_losses.get(pair, 0) + 1
            if self.pair_consecutive_losses[pair] >= 2:
                self.pair_cooldown[pair] = 3  # skip 3 scan cycles
                logger.warning(
                    f"Pair {pair}: {self.pair_consecutive_losses[pair]} consecutive losses "
                    f"→ cooldown for 3 scans"
                )
        else:
            self.pair_consecutive_losses[pair] = 0
            self.pair_cooldown.pop(pair, None)

        # Check daily stop
        max_daily_loss = self.initial_capital * (RISK.max_daily_loss_pct / 100)
        if self.daily_stats.realized_pnl < -max_daily_loss:
            self.daily_stats.is_stopped = True
            self.daily_stats.stop_reason = "Daily loss limit triggered"
            logger.warning(f"DAILY STOP: PnL ${self.daily_stats.realized_pnl:.2f}")

        logger.info(
            f"Position closed: {pair} PnL=${pnl:+.2f} ({reason}) | "
            f"Daily PnL: ${self.daily_stats.realized_pnl:+.2f} | "
            f"Total PnL: ${self.total_realized_pnl:+.2f} | "
            f"Streak: {self.consecutive_losses}"
        )

    def check_emergency(self) -> bool:
        """Check if emergency close-all is needed."""
        max_loss = self.initial_capital * (RISK.emergency_stop_pct / 100)
        return self.total_realized_pnl < -max_loss

    def get_position_scale(self, direction: str) -> float:
        """Get the position size multiplier for a new trade."""
        scale = self.position_scale
        if direction == "short":
            scale *= RISK.short_position_scale
        return scale

    def summary(self) -> str:
        lines = [
            "=== Risk Manager Status ===",
            f"Balance: ${self.current_balance:.2f} (peak: ${self.peak_balance:.2f})",
            f"Drawdown: {self.drawdown_pct:.1f}%",
            f"Daily PnL: ${self.daily_stats.realized_pnl:+.2f}",
            f"Total PnL: ${self.total_realized_pnl:+.2f}",
            f"Open positions: {self.open_position_count}/{RISK.max_concurrent_positions}",
            f"Daily trades: {self.daily_stats.trades_count}/{RISK.max_daily_trades}",
            f"Consecutive losses: {self.consecutive_losses}",
            f"Position scale: {self.position_scale:.0%}",
            f"Stopped: {'YES - ' + self.daily_stats.stop_reason if self.daily_stats.is_stopped else 'No'}",
        ]
        return "\n".join(lines)
