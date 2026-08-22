"""
Phase 3.A.1 - Debate summarizer node (shadow mode).

Inserted in the LangGraph workflow between Research Manager and Trader.
Reads bull_history/bear_history from state, calls a dedicated Qwen3.5-27B
client through LiteLLM, validates format, persists a JSONL audit log,
and stores a structured `debate_summary` dict in the state.

Shadow mode: nothing downstream consumes `debate_summary` yet. The summary
is observability-only. Trader/Risk/PM keep receiving `debate_history` as
they did before.

Toggle: env var `DEBATE_SUMMARIZER_ENABLED` overrides config. Truthy values
are anything other than "0", "false", "no", "off" (case-insensitive).
Setting it to "false" turns the node into a no-op without code change.

Reference price source: env vars `HKCONSEILS_REF_PRICE`, `HKCONSEILS_REF_TS`,
`HKCONSEILS_REF_SOURCE`, `HKCONSEILS_REF_TICKER`. Same contract as the Ch.1
REFERENCE_PRICE_HEADER injected into the 13 prod agents.
"""
from __future__ import annotations

import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from tradingagents.default_config import DEFAULT_CONFIG


_PROMPT_TEMPLATE = """You are summarizing a Bull vs Bear debate for trading decision support.

DEBATE TRANSCRIPT:
{debate_history}

REFERENCE PRICE (AUTHORITATIVE):
{ticker} = ${reference_price} as of {reference_timestamp}

Produce a structured summary in this exact format:

## Bull Case (top 3 arguments, ranked by strength)
1. [argument 1, max 50 words]
2. [argument 2, max 50 words]
3. [argument 3, max 50 words]

## Bear Case (top 3 arguments, ranked by strength)
1. [argument 1, max 50 words]
2. [argument 2, max 50 words]
3. [argument 3, max 50 words]

## Key Disagreements (max 3 points where Bull and Bear directly clash)
- [point 1, max 30 words]
- [point 2, max 30 words]
- [point 3, max 30 words]

## Convergence Points (where Bull and Bear agree)
- [point 1, max 30 words]
- [point 2, max 30 words]

## Unresolved Questions (information gaps)
- [question 1]
- [question 2]

Constraints:
- Faithful to the original arguments - do NOT invent
- Quantify claims when possible (numbers, ratios, dates)
- Anchor any price discussion to the reference price above
- Ignore arguments that contradict the reference price
- Total output: <= 2000 tokens
"""


_SECTION_PATTERNS = [
    r"^##\s+Bull\s+Case",
    r"^##\s+Bear\s+Case",
    r"^##\s+Key\s+Disagreements",
    r"^##\s+Convergence\s+Points",
    r"^##\s+Unresolved\s+Questions",
]


def _validate_format(text: str | None) -> int:
    """Return number of required sections present in `text` (0..5)."""
    if not text:
        return 0
    return sum(
        1
        for pat in _SECTION_PATTERNS
        if re.search(pat, text, re.MULTILINE | re.IGNORECASE)
    )


def _is_truthy_env(value: str) -> bool:
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def _build_messages(
    debate_text: str, ticker: str, ref_price: str, ref_ts: str
) -> list[dict[str, str]]:
    prompt = _PROMPT_TEMPLATE.format(
        debate_history=debate_text,
        ticker=ticker or "?",
        reference_price=ref_price or "n/a",
        reference_timestamp=ref_ts or "n/a",
    )
    return [{"role": "user", "content": prompt}]


