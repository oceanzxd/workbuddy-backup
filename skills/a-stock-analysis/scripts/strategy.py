"""
Simple Backtesting Engine with Event-Driven Architecture.
Works with technical scores from analyzer.py for signal generation.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Types & Protocols
# ------------------------------------------------------------------

class OHLCVRow(Protocol):
    """Minimal protocol for a single bar of market data."""

    @property
    def date(self) -> Any: ...
    @property
    def open(self) -> float: ...
    @property
    def high(self) -> float: ...
    @property
    def low(self) -> float: ...
    @property
    def close(self) -> float: ...
    @property
    def volume(self) -> float: ...
    # analyzer.py tech_score field
    @property
    def tech_score(self) -> float: ...


@dataclass
class Trade:
    """A completed trade with entry and exit details."""
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    shares: float
    return_pct: float           # pct return on this trade
    pnl: float                 # absolute PnL


@dataclass
class Position:
    """An open position in the portfolio."""
    entry_date: str
    entry_price: float
    shares: float
    current_value: float = 0.0


# ------------------------------------------------------------------
# SimpleBacktestEngine
# ------------------------------------------------------------------

class SimpleBacktestEngine:
    """
    Lightweight event-driven backtesting engine for A-stock signals.

    Entry rule:  tech_score >= ENTRY_THRESHOLD (default 5)
    Exit rule:   tech_score <= EXIT_THRESHOLD (default -3)
                 OR profit target reached (default 2x / 100%)
    Portfolio:   equal weight, max MAX_POSITIONS simultaneous positions

    Args:
        data: list[dict] or pandas DataFrame with OHLCV + tech_score columns.
        entry_threshold: tech_score >= value -> generate BUY signal.
        exit_threshold:  tech_score <= value -> generate SELL signal.
        profit_target:  fractional, e.g. 1.0 means exit when price doubles.
        max_positions:  max simultaneous open positions (default 5).
        initial_capital: starting cash (default 1_000_000).
    """

    ENTRY_THRESHOLD: float = 5.0
    EXIT_THRESHOLD: float = -3.0

    def __init__(
        self,
        data: list[dict] | Any,
        entry_threshold: float = ENTRY_THRESHOLD,
        exit_threshold: float = EXIT_THRESHOLD,
        profit_target: float = 1.0,              # 2x = 100% gain
        max_positions: int = 5,
        initial_capital: float = 1_000_000.0,
    ):
        self._raw_data: list[dict] = self._normalize_data(data)
        self._bar_len = len(self._raw_data)

        # thresholds
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.profit_target = profit_target
        self.max_positions = max_positions

        # portfolio state
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}    # keyed by a symbol/bar id
        self.trades: list[Trade] = []

        # equity curve
        self.equity_curve: list[tuple[str, float]] = []

    # -------- public API --------

    def run(self) -> dict:
        """Execute the full backtest and return a report dict."""
        self._reset()
        for i, bar in enumerate(self._raw_data):
            event = self._make_event(bar, i)
            self._on_event(event)
            self._record_equity(bar)

        # Force-close any remaining positions at the last bar
        self._close_all_at_end()

        return self._build_report()

    # -------- internal: data normalisation --------

    @staticmethod
    def _normalize_data(data: list[dict] | Any) -> list[dict]:
        """Accept list[dict] or DataFrame-like, return list[dict]."""
        if hasattr(data, "to_dict"):
            return data.to_dict(orient="records")
        if isinstance(data, list):
            return data
        raise TypeError("data must be list[dict] or DataFrame-like object")

    def _make_event(self, bar: dict, index: int) -> dict:
        """Wrap a bar into a normalised event dict."""
        return {
            "index": index,
            "date": str(bar.get("date", "")),
            "open": float(bar.get("open", 0)),
            "high": float(bar.get("high", 0)),
            "low": float(bar.get("low", 0)),
            "close": float(bar.get("close", 0)),
            "volume": float(bar.get("volume", 0)),
            "tech_score": float(bar.get("tech_score", 0)),
        }

    # -------- internal: event loop --------

    def _on_event(self, event: dict) -> None:
        """Process a single bar event: check exits, then check entries."""
        current_price = event["close"]
        tech = event["tech_score"]
        date_str = event["date"]

        # 1. Check exit conditions for open positions
        keys_to_close = []
        for sym, pos in self.positions.items():
            # Profit target exit
            if current_price >= pos.entry_price * (1 + self.profit_target):
                keys_to_close.append(sym)
                continue
            # Tech-score exit (evaluate on current bar)
            if tech <= self.exit_threshold:
                keys_to_close.append(sym)

        for sym in keys_to_close:
            pos = self.positions.pop(sym)
            pnl = (current_price - pos.entry_price) * pos.shares
            ret_pct = (current_price - pos.entry_price) / pos.entry_price
            trade = Trade(
                entry_date=pos.entry_date,
                entry_price=pos.entry_price,
                exit_date=date_str,
                exit_price=current_price,
                shares=pos.shares,
                return_pct=ret_pct * 100,
                pnl=pnl,
            )
            self.trades.append(trade)
            self.cash += current_price * pos.shares

        # 2. Check entry condition (only if we have room)
        if tech >= self.entry_threshold and len(self.positions) < self.max_positions:
            slot = self.cash / (self.max_positions - len(self.positions))
            shares = slot / current_price if current_price > 0 else 0
            if shares > 0:
                sym_key = f"pos_{event['index']}"
                self.positions[sym_key] = Position(
                    entry_date=date_str,
                    entry_price=current_price,
                    shares=shares,
                    current_value=slot,
                )
                self.cash -= slot

    def _close_all_at_end(self) -> None:
        """Liquidate remaining positions using the last bar's close price."""
        if not self.positions or self.bar_len == 0:
            return
        last_bar = self._raw_data[-1]
        price = float(last_bar.get("close", 0))
        date_str = str(last_bar.get("date", ""))
        for sym, pos in list(self.positions.items()):
            pnl = (price - pos.entry_price) * pos.shares
            ret_pct = (price - pos.entry_price) / pos.entry_price
            self.trades.append(
                Trade(
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    exit_date=date_str,
                    exit_price=price,
                    shares=pos.shares,
                    return_pct=ret_pct * 100,
                    pnl=pnl,
                )
            )
            self.cash += price * pos.shares
        self.positions.clear()

    def _record_equity(self, bar: dict) -> None:
        """Append (date, total_equity) to equity curve."""
        pos_value = sum(
            float(bar.get("close", 0)) * p.shares for p in self.positions.values()
        )
        self.equity_curve.append((str(bar.get("date", "")), self.cash + pos_value))

    # -------- report building --------

    def _build_report(self) -> dict:
        """Build and return the backtest_report dict."""
        total_return = (self.cash - self.initial_capital) / self.initial_capital
        # Approximate number of years from date range
        years = self._estimate_years()
        annual_return = (
            (1 + total_return) ** (1.0 / years) - 1.0 if years > 0 else total_return
        )
        max_dd = self._max_drawdown()
        win_rate = self._win_rate()
        sharpe = self._sharpe_ratio(years)

        return {
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "max_dd": round(max_dd, 6),
            "win_rate": round(win_rate, 6),
            "sharpe": round(sharpe, 6),
            "total_trades": len(self.trades),
            "detailed_trades": [
                {
                    "entry_date": t.entry_date,
                    "entry_price": t.entry_price,
                    "exit_date": t.exit_date,
                    "exit_price": t.exit_price,
                    "shares": round(t.shares, 2),
                    "return_pct": round(t.return_pct, 4),
                    "pnl": round(t.pnl, 2),
                }
                for t in self.trades
            ],
            "equity_curve": self.equity_curve,
        }

    # -------- helpers --------

    @property
    def bar_len(self) -> int:
        return self._bar_len

    def _estimate_years(self) -> float:
        """Rough year estimate from data length (assume daily bars)."""
        return max(self._bar_len / 252.0, 0.01)

    def _max_drawdown(self) -> float:
        """Peak-to-trough max drawdown on equity curve."""
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0][1]
        max_dd = 0.0
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _win_rate(self) -> float:
        """Fraction of trades with positive PnL."""
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return wins / len(self.trades)

    def _sharpe_ratio(self, years: float) -> float:
        """Annualised Sharpe ratio using daily returns (risk-free ≈ 0)."""
        if len(self.equity_curve) < 2:
            return 0.0
        returns: list[float] = []
        prev_eq = self.equity_curve[0][1]
        for _, eq in self.equity_curve[1:]:
            if prev_eq > 0:
                returns.append((eq - prev_eq) / prev_eq)
            prev_eq = eq
        if not returns:
            return 0.0
        n = len(returns)
        mean_r = sum(returns) / n
        variance = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
        std_r = math.sqrt(variance) if variance > 0 else 1e-9
        daily_sharpe = mean_r / std_r
        # annualise
        return daily_sharpe * math.sqrt(252)

    def _reset(self) -> None:
        """Reset engine to initial state (idempotent for re-runs)."""
        self.cash = self.initial_capital
        self.positions.clear()
        self.trades.clear()
        self.equity_curve.clear()


