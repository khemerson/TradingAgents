"""
SOUL Enforcer — Hard-coded validation of TradingAgents decisions.
Checks Portfolio Manager output BEFORE paper trading registration.
Violations → reject + log + Telegram notification.

Rules are CONSTANTS — the LLM cannot modify them via prompt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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
VALID_ACTIONS = frozenset({"BUY", "SELL", "HOLD", "OVERWEIGHT", "UNDERWEIGHT"})


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
_ACTION_RE = re.compile(r"\b(BUY|SELL|HOLD|SHORT|OVERWEIGHT|UNDERWEIGHT)\b", re.IGNORECASE)
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


# ── Core enforcement ──────────────────────────────────────────────────
def enforce(decision: dict, portfolio_value: float = 0, cash_pct: float = 100) -> EnforcementResult:
    """Validate a Portfolio Manager decision against hard rules.

    Args:
        decision: Parsed decision dict (from parse_decision).
        portfolio_value: Current portfolio value (informational).
        cash_pct: Current cash as % of portfolio.

    Returns:
        EnforcementResult with valid=True/False and list of violations.
    """
    violations: list[str] = []
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
        if RULES["require_stop_loss"] and sl <= 0:
            violations.append("STOP_LOSS_REQUIRED: no stop-loss defined")
        elif sl > 0 and entry > 0:
            sl_pct = (sl - entry) / entry * 100
            if sl_pct < RULES["max_stop_loss_pct"]:
                violations.append(
                    f"STOP_LOSS_TOO_WIDE: {sl_pct:.1f}% exceeds limit of {RULES['max_stop_loss_pct']}%"
                )

        # Take-profit required
        if RULES["require_take_profit"] and tp <= 0:
            violations.append("TAKE_PROFIT_REQUIRED: no take-profit defined")

        # Position sizing
        if pos_pct > RULES["max_position_pct"]:
            violations.append(
                f"POSITION_TOO_LARGE: {pos_pct:.0f}% > {RULES['max_position_pct']}% max"
            )

        # Cash minimum
        if pos_pct > 0 and (cash_pct - pos_pct) < RULES["min_cash_pct"]:
            remaining = cash_pct - pos_pct
            violations.append(
                f"CASH_MINIMUM: remaining cash {remaining:.1f}% < {RULES['min_cash_pct']}% min"
            )

        # Confidence minimum
        if confidence > 0 and confidence < RULES["min_confidence"]:
            violations.append(
                f"LOW_CONFIDENCE: {confidence:.0f}/10 < {RULES['min_confidence']}/10 minimum"
            )

    return EnforcementResult(
        valid=len(violations) == 0,
        violations=violations,
        decision=decision,
    )


def _float(val: Any) -> float:
    """Safe float conversion."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
