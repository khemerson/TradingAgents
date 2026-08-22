"""
watchlist_checker.py — Light check for WATCH tickers.
Checks price + RSI + volume (~2 min per ticker, no full pipeline).
If conditions approaching -> asks brain -> if yes -> triggers full analysis.
"""
from __future__ import annotations

import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(__file__))

from microcap_data import normalize_symbol, fetch_ohlcv, compute_indicators
from watchlist_manager import get_active, update_check, trigger

BRAIN_URL = os.environ.get(
    "HKCONSEILS_BRAIN_URL_V1", "http://localhost:8081/v1"
)


def light_check(item: dict) -> dict:
    """Light check: spot price + 1h indicators. ~2s per ticker."""
    ticker = item["ticker"]
    symbol = normalize_symbol(ticker)

    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=5
        )
        if r.status_code != 200:
            return {"status": "error", "detail": f"Ticker {symbol} not found on Binance"}
        spot = float(r.json()["price"])
    except Exception as e:
        return {"status": "error", "detail": str(e)}

    candles = fetch_ohlcv(symbol, "1h", 168)
    if not candles or len(candles) < 20:
        return {"status": "error", "detail": "Insufficient OHLCV data"}

    indicators = compute_indicators(candles)
    if "error" in indicators:
        return {"status": "error", "detail": indicators["error"]}

    entry_target = item.get("entry_target", 0)
    price_dist_pct = (
        abs(spot - entry_target) / entry_target * 100 if entry_target > 0 else 999
    )

    detail = (
        f"Spot: ${spot:.6f} | Target: ${entry_target:.6f} | Distance: {price_dist_pct:.1f}%\n"
        f"RSI(14): {indicators['rsi_14']} | Volume ratio: {indicators['volume_ratio']}x | "
        f"24h change: {indicators['change_24h_pct']}%"
    )

    # Trigger heuristics
    if price_dist_pct < 5:
        return {"status": "approaching", "detail": detail, "spot": spot, "indicators": indicators}

    if indicators["volume_ratio"] > 3 and (
        (spot > entry_target and entry_target > 0) or indicators["change_24h_pct"] > 10
    ):
        return {"status": "approaching", "detail": detail, "spot": spot, "indicators": indicators}

    if indicators["rsi_14"] < 30 and spot < entry_target:
        return {"status": "approaching", "detail": detail, "spot": spot, "indicators": indicators}

    return {"status": "not_ready", "detail": detail, "spot": spot, "indicators": indicators}


def brain_evaluate_conditions(item: dict, check_result: dict) -> bool:
    """Ask brain (1 short LLM call) if WATCH conditions are met."""
    prompt = (
        f"Tu surveilles le ticker {item['ticker']} pour une entree potentielle.\n\n"
        f"Conditions d'entree definies : {item['conditions']}\n"
        f"Entry target : ${item.get('entry_target', 'N/A')}\n\n"
        f"Donnees actuelles :\n{check_result['detail']}\n\n"
        f"Question : Les conditions d'entree sont-elles reunies ou proches d'etre reunies ?\n"
        f'Reponds UNIQUEMENT par un JSON :\n'
        f'{{"launch_full_analysis": true/false, "reason": "explication courte"}}'
    )

    try:
        r = requests.post(
            f"{BRAIN_URL}/chat/completions",
            json={
                "model": "qwen38-27b",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.3,
            },
            headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', 'none')}"},
            timeout=60,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{[^}]+\}", content)
            if match:
                result = json.loads(match.group())
                return result.get("launch_full_analysis", False)
    except Exception as e:
        print(f"[watch] Brain evaluate error for {item['ticker']}: {e}")
    return False


def check_all() -> list[str]:
    """Check all active watchlist tickers. Returns tickers to run full pipeline on."""
    active = get_active()
    if not active:
        print("[watch] Watchlist vide")
        return []

    print(f"[watch] {len(active)} tickers actifs")
    tickers_to_analyze = []

    for item in active:
        ticker = item["ticker"]
        print(f"\n  [watch] {ticker} (check #{item['checks_count'] + 1}, added {item['added'][:10]})")

        result = light_check(item)
        print(f"     {result['status']} -- {result['detail']}")

        update_check(ticker, result["status"])

        if result["status"] == "error":
            continue

        if result["status"] == "approaching":
            print(f"     Conditions proches -- evaluation cerveau...")
            should_launch = brain_evaluate_conditions(item, result)
            if should_launch:
                print(f"     -> Cerveau recommande analyse complete")
                tickers_to_analyze.append(ticker)
                trigger(ticker, f"Light check: approaching + brain confirmed")
            else:
                print(f"     -> Cerveau dit d'attendre")
        else:
            print(f"     -> Conditions pas encore reunies")

    return tickers_to_analyze


if __name__ == "__main__":
    tickers = check_all()
    if tickers:
        print(f"\nTickers a analyser : {tickers}")
    else:
        print(f"\nAucun ticker a analyser")
