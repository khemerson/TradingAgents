"""GATE 4 - test isole de l'analyste vision.

Trois volets :
  1. sans image -> rapport de substitution, aucun appel LLM ;
  2. avec image -> appel multimodal reel au modele cerveau, rapport produit ;
  3. routage -> le graphe se compile avec l'analyste vision seul, puis avec les
     cinq, sans cible manquante.
"""

import base64
import sys
import time
from pathlib import Path

from tradingagents.agents.analysts.vision_analyst import (
    NO_IMAGE_REPORT,
    create_vision_analyst,
)
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.factory import create_llm_client

IMAGE = Path("testdata/chart_test_AAPL.png")
ok = True

print("=== 1. sans image : rapport de substitution, aucun appel LLM ===")


class _LLMInterdit:
    def invoke(self, *a, **k):
        raise AssertionError("le noeud ne doit PAS appeler le LLM sans image")


node = create_vision_analyst(_LLMInterdit())
out = node({"company_of_interest": "AAPL", "trade_date": "2026-08-22"})
if out.get("vision_report") == NO_IMAGE_REPORT:
    print("  OK — rapport de substitution rendu, LLM non sollicite")
else:
    print(f"  ECHEC — rendu inattendu : {out!r}")
    ok = False

print()
print("=== 2. avec image : appel multimodal reel ===")
if not IMAGE.exists():
    print(f"  ECHEC — image absente : {IMAGE}")
    sys.exit(1)
b64 = base64.b64encode(IMAGE.read_bytes()).decode("ascii")
print(f"  image : {IMAGE} ({IMAGE.stat().st_size} octets, {len(b64)} caracteres base64)")

llm = create_llm_client(
    provider=DEFAULT_CONFIG["llm_provider"],
    model=DEFAULT_CONFIG["deep_think_llm"],
    base_url=DEFAULT_CONFIG["backend_url"],
    max_retries=3,
).get_llm()

node = create_vision_analyst(llm)
etat = {
    "company_of_interest": "AAPL",
    "trade_date": "2026-08-22",
    "asset_type": "stock",
    "instrument_context": "The instrument to analyze is `AAPL`.",
    "chart_image_b64": b64,
}
t0 = time.time()
out = node(etat)
dt = time.time() - t0
rapport = out.get("vision_report", "")

print(f"  duree : {dt:.1f}s   longueur du rapport : {len(rapport)} caracteres")
if rapport.startswith("Vision Analyst: erreur"):
    print(f"  ECHEC — {rapport[:300]}")
    ok = False
elif len(rapport) < 200:
    print(f"  ECHEC — rapport trop court : {rapport!r}")
    ok = False
else:
    print("  OK — rapport produit")
    print("  --- extrait ---")
    for ligne in rapport.splitlines()[:14]:
        print("   ", ligne[:110])
    print("  ---------------")
    attendus = ["PATTERN", "NIVEAU", "TENDANCE", "BIAIS", "CONFIANCE"]
    presents = [s for s in attendus if s.lower() in rapport.lower()]
    print(f"  sections attendues presentes : {len(presents)}/{len(attendus)} -> {presents}")
    if len(presents) < 3:
        print("  ECHEC — le rapport ne suit pas la structure demandee")
        ok = False

print()
print("=== 3. routage : compilation du graphe ===")
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

for selection in (["vision"], ["market", "social", "news", "fundamentals", "vision"]):
    try:
        g = TradingAgentsGraph(selected_analysts=selection).graph.get_graph()
        cibles = {str(e.target) for e in g.edges}
        noeuds = {str(n) for n in g.nodes}
        orphelines = cibles - noeuds
        etat_txt = "OK" if not orphelines else f"ECHEC — cibles sans noeud : {orphelines}"
        if orphelines:
            ok = False
        print(f"  {str(selection):58} {etat_txt}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {str(selection):58} ECHEC — {type(exc).__name__}: {exc}")
        ok = False

print()
print("VERDICT :", "TOUT OK" if ok else "ECHEC")
sys.exit(0 if ok else 1)