def _call_qwen(
    messages: list[dict[str, str]],
    *,
    model: str,
    base_url: str,
    api_key: str,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> dict[str, Any]:
    """Call the dedicated summarizer model via LiteLLM. Best-effort."""
    t0 = time.perf_counter()
    err: str | None = None
    output_text = ""
    finish_reason: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        choice = resp.choices[0]
        msg = choice.message
        output_text = msg.content or ""
        # Qwen3 thinking-mode fallback: if content is empty, salvage reasoning_content.
        rc = getattr(msg, "reasoning_content", "") or ""
        if not output_text and rc:
            output_text = rc
        finish_reason = choice.finish_reason
        usage = getattr(resp, "usage", None)
        if usage is not None:
            tokens_in = getattr(usage, "prompt_tokens", None)
            tokens_out = getattr(usage, "completion_tokens", None)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    return {
        "summary_text": output_text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "latency_seconds": round(time.perf_counter() - t0, 2),
        "finish_reason": finish_reason,
        "error": err,
    }


def _log_to_jsonl(record: dict[str, Any], log_dir: Path) -> None:
    """Append a single JSON record to data/debate_summaries_<date>.jsonl. Best-effort."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = log_dir / f"debate_summaries_{date_str}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # Never break the pipeline because of a logging failure.
        pass


def _resolve_log_dir(cfg: dict[str, Any]) -> Path:
    """Resolve summarizer log dir, prefer absolute next to data/."""
    raw = os.environ.get("DEBATE_SUMMARIZER_LOG_DIR") or cfg.get(
        "DEBATE_SUMMARIZER_LOG_DIR", "data"
    )
    p = Path(raw)
    if not p.is_absolute():
        # anchor to project_dir/.. (i.e., <clone>/data)
        proj_dir = Path(cfg.get("project_dir", "."))
        # default_config.project_dir is .../tradingagents/tradingagents
        # data/ lives one level up at .../tradingagents/data
        p = (proj_dir.parent / raw).resolve() if proj_dir.name == "tradingagents" else (proj_dir / raw).resolve()
    return p


def create_debate_summarizer(config: dict[str, Any] | None = None):
    """
    Factory returning the LangGraph node function.

    The returned function accepts the AgentState and returns a partial state
    dict with key `debate_summary` set to a metadata dict. It never raises.
    """
    cfg = config or DEFAULT_CONFIG

    def debate_summarizer_node(state):  # type: ignore[no-untyped-def]
        ts_now = datetime.now(timezone.utc).isoformat()

        # Toggle: env wins over config for instant rollback without redeploy
        toggle_env = os.environ.get("DEBATE_SUMMARIZER_ENABLED", "")
        if toggle_env:
            enabled = _is_truthy_env(toggle_env)
        else:
            enabled = bool(cfg.get("DEBATE_SUMMARIZER_ENABLED", True))
        if not enabled:
            return {}  # no-op, partial state empty

        # Extract debate from state
        try:
            debate = state.get("investment_debate_state", {}) or {}
            bull = debate.get("bull_history", "") or ""
            bear = debate.get("bear_history", "") or ""
        except Exception:
            bull, bear = "", ""

        ticker = state.get("company_of_interest", "") or ""
        trade_date = state.get("trade_date", "") or ""

        # Reference price contract: env vars set by pipeline_runner before subprocess
        ref_price = os.environ.get("HKCONSEILS_REF_PRICE", "") or ""
        ref_ts = os.environ.get("HKCONSEILS_REF_TS", "") or ""
        ref_source = os.environ.get("HKCONSEILS_REF_SOURCE", "") or ""

        log_dir = _resolve_log_dir(cfg)

        # Guard: missing debate -> log skipped, return partial state with status
        if not bull and not bear:
            record = {
                "ts": ts_now,
                "ticker": ticker,
                "trade_date": trade_date,
                "ref_price": ref_price,
                "ref_ts": ref_ts,
                "ref_source": ref_source,
                "status": "skipped",
                "reason": "empty bull_history and bear_history",
                "summary_text": None,
            }
            _log_to_jsonl(record, log_dir)
            return {"debate_summary": record}

        debate_text = bull + "\n\n---\n\n" + bear

        model = (
            os.environ.get("DEBATE_SUMMARIZER_MODEL")
            or cfg.get("DEBATE_SUMMARIZER_MODEL", "qwen38-27b")
        )
        try:
            max_tokens = int(
                os.environ.get("DEBATE_SUMMARIZER_MAX_TOKENS")
                or cfg.get("DEBATE_SUMMARIZER_MAX_TOKENS", 3500)
            )
            temperature = float(
                os.environ.get("DEBATE_SUMMARIZER_TEMPERATURE")
                or cfg.get("DEBATE_SUMMARIZER_TEMPERATURE", 0.3)
            )
            timeout_s = float(
                os.environ.get("DEBATE_SUMMARIZER_TIMEOUT_S")
                or cfg.get("DEBATE_SUMMARIZER_TIMEOUT_S", 90)
            )
        except (TypeError, ValueError):
            max_tokens, temperature, timeout_s = 3500, 0.3, 90.0

        base_url = (
            os.environ.get("DEBATE_SUMMARIZER_BASE_URL")
            or cfg.get("DEBATE_SUMMARIZER_BASE_URL")
            or os.environ.get("HKCONSEILS_GATEWAY_URL", "http://localhost:4000/v1")
        )
        api_key = os.environ.get("OPENAI_API_KEY", "")

        if not api_key:
            record = {
                "ts": ts_now,
                "ticker": ticker,
                "trade_date": trade_date,
                "ref_price": ref_price,
                "ref_ts": ref_ts,
                "ref_source": ref_source,
                "model": model,
                "status": "error",
                "error": "OPENAI_API_KEY not set in environment",
                "summary_text": None,
                "debate_chars": len(debate_text),
                "bull_chars": len(bull),
                "bear_chars": len(bear),
            }
            _log_to_jsonl(record, log_dir)
            return {"debate_summary": record}

        # Main call (try/except global, never break pipeline)
        try:
            messages = _build_messages(debate_text, ticker, ref_price, ref_ts)
            result = _call_qwen(
                messages,
                model=model,
                base_url=base_url,
                api_key=api_key,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout_s,
            )
            summary_text = result["summary_text"]
            format_score = _validate_format(summary_text)
            if result["error"]:
                status = "error"
            elif not summary_text:
                status = "error"
                result["error"] = "empty summary_text"
            else:
                status = "success"

            record = {
                "ts": ts_now,
                "ticker": ticker,
                "trade_date": trade_date,
                "ref_price": ref_price,
                "ref_ts": ref_ts,
                "ref_source": ref_source,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout_s": timeout_s,
                "debate_chars": len(debate_text),
                "bull_chars": len(bull),
                "bear_chars": len(bear),
                "summary_text": summary_text or None,
                "tokens_in": result["tokens_in"],
                "tokens_out": result["tokens_out"],
                "latency_seconds": result["latency_seconds"],
                "finish_reason": result["finish_reason"],
                "format_score": format_score,
                "status": status,
                "error": result["error"],
            }
        except Exception as e:  # noqa: BLE001
            record = {
                "ts": ts_now,
                "ticker": ticker,
                "trade_date": trade_date,
                "ref_price": ref_price,
                "ref_ts": ref_ts,
                "ref_source": ref_source,
                "model": model,
                "summary_text": None,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
                "debate_chars": len(debate_text),
                "bull_chars": len(bull),
                "bear_chars": len(bear),
            }

        _log_to_jsonl(record, log_dir)
        return {"debate_summary": record}

    return debate_summarizer_node
