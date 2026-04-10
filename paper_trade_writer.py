#!/usr/bin/env python3
"""
paper_trade_writer.py — Writes validated decisions to the paper trading SQLite.

DB: /home/oc-trading/.openclaw/paper_trading.db
Tables: accounts, positions, trades, snapshots

Uses WAL mode to avoid locking conflicts with the dashboard (read-only).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(
    os.environ.get(
        "PAPER_TRADING_DB", "/home/oc-trading/.openclaw/paper_trading.db"
    )
)

# Map TradingAgents tickers to paper trading symbols
# Paper trading uses BTCUSDT, ETHUSDT format; TradingAgents uses BTC-USD, ETH-USD
_TICKER_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "XRP-USD": "XRPUSDT",
    "BNB-USD": "BNBUSDT",
    "SOL-USD": "SOLUSDT",
    "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT",
    "TRX-USD": "TRXUSDT",
    "LINK-USD": "LINKUSDT",
    "TAO-USD": "TAOUSDT",
    "ZEC-USD": "ZECUSDT",
    "SUI20947-USD": "SUIUSDT",
}

# Default strategy for TradingAgents pipeline positions
STRATEGY_SIGNALS = "tradingagents_signals"
STRATEGY_WATCHLIST = "tradingagents_watchlist"
DEFAULT_INITIAL_BALANCE = 10000.0


def _normalize_symbol(ticker: str) -> str:
    """Convert TradingAgents ticker to paper trading symbol."""
    upper = ticker.upper()
    if upper in _TICKER_MAP:
        return _TICKER_MAP[upper]
    # Stocks stay as-is (AAPL, NVDA, etc.)
    return upper.replace("-USD", "USDT").replace("-", "")


def _get_conn() -> sqlite3.Connection:
    """Get a connection with WAL mode enabled."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_strategy(strategy: str, initial_balance: float = DEFAULT_INITIAL_BALANCE) -> None:
    """Create the strategy account if it doesn't exist."""
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT strategy FROM accounts WHERE strategy = ?", (strategy,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO accounts (strategy, balance, initial_balance, created_at) VALUES (?, ?, ?, ?)",
                (strategy, initial_balance, initial_balance, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            print(f"[writer] Created strategy account '{strategy}' with balance {initial_balance}")
    finally:
        conn.close()


def has_open_position(strategy: str, symbol: str) -> bool:
    """Check if there's already an open position for this symbol in this strategy."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE strategy = ? AND symbol = ? AND status = 'open'",
            (strategy, symbol),
        ).fetchone()
        return row["cnt"] > 0
    finally:
        conn.close()


def get_account_balance(strategy: str) -> float:
    """Get current cash balance for a strategy."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT balance FROM accounts WHERE strategy = ?", (strategy,)
        ).fetchone()
        return float(row["balance"]) if row else 0.0
    finally:
        conn.close()


def record_decision(
    decision: dict[str, Any],
    analysis_id: str = "",
    strategy: str = STRATEGY_WATCHLIST,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record a validated decision into the paper trading DB.

    Args:
        decision: Dict from soul_enforcer with action, ticker, entry_price,
                  stop_loss, take_profit, position_size_pct, etc.
        analysis_id: Reference to the analysis output file.
        strategy: Which strategy account to use.
        dry_run: If True, don't actually write.

    Returns:
        Dict with status, message, and details.
    """
    action = str(decision.get("action", "")).upper()
    ticker = str(decision.get("ticker", ""))
    symbol = _normalize_symbol(ticker)

    # Only BUY creates new positions
    if action not in ("BUY", "OVERWEIGHT"):
        return {
            "status": "skipped",
            "reason": f"Action '{action}' does not create a position",
            "symbol": symbol,
        }

    entry_price = float(decision.get("entry_price", 0))
    if entry_price <= 0:
        return {"status": "skipped", "reason": "No valid entry_price", "symbol": symbol}

    # Ensure strategy exists
    ensure_strategy(strategy)

    # Check for duplicate
    if has_open_position(strategy, symbol):
        return {
            "status": "skipped",
            "reason": f"Already has open position for {symbol} in {strategy}",
            "symbol": symbol,
        }

    # Calculate position
    balance = get_account_balance(strategy)
    pos_pct = float(decision.get("position_size_pct", 10))
    cost = balance * (pos_pct / 100.0)
    if cost <= 0 or cost > balance:
        return {
            "status": "skipped",
            "reason": f"Insufficient balance ({balance:.2f}) for {pos_pct}% position",
            "symbol": symbol,
        }

    quantity = cost / entry_price
    stop_loss = float(decision.get("stop_loss", 0)) or None
    take_profit = float(decision.get("take_profit", 0)) or None
    now = datetime.now(timezone.utc).isoformat()

    result = {
        "status": "recorded",
        "symbol": symbol,
        "strategy": strategy,
        "side": "long",
        "entry_price": entry_price,
        "quantity": quantity,
        "cost": cost,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "analysis_id": analysis_id,
    }

    if dry_run:
        result["status"] = "dry_run"
        print(f"[writer] DRY RUN: Would open {symbol} long @ {entry_price}, "
              f"qty={quantity:.6f}, cost={cost:.2f}, SL={stop_loss}, TP={take_profit}")
        return result

    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO positions
               (strategy, symbol, side, entry_price, quantity, cost_usdt,
                opened_at, status, stop_loss_price, take_profit_price)
               VALUES (?, ?, 'long', ?, ?, ?, ?, 'open', ?, ?)""",
            (strategy, symbol, entry_price, quantity, cost, now, stop_loss, take_profit),
        )
        # Debit balance
        conn.execute(
            "UPDATE accounts SET balance = balance - ? WHERE strategy = ?",
            (cost, strategy),
        )
        conn.commit()
        print(f"[writer] ✅ Opened {symbol} long @ {entry_price}, qty={quantity:.6f}, "
              f"cost={cost:.2f} in strategy '{strategy}'")
    finally:
        conn.close()

    return result
