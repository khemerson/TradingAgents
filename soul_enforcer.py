"""
SOUL Enforcer — Hard-coded validation of TradingAgents decisions.
Checks Portfolio Manager output BEFORE paper trading registration.
Violations → reject + log + Telegram notification.

Rules are CONSTANTS — the LLM cannot modify them via prompt.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Hard rules (non-negotiable) ──────────────────────────────────────
RULES = {
    "long_only": True,
    "max_position_pct": 30,
    "max_stop_loss_pct": -7,       # -7% = worst allowed
    "require_stop_loss": True,
    "require_take_profit": True,
    "min_cash_pct": 20,
    "min_confidence": 4,
    "max_sector_positions": 3,
}

BLOCKED_ACTIONS = frozenset({"SHORT", "SELL_SHORT", "PUT", "SHORT_SELL"})
VALID_ACTIONS = frozenset({"BUY", "SELL", "HOLD", "OVERWEIGHT", "UNDERWEIGHT", "WATCH"})

MICROCAP_RULES = {
    "max_position_pct": 5,
    "max_stop_loss_pct": -15,
    "min_confidence": 6,
}


@dataclass
class EnforcementResult:
    valid: bool
    violations: list[str] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"valid": self.valid, "violations": self.violations, "decision": self.decision}


# ── JSON extraction from LLM free-text ───────────────────────────────
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(
    r'\{\s*"action"\s*:.*?\}',
    re.DOTALL,
)


def extract_decision_json(text: str) -> dict | None:
    """Try to extract a structured decision JSON from Portfolio Manager output.

    Looks for ```json {...} ``` blocks first, then bare JSON objects with "action".
    Returns None if nothing parseable is found.
    """
    # Try fenced code block first
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try bare JSON with "action" key
    m = _BARE_JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ── Fallback: regex extraction from free text ─────────────────────────
_ACTION_RE = re.compile(r"\b(BUY|SELL|HOLD|SHORT|OVERWEIGHT|UNDERWEIGHT|WATCH)\b", re.IGNORECASE)
_PRICE_PATTERNS = {
    "entry_price": re.compile(
        r"(?:entry|entr[ée]e|prix\s+d['e]\s*entr[ée]e)[^0-9\n]{0,30}([0-9]+(?:[.,][0-9]+)?)",
        re.IGNORECASE,
    ),
    "stop_loss": re.compile(
        r"(?:stop[\s_-]?loss|stop|sl)[^0-9\n]{0,30}([0-9]+(?:[.,][0-9]+)?)",
        re.IGNORECASE,
    ),
    "take_profit": re.compile(
        r"(?:take[\s_-]?profit|target|tp|objectif|cible)[^0-9\n]{0,30}([0-9]+(?:[.,][0-9]+)?)",
        re.IGNORECASE,
    ),
    "position_size_pct": re.compile(
        r"(?:position[\s_-]?size|allocation|taille)[^0-9\n]{0,30}([0-9]+(?:[.,][0-9]+)?)\s*%",
        re.IGNORECASE,
    ),
    "confidence": re.compile(
        r"(?:confian?ce|confidence)[^0-9\n]{0,15}([0-9]+(?:\.[0-9]+)?)\s*(?:/\s*10)?",
        re.IGNORECASE,
    ),
}


def extract_decision_fallback(text: str, ticker: str = "") -> dict:
    """Best-effort extraction from unstructured LLM text."""
    decision = {"ticker": ticker}

    m = _ACTION_RE.search(text)
    decision["action"] = m.group(1).upper() if m else "UNKNOWN"

    for key, pat in _PRICE_PATTERNS.items():
        m = pat.search(text)
        if m:
            val = m.group(1).replace(",", ".")
            try:
                decision[key] = float(val)
            except ValueError:
                pass

    return decision


def parse_decision(final_decision_text: str, ticker: str = "") -> dict:
    """Parse the Portfolio Manager's output into a structured decision dict.

    Tries JSON extraction first, falls back to regex.
    """
    decision = extract_decision_json(final_decision_text)
    if decision is not None:
        # Normalize
        decision.setdefault("ticker", ticker)
        if "action" in decision:
            decision["action"] = str(decision["action"]).upper()
        return decision

    return extract_decision_fallback(final_decision_text, ticker)


# ── Journal probatoire des decisions (Chantier G, 2026-08-22) ─────────
# Instrumentation PURE : n'altere jamais la decision d'enforcement.
# Toute erreur d'ecriture est avalee — journaliser ne doit jamais bloquer un trade.
_DECISION_LOG = Path(
    os.environ.get(
        "HKCONSEILS_ENFORCER_LOG",
        "/home/khemerson/tradingagents/logs/enforcer_decisions.jsonl",
    )
)
_DETAILS_MAX = 500


def _log_decision(
    result: "EnforcementResult",
    portfolio_value: float,
    cash_pct: float,
    is_microcap: bool,
) -> None:
    """Append one JSONL line per enforcement decision. Never raises."""
    try:
        decision = result.decision or {}
        details = {
            "portfolio_value": portfolio_value,
            "cash_pct": cash_pct,
            "is_microcap": is_microcap,
            "entry_price": decision.get("entry_price"),
            "stop_loss": decision.get("stop_loss"),
            "take_profit": decision.get("take_profit"),
            "position_size_pct": decision.get("position_size_pct"),
            "confidence": decision.get("confidence"),
        }
        details_s = json.dumps(details, ensure_ascii=False, default=str)[:_DETAILS_MAX]
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ticker": str(decision.get("ticker", ""))[:32],
            "action_proposee": str(decision.get("action", ""))[:32],
            "verdict": "allow" if result.valid else "block",
            "regle_soul": list(result.violations),
            "details": details_s,
        }
        _DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DECISION_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Fail-safe absolu : l'enforcement prime sur sa tracabilite.
        pass


# ── Core enforcement ──────────────────────────────────────────────────
def enforce(decision: dict, portfolio_value: float = 0, cash_pct: float = 100, is_microcap: bool = False) -> EnforcementResult:
    """Validate a Portfolio Manager decision against hard rules.

    Args:
        decision: Parsed decision dict (from parse_decision).
        portfolio_value: Current portfolio value (informational).
        cash_pct: Current cash as % of portfolio.

    Returns:
        EnforcementResult with valid=True/False and list of violations.
    """
    violations: list[str] = []
    rules = {**RULES, **(MICROCAP_RULES if is_microcap else {})}
    action = str(decision.get("action", "")).upper()

    # ── Long only ─────────────────────────────────────────────────────
    if action in BLOCKED_ACTIONS:
        violations.append(f"LONG_ONLY: action '{action}' is forbidden")

    # ── Unknown action ────────────────────────────────────────────────
    if action not in VALID_ACTIONS and action not in BLOCKED_ACTIONS:
        violations.append(f"UNKNOWN_ACTION: '{action}' is not a recognized action")

    # ── BUY-specific checks ───────────────────────────────────────────
    if action in ("BUY", "OVERWEIGHT"):
        entry = _float(decision.get("entry_price"))
        sl = _float(decision.get("stop_loss"))
        tp = _float(decision.get("take_profit"))
        pos_pct = _float(decision.get("position_size_pct"))
        confidence = _float(decision.get("confidence"))

        # Stop-loss required
        if rules["require_stop_loss"] and sl <= 0:
            violations.append("STOP_LOSS_REQUIRED: no stop-loss defined")
        elif sl > 0 and entry > 0:
            sl_pct = (sl - entry) / entry * 100
            if sl_pct < rules["max_stop_loss_pct"]:
                violations.append(
                    f"STOP_LOSS_TOO_WIDE: {sl_pct:.1f}% exceeds limit of {RULES['max_stop_loss_pct']}%"
                )

        # Take-profit required
        if rules["require_take_profit"] and tp <= 0:
            violations.append("TAKE_PROFIT_REQUIRED: no take-profit defined")

        # Position sizing
        if pos_pct > rules["max_position_pct"]:
            violations.append(
                f"POSITION_TOO_LARGE: {pos_pct:.0f}% > {RULES['max_position_pct']}% max"
            )

        # Cash minimum
        if pos_pct > 0 and (cash_pct - pos_pct) < rules["min_cash_pct"]:
            remaining = cash_pct - pos_pct
            violations.append(
                f"CASH_MINIMUM: remaining cash {remaining:.1f}% < {RULES['min_cash_pct']}% min"
            )

        # Confidence minimum
        if confidence > 0 and confidence < rules["min_confidence"]:
            violations.append(
                f"LOW_CONFIDENCE: {confidence:.0f}/10 < {RULES['min_confidence']}/10 minimum"
            )

    result = EnforcementResult(
        valid=len(violations) == 0,
        violations=violations,
        decision=decision,
    )
    _log_decision(result, portfolio_value, cash_pct, is_microcap)
    return result


def _float(val: Any) -> float:
    """Safe float conversion."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
