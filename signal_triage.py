#!/usr/bin/env python3
"""
signal_triage.py — Polls Telegram private channel for new messages,
triages each via Qwen3.5-27B (vision-capable), returns actionable signals.

Requires:
- Telethon session for reading the private channel
- .env with TRIAGE_API_ID, TRIAGE_API_HASH, TRIAGE_SESSION_STRING, TRIAGE_CHANNEL_ID
- Qwen3.5-27B accessible at HKCONSEILS_BASE_URL__QWEN3_5_27B (default 192.168.1.225:8080)

Usage:
    python signal_triage.py                   # normal run
    python signal_triage.py --dry-run         # classify but don't update state
    python signal_triage.py --max-messages 5  # limit messages to process
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
STATE_FILE = DATA_DIR / "triage_state.json"
ENV_FILE = Path(__file__).parent / ".env.triage"
CERVEAU_URL = os.environ.get(
    "HKCONSEILS_BASE_URL__QWEN3_5_27B", "http://192.168.1.225:8080"
)
MAX_INITIAL_MESSAGES = 20
TRIAGE_TIMEOUT = 60  # seconds per message


# ── Env loading ───────────────────────────────────────────────────────
def _load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _get_config() -> dict:
    """Load triage config from .env.triage or environment."""
    env = _load_env(ENV_FILE)
    return {
        "api_id": int(os.environ.get("TRIAGE_API_ID", env.get("TRIAGE_API_ID", "0"))),
        "api_hash": os.environ.get("TRIAGE_API_HASH", env.get("TRIAGE_API_HASH", "")),
        "session_string": os.environ.get(
            "TRIAGE_SESSION_STRING", env.get("TRIAGE_SESSION_STRING", "")
        ),
        "channel_id": int(
            os.environ.get("TRIAGE_CHANNEL_ID", env.get("TRIAGE_CHANNEL_ID", "-5158205216"))
        ),
    }


# ── State management ─────────────────────────────────────────────────
def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_processed_id": 0, "last_run": None}


def _save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── LLM triage via llama-server (OpenAI-compatible) ──────────────────
TRIAGE_PROMPT = """Tu es un trieur de signaux trading. Analyse le message suivant provenant d'un channel Telegram d'analyse trading.
Classifie-le en exactement une de ces 3 catégories :

SIGNAL : le message contient une recommandation claire d'achat ou de vente avec un ticker identifiable. Exemples : "BUY BTC", "Long ETH target 4000", "Short NVDA", chart annoté avec flèches d'entrée/sortie.
ANALYSE : le message contient une analyse de marché intéressante mais sans recommandation directe. Exemples : "BTC looks bullish on the weekly", "Interesting divergence on RSI for ETH".
BRUIT : le message est du bruit — promo, meme, lien sponsorisé, conversation, off-topic, message trop court ou incompréhensible.

RÈGLE CRITIQUE : en cas de doute entre SIGNAL et ANALYSE, choisis ANALYSE. Mieux vaut rater un signal que lancer une analyse sur du bruit.
Réponds UNIQUEMENT en JSON strict, pas de texte avant ou après :
{"classification": "SIGNAL" | "ANALYSE" | "BRUIT", "ticker": "BTC-USD" | null, "direction": "BUY" | "SELL" | null, "confidence": 1-10, "reason": "explication courte"}

