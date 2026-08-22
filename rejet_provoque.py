"""GATE 7.4 - rejet fail-closed PROVOQUE dans le chemin reel.

Un « zero violation » ne prouve rien a lui seul : il faut montrer que le refus
fonctionne. On injecte donc une decision violant une regle SOUL et on exige un
verdict `block`, la regle nommee, et une trace dans le journal.

Deux voies d'injection, car une decision interdite peut atteindre l'enforcer par
deux chemins distincts :
  A. par le filet textuel — le schema refuse SHORT, la sortie structuree echoue,
     et c'est l'analyse textuelle qui presente la decision ;
  B. par le chemin typé — on force un dictionnaire deja projete, pour verifier
     que l'enforcer ne se repose pas sur le schema pour bloquer.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from soul_enforcer import FALLBACK_COUNTER, _DECISION_LOG, decision_from_state, enforce  # noqa: E402

journal = Path(_DECISION_LOG)
avant = journal.stat().st_size if journal.exists() else 0
echecs = 0


def controler(nom, decision, attendu_regle):
    global echecs
    res = enforce(decision, cash_pct=80, is_microcap=False)
    nomme = any(attendu_regle in v for v in res.violations)
    ok = (not res.valid) and nomme
    print(f"  {'OK   ' if ok else 'ECHEC'} {nom}")
    print(f"         verdict    : {'block' if not res.valid else 'allow'}")
    print(f"         violations : {res.violations}")
    if not ok:
        echecs += 1
    return res


print("=== A. injection par le filet textuel (chemin reel du repli) ===")
# Texte NON ambigu. Le motif d extraction retient la PREMIERE action
# rencontree : une prose mentionnant une autre action avant la decision
# ferait lire cette autre action. Faiblesse HISTORIQUE du repli textuel,
# verifiee identique dans l implementation de reference, et l une des
# raisons pour lesquelles le chemin nominal est desormais la decision typee.
texte = "Decision finale : SHORT sur AAPL. Le momentum s inverse nettement.\n"
d_a = decision_from_state({}, texte, ticker="AAPL")
print(f"  decision reconstruite : {d_a}")
controler("SHORT via analyse textuelle", d_a, "LONG_ONLY")

print()
print("=== B. injection par le chemin typé (dictionnaire force) ===")
d_b = decision_from_state(
    {"soul_decision": {"action": "SHORT", "ticker": "AAPL", "entry_price": 231.4,
                       "stop_loss": 240.0, "take_profit": 200.0,
                       "position_size_pct": 5.0, "confidence": 8.0}},
    "", ticker="AAPL",
)
print(f"  decision presentee : {d_b}")
controler("SHORT via chemin typé", d_b, "LONG_ONLY")

print()
print("=== C. temoin inverse : une decision conforme doit passer ===")
d_c = {"action": "BUY", "ticker": "AAPL", "entry_price": 100.0, "stop_loss": 95.0,
       "take_profit": 120.0, "position_size_pct": 10.0, "confidence": 7.0}
res_c = enforce(d_c, cash_pct=80, is_microcap=False)
if res_c.valid and not res_c.violations:
    print("  OK    decision conforme acceptee — l'enforcer ne refuse pas tout")
else:
    print(f"  ECHEC la decision conforme est refusee : {res_c.violations}")
    echecs += 1

print()
print("=== trace dans le journal ===")
apres = journal.stat().st_size if journal.exists() else 0
lignes = journal.read_text(encoding="utf-8").strip().splitlines()
print(f"  journal : {avant} -> {apres} octets, {len(lignes)} ligne(s) au total")
for ligne in lignes[-3:]:
    e = json.loads(ligne)
    e["details"] = "<tronqué>"
    print(f"    {json.dumps(e, ensure_ascii=False)}")

bloques = [json.loads(x) for x in lignes if json.loads(x).get("verdict") == "block"]
print(f"  lignes de verdict 'block' : {len(bloques)}")
nommees = [b for b in bloques if any("LONG_ONLY" in r for r in b.get("regle_soul", []))]
print(f"  dont regle SOUL nommee    : {len(nommees)}")
if len(nommees) < 2:
    print("  ECHEC : les deux rejets ne sont pas traces avec leur regle")
    echecs += 1

print()
print(f"  compteur de provenance : {dict(FALLBACK_COUNTER)}")
print()
print("VERDICT :", "rejet fail-closed PROUVE sur les deux chemins" if echecs == 0 else f"{echecs} ECHEC(S)")
sys.exit(1 if echecs else 0)
