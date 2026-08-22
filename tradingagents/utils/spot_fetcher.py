"""spot_fetcher.py — Authoritative spot price lookup for pipeline anchoring.

Used by pipeline_runner (pre-subprocess env injection) and paper_trade_writer
(divergence reject). Crypto → Binance, stocks → yfinance.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests


class SpotUnavailableError(RuntimeError):
    """Raised when no spot price could be fetched after retries."""


_CRYPTO_MAP = {
    "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "XRP-USD": "XRPUSDT",
    "BNB-USD": "BNBUSDT", "SOL-USD": "SOLUSDT", "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT", "TRX-USD": "TRXUSDT", "LINK-USD": "LINKUSDT",
    "TAO-USD": "TAOUSDT", "ZEC-USD": "ZECUSDT", "SUI20947-USD": "SUIUSDT",
}

_BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
_REQUEST_TIMEOUT = 10
_RETRIES = 2


def _is_crypto(ticker: str) -> str | None:
    up = ticker.upper()
    if up in _CRYPTO_MAP:
        return _CRYPTO_MAP[up]
    if up.endswith("-USD"):
        return up.replace("-USD", "USDT").replace("-", "")
    return None


def _fetch_binance(symbol: str) -> float:
    r = requests.get(
        _BINANCE_URL, params={"symbol": symbol}, timeout=_REQUEST_TIMEOUT
    )
    r.raise_for_status()
    return float(r.json()["price"])


def _fetch_yfinance(ticker: str) -> float:
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period="5d")
    if hist.empty:
        raise RuntimeError(f"yfinance empty history for {ticker}")
    return float(hist["Close"].iloc[-1])


def fetch_spot_price(ticker: str) -> dict[str, Any]:
    """Fetch spot price with retries. Raise SpotUnavailableError on failure.

    Returns: {"ticker": str, "spot": float, "source": str, "timestamp": ISO8601}
    """
    last_exc: Exception | None = None
    binance_symbol = _is_crypto(ticker)

    for attempt in range(_RETRIES + 1):
        try:
            if binance_symbol:
                price = _fetch_binance(binance_symbol)
                source = f"binance:{binance_symbol}"
            else:
                price = _fetch_yfinance(ticker)
                source = f"yfinance:{ticker}"
            return {
                "ticker": ticker,
                "spot": price,
                "source": source,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < _RETRIES:
                time.sleep(1.0 * (attempt + 1))

    raise SpotUnavailableError(
        f"Could not fetch spot for {ticker} after {_RETRIES + 1} tries: {last_exc}"
    )


if __name__ == "__main__":
    import json
    import sys
    for t in sys.argv[1:] or ["BTC-USD", "AAPL"]:
        try:
            print(json.dumps(fetch_spot_price(t)))
        except SpotUnavailableError as exc:
            print(f"ERROR {t}: {exc}")