Message à trier :
"""


async def _triage_message(text: str, image_b64: str | None = None) -> dict:
    """Send message to Qwen3.5-27B for triage classification."""
    messages = [{"role": "user", "content": TRIAGE_PROMPT + text}]

    # If image available, use vision endpoint
    if image_b64:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": TRIAGE_PROMPT + (text or "(image only)")},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                ],
            }
        ]

    payload = {
        "model": "qwen3.5-27b",
        "messages": messages,
        "max_tokens": 300,
        "temperature": 0.1,
    }

    async with httpx.AsyncClient(timeout=TRIAGE_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{CERVEAU_URL}/v1/chat/completions", json=payload
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            # Extract JSON from response (handle possible markdown wrapping)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            return json.loads(content)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError) as e:
            return {
                "classification": "BRUIT",
                "ticker": None,
                "direction": None,
                "confidence": 0,
                "reason": f"triage_error: {type(e).__name__}: {e}",
            }


# ── Telegram message fetching via Telethon ────────────────────────────
async def _fetch_messages(config: dict, last_id: int, max_msgs: int) -> list[dict]:
    """Fetch new messages from the private channel via Telethon."""
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("ERROR: telethon not installed. Run: pip install telethon", file=sys.stderr)
        return []

    if not config["session_string"]:
        print("ERROR: TRIAGE_SESSION_STRING not set in .env.triage", file=sys.stderr)
        print("Generate with: python3 -c \"from telethon.sync import TelegramClient; "
              "from telethon.sessions import StringSession; "
              f"c=TelegramClient(StringSession(), {config['api_id']}, '{config['api_hash']}'); "
              "c.start(); print(c.session.save())\"", file=sys.stderr)
        return []

    messages = []
    client = TelegramClient(
        StringSession(config["session_string"]),
        config["api_id"],
        config["api_hash"],
    )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("ERROR: Telethon session unauthorized", file=sys.stderr)
            return []

        channel = await client.get_entity(config["channel_id"])
        async for msg in client.iter_messages(channel, limit=max_msgs, min_id=last_id):
            entry = {
                "message_id": msg.id,
                "date": msg.date.isoformat() if msg.date else None,
                "text": msg.text or "",
                "has_media": msg.media is not None,
                "image_b64": None,
                "sender": str(msg.forward.chat.title if msg.forward and hasattr(msg.forward, 'chat') and msg.forward.chat else "unknown"),
            }

            # Try to download image if present
            if msg.photo:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=True) as tmp:
                        await msg.download_media(file=tmp.name)
                        with open(tmp.name, "rb") as f:
                            entry["image_b64"] = base64.b64encode(f.read()).decode()
                except Exception:
                    pass  # Skip image, triage on text only

            messages.append(entry)

    finally:
        await client.disconnect()

    # Return in chronological order (oldest first)
    return sorted(messages, key=lambda m: m["message_id"])


# ── Main triage flow ──────────────────────────────────────────────────
async def run_triage(dry_run: bool = False, max_messages: int | None = None) -> list[dict]:
    """Main entry point. Returns list of actionable signals."""
    config = _get_config()
    state = _load_state()
    last_id = state.get("last_processed_id", 0)

    limit = max_messages or MAX_INITIAL_MESSAGES
    print(f"[triage] Fetching messages after id={last_id} (limit={limit})...")

    messages = await _fetch_messages(config, last_id, limit)
    if not messages:
        print("[triage] No new messages.")
        return []

    print(f"[triage] {len(messages)} new message(s) to classify.")

    signals = []
    stats = {"SIGNAL": 0, "ANALYSE": 0, "BRUIT": 0, "error": 0}
    highest_id = last_id

    for msg in messages:
        result = await _triage_message(msg["text"], msg.get("image_b64"))
        classification = result.get("classification", "BRUIT").upper()
        stats[classification] = stats.get(classification, 0) + 1

        print(
            f"  [{classification:7s}] id={msg['message_id']} "
            f"conf={result.get('confidence', '?')} "
            f"ticker={result.get('ticker', '-')} "
            f"— {msg['text'][:60]}..."
        )

        if classification == "SIGNAL":
            signals.append(
                {
                    "message_id": msg["message_id"],
                    "ticker": result.get("ticker"),
                    "direction": result.get("direction"),
                    "confidence": result.get("confidence", 0),
                    "source": msg.get("sender", "unknown"),
                    "raw_text": msg["text"][:500],
                    "has_image": bool(msg.get("image_b64")),
                    "triage_reason": result.get("reason", ""),
                }
            )

        highest_id = max(highest_id, msg["message_id"])

    # Update state
    if not dry_run and highest_id > last_id:
        _save_state(
            {
                "last_processed_id": highest_id,
                "last_run": datetime.now(timezone.utc).isoformat(),
            }
        )
        print(f"[triage] State updated: last_processed_id={highest_id}")
    elif dry_run:
        print(f"[triage] DRY RUN — state not updated (would be {highest_id})")

    print(f"[triage] Stats: {stats}")
    print(f"[triage] Actionable signals: {len(signals)}")
    return signals


# ── CLI ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Telegram signal triage")
    parser.add_argument("--dry-run", action="store_true", help="Don't update state")
    parser.add_argument("--max-messages", type=int, default=None, help="Max messages to process")
    args = parser.parse_args()

    signals = asyncio.run(run_triage(dry_run=args.dry_run, max_messages=args.max_messages))

    if signals:
        print("\n=== Actionable Signals ===")
        for s in signals:
            print(json.dumps(s, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
