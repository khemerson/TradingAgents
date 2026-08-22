"""Tests du journal probatoire de l'enforcer (Chantier G, 2026-08-22).

Vérifie trois propriétés :
  1. un verdict `allow` produit une ligne JSONL correcte
  2. un verdict `block` produit une ligne avec les règles SOUL violées
  3. une panne d'écriture ne bloque NI n'altère la décision d'enforcement
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  OK   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


def load_enforcer(log_path):
    os.environ["HKCONSEILS_ENFORCER_LOG"] = str(log_path)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import soul_enforcer
    importlib.reload(soul_enforcer)
    return soul_enforcer


def read_lines(p):
    if not Path(p).exists():
        return []
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def test_allow():
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "sub" / "journal.jsonl"     # sous-dossier: teste aussi mkdir
        se = load_enforcer(log)
        r = se.enforce({"ticker": "AAPL", "action": "BUY", "entry_price": 100,
                        "stop_loss": 95, "take_profit": 120,
                        "position_size_pct": 10, "confidence": 8}, cash_pct=100)
        check("allow : decision valide", r.valid is True)
        lines = read_lines(log)
        check("allow : une ligne ecrite", len(lines) == 1, f"({len(lines)})")
        if lines:
            e = lines[0]
            check("allow : verdict=allow", e["verdict"] == "allow", e.get("verdict"))
            check("allow : ticker releve", e["ticker"] == "AAPL", e.get("ticker"))
            check("allow : action relevee", e["action_proposee"] == "BUY", e.get("action_proposee"))
            check("allow : aucune regle violee", e["regle_soul"] == [], e.get("regle_soul"))
            check("allow : ts ISO present", "T" in e["ts"])
            check("allow : details <= 500 c.", len(e["details"]) <= 500, len(e["details"]))


def test_block():
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "journal.jsonl"
        se = load_enforcer(log)
        r = se.enforce({"ticker": "TSLA", "action": "SHORT"}, cash_pct=100)
        check("block : decision refusee", r.valid is False)
        lines = read_lines(log)
        check("block : une ligne ecrite", len(lines) == 1, f"({len(lines)})")
        if lines:
            e = lines[0]
            check("block : verdict=block", e["verdict"] == "block", e.get("verdict"))
            check("block : regle SOUL tracee",
                  any("LONG_ONLY" in v for v in e["regle_soul"]), e.get("regle_soul"))
            check("block : verdict == etat reel", (e["verdict"] == "block") == (not r.valid))


def test_ecriture_impossible():
    """Une panne de journalisation ne doit ni lever, ni changer la decision."""
    with tempfile.TemporaryDirectory() as d:
        # un FICHIER la ou le code veut un DOSSIER -> mkdir/open echouent
        blocker = Path(d) / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        log = blocker / "journal.jsonl"
        se = load_enforcer(log)
        try:
            r_ok = se.enforce({"ticker": "MSFT", "action": "BUY", "entry_price": 100,
                               "stop_loss": 97, "take_profit": 110,
                               "position_size_pct": 5, "confidence": 9}, cash_pct=100)
            r_ko = se.enforce({"ticker": "NVDA", "action": "SHORT"}, cash_pct=100)
            check("panne : aucune exception levee", True)
            check("panne : verdict allow inchange", r_ok.valid is True)
            check("panne : verdict block inchange", r_ko.valid is False)
            check("panne : violations preservees",
                  any("LONG_ONLY" in v for v in r_ko.violations), r_ko.violations)
        except Exception as exc:                                  # noqa: BLE001
            check("panne : aucune exception levee", False, f"-> {type(exc).__name__}: {exc}")


def test_append_only():
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "journal.jsonl"
        se = load_enforcer(log)
        for i in range(3):
            se.enforce({"ticker": f"T{i}", "action": "HOLD"}, cash_pct=100)
        lines = read_lines(log)
        check("append-only : 3 lignes cumulees", len(lines) == 3, f"({len(lines)})")
        check("append-only : ordre preserve",
              [l["ticker"] for l in lines] == ["T0", "T1", "T2"],
              [l["ticker"] for l in lines])


if __name__ == "__main__":
    print("Tests du journal probatoire de l'enforcer")
    print("=" * 50)
    test_allow()
    test_block()
    test_ecriture_impossible()
    test_append_only()
    print("=" * 50)
    if FAILED:
        print(f"ECHECS ({len(FAILED)}) : {', '.join(FAILED)}")
        sys.exit(1)
    print("Tous les tests du journal passent.")