# ------------------------------------------------------------------
# Convenience helpers
# ------------------------------------------------------------------

def run_backtest(data, **kwargs) -> dict:
    """
    Quick one-liner backtest.

    Args:
        data: list[dict] or DataFrame with OHLCV + tech_score.
        **kwargs: forwarded to SimpleBacktestEngine.
    Returns:
        backtest_report dict.
    """
    engine = SimpleBacktestEngine(data, **kwargs)
    return engine.run()


def format_trade_log(report: dict) -> str:
    """Render a human-readable trade log from a report dict."""
    lines = ["=== BACKTEST REPORT ===", ""]
    lines.append(f"Total Return   : {report['total_return']:.2%}")
    lines.append(f"Annual Return  : {report['annual_return']:.2%}")
    lines.append(f"Max Drawdown   : {report['max_dd']:.2%}")
    lines.append(f"Win Rate       : {report['win_rate']:.2%}")
    lines.append(f"Sharpe Ratio   : {report['sharpe']:.4f}")
    lines.append(f"Total Trades   : {report['total_trades']}")
    lines.append("")
    lines.append("--- TRADE LOG ---")
    lines.append(f"{'#':<4} {'Entry':<12} {'Entry Px':>10} "
                 f"{'Exit':<12} {'Exit Px':>10} {'Shares':>10} "
                 f"{'Ret%':>8} {'PnL':>12}")
    for i, t in enumerate(report.get("detailed_trades", []), 1):
        lines.append(
            f"{i:<4} {t['entry_date']:<12} {t['entry_price']:>10.2f} "
            f"{t['exit_date']:<12} {t['exit_price']:>10.2f} "
            f"{t['shares']:>10.2f} {t['return_pct']:>7.2f}% "
            f"{t['pnl']:>12.2f}"
        )
    return "\n".join(lines)
